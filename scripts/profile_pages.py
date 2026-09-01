"""Measure query count and latency for the data each page loads.

Run against the live database:  python3 scripts/profile_pages.py
Railway sits far from the database, so a page's cost is dominated by how many
round trips it makes, not by how much SQL work the server does. This prints
both so the two can be told apart.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import event  # noqa: E402

import database as db  # noqa: E402

STATEMENTS: list[tuple[str, float]] = []


def _install_counter() -> None:
    @event.listens_for(db.ENGINE, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._prof_start = time.perf_counter()

    @event.listens_for(db.ENGINE, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        elapsed = time.perf_counter() - getattr(context, "_prof_start", time.perf_counter())
        STATEMENTS.append((" ".join(statement.split())[:110], elapsed))


def measure(label: str, fn) -> None:
    STATEMENTS.clear()
    start = time.perf_counter()
    error = ""
    try:
        fn()
    except Exception as exc:  # keep profiling the other pages
        error = f"  !! {type(exc).__name__}: {exc}"
    wall = time.perf_counter() - start
    in_db = sum(t for _s, t in STATEMENTS)
    print(
        f"{label:<34} {len(STATEMENTS):>4} stmts  "
        f"{wall * 1000:>8.0f} ms wall  {in_db * 1000:>8.0f} ms in db{error}"
    )
    slow = sorted(STATEMENTS, key=lambda x: -x[1])[:3]
    for text, seconds in slow:
        if seconds > 0.05:
            print(f"       {seconds * 1000:>7.0f} ms  {text}")


def dashboard() -> None:
    # Mirrors app.py, which wraps this block in one transaction.
    with db.shared_connection():
        db.list_po_supply_status()
        db.list_batches()
        db.list_raw_materials()
        db.list_inventory_lots()
        db.list_alloys()
        db.get_furnace_oil_stock()
        db.electricity_month_totals(2026, 9)


def production_batches() -> None:
    db.list_batches()
    db.list_alloys()
    db.list_furnaces()
    db.list_melters()


def packing_list() -> None:
    db.list_packing_lists()
    db.list_customers()
    db.list_alloys()
    db.list_finished_goods()


def test_certificate() -> None:
    rows = db.list_packing_lists_for_certificate()
    if rows:
        pl_id = int(rows[0]["Packing_list_id"])
        db.get_packing_list(pl_id)
        db.get_packing_list_certificate(pl_id)
        db.get_visual_inspection(pl_id)
        db.list_packing_list_chemistry_vs_spec(pl_id)
        db.get_test_certificate_print_payload(pl_id)


def raw_material_inventory() -> None:
    db.list_inventory_lots()
    db.list_raw_materials()


def finished_goods() -> None:
    db.list_finished_goods()
    db.list_alloys()


def auth_and_nav() -> None:
    """Runs on every single rerun, before any page body."""
    db.is_admin_user()
    db.nav_section_keys_for_role(2, "Admin")


def production_page() -> None:
    with db.shared_connection():
        db.list_furnaces()
        db.list_melters()
        db.list_production_supervisors()
        db.list_alloys(include_sidestream=False)
        db.list_raw_materials()


PAGES = [
    ("auth + nav (every rerun)", auth_and_nav),
    ("Dashboard", dashboard),
    ("Production Batch & Chemistry", production_page),
    ("Production Batches", production_batches),
    ("Packing List", packing_list),
    ("Test Certificate", test_certificate),
    ("Raw Material Inventory", raw_material_inventory),
    ("Finished Goods Inventory", finished_goods),
]


def main() -> None:
    _install_counter()
    print(f"DB: {db.DB_LABEL}\n")

    start = time.perf_counter()
    db._ensure_packing_list_ready()
    print(f"schema bootstrap: {(time.perf_counter() - start) * 1000:.0f} ms")

    db.reset_migration_guard()
    start = time.perf_counter()
    db._ensure_packing_list_ready()
    print(f"schema bootstrap (fresh process, already migrated): "
          f"{(time.perf_counter() - start) * 1000:.0f} ms\n")

    STATEMENTS.clear()
    start = time.perf_counter()
    db.fetch_one("SELECT 1 AS one")
    print(f"single query, own transaction: {(time.perf_counter() - start) * 1000:.0f} ms\n")

    print("--- first visit to each page (cold cache) ---")
    db.bump_data_version()
    for label, fn in PAGES:
        measure(label, fn)

    print("\n--- same pages again, e.g. after any click (warm cache) ---")
    for label, fn in PAGES:
        measure(label, fn)

    print("\n--- after a save, which must invalidate everything ---")
    db.bump_data_version()
    measure("Dashboard right after a write", dashboard)
    measure("Dashboard on the click after that", dashboard)


if __name__ == "__main__":
    main()
