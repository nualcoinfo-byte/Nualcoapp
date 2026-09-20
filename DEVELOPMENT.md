# Development and release workflow

| Tier | App | Database |
|---|---|---|
| Local | `streamlit run app.py` on your PC | Supabase **Mumbai** project (development / staging) |
| Staging | Railway `staging` environment | Same Mumbai project |
| Production | Railway `production` environment | Supabase **Singapore** project (`nualco-sg`) |

Non-production runs show a yellow banner, a `[DEVELOPMENT]` / `[STAGING]` browser-tab prefix and an `ENV:` line in the
sidebar. Production never shows them (Railway's `RAILWAY_ENVIRONMENT_NAME=production`).

## Env files (all gitignored)
- `.env.local`: the **development** database. `DATABASE_URL` = Mumbai, `APP_ENV=development`. Never put the production URL here.
- `.env.production.local`: `PRODUCTION_DATABASE_URL`. Not read by the app. Used only as the read-only source for the refresh script.
- Production credentials otherwise live only in Railway's variables.

## Day to day
1. Work on a branch and run it locally against the Mumbai database.
2. Before testing anything that changes the schema, refresh Mumbai with a copy of production:
   `python scripts/refresh_staging_db.py --yes` (about a minute). It only reads production and refuses to write to it.
3. Merge to `main`; the Railway `staging` environment deploys from `main`. Test the UI and the migration there.
4. Release to users when nobody is active: `git push origin main:production` (production deploys from the `production` branch).
5. Roll back by pushing the previous commit to `production`, or use "Redeploy" on an older Railway deployment.

## Railway staging environment (one-time setup)
Create it **without** the production `DATABASE_URL`: a duplicate of production copies its variables, including the
production database URL. Set `DATABASE_URL` (Mumbai, session pooler) and `APP_ENV=staging` on the staging service
before its first deploy, and turn on app sleeping so it costs almost nothing when idle.
