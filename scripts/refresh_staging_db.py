"""Refresh the development/staging database with a copy of production.

    python scripts/refresh_staging_db.py          # show the plan, change nothing
    python scripts/refresh_staging_db.py --yes    # do it

What it does
  * READS production (PRODUCTION_DATABASE_URL in .env.production.local) with pg_dump. Production is never written to.
  * REPLACES the `public` schema of the database in .env.local (DATABASE_URL): all its tables, data and functions.
  * Re-applies the permission hardening (anon has no access), then runs the app's own setup routines so row-level
    security and role grants are exactly what the app expects, and finally compares row counts with production.

Safety rules (the script refuses to run otherwise)
  * .env.local must say APP_ENV=development or APP_ENV=staging.
  * The target project must be a different Supabase project from production.
  * --yes is required to change anything.

Needs the PostgreSQL client tools (pg_dump / pg_restore) on PATH or in C:\\Program Files\\PostgreSQL\\<version>\\bin.
Never prints credentials.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Supabase gives the `anon` role access to everything created in `public`; production has that revoked. pg_dump's
# --no-privileges drops the dump's own ACLs, so restore the hardened state explicitly.
HARDEN_SQL = [
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon",
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon",
    "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon",
    "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon",
    "ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
]


def die(message: str) -> "None":
    print(f"REFUSING: {message}")
    raise SystemExit(2)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, raw = line.partition("=")
                values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def parts(url: str):
    import database as db  # lazy: importing binds the app engine (lazily) to .env.local

    return urlparse(db._quote_pg_password(url))


def project_ref(url: str) -> str:
    user = parts(url).username or ""
    return user.split(".", 1)[1] if "." in user else (parts(url).hostname or "")


def describe(url: str) -> str:
    p = parts(url)
    return f"project {project_ref(url)} on {p.hostname}"


def libpq_env(url: str) -> dict[str, str]:
    p = parts(url)
    env = dict(os.environ)
    env.update(
        PGHOST=p.hostname or "",
        PGPORT=str(p.port or 5432),
        PGUSER=unquote(p.username or ""),
        PGPASSWORD=unquote(p.password or ""),
        PGDATABASE=(p.path or "/postgres").lstrip("/") or "postgres",
        PGSSLMODE="require",
        PGGSSENCMODE="disable",
        PGCONNECT_TIMEOUT="30",
    )
    return env


def engine(url: str):
    import database as db
    from sqlalchemy import create_engine

    return create_engine(
        db._prepare_postgres_url(url),
        connect_args={"connect_timeout": 30, "sslmode": "require", "gssencmode": "disable"},
    )


def pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"), reverse=True)
    if candidates:
        return candidates[0]
    die(f"{name} not found. Install the PostgreSQL client tools or add them to PATH.")


def run(cmd: list[str], env: dict[str, str], label: str) -> None:
    started = time.time()
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print(f"  {label}: exit {result.returncode} in {time.time() - started:.1f}s")
    if result.returncode != 0:
        for line in (result.stderr or "").strip().splitlines()[-12:]:
            print("     ", line)
        raise SystemExit(f"{label} failed; the target may be partly refreshed. Fix the error and run again.")


def reset_public(url: str) -> None:
    """Empty the target's public schema object by object (keeps the schema and its grants)."""
    eng = engine(url)
    with eng.begin() as c:
        for kind, sql in (
            ("materialized view", "select matviewname from pg_matviews where schemaname='public'"),
            ("view", "select viewname from pg_views where schemaname='public'"),
            ("table", "select tablename from pg_tables where schemaname='public'"),
        ):
            for (name,) in c.exec_driver_sql(sql).fetchall():
                c.exec_driver_sql(f'drop {kind} if exists public."{name}" cascade')
        for (sig,) in c.exec_driver_sql(
            "select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='public'"
        ).fetchall():
            c.exec_driver_sql(f"drop function if exists {sig} cascade")
        for (name,) in c.exec_driver_sql("select sequencename from pg_sequences where schemaname='public'").fetchall():
            c.exec_driver_sql(f'drop sequence if exists public."{name}" cascade')
    eng.dispose()


def table_counts(url: str) -> dict[str, int]:
    eng = engine(url)
    with eng.connect() as c:
        names = [r[0] for r in c.exec_driver_sql(
            "select tablename from pg_tables where schemaname='public' order by 1").fetchall()]
        counts = {n: c.exec_driver_sql(f'select count(*) from public."{n}"').scalar() for n in names}
    eng.dispose()
    return counts


def anon_exposure(url: str) -> dict[str, int]:
    eng = engine(url)
    with eng.connect() as c:
        q = lambda s: c.exec_driver_sql(s).scalar()  # noqa: E731
        out = {
            "tables and views anon can use": q(
                "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' "
                "and c.relkind in ('r','m','v') and has_table_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,DELETE')"),
            "sequences anon can use": q(
                "select count(*) from pg_sequences where schemaname='public' and has_sequence_privilege("
                "'anon', quote_ident(schemaname)||'.'||quote_ident(sequencename), 'USAGE,SELECT,UPDATE')"),
            "functions anon can run": q(
                "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' "
                "and has_function_privilege('anon', p.oid, 'EXECUTE')"),
        }
    eng.dispose()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--yes", action="store_true", help="actually refresh the development database")
    args = ap.parse_args()

    dev_env = read_env(REPO / ".env.local")
    prod_env = read_env(REPO / ".env.production.local")
    target_url = dev_env.get("DATABASE_URL", "")
    source_url = prod_env.get("PRODUCTION_DATABASE_URL", "")
    if not source_url:
        die("PRODUCTION_DATABASE_URL is missing from .env.production.local.")
    if not target_url:
        die("DATABASE_URL is missing from .env.local.")
    if dev_env.get("APP_ENV", "").lower() not in ("development", "staging"):
        die("APP_ENV in .env.local must be 'development' or 'staging' before it can be overwritten.")
    if project_ref(source_url) == project_ref(target_url) or parts(source_url).hostname == parts(target_url).hostname:
        die("the target is the same Supabase project as production.")

    print("Refresh the development database from production")
    print(f"  source (read-only): {describe(source_url)}")
    print(f"  target (REPLACED) : {describe(target_url)}")
    if not args.yes:
        print("\nNothing changed. Run again with --yes to refresh the target.")
        return

    # The app's own setup routines run against the target, so make sure they can only see the target.
    os.environ["DATABASE_URL"] = target_url
    import database as db

    if project_ref(str(db._URL or "")) != project_ref(target_url):
        die("the app would not connect to the target database; check .env.local.")

    total = time.time()
    dump = Path(tempfile.gettempdir()) / f"nualco_refresh_{os.getpid()}.dump"
    toc = Path(tempfile.gettempdir()) / f"nualco_refresh_{os.getpid()}.list"
    try:
        print("1/5 dump production (read-only)")
        run([pg_tool("pg_dump"), "--schema=public", "--format=custom", "--no-owner", "--no-privileges",
             f"--file={dump}"], libpq_env(source_url), "pg_dump")
        print("2/5 empty the target's public schema")
        reset_public(target_url)
        print("3/5 restore into the target")
        listing = subprocess.run([pg_tool("pg_restore"), "-l", str(dump)], capture_output=True, text=True, check=True).stdout
        # The target already has a `public` schema: leave its CREATE/COMMENT entries out.
        toc.write_text("\n".join(l for l in listing.splitlines()
                                 if " SCHEMA - public " not in l and " COMMENT - SCHEMA public " not in l) + "\n")
        # --clean --if-exists: a running app (e.g. the staging service) re-runs its setup against the
        # emptied schema within seconds, recreating empty tables and functions. Dropping each object inside the
        # restore's own transaction means those can't make the restore fail with "already exists".
        run([pg_tool("pg_restore"), "--no-owner", "--no-privileges", f"--use-list={toc}",
             "--clean", "--if-exists", "--single-transaction",
             "--exit-on-error", f"--dbname={libpq_env(target_url)['PGDATABASE']}", str(dump)],
            libpq_env(target_url), "pg_restore")
        print("4/5 restore permissions: harden anon, then run the app's setup (row-level security and grants)")
        eng = engine(target_url)
        with eng.begin() as c:
            for stmt in HARDEN_SQL:
                c.exec_driver_sql(stmt)
        eng.dispose()
        t = time.time()
        db._ensure_packing_list_ready()
        db._ensure_company_ready()
        print(f"  app setup routines: ok in {time.time() - t:.1f}s")
        print("5/5 verify")
        src, dst = table_counts(source_url), table_counts(target_url)
        diff = {k: (src.get(k), dst.get(k)) for k in sorted(set(src) | set(dst)) if src.get(k) != dst.get(k)}
        exposure = anon_exposure(target_url)
        print(f"  tables: production {len(src)} / target {len(dst)} | rows: production {sum(src.values())} / target {sum(dst.values())}")
        print(f"  row-count differences: {diff or 'none'}")
        print(f"  anon access on target: {exposure}")
        ok = not diff and all(v == 0 for v in exposure.values())
        print(f"RESULT: {'REFRESHED' if ok else 'PROBLEMS - review the lines above'} in {time.time() - total:.0f}s")
        sys.exit(0 if ok else 1)
    finally:
        for f in (dump, toc):
            try:
                f.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
