"""Count the round trips one logical query costs, and test cheaper variants.

Latency to the database dominates every page, so this measures the overhead
that wraps a query rather than the query itself.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, event  # noqa: E402

import database as db  # noqa: E402

N = 6


def timed(label: str, fn) -> float:
    fn()  # warm
    start = time.perf_counter()
    for _ in range(N):
        fn()
    per = (time.perf_counter() - start) / N * 1000
    print(f"  {label:<46} {per:>7.0f} ms / call")
    return per


def main() -> None:
    print(f"DB: {db.DB_LABEL}\n")

    raw_url = db._URL
    base = dict(connect_args=db._PG_CONNECT_ARGS)

    print("one bare statement on a held-open connection:")
    eng_plain = create_engine(raw_url, pool_pre_ping=False, **base)
    with eng_plain.connect() as conn:
        rtt = timed("exec SELECT 1 (no connect, no txn)", lambda: conn.exec_driver_sql("SELECT 1"))

    print(f"\n=> one round trip is about {rtt:.0f} ms\n")

    print("cost of the wrapper around each query:")
    eng_ping = create_engine(raw_url, pool_pre_ping=True, **base)

    def with_ping():
        with eng_ping.connect() as c:
            c.exec_driver_sql("SELECT 1")

    def without_ping():
        with eng_plain.connect() as c:
            c.exec_driver_sql("SELECT 1")

    def begin_block():
        with eng_plain.begin() as c:
            c.exec_driver_sql("SELECT 1")

    def begin_plus_setconfig():
        with eng_plain.begin() as c:
            c.exec_driver_sql(
                "SELECT set_config('nualco.role_name', %s, true), "
                "set_config('nualco.employee_id', %s, true)",
                ("system", ""),
            )
            c.exec_driver_sql("SELECT 1")

    def setconfig_folded():
        """Same guarantees, but the stamp rides along with the query."""
        with eng_plain.begin() as c:
            c.exec_driver_sql(
                "SELECT set_config('nualco.role_name', %s, true), "
                "set_config('nualco.employee_id', %s, true); SELECT 1",
                ("system", ""),
            )

    a = timed("connect + pre_ping + SELECT 1", with_ping)
    b = timed("connect + SELECT 1 (no pre_ping)", without_ping)
    c = timed("begin() + SELECT 1", begin_block)
    d = timed("begin() + set_config + SELECT 1  (today)", begin_plus_setconfig)
    e = timed("begin() + set_config folded into query", setconfig_folded)

    print(f"\n  pre_ping costs           ~{a - b:>6.0f} ms per checkout")
    print(f"  separate set_config costs ~{d - c:>6.0f} ms per transaction")
    print(f"  folding the stamp saves   ~{d - e:>6.0f} ms per transaction")

    print("\n10 reads, separate transactions vs one shared transaction:")

    def ten_separate():
        for _ in range(10):
            with eng_plain.begin() as conn:
                conn.exec_driver_sql(
                    "SELECT set_config('nualco.role_name', %s, true), "
                    "set_config('nualco.employee_id', %s, true)",
                    ("system", ""),
                )
                conn.exec_driver_sql("SELECT 1")

    def ten_shared():
        with eng_plain.begin() as conn:
            conn.exec_driver_sql(
                "SELECT set_config('nualco.role_name', %s, true), "
                "set_config('nualco.employee_id', %s, true)",
                ("system", ""),
            )
            for _ in range(10):
                conn.exec_driver_sql("SELECT 1")

    sep = timed("10x separate transactions", ten_separate)
    sh = timed("10x inside one shared transaction", ten_shared)
    print(f"\n  shared transaction saves ~{sep - sh:.0f} ms for 10 reads")

    eng_plain.dispose()
    eng_ping.dispose()


if __name__ == "__main__":
    main()
