# CLAUDE.md: Nualco Alloy Tracker

Long-term context for working on this repository. Read this first, then `DEVELOPMENT.md` (environments and releases)
and `TODO.md` (open work).

## 1. What this app is

An in-house ERP for **Nualco Private Limited** (Ambattur, Chennai), a **secondary aluminium alloy manufacturer**. Nualco buys
aluminium scrap (borings, turnings, ingot returns, etc.), sorts it, melts it in crucible furnaces, corrects the chemistry,
casts ingots to customer alloy specs (LM25 and similar, BIS designations), and dispatches them with a mill test certificate.

The app tracks the whole chain: **purchase → raw material stock → furnace heat (batch) → chemistry → output/yield → finished
goods → packing list → test certificate → dispatch**, plus utilities (furnace oil, electricity), cost of conversion,
profitability and melter pay reports. Users are shop-floor supervisors (often on phones), purchase, accounts, inventory,
management and an Admin. Everything is in **IST** and **kg / ₹**.

## 2. Tech stack

| Layer | What |
|---|---|
| Language | Python 3.12 (`.python-version`, `Dockerfile`) |
| UI / server | **Streamlit 1.62** (single process; `streamlit run app.py`) |
| Data access | **SQLAlchemy 2.0** Core with hand-written SQL (no ORM models), `psycopg2-binary` |
| Database | **PostgreSQL on Supabase**. Production = Singapore project (`nualco-sg`), dev/staging = Mumbai. Local **SQLite** (`nualco.db`) is an offline fallback only |
| Data / reports | pandas 2.3, openpyxl (Excel export), **fpdf2** (test-certificate PDF), Pillow (photo compression) |
| Hosting | **Railway**, Docker build (`Dockerfile`, `railway.toml`), 1 replica, healthcheck `/_stcore/health` |
| CI | GitHub Actions only for `refresh-staging-db.yml` (copies prod → Mumbai, daily 06:00 IST; needs repo secrets `PRODUCTION_DATABASE_URL`, `STAGING_DATABASE_URL`). No test suite |
| Branches | `main` → staging (auto-deploy). `production` → production, moved only by `git push origin main:production` |

Versions are pinned exactly in `requirements.txt` on purpose. Don't loosen them.

## 3. Code layout

```
app.py            Entry: page config, DB bootstrap, login, role-based sidebar nav, page routing, Production Dashboard
database.py       ~18.5k lines. ALL SQL, schema + startup migrations, business rules, constants, RLS, MV refresh, workers
pages_common.py   Shared UI helpers (inputs, dataframe display, dates, photos, the batch-output editor)
app_pages/*.py    One Streamlit page per file (run via st.navigation, see §6)
scripts/          One-off/ops scripts: refresh_staging_db.py, compress_existing_photos.py, import_tank_measurements.py
assets/           Logo, ISO 9001:2015 mark (used on certificates)
DEVELOPMENT.md    Environments, release process, Railway/Supabase notes, IST rule, heat-number rules
```

## 4. Data model (summary)

Schema DDL lives in `_SCHEMA_SHARED` in `database.py` (placeholders `{autopk}`, `{float}`, `{pct4}`, `{blob}`, `{now}` are
filled per dialect). Later tables/columns are added by `_ensure_*` functions at startup. Table names are mixed case;
Postgres folds them to lowercase, so every SELECT aliases columns with quoted names (`x AS "X"`).

**Masters**
- `Customer_Master` (Cust_code), `Vendor_Master` (Vendor_code, auto serial), `Company_profile` (single row, Nualco as issuer)
- `Element_Master` (Serial_no, symbol; plus pseudo-elements **OE** other-each, **OT** other-total, **SF** sludge factor)
- `ISRI_CODE_TABLE`: scrap grade codes
- `Raw_Material_Master` (PK Raw_Material_Name + Effective_date; `Recovery` %, availability class, ISRI code) and
  `Raw_Material_Spec` (expected chemistry per grade/date)
- `Alloy_Master` (Alloy_id, customer, family, BIS designation, colour code) and `Alloy_Master_spec` (min/max % per element)
  - Alloy IDs **78 Broken Ingot, 79 Furnace Empty, 80 Not Ok Ingot** are *sidestream* (non-spec) outputs (`SIDESTREAM_ALLOY_IDS`)
  - Alloy **34 = LM25**, the toll-conversion return alloy for Brakes India
- `Furnace_Master`, `Crucible_Master` (one *Available* crucible per furnace), `Melter_Master`, `Trolley_Master` (tare),
  `Production_supervisor`, `State_City_Master`, `Month_code` (month → letter used in heat numbers)
- Auth: `employees` (pbkdf2 password hash), `roles`, `role_permissions` (role → nav section keys). `Access_matrix` is a legacy fallback

**Purchasing & raw material**
- `Raw_Material_Purchase`: one invoice (vendor, invoice doc, vehicle + weighment-slip photos, `Invoice_status`
  Pending with purchase → Pending with accounts → Approved / Cancelled, `Receipt_type` Purchase/Conversion, `BIL_plant`)
- `Raw_Material_Inventory`: one **lot** per material per invoice (`Lot_id`; received/remaining kg, `Cost_per_kg`,
  `Raw_Material_Status` Awaiting Assay / Ready For Melt / Not Ready for Melt, `Usable_pct`, `Parent_lot_id`,
  `Source_Batch_ID`/`Source_Alloy_id` for remelt lots made from sidestream output)
- `raw_material_correction` (+ `_line`), `scrap_inventory`, `raw_material_returns`: lot split / move-to-scrap / return-to-supplier
- `bil_briquetting` (+ `_line`): BIL BORING → BIL BRIQUTTE conversion

**Production**
- `Production_batch`: one **heat**. `Batch_ID` = `DDMMYY + furnace + shift + melt no`; `Heat_no` = `YY-<furnace><month code><NNN>`
  (see DEVELOPMENT.md; shared counter from Oct 2026, advisory lock). Holds alloy, furnace, crucible, shift A/B, melting team,
  supervisor, degassing time, sampled/defect pcs (K-mould), top/middle/bottom/vacuum sample OK/NOT OK + remarks,
  `Production_status` (input) and `Output_status`, both In-Progress/Completed
- `batch_input`: charge lines (lot, scale weight − trolley tare = net, photos, who/when)
- `batch_input_return`: unused charge sent back to inventory or scrap
- `Batch_Chemical_Composition`: spectrometer % per element for the heat
- `batch_output`: output lines (alloy incl. 78/79/80, scale − stand = net kg, pieces, photos, and stored
  `cost_of_production_per_kg`, `cost_of_production_overall_per_kg`, `conversion_rate_applied`, `conversion_expense_month`)
- `batch_output_correction` (+ `_line`): audited edits after output is Completed
- `production_batch_correction` (+ `_line`): audited Admin edits to a Completed heat (header, QA, chemistry), reason required
- `batch_id_change_log`, `Heat_no_counter_start`

**Sales & dispatch**
- `Purchase_Order` (customer PO × alloy, qty, rate, status Open/Closed/Cancelled, document)
- `Finished_Goods_Inventory`: one bundle per completed heat (Under_Testing / Available / Assigned / Dispatched / Rejected)
- `Packing_list` (+ `_batch`, `_po`): In-Progress / Approved / Cancelled; `Dispatch_type` Sale / Conversion return
- `Packing_list_certificate` (+ `_line`, `_source`): test certificate Draft → Pending verification → Verified → Issued
  (or Rejected / Void); lines can blend heats
- `Packing_list_visual_inspection`: 9 fixed QA questions (`VISUAL_INSPECTION_QUESTIONS`)

**Utilities & costing**
- `Furnace_Oil_Purchase` / `_Consumption` / `_Inventory` (+ per-tank tables), `Service_Oil_Tank_Measurement`,
  `Ten_KL_Tank_Measurement` (dip charts). `Furnace_Oil_Consumption` = one row per day (total litres);
  `Furnace_Oil_Consumption_Tank` = one row per dip reading (`Reading_id` key since Oct 2026, so a tank can be read
  several times a day). `Furnace_Oil_Purchase_Tank` stays one row per tank per purchase
- `Electricity_Consumption` (EB Line 1 / 2 meter readings)
- `Cost_of_conversion`: monthly ₹/kg rates (oil, electricity, labour, salaries, consumables, overheads, total)

**Reporting**: Postgres **materialized views** (`mv_po_supply_status`, `mv_production_analysis_*`,
`mv_raw_material_stock_summary`, `mv_melter_output`, …) refreshed every 4 h by a background scheduler
(`start_dashboard_refresh_scheduler`), logged in `materialized_view_refresh_log`; pages show a "Refresh now" bar.

## 5. Manufacturing workflows in the code

1. **Scrap receipt & sorting.** *Raw Material Logging* records one invoice and adds each material as a lot (photos of
   vehicle and weighment slip). Invoice approval goes through *Purchase Invoice Review* → *Accounts Invoice Review*.
   After yard segregation, *Raw Material Purchase Correction* **splits** a lot into other grades at the same ₹/kg,
   **moves contaminants to scrap** (Iron, Plastic, Rubber, Dirt, Non-metallic, Other, traced to vendor) or **returns to
   supplier** (finance then raises a sales invoice or debit note).
2. **Pre-processing.** BIL BORING (Brakes India) is centrifuged and magnetically separated: stocked at **85% usable**
   (`PROCESSING_USABLE_PCT`), and costed per usable kg (`lot_charge_cost_sql`). *BIL Briquetting* presses borings into
   briquettes, FIFO from oldest lots.
3. **Furnace heat (batch).** *Production Batch & Chemistry* (desktop) or *Quick Batch Input* (phone) creates the heat
   and adds trolley charge lines from **Ready For Melt** lots (FIFO allocation, `allocate_fifo`). Melter name and
   production supervisor start **blank** on a new heat and must be selected before saving (Quick Batch Input creates
   heats without them). Then degassing, K-mould test (defect/sampled ≤ `K_MOLD_MAX` 0.5), top/middle/bottom/vacuum
   samples, then **Mark input Completed**. A Completed heat is locked for everyone on that page; **Admin** corrects
   it only on *Production Batch Correction* (audited). Once the heat is on a packing list (= on a test certificate)
   only Melter name and Production supervisor can change (`PRODUCTION_BATCH_CREW_FIELDS`); otherwise any field,
   and a change beyond the crew must still pass every completion rule; an alloy change moves the product output
   lines. Batch ID/date/shift/melt via Admin → Correct batch ID.
4. **Spectro analysis.** Chemistry is typed in from the **Bruker Q2 ION optical emission spectrometer** per heat and
   checked against `Alloy_Master_spec` min/max (out-of-spec in red). **SF** = Fe + 2×Mn + 3×Cr is entered from the spectro;
   the formula is shown as a guide only. Below-detection values print as `<`. There is no instrument integration.
5. **Output & yield.** *Batch Output* / *Quick Batch Output* record output lines: product alloy plus **78 Broken Ingot,
   79 Furnace Empty, 80 Not Ok Ingot** (samples, furnace heel/empties, off-chem ingot). Net = scale − stand (stand is
   mandatory, 0 allowed). Avg piece weight is flagged outside **5.6–6.1 kg**. **Yield % = total output ÷ charge input**,
   target **70%** (`YIELD_TARGET_PCT`); melt loss = input − output. **Mark Output as Completed** locks the heat, posts
   product alloy to Finished Goods and turns 78/79/80 into **remelt lots** (recovery 99%) in raw material stock.
   Later fixes go only through *Batch Output Correction* (audited, re-costs).
6. **Costing.** Material ₹/kg = Σ(charge kg × lot charge cost) ÷ total output kg; overall ₹/kg adds the month's total
   conversion rate (previous available month if not yet entered). Stored on every output line.
7. **Planning.** The Dashboard lists open PO lines by priority (Overdue / Due today / Produce / Covered / Supplied) and
   "To produce" kg after allocating shared finished goods to earlier delivery dates.
8. **Dispatch & QA.** *Packing List* takes kg/pcs out of FG (part heats allowed, several POs per list) → Approved →
   *Test Certificate* Draft (Inventory/Management merge heats, round kg up ≤ 0.15%, pieces exact) → visual inspection +
   Verify (Production/Management) → Issued = dispatched. Admin can void an Issued certificate.
9. **Toll conversion.** Brakes India borings can come in as *Conversion* receipts; LM25 goes back at **70%** of collected
   weight as a "Conversion return" packing list, tracked on *Brakes India Conversion*.
10. **Utilities & people.** Furnace oil purchase/consumption from dip readings (tank charts, interpolated), electricity
    meter readings, monthly cost of conversion, *Melter Output* report (output per melting team for pay).

**Dross:** there is no dross table or dross-weight capture. Dross/slag is only implicit in melt loss and a visual
inspection question. See TODO.md.

## 6. Architecture patterns and conventions

- **All database access goes through `database.py`** (`import database as db`). Pages never build connections. Use
  `db.fetch_all`, `db.fetch_one`, `db.execute`, or a named `db.*` function. Multi-step writes use
  `with get_connection() as conn:` + `_exec(conn, ...)` so they commit or roll back together.
- **Portable SQL**: write `?` placeholders (translated to `%s` on Postgres), `ON CONFLICT` upserts, `RETURNING` ids, and
  **quote every select alias** (`Batch_ID AS "Batch_ID"`) so dict keys keep their case on Postgres.
- **Schema changes are code**: add a `CREATE TABLE IF NOT EXISTS` / `_ensure_columns(conn, table, [...])` step that runs at
  startup. They must be idempotent and safe on a populated production DB. Staging is the rehearsal (DEVELOPMENT.md).
- **Row-level security** is enabled on Postgres tables; `_apply_rls_session` stamps the acting role and sets the
  transaction time zone to Asia/Kolkata. New tables need the RLS/grant setup (`_ensure_row_level_security`) and
  `scripts/refresh_staging_db.py` `HARDEN_SQL` covers MVs, sequences and functions.
- **Audit stamps**: tables in `AUDIT_TABLES` and most transactional tables carry `Last_updated_by` and `Last_updated_datetime`
  from `db.audit_stamp()`. Corrections to locked data get their own log tables (`*_correction`, `*_change_log`,
  `*_delete_log`, `*_merge_log`). Never silently edit locked records.
- **Dates and times are IST**: use `db.now_ist()` / `db.today_ist()`, never `datetime.now()` / `date.today()`. Display
  with `format_ui_date`, enter with `ui_date_input`, store with `to_storage_date`.
- **Status-driven workflows** with explicit allowed transitions (invoice, batch input/output, FG, packing list,
  certificate). Constants for statuses, roles and thresholds live at the top of `database.py`. Reuse them, don't
  hard-code strings.
- **Permissions**: nav sections per role (`role_permissions`, defaults in `DEFAULT_ROLE_SECTIONS_BY_*`); Admin sees all.
  Some actions check roles in code (`PACKING_ROLES`, `CERT_VERIFIER_ROLES`, `RM_FINANCE_ROLES`).
  Pages in `ADMIN_ONLY_PAGES` (`app.py`) are hidden from every non-Admin role whatever sections it has; such pages
  also check `db.is_admin_user()` at the top and the `database.py` write refuses non-Admins (three layers).
- **Navigation**: the sidebar is custom (`NAV_SECTIONS` in `app.py`). Each page is registered in `MIGRATED_PAGES`
  (`st.Page("app_pages/x.py", url_path=...)`) and routed with a hidden `st.navigation`. Only the Dashboard is still
  inline in `app.py` (legacy `if PAGE == "Dashboard"`). To add a page: create `app_pages/x.py`, add it to `NAV_SECTIONS` and
  `MIGRATED_PAGES`. To remove one: delete the file and both entries (and grep for `nav_page = "<name>"` links).
- **Page style**: `st.title` + a plain-language `st.caption` explaining the rule and pointing to related pages in
  **bold**. Shop-floor pages are mobile-first (Quick pages, camera/gallery photo pickers, `prefer_rear_camera`). Use
  `empty_percent_input` / `empty_int_input` for blank-by-default numbers, `show_dataframe` / `df_from_rows` for tables,
  `compress_photo` before storing images.
- **Shared editors live in `pages_common.py`** (e.g. `render_batch_output_editor` is used by Batch Output). Reuse them;
  don't create a second page that wraps the same editor.
- **Caching**: `st.cache_resource` for one-time DB init; short-TTL `st.cache_data` only on read-only dashboards; heavy
  reports read materialized views.
- **Background threads**: dashboard MV refresher and an OCR job worker (`ocr_extraction_job`) start in `_init_postgres`.
- Comments explain *why* (often with the shop-floor incident that caused a rule). Keep that style. User-facing text is
  plain English with Indian units.

## 7. Working rules for Claude in this repo

- Branch → PR → merge to `main` (staging) → user checks staging → user says when to `git push origin main:production`.
  Never push to `production` without an explicit request; it logs everyone out.
- Before releasing: `python -m py_compile app.py database.py pages_common.py app_pages/*.py` and the IST grep in
  DEVELOPMENT.md. There are no automated tests, so describe what to click on staging to verify.
- Staging requires an employee login; Claude cannot sign in. Ask the user to verify UI changes there.
- `database.py` is huge: search with `grep -n "def name"` and read the function rather than the whole file.
- Read `TODO.md` → "Next session: start here" first; it lists what was released last time and what to check.
- Removing a page or making one admin-only: see §6 Navigation / Permissions. Prefer an audited correction page over
  unlocking a locked record in place (pattern: `correct_batch_output`, `correct_production_batch` with an `expected`
  snapshot for stale-save protection, a `*_correction` + `*_correction_line` log, and a required reason).

### Testing from the Claude cloud workspace
- PyPI is blocked there (403), so `pip install -r requirements.txt` fails and Streamlit cannot be installed. GitHub works.
- Database logic can still be run on SQLite: shallow-clone SQLAlchemy at tag `rel_2_0_52` from GitHub into the
  scratchpad, put its `lib/` on `sys.path`, set `NUALCO_FORCE_SQLITE=1`, copy `database.py` into a scratch folder (it
  creates `nualco.db` next to itself), call `db.init_db()`, seed masters (Furnace, Crucible, Melter, Production_supervisor,
  Month_code for the month, Customer, Alloy, Vendor, a purchase + Ready For Melt lot), and act as a role with
  `db.set_session_actor(name=..., role_name="Admin", role_id=2)`.
- Page scripts can be smoke-run with a small stand-in `streamlit` module (widgets return `st.session_state` values,
  `st.stop` / `st.rerun` raise) via `runpy.run_path`. It checks control flow and HTML layout, not real rendering.
- Known SQLite-only quirk: `production_snapshot()` fails on SQLite (double alias); patch it or stub it in tests.
- Always say in the PR and to the user that the real UI was not run; ask them to click through on staging.

### Releasing
- Merge the PR to `main`, then watch the staging deploy: `gh api "repos/nualcoinfo-byte/Nualcoapp/deployments?per_page=1"`
  and its `/statuses` (`success`). Open https://nualco-staging-staging.up.railway.app/ and confirm the sign-in page
  loads (it shows "Running _init_postgres()" for a few seconds after a deploy; startup schema changes run then).
- Production only on the user's explicit request: fetch `production` (`git fetch --depth=50 origin
  +refs/heads/production:refs/remotes/origin/production`), check it is an ancestor of `origin/main`, list
  `git log origin/production..origin/main` for the user, then `git push origin origin/main:refs/heads/production`.
  Watch the `nualco-app / production` deployment and load https://nualco-production.up.railway.app/.
- GitHub GraphQL is unavailable from this workspace: create and merge PRs with `gh api` REST
  (`repos/.../pulls`, `PUT repos/.../pulls/<n>/merge`).
