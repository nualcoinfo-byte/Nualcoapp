# Development and release workflow

| Tier | App | Branch | Database |
|---|---|---|---|
| Local | `streamlit run app.py` on your PC | any | Supabase **Mumbai** project (development / staging) |
| Staging | Railway `staging` environment, service `nualco-staging`: https://nualco-staging-staging.up.railway.app | `main` | Same Mumbai project |
| Production | Railway `production` environment, service `nualco`: https://nualco-production.up.railway.app | `production` | Supabase **Singapore** project (`nualco-sg`) |

Non-production runs show a yellow banner, a `[DEVELOPMENT]` / `[STAGING]` browser-tab prefix and an `ENV:` line in the
sidebar. Production never shows them (Railway's `RAILWAY_ENVIRONMENT_NAME=production`; an unknown name on Railway is
treated as production too). Staging sleeps when idle, so the first visit after a quiet spell is slower.

Supabase project ids: Mumbai starts `wdbeyyfg...`, production (`nualco-sg`) starts `oryswtvr...`. Check the id before
changing anything in the Supabase dashboard.

## Env files (all gitignored)
- `.env.local`: the **development** database. `DATABASE_URL` = Mumbai (session pooler, port 5432), `APP_ENV=development`.
  Never put the production URL here.
- `.env.production.local`: `PRODUCTION_DATABASE_URL`. Not read by the app. Used only as the read-only source for the refresh script.
- Deployed services get their credentials from Railway's variables, using the **transaction pooler (port 6543)**.

## Day to day
1. Work on a branch and run it locally against the Mumbai database.
2. Before testing anything that changes the schema, refresh Mumbai with a copy of production:
   `python scripts/refresh_staging_db.py --yes` (about a minute). It only reads production, and refuses to run unless the
   target is a different Supabase project with `APP_ENV=development|staging`.
3. Merge to `main`. Only **staging** rebuilds. Test the UI and the migration there (the app applies its own schema
   changes at startup, so staging is the rehearsal against real data).
4. Release to users when nobody is active: `git push origin main:production`. Production redeploys and everyone is logged out.
   Never commit directly on `production`; it only ever moves by pushing `main` to it.
5. Roll back by pushing an older commit to `production`, or use "Redeploy" on an older deployment in Railway.

## Changing Railway variables (without printing secrets)
Pass URLs through stdin so passwords never appear on a command line or in a log:

    python -c "print(url)" | railway variables --set-from-stdin DATABASE_URL --service nualco-staging --environment staging

Setting a variable redeploys that service from its branch. Always pass `--service` and `--environment` explicitly.

## Rotating the Mumbai (development) database password
1. Supabase, Mumbai project: Database -> Settings -> Reset database password (letters and digits only).
2. Put the new password in `.env.local`.
3. Copy the new URL into the staging service's `DATABASE_URL` (port 6543, `?sslmode=require`) as above.

## Railway notes
- **No pip cache mount in the `Dockerfile`.** Railway only accepts cache mount ids of the form `s/<service id>-<path>`, so a
  hardcoded id breaks the build for every other service (this is what first broke staging).
- **Creating another environment:** create it *empty* (`railway environment new <name>`), then add the service from the repo
  with no `DATABASE_URL`, then set its variables. A duplicate of production copies production's `DATABASE_URL`.
- **The CLI acts on the linked environment and service.** After `railway environment link staging`, relink with
  `railway environment link production` and `railway service link nualco`.
- **`railway environment edit --service-config` cannot change a service's source branch or `sleepApplication`.**
  It reports "No changes to apply" and does nothing. Use `railway api` instead: `serviceConnect(id, input: {repo, branch})`
  for the branch, `serviceInstanceUpdate(serviceId, environmentId, input: {sleepApplication: true})` for sleeping.
  Read the stored config back with `railway environment config -e <env> --json` (variable values are in that output).
- Railway cannot scale a service to zero regions: removing the last region creates a deployment in a default region.

## Database permissions
Supabase gives the `anon` role access to everything created in `public`; the app's own setup revokes it on tables, but not
on materialized views, sequences or functions. `HARDEN_SQL` in `scripts/refresh_staging_db.py` covers those (and the default
privileges for future objects). Apply it to any new Supabase project, and keep it applied after a database is rebuilt.

## Dates and times: always Indian Standard Time
The app is used in India, and the Railway servers run in UTC (5.5 hours behind). Every date and time the app stamps,
defaults to or displays is IST (UTC+05:30, no daylight saving).
- In Python use `db.now_ist()` and `db.today_ist()` (in `database.py`). **Never** call `datetime.now()`, `date.today()` or
  `datetime.utcnow()` for an app date, and don't rely on Streamlit's `"today"` (`ui_date_input` already maps it to IST).
- Stored times are naive IST strings (`2026-09-21T01:13:01`, no offset), as `audit_stamp()` writes them.
- Every Postgres transaction has its clock set to `Asia/Kolkata` (`_apply_rls_session`), so `CURRENT_TIMESTAMP` column
  defaults are IST too and are written with a `+05:30` offset. A bare connection (psql, the Supabase SQL editor) is still UTC.
- Check before releasing: `grep -n -E "datetime\.now\(|date\.today\(|utcnow" app.py pages_common.py app_pages/*.py` should
  print nothing; in `database.py` only the IST helper and the deliberately timezone-aware dashboard timestamps remain.
- Rows stamped before this rule (Railway's UTC clock, or a developer PC's clock) were deliberately **not** converted: the app
  was still in development then, and their provenance is mixed. IST applies from the release that introduced it onward.

## Heat numbers and their starting number
A `Heat_no` is `YY-<furnace><month code><NNN>`, e.g. `26-1K005` (furnace 1, September 2026). The next number for a furnace-month is
the highest suffix already used plus one, so it restarts at `001` every month. To make a furnace-month continue from a
higher number (as at go-live, when the count started at 200), add a row to **Data Browser -> Heat number start**
(`Heat_no_counter_start`: `Heat_prefix` such as `26-1K`, and `Start_no`). It only ever raises the next number, never lowers it,
and never changes heat numbers already issued. It applies to that prefix only, so the next month is unaffected.
