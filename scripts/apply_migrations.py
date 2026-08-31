"""Apply Nualco Postgres migrations and provision the restricted app role."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
APP_ROLE = "nualco_app"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument(
        "--app-password",
        default=os.environ.get("NUALCO_APP_PASSWORD"),
        help="Password for nualco_app. A random value is generated when omitted.",
    )
    parser.add_argument(
        "--print-app-url",
        action="store_true",
        help="Print the restricted connection URL (use only in a secure pipe).",
    )
    return parser.parse_args()


def _app_url(admin_url: str, password: str) -> str:
    parsed = urlparse(admin_url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    admin_user = parsed.username or ""
    app_user = APP_ROLE
    if "pooler.supabase.com" in host and "." in admin_user:
        app_user = f"{APP_ROLE}.{admin_user.split('.', 1)[1]}"
    netloc = f"{app_user}:{quote(password, safe='')}@{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))


def main() -> None:
    args = _args()
    if not args.database_url:
        raise SystemExit("ADMIN_DATABASE_URL or DATABASE_URL is required")
    password = args.app_password or secrets.token_urlsafe(36)
    conn = psycopg2.connect(args.database_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD %s NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT NOBYPASSRLS").format(
                        sql.Identifier(APP_ROLE)
                    ),
                    (password,),
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT NOBYPASSRLS").format(
                        sql.Identifier(APP_ROLE)
                    ),
                    (password,),
                )

        for path in sorted(MIGRATIONS.glob("*.sql")):
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))

        with conn.cursor() as cur:
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(APP_ROLE)
            ))
            cur.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(sql.Identifier(APP_ROLE)))
            cur.execute(sql.SQL(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(sql.Identifier(APP_ROLE)))
            for signature in (
                "public.nualco_current_role_id()",
                "public.nualco_role_name()",
                "public.nualco_is_admin()",
                "public.nualco_can(text,text)",
                "public.nualco_auth_record(text)",
                "public.nualco_bootstrap_admins()",
                "public.nualco_passwords_initialized()",
                "public.nualco_set_first_admin_password(text,text)",
            ):
                cur.execute(
                    sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                        sql.SQL(signature), sql.Identifier(APP_ROLE)
                    )
                )
    finally:
        conn.close()
    if args.print_app_url:
        print(_app_url(args.database_url, password), end="")


if __name__ == "__main__":
    main()
