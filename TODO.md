# TODO.md: Nualco Alloy Tracker

Status from a full code scan on **8 Oct 2026**, updated at the end of that day's session (production = `main` = `e5a1216`).
Update this file when items are done or found.
Legend: ✅ done · 🟡 partial / works with known limits · ⬜ not started

## Session log

### 8 Oct 2026 (all released to production; production and `main` both at `e5a1216`)
| PR | Change | Released |
|---|---|---|
| #35 | Removed duplicate *Material Recovery & Yield* page; its product / non-spec kg split moved to Batch Output's yield row | ✅ 17:05 IST |
| #36 | Added `CLAUDE.md` and `TODO.md` | ✅ (docs only) |
| #37 | Production Batch & Chemistry: Melter name / Production supervisor start blank on a new heat and are required. New Admin-only **Production Batch Correction** page (audited, `production_batch_correction` + `_line` tables). Removed the unaudited "Correct history" unlock | ✅ 17:05 IST |
| #38 | Production Batch Correction: on a heat that is on a packing list only Melter name and Production supervisor can change; a crew-only correction skips the completion rules (older heats with QA gaps can be fixed) | ✅ 23:50 IST |
| #39 | Production Snapshot: dropped Batch ID and Actual output (kg) columns; Production date hidden for "Choose by date" (kept in the CSV) | ✅ 23:50 IST |

Rollback points if needed: `9d4a696` (before today), `8b074f0` (after #35–#37), `e5a1216` (current).

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
- ✅ New heats start with Melter name and Production supervisor blank and must be selected (#37)
- ✅ Charge lines with trolley tare, FIFO lot allocation, photos, who saved each line; charge returns (inventory / scrap)
- ✅ Degassing, K-mould value, top/middle/bottom/vacuum samples
- ✅ Spectro chemistry entry vs alloy min/max, SF entered from spectrometer, below-detection-limit handling
- ✅ Quick Batch Input / Quick Batch Output (mobile)
- ✅ Batch Output: product + sidestream (78/79/80) lines, stand weight mandatory, piece-weight check, remelt-only guard,
  yield % vs 70% target with product / non-spec split, Mark Output Completed → FG + remelt lots
- ✅ Batch Output Correction with full audit trail and re-costing
- ✅ Production Batch Correction (Admin only, #37/#38): audited correction of Completed heats (alloy, crew, notes, QA,
  chemistry). On a packed heat only the crew can change. Not covered: charge lines, batch ID/date/shift/melt
- ✅ Daily Batch Summary, Production Batches, Production Snapshot (columns trimmed in #39), Production Data Analysis
- ✅ Melter Output report (CSV / Excel) for melter pay
- ✅ Admin: Correct batch ID (all-or-nothing rename across tables)
- ✅ Removed duplicate *Material Recovery & Yield* page (#35)

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
- ✅ Admin-only pages (`ADMIN_ONLY_PAGES` in `app.py`), hidden from every other role (#37)
- ✅ Postgres RLS + IST session clock, audit stamps, correction / merge / delete logs
- ✅ Materialized-view dashboards with 4-hourly background refresh and "Refresh now"
- ✅ Staging / production split on Railway, automated prod → staging DB refresh, photo compression
- 🟡 Bill of Materials: add lines and list only; not used anywhere in production planning
- 🟡 Data Browser: generic grid editor for master data (powerful, bypasses most business rules; Admin-level tool)
- 🟡 OCR background job queue: worker and table exist; extraction handler is a stub, demo page not in the sidebar

## Next session: start here (in this order)

1. ⬜ **Ask the user how the 8 Oct release went on production** (Claude cannot sign in). Points to confirm:
   - Admin sees *Production Batch Correction* under Production; a Production / Inventory user does not.
   - On a heat that is on a packing list, only Melter name and Production supervisor are editable and a crew change saves.
   - A new heat on Production Batch & Chemistry starts with Melter / Supervisor blank and refuses to save without them.
   - Production Snapshot: no Batch ID / Actual output columns; Production date only in the date-range view.
   - Batch Output yield row shows Product alloy and Non-spec output kg.
   If anything is wrong, fix forward on a branch, or roll back with `git push --force origin <sha>:production` **only**
   after the user explicitly asks (rollback points in the session log above).
2. ⬜ **Quick wins (one small PR):**
   - `app.py` `APP_BUILD` is still the hand-edited `2026-09-03-dashboard-produce`, so the sidebar does not show what is
     deployed. Use `os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7]` with the old string as fallback.
   - Stale text: *Production Batches* caption says output is entered "after the heat is marked Completed" (output can be
     entered while input is In-Progress). `database.py` `save_batch_outputs` errors (around lines 14677 / 14713) still
     say "unlock it on Batch Output to correct history" → point to **Batch Output Correction**.
   - Remove the stale "Phase 1 … only the 7 pages already migrated" comment above `MIGRATED_PAGES` in `app.py`.
   - SQLite-only bug: `production_snapshot()` builds `({select}) mv_batch_production_summary s`, a double alias that
     SQLite rejects ("near s: syntax error"). The `_src()` helper returns `({sql}) view` and the caller adds another alias.
     Postgres is unaffected. Fix so the offline/SQLite path and local tests work.
3. ⬜ **Data Browser bypasses audited flows.** `EDITABLE_TABLES` lets the grid edit `batch_output`,
   `finished_goods_inventory`, `raw_material_inventory` and `employees` directly, with no
   correction log or re-costing. Make those read-only in the Data Browser (the audited pages exist now: Batch Output
   Correction, Production Batch Correction, Raw Material Purchase Correction, Employee passwords).
4. ⬜ **Decide with the user:** should Production Batch Correction also correct charge lines on a Completed heat
   (affects lot stock and costing; would need its own audit lines and re-costing)? Currently out of scope.

## Backlog

### Gaps in manufacturing workflows
5. ⬜ **Dross management.** No capture of dross / skimmings kg per heat, no dross stock, no dross sale or recovery.
   Today it is hidden inside "melt loss". Add dross weight on output (or a dross table), dross inventory, and sales/
   reprocessing so yield can be split into dross vs true metal loss.
6. ⬜ **Incoming scrap assay step.** Lots are given a status at logging and nothing moves *Awaiting Assay → Ready For
   Melt* afterwards (except the Data Browser). Only *Ready For Melt* lots can be charged. Add an assay page: spectro / sample
   result per lot, compare with `Raw_Material_Spec`, then release or hold the lot.
7. ⬜ **Charge planning from chemistry.** `Raw_Material_Spec` + `Recovery` + `Alloy_Master_spec` are all present, but there is
   no charge-mix calculator to predict heat chemistry/yield before melting. The BOM table could feed this or be retired.
8. ⬜ **Spectrometer import.** Chemistry is typed by hand. Consider importing the Bruker Q2 ION export (CSV) per heat to
   remove transcription errors (the OCR job queue is the intended pattern for document extraction).
9. 🟡 **Finished goods "Under_Testing".** Status exists and is the column default; current flow posts straight to
   Available on output completion. Decide whether lab release should gate FG (link to spectro / sample OK), or drop it.

### Code health
10. ⬜ **Move the Dashboard out of `app.py`** into `app_pages/dashboard.py` and delete the legacy `if PAGE ==` path and
    `_legacy_stub`.
11. ⬜ **Split `database.py` (~18.5k lines)** by domain (schema/migrations, purchasing, production, dispatch, utilities,
    auth, reporting) behind the same `db.*` names so pages don't change.
12. ⬜ **Automated tests.** None in the repo. Today's throwaway checks (SQLite run of `correct_production_batch`, a
    Streamlit stand-in that executes page scripts) show the approach works; turn them into `tests/` with pytest:
    pure functions (`calc_yield`, `allocate_fifo`, `lot_charge_cost`, `usable_weight`, heat-no / batch-id builders,
    certificate rounding), `production_batch_correction_changes`, and an `init_db()` SQLite smoke test. Add a GitHub
    Action running `py_compile`, the tests and the IST grep on PRs (CI can pip-install; this cloud workspace cannot).
13. ⬜ **Remove dead schema/code** after confirming no data depends on it: `Production_batch.Workflow_stage`,
    `WORKFLOW_STAGES`, `update_batch_workflow` (unused), `BATCH_QA_STATUS`, `Alloy_Data_Checker` table (never read),
    FG status `Assigned` (legacy), `Access_matrix` login fallback once every user has an employee password.
