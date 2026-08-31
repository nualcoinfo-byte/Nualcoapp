"""Verify RLS, role capabilities, and inventory reconciliation after migration."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


def _fetchall(conn, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def _scalar(conn, query: str, params: tuple[Any, ...] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return row[0] if row else None


def _assert_reconciled(admin) -> dict[str, int]:
    raw = _fetchall(
        admin,
        """
        SELECT i.lot_id, i.remaining_weight AS balance,
               COALESCE(SUM(m.quantity_delta), 0) AS movements
        FROM public.raw_material_inventory i
        LEFT JOIN public.inventory_movements m
          ON m.lot_id = i.lot_id AND m.unit = 'kg'
        GROUP BY i.lot_id, i.remaining_weight
        HAVING abs(COALESCE(i.remaining_weight, 0) -
                   COALESCE(SUM(m.quantity_delta), 0)) > 0.0005
        """,
    )
    finished = _fetchall(
        admin,
        """
        SELECT fg.bundle_id
        FROM public.finished_goods_inventory fg
        LEFT JOIN public.inventory_movements m ON m.bundle_id = fg.bundle_id
        GROUP BY fg.bundle_id, fg.output_weight, fg.output_pieces
        HAVING abs(COALESCE(fg.output_weight, 0) -
                   COALESCE(SUM(m.quantity_delta) FILTER (WHERE m.unit = 'kg'), 0))
                   > 0.0005
            OR COALESCE(fg.output_pieces, 0) <>
               COALESCE(SUM(m.quantity_delta) FILTER (WHERE m.unit = 'piece'), 0)
        """,
    )
    oil_balance = float(
        _scalar(
            admin,
            """
            SELECT COALESCE((
                SELECT closing_qty FROM public.furnace_oil_inventory
                ORDER BY inventory_date DESC LIMIT 1
            ), 0)
            """,
        )
        or 0
    )
    oil_movements = float(
        _scalar(
            admin,
            """
            SELECT COALESCE(SUM(quantity_delta), 0)
            FROM public.inventory_movements
            WHERE inventory_kind = 'furnace_oil' AND unit = 'litre'
            """,
        )
        or 0
    )
    if raw or finished or abs(oil_balance - oil_movements) > 0.0005:
        raise RuntimeError(
            "Inventory reconciliation failed: "
            f"raw={len(raw)}, finished={len(finished)}, "
            f"oil_delta={oil_balance - oil_movements:g}"
        )
    return {"raw_lots": 0, "finished_bundles": 0, "oil_mismatches": 0}


def main() -> None:
    admin_url = os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_DATABASE_URL")
    if not admin_url or not app_url:
        raise SystemExit("ADMIN_DATABASE_URL and APP_DATABASE_URL are required")
    admin = psycopg2.connect(admin_url)
    app = psycopg2.connect(app_url)
    try:
        not_forced = _fetchall(
            admin,
            """
            SELECT c.relname AS table_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND c.relname <> 'schema_migrations'
              AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
            ORDER BY c.relname
            """,
        )
        unsplit = _fetchall(
            admin,
            """
            SELECT c.relname AS table_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND c.relname <> 'schema_migrations'
              AND (
                SELECT COUNT(DISTINCT p.polcmd)
                FROM pg_policy p WHERE p.polrelid = c.oid
              ) < 4
            ORDER BY c.relname
            """,
        )
        if not_forced or unsplit:
            raise RuntimeError(
                f"RLS verification failed: not_forced={not_forced}, unsplit={unsplit}"
            )
        role = _fetchall(
            admin,
            """
            SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
            FROM pg_roles WHERE rolname = 'nualco_app'
            """,
        )
        if len(role) != 1 or any(
            role[0][flag]
            for flag in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole")
        ):
            raise RuntimeError(f"nualco_app role is not restricted: {role}")

        samples = _fetchall(
            admin,
            """
            SELECT DISTINCT ON (lower(r.role_name))
                   lower(r.role_name) AS role_name, e.employee_id
            FROM public.roles r
            JOIN public.employees e ON e.role_id = r.role_id
            WHERE lower(r.role_name) IN
                  ('admin','management','purchase','production','accounts','inventory')
              AND lower(COALESCE(e.status, 'Active')) = 'active'
            ORDER BY lower(r.role_name), e.employee_id
            """,
        )
        capabilities: dict[str, dict[str, bool]] = {}
        for sample in samples:
            with app.cursor() as cur:
                cur.execute(
                    "SELECT set_config('nualco.employee_id', %s, true)",
                    (sample["employee_id"],),
                )
                cur.execute(
                    """
                    SELECT public.nualco_can('production_batch', 'update'),
                           public.nualco_can('roles', 'delete')
                    """
                )
                can_produce, can_delete_roles = cur.fetchone()
            app.rollback()
            capabilities[sample["role_name"]] = {
                "production_update": bool(can_produce),
                "role_delete": bool(can_delete_roles),
            }
        if capabilities.get("admin", {}).get("role_delete") is not True:
            raise RuntimeError("Admin delete capability is missing")
        for role_name in ("accounts", "inventory"):
            if capabilities.get(role_name, {}).get("production_update"):
                raise RuntimeError(f"{role_name} unexpectedly has production update")

        reconciliation = _assert_reconciled(admin)
        result = {
            "tables_with_forced_rls": int(
                _scalar(
                    admin,
                    """
                    SELECT COUNT(*) FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND c.relname <> 'schema_migrations'
                      AND c.relrowsecurity AND c.relforcerowsecurity
                    """,
                )
            ),
            "split_policy_tables": "all",
            "restricted_role": role[0]["rolname"],
            "capabilities": capabilities,
            "reconciliation": reconciliation,
        }
        print(json.dumps(result, indent=2, default=str))
    finally:
        app.close()
        admin.close()


if __name__ == "__main__":
    main()
