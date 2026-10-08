# TODO.md: Nualco Alloy Tracker

Status snapshot from a full code scan on **8 Oct 2026** (commit `bd3bdec`). Update this file when items are done or found.
Legend: ✅ done · 🟡 partial / works with known limits · ⬜ not started

## Features implemented

### Purchasing & raw material
- ✅ Raw Material Logging: one invoice, many lots, invoice doc + vehicle / weighment-slip photos
- ✅ Invoice approval chain: Purchase Invoice Review → Accounts Invoice Review (enforced transitions)
- ✅ Raw Material Inventory (lots, remaining kg, grade spec)
- ✅ Raw Material Purchase Correction: split lot, move to scrap (by contaminant category), return to supplier + finance action
- ✅ Brakes India: single BIL BORING material with plant per receipt, 85% usable stocking and costing, BIL Briquetting
- ✅ Brakes India toll conversion (Conversion receipts, LM25 returns at 70%, tracker page)
- ✅ Raw Material Master with effective-dated recovery % and chemistry spec; ISRI codes
- 🟡 Assay status on lots (Awaiting Assay / Ready For Melt / Not Ready for Melt): chosen once at logging; no assay step (see Next Steps)

### Production
- ✅ Production Batch & Chemistry: heat creation, Batch ID + monthly Heat no (shared counter from Oct 2026, advisory lock)
- ✅ Charge lines with trolley tare, FIFO lot allocation, photos, who saved each line; charge returns (inventory / scrap)
- ✅ Degassing, K-mould value, top/middle/bottom/vacuum samples
- ✅ Spectro chemistry entry vs alloy min/max, SF entered from spectrometer, below-detection-limit handling
- ✅ Quick Batch Input / Quick Batch Output (mobile)
- ✅ Batch Output: product + sidestream (78/79/80) lines, stand weight mandatory, piece-weight check, remelt-only guard,
  yield % vs 70% target with product / non-spec split, Mark Output Completed → FG + remelt lots
- ✅ Batch Output Correction with full audit trail and re-costing
- ✅ Daily Batch Summary, Production Batches, Production Snapshot, Production Data Analysis (estimated vs actual recovery)
- ✅ Melter Output report (CSV / Excel) for melter pay
- ✅ Admin: Correct batch ID (all-or-nothing rename across tables)
- ✅ Removed duplicate *Material Recovery & Yield* page (PR #35); its product/non-spec split moved to Batch Output

### Costing & utilities
- ✅ Furnace Oil Purchase / Consumption from tank dip readings, tank depth lookup (interpolated dip charts)
- ✅ Electricity Consumption (EB Line 1 / 2)
- ✅ Cost of Conversion per month; material + conversion ₹/kg stored on every output line
- ✅ Production and Profitability dashboard (gross margin = dispatched kg × PO rate − cost)

### Sales, dispatch & QA
- ✅ Purchase Orders (multi-alloy, documents) and All Purchase Orders
- ✅ Production Dashboard: PO priority and "to produce" allocation against shared FG
- ✅ Finished Goods Inventory
- ✅ Packing List: partial heats, several POs per list, search by heat no, Sale vs Conversion return
- ✅ Test Certificate: draft (merge heats, ≤0.15% kg round-up), visual inspection, verify / reject / return, issue, PDF
- ✅ Admin: cancel (void) an issued certificate

### Platform
- ✅ Employee login (pbkdf2), roles → nav sections, Admin password management
- ✅ Postgres RLS + IST session clock, audit stamps, correction / merge / delete logs
- ✅ Materialized-view dashboards with 4-hourly background refresh and "Refresh now"
- ✅ Staging / production split on Railway, automated prod → staging DB refresh, photo compression
- 🟡 Bill of Materials: add lines and list only; not used anywhere in production planning
- 🟡 Data Browser: generic grid editor for master data (powerful, bypasses most business rules; Admin-level tool)
- 🟡 OCR background job queue: worker and table exist; extraction handler is a stub, demo page not in the sidebar

## Active Tasks / Next Steps

### Gaps in manufacturing workflows
1. ⬜ **Dross management.** No capture of dross / skimmings kg per heat, no dross stock, no dross sale or recovery.
   Today it is hidden inside "melt loss". Add dross weight on output (or a dross table), dross inventory, and sales/
   reprocessing so yield can be split into dross vs true metal loss.
2. ⬜ **Incoming scrap assay step.** Lots are given a status at logging and nothing moves *Awaiting Assay → Ready For
   Melt* afterwards (except the Data Browser). Only *Ready For Melt* lots can be charged. Add an assay page: spectro / sample
   result per lot, compare with `Raw_Material_Spec`, then release or hold the lot.
3. ⬜ **Charge planning from chemistry.** `Raw_Material_Spec` + `Recovery` + `Alloy_Master_spec` are all present, but there is
   no charge-mix calculator to predict heat chemistry/yield before melting. The BOM table could feed this or be retired.
4. ⬜ **Spectrometer import.** Chemistry is typed by hand. Consider importing the Bruker Q2 ION export (CSV) per heat to
   remove transcription errors (the OCR job queue is the intended pattern for document extraction).
5. 🟡 **Finished goods "Under_Testing".** Status exists and is the column default; current flow posts straight to
   Available on output completion. Decide whether lab release should gate FG (link to spectro / sample OK), or drop it.

### Code health
6. ⬜ **Move the Dashboard out of `app.py`** into `app_pages/dashboard.py` and delete the legacy `if PAGE ==` path and
   `_legacy_stub`. The "Phase 1 … only the 7 pages already migrated" comment in `app.py` is stale (all other pages are migrated).
7. ⬜ **Split `database.py` (~18k lines)** by domain (schema/migrations, purchasing, production, dispatch, utilities,
   auth, reporting) behind the same `db.*` names so pages don't change.
8. ⬜ **Automated tests.** None exist. Start with pure functions (`calc_yield`, `allocate_fifo`, `lot_charge_cost`,
   `usable_weight`, heat-no / batch-id builders, certificate rounding) and a SQLite smoke test that runs `init_db()`.
   Add a GitHub Action running `py_compile`, the tests and the IST grep from DEVELOPMENT.md on PRs.
9. ⬜ **Remove dead schema/code** after confirming no data depends on it: `Production_batch.Workflow_stage`,
   `WORKFLOW_STAGES`, `update_batch_workflow` (unused), `BATCH_QA_STATUS`, `Alloy_Data_Checker` table (never read),
   FG status `Assigned` (legacy), `Access_matrix` login fallback once every user has an employee password.
10. ⬜ **Stale UI text.** *Production Batches* caption still says output is entered "after the heat is marked Completed";
    output can now be entered while input is In-Progress (commit 3813adb).
11. ⬜ **Data Browser bypasses audited flows.** `EDITABLE_TABLES` lets the grid edit `batch_output`,
    `finished_goods_inventory`, `raw_material_inventory` and `employees` directly, with no correction log and no re-costing.
    It is in the Tools section (Admin-only by default, but grantable to any role). Make those tables read-only there, or
    route the edits through Batch Output Correction / Purchase Correction.
12. ⬜ `APP_BUILD` in `app.py` is a hand-edited string (`2026-09-03-dashboard-produce`). Derive it from the git SHA
    (e.g. Railway's `RAILWAY_GIT_COMMIT_SHA`) so the sidebar shows what is really deployed.

### Release / ops
13. 🟡 PR #35 (remove Material Recovery & Yield) is on **staging**; check it there, then release with
    `git push origin main:production` at a quiet time.
