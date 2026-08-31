from __future__ import annotations

from contextvars import copy_context
from pathlib import Path

import pytest


def test_default_sections_for_all_six_roles(database):
    expected = {
        1: ("overview", "purchasing", "production", "utilities", "masters"),
        2: database.ALL_NAV_SECTION_KEYS,
        5: ("overview", "purchasing", "production", "utilities", "masters"),
        6: ("overview", "purchasing", "production", "utilities"),
        11: ("overview", "purchasing", "utilities"),
        13: ("overview", "purchasing", "utilities"),
    }
    for role_id, sections in expected.items():
        assert database.default_sections_for_role(role_id, None) == sections


def test_actor_context_is_request_scoped(database):
    database.set_session_actor(
        name="Admin User", employee_id="A1", role_name="Admin", role_id=2
    )
    child = copy_context()
    child.run(
        database.set_session_actor,
        name="Production User",
        employee_id="P1",
        role_name="Production",
        role_id=6,
    )
    assert database.get_acting_employee_id() == "A1"
    assert child.run(database.get_acting_employee_id) == "P1"


def test_password_authentication_uses_hash(database):
    password_hash = database.hash_password("correct horse")
    database.execute(
        "INSERT INTO roles (role_id, role_name) VALUES (?, ?)",
        (2, "Admin"),
    )
    database.execute(
        """
        INSERT INTO employees (
            employee_id, first_name, last_name, role_id, status, password_hash
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("A1", "Admin", "User", 2, "Active", password_hash),
    )
    assert database.authenticate_employee("A1", "correct horse")["role_name"] == "Admin"
    assert database.authenticate_employee("A1", "wrong password") is None


def test_grouped_charge_is_atomic_and_reconciles_movements(database):
    _purchase_id, lots = database.save_raw_material_invoice(
        None,
        "INV-1",
        "2026-08-31",
        [{"material": "Scrap", "weight": 100.0, "photo": None, "cost": 10.0}],
        "A1",
        "Ready For Melt",
    )
    lot_id = lots[0]
    database.execute(
        "INSERT INTO Production_batch (Batch_ID) VALUES (?)",
        ("B-1",),
    )
    duplicate_lot_lines = [
        {
            "Raw_Material_Name": "Scrap",
            "Lot_id": lot_id,
            "Weight": 60.0,
            "Charge_time": "2026-08-31T10:00:00",
        },
        {
            "Raw_Material_Name": "Scrap",
            "Lot_id": lot_id,
            "Weight": 50.0,
            "Charge_time": "2026-08-31T10:01:00",
        },
    ]
    with pytest.raises(ValueError, match="Insufficient stock"):
        with database.get_connection() as conn:
            database._insert_charge_lines(conn, "B-1", duplicate_lot_lines)
    assert database.fetch_one(
        'SELECT Remaining_Weight AS "remaining" FROM Raw_Material_Inventory WHERE Lot_id = ?',
        (lot_id,),
    )["remaining"] == 100.0

    with database.get_connection() as conn:
        database._insert_charge_lines(conn, "B-1", duplicate_lot_lines[:1])
    remaining = float(
        database.fetch_one(
            'SELECT Remaining_Weight AS "remaining" FROM Raw_Material_Inventory WHERE Lot_id = ?',
            (lot_id,),
        )["remaining"]
    )
    movement_total = float(
        database.fetch_one(
            """
            SELECT COALESCE(SUM(Quantity_delta), 0) AS "movement_total"
            FROM Inventory_Movements
            WHERE Inventory_kind = 'raw_material' AND Lot_id = ?
            """,
            (lot_id,),
        )["movement_total"]
    )
    assert remaining == 40.0
    assert movement_total == remaining


def test_dispatch_and_reversal_reconcile_finished_goods(database):
    database.execute("INSERT INTO Production_batch (Batch_ID) VALUES (?)", ("B-2",))
    bundle_id = database.add_finished_goods_bundle("B-2", 100.0, 10)
    lines = [{"Batch_ID": "B-2", "Weight": 20.0, "Pieces": 2}]
    with database.get_connection() as conn:
        database._apply_packing_lines_to_fg(
            conn, lines, restore=False, packing_list_id=10
        )
    with database.get_connection() as conn:
        database._apply_packing_lines_to_fg(
            conn, lines, restore=True, packing_list_id=10
        )
    balance = database.fetch_one(
        """
        SELECT Output_Weight AS "weight", Output_pieces AS "pieces"
        FROM Finished_Goods_Inventory WHERE Bundle_id = ?
        """,
        (bundle_id,),
    )
    movement = database.fetch_all(
        """
        SELECT Unit AS "unit", SUM(Quantity_delta) AS "quantity"
        FROM Inventory_Movements WHERE Bundle_id = ? GROUP BY Unit
        """,
        (bundle_id,),
    )
    totals = {row["unit"]: float(row["quantity"]) for row in movement}
    assert float(balance["weight"]) == totals["kg"] == 100.0
    assert int(balance["pieces"]) == int(totals["piece"]) == 10


def test_oil_ledger_updates_incrementally_and_reconciles(database):
    database.add_furnace_oil_purchase(
        None,
        "",
        "2026-08-01",
        "2026-08-01",
        100.0,
        purchase_type="Opening",
    )
    database.add_furnace_oil_consumption("2026-08-02", 25.0)
    database.add_furnace_oil_consumption("2026-08-02", 20.0)
    assert database.get_furnace_oil_stock() == 80.0
    movement_total = database.fetch_one(
        """
        SELECT COALESCE(SUM(Quantity_delta), 0) AS "total"
        FROM Inventory_Movements WHERE Inventory_kind = 'furnace_oil'
        """
    )
    assert float(movement_total["total"]) == 80.0


def test_packing_candidates_do_not_query_each_incomplete_batch(database, monkeypatch):
    monkeypatch.setattr(
        database,
        "get_alloy",
        lambda _alloy_id: {"Alloy_id": 1, "Alloy_name": "ADC12", "Alloy_group": "ADC"},
    )
    calls = []

    def fake_fetch(sql, params=()):
        calls.append(sql)
        return [
            {
                "Batch_ID": "B-3",
                "Production_status": "In-Progress",
                "Alloy_id": 1,
                "Alloy_name": "ADC12",
                "Alloy_group": "ADC",
                "Chemistry_count": 0,
                "Charge_line_count": 0,
            }
        ]

    monkeypatch.setattr(database, "fetch_all", fake_fetch)
    monkeypatch.setattr(
        database,
        "production_batch_completion_gaps_for_id",
        lambda _batch_id: pytest.fail("N+1 completion query was used"),
    )
    eligible, blocked = database.list_packing_batch_candidates(1)
    assert not eligible
    assert len(blocked) == 1
    assert len(calls) == 1


def test_dashboard_counts_are_not_limited_to_recent_rows(database):
    for batch_id in ("B-10", "B-11", "B-12"):
        database.execute(
            "INSERT INTO Production_batch (Batch_ID) VALUES (?)",
            (batch_id,),
        )
    assert len(database.list_batches(limit=1)) == 1
    assert database.count_batches() == 3


def test_migration_contract_contains_split_rls_and_traceability_indexes():
    sql = (
        Path(__file__).parents[1] / "migrations" / "001_harden_supabase.sql"
    ).read_text(encoding="utf-8").lower()
    assert "nobypassrls" not in sql  # role provisioning is parameterized in Python
    assert "create policy nualco_select" in sql
    assert "create policy nualco_insert" in sql
    assert "create policy nualco_update" in sql
    assert "create policy nualco_delete" in sql
    assert "force row level security" in sql
    assert "idx_inventory_movements_lot_time" in sql
    assert "idx_batch_input_lot_id" in sql


def test_restricted_pooler_url_keeps_supabase_tenant_suffix():
    from scripts.apply_migrations import _app_url

    url = _app_url(
        "postgresql://postgres.project-ref:owner@aws-1.pooler.supabase.com:5432/postgres",
        "p@ss word",
    )
    assert url.startswith(
        "postgresql://nualco_app.project-ref:p%40ss%20word@"
    )
