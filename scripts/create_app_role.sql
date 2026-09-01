-- Create the least-privilege application role for the Nualco Alloy Tracker.
--
-- WHY THIS IS REQUIRED
--
-- The app currently connects as `postgres`. That role carries the BYPASSRLS
-- attribute, which skips every row-level security policy on every table
-- unconditionally -- FORCE ROW LEVEL SECURITY included. Verify for yourself:
--
--   SELECT current_user, rolsuper, rolbypassrls
--   FROM pg_roles WHERE rolname = current_user;
--
-- While that returns rolbypassrls = true, the policies installed by
-- _ensure_row_level_security() in database.py are decorative, and access
-- control rests entirely on the Python navigation checks in app.py.
--
-- HOW TO RUN
--
-- Run this once in the Supabase SQL editor as `postgres`, replacing the
-- password placeholder first. Then put the new role in DATABASE_URL.

BEGIN;

-- 1. The role itself. It gets LOGIN and nothing else: no SUPERUSER, no
--    CREATEDB, no CREATEROLE, and critically no BYPASSRLS.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nualco_app') THEN
    CREATE ROLE nualco_app LOGIN PASSWORD 'REPLACE_WITH_A_STRONG_PASSWORD';
  END IF;
END
$$;

-- Defence in depth: make the absence of BYPASSRLS explicit and durable, so a
-- later ALTER ROLE cannot quietly grant it back without showing up here.
ALTER ROLE nualco_app NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- 2. Schema and table access. The app needs DML on its own tables but must not
--    be able to reshape the schema; migrations run separately as `postgres`.
GRANT USAGE ON SCHEMA public TO nualco_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nualco_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nualco_app;

-- Tables created later (by a future migration) should be reachable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nualco_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO nualco_app;

-- 3. The RLS helper functions the policies call.
GRANT EXECUTE ON FUNCTION public.nualco_role_name() TO nualco_app;
GRANT EXECUTE ON FUNCTION public.nualco_is_privileged() TO nualco_app;
GRANT EXECUTE ON FUNCTION public.nualco_table_allowed(text) TO nualco_app;

COMMIT;

-- 4. Confirm the role cannot bypass RLS. Expect rolbypassrls = false.
SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
FROM pg_roles
WHERE rolname = 'nualco_app';

-- 5. Confirm every public table still forces RLS. Expect zero rows.
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);

-- 6. Then set DATABASE_URL to the new role and restart the app:
--
--   postgresql://nualco_app.<project-ref>:<password>@aws-1-<region>.pooler.supabase.com:6543/postgres?sslmode=require
--
-- NOTE: schema migrations in database.py (init_db, the _ensure_* helpers,
-- _ensure_row_level_security, _ensure_inventory_guards) issue CREATE TABLE and
-- ALTER TABLE and will fail as nualco_app. Run the app once as `postgres`
-- after deploying schema changes, then switch back to nualco_app for normal
-- operation.
