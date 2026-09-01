"""Break down the one-time schema bootstrap and count real round trips.

Railway restarts the container on every deploy, so whatever this costs is paid
by the first person to open the app afterwards.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import event  # noqa: E402

import database as db  # noqa: E402

COUNT = {"n": 0, "t": 0.0}
BY_KIND: dict[str, tuple[int, float]] = {}


def _kind(statement: str) -> str:
    head = " ".join(statement.split()).upper()
    for prefix in (
        "CREATE TABLE",
        "CREATE UNIQUE INDEX",
        "CREATE INDEX",
        "CREATE POLICY",
        "ALTER TABLE",
        "DROP POLICY",
        "SELECT SET_CONFIG",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "GRANT",
        "COMMENT",
        "DO",
    ):
        if head.startswith(prefix):
            return prefix
    return head.split(" ")[0]


def _install() -> None:
    @event.listens_for(db.ENGINE, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._t0 = time.perf_counter()

    @event.listens_for(db.ENGINE, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        dt = time.perf_counter() - getattr(context, "_t0", time.perf_counter())
        COUNT["n"] += 1
        COUNT["t"] += dt
        k = _kind(statement)
        n, t = BY_KIND.get(k, (0, 0.0))
        BY_KIND[k] = (n + 1, t + dt)

    @event.listens_for(db.ENGINE, "connect")
    def _connect(dbapi_conn, rec):
        COUNT["connects"] = COUNT.get("connects", 0) + 1


def step(label: str, fn) -> None:
    n0, t0 = COUNT["n"], COUNT["t"]
    start = time.perf_counter()
    err = ""
    try:
        fn()
    except Exception as exc:
        err = f"  !! {type(exc).__name__}: {exc}"
    wall = time.perf_counter() - start
    print(
        f"  {label:<30} {COUNT['n'] - n0:>4} stmts  "
        f"{wall * 1000:>7.0f} ms wall  {(COUNT['t'] - t0) * 1000:>7.0f} ms in db{err}"
    )


def main() -> None:
    _install()
    print(f"DB: {db.DB_LABEL}\n")

    # Force a physical connection first so TLS/auth is not billed to step one.
    start = time.perf_counter()
    with db.ENGINE.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    print(f"connect + TLS handshake: {(time.perf_counter() - start) * 1000:.0f} ms")

    samples = []
    for _ in range(5):
        s = time.perf_counter()
        with db.ENGINE.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        samples.append((time.perf_counter() - s) * 1000)
    print(f"warm SELECT 1 round trips: {[f'{s:.0f}' for s in samples]} ms\n")

    print("schema bootstrap, step by step:")
    total = time.perf_counter()
    with db.get_connection() as conn:
        step("_ensure_packing_list", lambda: db._ensure_packing_list(conn))
        step("_ensure_company_profile", lambda: db._ensure_company_profile(conn))
        step("_ensure_employees", lambda: db._ensure_employees(conn))
        step("_ensure_inventory_guards", lambda: db._ensure_inventory_guards(conn))
        step("_ensure_row_level_security", lambda: db._ensure_row_level_security(conn))
    print(f"\n  TOTAL bootstrap: {(time.perf_counter() - total) * 1000:.0f} ms")
    print(f"  statements: {COUNT['n']}   physical connects: {COUNT.get('connects', 0)}")

    print("\nby statement kind:")
    for k, (n, t) in sorted(BY_KIND.items(), key=lambda x: -x[1][1]):
        print(f"  {k:<22} {n:>4} x   {t * 1000:>7.0f} ms")


if __name__ == "__main__":
    main()
