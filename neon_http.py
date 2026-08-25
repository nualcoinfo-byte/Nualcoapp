"""
Neon SQL-over-HTTPS driver.

Used when this machine can reach Neon on port 443 but Postgres on port 5432
times out (common on some Windows / corporate networks). Speaks the same
`/sql` protocol as `@neondatabase/serverless`.
"""

from __future__ import annotations

import json
import ssl
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.exc import OperationalError

_BYTEA_OID = 17
_JSON_TIMEOUT_S = 60


def _json_param(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, bytes):
        return "\\x" + value.hex()
    if isinstance(value, memoryview):
        return "\\x" + bytes(value).hex()
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _to_pg_placeholders(sql: str) -> str:
    """Convert psycopg2-style ``%s`` / ``%%`` into ``$1``, ``$2``, …"""
    out: list[str] = []
    i = 0
    n = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "%" and i + 1 < len(sql):
            nxt = sql[i + 1]
            if nxt == "s":
                n += 1
                out.append(f"${n}")
                i += 2
                continue
            if nxt == "%":
                out.append("%")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _decode_bytea(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith("\\x"):
        try:
            return bytes.fromhex(value[2:])
        except ValueError:
            return value
    if value.startswith("\\\\x"):
        try:
            return bytes.fromhex(value[3:])
        except ValueError:
            return value
    return value


def _normalize_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = payload.get("fields") or []
    bytea_cols = {
        f["name"] for f in fields if int(f.get("dataTypeID") or 0) == _BYTEA_OID
    }
    rows = payload.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            names = [f["name"] for f in fields]
            item = {names[i]: row[i] for i in range(len(names))}
        for col in bytea_cols:
            if col in item:
                item[col] = _decode_bytea(item[col])
        out.append(item)
    return out


class HttpMappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class HttpResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._rows = _normalize_rows(payload)
        self.rowcount = int(payload.get("rowCount") or 0)
        self._fields = payload.get("fields") or []

    def mappings(self) -> HttpMappingResult:
        return HttpMappingResult(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        if len(self._rows) != 1:
            raise OperationalError(
                "scalar_one() expected 1 row",
                None,
                None,
            )
        row = self._rows[0]
        if not row:
            return None
        return next(iter(row.values()))


class NeonHttpClient:
    def __init__(self, dsn: str) -> None:
        parsed = urlparse(dsn)
        if not parsed.hostname:
            raise OperationalError("Neon URL is missing a host", None, None)
        self._dsn = dsn
        self._endpoint = f"https://{parsed.hostname}/sql"
        self._ctx = ssl.create_default_context()

    def execute(self, sql: str, params: Any = None) -> HttpResult:
        if self._is_txn_control(sql):
            # Each HTTPS request is auto-commit. SAVEPOINT / BEGIN / COMMIT
            # are session commands and return 400 outside a real txn.
            return HttpResult({"rows": [], "rowCount": 0, "fields": []})
        if self._is_executemany(params):
            last = HttpResult({"rows": [], "rowCount": 0, "fields": []})
            for item in params:
                last = self._one(sql, item)
            return last
        return self._one(sql, params)

    @staticmethod
    def _is_txn_control(sql: str) -> bool:
        text = " ".join(sql.strip().rstrip(";").split()).upper()
        if text in {
            "BEGIN",
            "BEGIN TRANSACTION",
            "START TRANSACTION",
            "COMMIT",
            "ROLLBACK",
            "END",
        }:
            return True
        return (
            text.startswith("SAVEPOINT ")
            or text.startswith("RELEASE SAVEPOINT ")
            or text.startswith("RELEASE ")
            or text.startswith("ROLLBACK TO")
        )

    @staticmethod
    def _is_executemany(params: Any) -> bool:
        if not isinstance(params, (list, tuple)) or not params:
            return False
        return isinstance(params[0], (list, tuple))

    def _one(self, sql: str, params: Any) -> HttpResult:
        query = _to_pg_placeholders(sql)
        if params is None:
            values: list[Any] = []
        elif isinstance(params, dict):
            raise OperationalError("Named SQL parameters are not supported", None, None)
        else:
            values = [_json_param(v) for v in params]
        payload = {"query": query, "params": values}
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Neon-Connection-String": self._dsn,
            },
        )
        try:
            with urlopen(req, timeout=_JSON_TIMEOUT_S, context=self._ctx) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
            except Exception:
                pass
            raise OperationalError(
                f"Neon HTTP SQL failed ({exc.code}): {detail or exc}",
                None,
                exc,
            ) from exc
        except URLError as exc:
            raise OperationalError(
                f"Neon HTTP SQL connection failed: {exc}",
                None,
                exc,
            ) from exc
        except TimeoutError as exc:
            raise OperationalError("Neon HTTP SQL timed out", None, exc) from exc
        if not isinstance(raw, dict):
            raise OperationalError("Unexpected Neon HTTP SQL response", None, None)
        if raw.get("message") and "fields" not in raw and "rows" not in raw:
            raise OperationalError(str(raw.get("message")), None, None)
        return HttpResult(raw)


class HttpConnection:
    def __init__(self, client: NeonHttpClient) -> None:
        self._client = client

    def exec_driver_sql(self, sql: str, params: Any = None) -> HttpResult:
        return self._client.execute(sql, params)

    def execute(self, *args: Any, **kwargs: Any) -> HttpResult:
        raise OperationalError(
            "ORM execute() is not available over Neon HTTPS; use SQL helpers",
            None,
            None,
        )


class _HttpTxn:
    def __init__(self, client: NeonHttpClient) -> None:
        self._client = client

    def __enter__(self) -> HttpConnection:
        return HttpConnection(self._client)

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class HttpEngine:
    """Drop-in for the SQLAlchemy engine methods this app actually uses."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, dsn: str) -> None:
        self._client = NeonHttpClient(dsn)

    def begin(self) -> _HttpTxn:
        return _HttpTxn(self._client)

    def connect(self) -> _HttpTxn:
        return _HttpTxn(self._client)

    def dispose(self) -> None:
        return None
