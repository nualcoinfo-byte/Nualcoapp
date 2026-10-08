from datetime import date, datetime

import pandas as pd
import streamlit as st

import database as db
from pages_common import (
    UI_DATE_WIDGET_FORMAT,
    df_from_rows,
    empty_percent_input,
    format_ui_date,
    parse_any_date,
    show_dataframe,
)


st.title("Production Batch Correction")
st.caption(
    "**Admin only.** Correct a heat whose input is already **Completed** and locked "
    "on **Production Batch & Chemistry**: alloy, melter, production supervisor, notes, "
    "degassing, sampled / defect pcs, sample results and chemistry. The heat must "
    "still meet every **Mark as Completed** rule after the correction. Every saved "
    "correction is logged with **who** made it, **when** (IST), the reason, and the "
    "old and new value of each field. Batch ID, production date, shift and melt no "
    "are changed on **Admin → Correct batch ID**; charge lines are not changed here. "
    "Heats still In-Progress are edited on Production Batch & Chemistry."
)

# Visible only to Admin in the sidebar; checked again here (and in the database
# layer) so a direct link or a role given the Production section cannot use it.
if not db.is_admin_user():
    st.error("Only Admin can open Production Batch Correction.")
    st.stop()

_FLASH_KEY = "pbc_flash"
flash = st.session_state.pop(_FLASH_KEY, None)
if flash:
    st.success(flash)

SAMPLE_BLANK = "— not set —"
SAMPLE_FIELDS = (
    ("Top_Sample", "Top sample"),
    ("Middle_Sample", "Middle sample"),
    ("Bottom_Sample", "Bottom sample"),
    ("Vacum_Sample", "Vacum sample"),
)


def _kg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_datetime(value: object) -> datetime | None:
    parsed = parse_any_date(value)
    if isinstance(parsed, datetime):
        return parsed
    if isinstance(parsed, date):
        return datetime.combine(parsed, datetime.min.time())
    return None


def _render_history(corrections: list[dict], *, show_heat: bool) -> None:
    lines = db.list_production_batch_correction_lines(
        [c["Correction_id"] for c in corrections]
    )
    by_corr: dict[int, list[dict]] = {}
    for ln in lines:
        by_corr.setdefault(int(ln["Correction_id"]), []).append(ln)
    for c in corrections:
        when = format_ui_date(c.get("Corrected_datetime"), with_time=True)
        heat = f"{c['Batch_ID']} (heat {c.get('Heat_no') or '—'}) · " if show_heat else ""
        title = (
            f"#{c['Correction_id']} · {heat}{when} · {c.get('Corrected_by') or '—'} · "
            f"{int(c.get('Changes') or 0)} change(s)"
        )
        with st.expander(title):
            i1, i2, i3 = st.columns(3)
            i1.markdown(
                f"**Corrected by:** {c.get('Corrected_by') or '—'}"
                + (
                    f" (employee {c['Corrected_by_employee_id']})"
                    if c.get("Corrected_by_employee_id")
                    else ""
                )
            )
            i2.markdown(f"**When:** {when}")
            i3.markdown(f"**Found during:** {c.get('Found_during') or '—'}")
            st.markdown(f"**Reason:** {c.get('Reason') or '—'}")
            rows = by_corr.get(int(c["Correction_id"]), [])
            if rows:
                show_dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Section": r["Section"],
                                "Field": r["Field_name"],
                                "Old value": r.get("Old_value") or "",
                                "New value": r.get("New_value") or "",
                            }
                            for r in rows
                        ]
                    )
                )


# ── Pick a Completed heat ────────────────────────────────────────────────────
batches = db.list_completed_production_batches()
if not batches:
    st.info(
        "No heat is **Completed** yet. Heats are completed on **Production Batch & "
        "Chemistry**."
    )
    st.stop()

f1, f2 = st.columns([1, 2])
with f1:
    furnace_opts = ["All furnaces"] + sorted(
        {str(b["Furnace"]) for b in batches if b.get("Furnace")}
    )
    furnace = st.selectbox("Furnace", furnace_opts, key="pbc_furnace")
with f2:
    search = st.text_input(
        "Find heat", key="pbc_search", placeholder="Heat no or Batch ID, e.g. 26-1K005"
    ).strip().lower()
filtered = [
    b
    for b in batches
    if (furnace == "All furnaces" or str(b.get("Furnace")) == furnace)
    and (
        not search
        or search in str(b.get("Heat_no") or "").lower()
        or search in str(b.get("Batch_ID") or "").lower()
    )
]
if not filtered:
    st.info("No Completed heat matches.")
    st.stop()

labels = {
    b["Batch_ID"]: f"{b['Batch_ID']}  |  Heat {b.get('Heat_no') or '—'}  |  "
    f"{format_ui_date(b.get('Production_Date'))}  |  {b.get('Alloy_name') or '—'}  |  "
    f"{b.get('Customer_name') or '—'}"
    for b in filtered
}
bid = st.selectbox("Heat *", list(labels), format_func=lambda v: labels[v], key="pbc_batch")
batch = db.get_batch(bid)
if not batch:
    st.error(f"Batch {bid} was not found.")
    st.stop()

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Heat no", batch.get("Heat_no") or "—")
m2.metric("Production date", format_ui_date(batch.get("Production_Date")) or "—")
m3.metric("Shift / melt", f"{batch.get('Shift') or '—'} / {batch.get('Melt_No') or '—'}")
m4.metric("Furnace", batch.get("Furnace") or "—")
m5.metric("Crucible", batch.get("Crucible_no") or "—")
m6.metric("Output status", batch.get("Output_status") or "—")

packing_lists = db.list_batch_packing_lists(bid)
if packing_lists:
    st.warning(
        "This heat is on packing list(s) "
        + ", ".join(
            f"#{p['Packing_list_id']} ({p.get('Packing_list_status')})"
            for p in packing_lists
        )
        + ". Its **alloy cannot be changed** here. Test certificates read the heat's "
        "chemistry when they are printed, so a chemistry correction also shows on "
        "any certificate printed or reprinted after you save."
    )

charges = db.get_batch_inputs(bid)
with st.expander(
    f"Charge lines (read only): {len(charges)} line(s), "
    f"{sum(_kg(c.get('Weight')) for c in charges):,.2f} kg net"
):
    if charges:
        show_dataframe(df_from_rows(charges))
    else:
        st.caption("No charge lines on this heat.")

# ── Load the saved heat into the form once per heat (and again after a save) ─
version = int(st.session_state.get(f"pbc_ver_{bid}", 0))
prefix = f"pbc_{bid}_{version}"


def _k(name: str) -> str:
    return f"{prefix}_{name}"


alloys = db.list_alloys(include_sidestream=False)
alloy_label_by_id = {
    int(a["Alloy_id"]): f"{a['Alloy_id']} — {a['Alloy_name']}"
    + (f" ({a['Customer_name']})" if a.get("Customer_name") else "")
    for a in alloys
}
saved_alloy = batch.get("Alloy_id")
if saved_alloy not in (None, "") and int(saved_alloy) not in alloy_label_by_id:
    alloy_label_by_id[int(saved_alloy)] = f"{saved_alloy} — (not in Alloy Master)"

melter_opts = [""] + db.list_melters(active_only=True)
supervisor_opts = [""] + db.list_production_supervisors(active_only=True)
for opts, saved in (
    (melter_opts, batch.get("Melting_team")),
    (supervisor_opts, batch.get("Production_supervisor")),
):
    if saved and saved not in opts:
        opts.append(saved)

chem_elements = list(db.list_batch_chem_elements())
sf_el = next((el for el in chem_elements if el["Element_Symbol"] == "SF"), None)
if sf_el is not None:
    # SF sits just before OE / OT, as on Production Batch & Chemistry.
    chem_elements.remove(sf_el)
    first_extra = next(
        (i for i, el in enumerate(chem_elements) if el["Element_Symbol"] in ("OE", "OT")),
        len(chem_elements),
    )
    chem_elements.insert(first_extra, sf_el)

expected_key = _k("expected")
if expected_key not in st.session_state:
    saved_chem = db.get_batch_chemistry(bid)
    st.session_state[expected_key] = (dict(batch), saved_chem)
    st.session_state[_k("alloy")] = (
        int(saved_alloy) if saved_alloy not in (None, "") else None
    )
    st.session_state[_k("melter")] = batch.get("Melting_team") or ""
    st.session_state[_k("supervisor")] = batch.get("Production_supervisor") or ""
    st.session_state[_k("notes")] = batch.get("Notes") or ""
    st.session_state[_k("degassing")] = batch.get("Degassing_time") or ""
    for col in ("Sampled_pcs", "Defect_pcs"):
        val = batch.get(col)
        st.session_state[_k(col)] = float(val) if val not in (None, "") else None
    for col, _label in SAMPLE_FIELDS:
        val = str(batch.get(col) or "").strip()
        st.session_state[_k(col)] = val if val in db.SAMPLE_OK_STATUS else SAMPLE_BLANK
    for pos in ("Top", "Middle", "Bottom"):
        st.session_state[_k(f"{pos}_remarks")] = batch.get(f"{pos}_Sample_Remarks") or ""
        st.session_state[_k(f"{pos}_dt")] = _as_datetime(batch.get(f"{pos}_Sample_datetime"))
    # Chemistry grid: the entry elements plus anything else saved on this heat.
    by_sym = {
        str(r["Element_symbol"]): r for r in saved_chem if r.get("Element_symbol")
    }
    names = {e["Element_Symbol"]: e["Element_Name"] for e in db.list_elements()}
    rows = []
    listed = set()
    for el in chem_elements + [
        {"Element_Symbol": s, "Element_Name": names.get(s, s)}
        for s in by_sym
        if s not in {e["Element_Symbol"] for e in chem_elements}
    ]:
        sym = el["Element_Symbol"]
        if sym in listed:
            continue
        listed.add(sym)
        saved_row = by_sym.get(sym) or {}
        pct = saved_row.get("Percentage")
        rows.append(
            {
                "Element": sym,
                "Name": el.get("Element_Name") or sym,
                "%": float(pct) if pct not in (None, "") and float(pct) > 0 else None,
                "Below detection limit": bool(saved_row.get("Less_than")) and sym != "SF",
            }
        )
    st.session_state[_k("chem_df")] = pd.DataFrame(rows)

expected_batch, expected_chem = st.session_state[expected_key]

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("#### Heat details")
alloy_locked = bool(packing_lists)
h1, h2, h3 = st.columns(3)
with h1:
    alloy_ids = list(alloy_label_by_id)
    if st.session_state.get(_k("alloy")) is None:
        alloy_ids = [None] + alloy_ids
    alloy_id = st.selectbox(
        "Alloy *",
        alloy_ids,
        key=_k("alloy"),
        format_func=lambda v: "Select alloy" if v is None else alloy_label_by_id.get(v, str(v)),
        disabled=alloy_locked,
        help=(
            "Changing the alloy also moves this heat's product output lines to the "
            "new alloy and updates its Finished Goods bundle. Not allowed once the "
            "heat is on a packing list."
        ),
    )
with h2:
    melter = st.selectbox(
        "Melter name *",
        melter_opts,
        key=_k("melter"),
        format_func=lambda v: v or "Select melter",
    )
with h3:
    supervisor = st.selectbox(
        "Production supervisor *",
        supervisor_opts,
        key=_k("supervisor"),
        format_func=lambda v: v or "Select production supervisor",
    )
notes = st.text_area("Notes", key=_k("notes"), height=68)

st.markdown("#### Degassing & piece counts")
d1, d2, d3, d4 = st.columns(4)
with d1:
    degassing = st.text_input(
        "Degassing time *", key=_k("degassing"), placeholder="e.g. 14:30 or 12 min"
    )
with d2:
    sampled = empty_percent_input(
        "Sampled pcs *", key=_k("Sampled_pcs"), max_value=None, step=1.0
    )
with d3:
    defect = empty_percent_input(
        "Defect pcs *", key=_k("Defect_pcs"), max_value=None, step=1.0, allow_zero=True
    )
with d4:
    if sampled and sampled > 0 and defect is not None:
        k_mold = float(defect) / float(sampled)
        css = "yield-bad" if k_mold > db.K_MOLD_MAX else "yield-ok"
        st.markdown(
            f'<p class="{css}">K Mold Value<br>{k_mold:.3f}</p>', unsafe_allow_html=True
        )
    else:
        st.markdown('<p class="yield-ok">K Mold Value<br>—</p>', unsafe_allow_html=True)
    st.caption(f"Defect ÷ sampled; must be {db.K_MOLD_MAX:g} or below.")

st.markdown("#### Sample results")
sample_opts = [SAMPLE_BLANK] + list(db.SAMPLE_OK_STATUS)
s_cols = st.columns(4)
sample_values: dict[str, str | None] = {}
for (col, label), widget_col in zip(SAMPLE_FIELDS, s_cols):
    with widget_col:
        choice = st.selectbox(f"{label} *", sample_opts, key=_k(col))
        sample_values[col] = None if choice == SAMPLE_BLANK else choice
r_cols = st.columns(4)
remarks: dict[str, str] = {}
sample_dts: dict[str, datetime | None] = {}
for pos, widget_col in zip(("Top", "Middle", "Bottom"), r_cols):
    with widget_col:
        remarks[pos] = st.text_input(f"{pos} sample remarks", key=_k(f"{pos}_remarks"))
        sample_dts[pos] = st.datetime_input(
            f"{pos} sample datetime *",
            value=None,
            key=_k(f"{pos}_dt"),
            step=60,
            format=UI_DATE_WIDGET_FORMAT,
        )

# ── Chemistry ────────────────────────────────────────────────────────────────
st.markdown("#### Batch chemistry (spectrometer)")
st.caption(
    "Edit the % in the grid. Leave a cell empty to remove that element. Tick "
    "**Below detection limit** when the spectrometer could not resolve the value "
    "(the % is then the reported ceiling and prints as e.g. <0.0050). SF is entered "
    "from the spectrometer; the system figure below is only a guide."
)
edited = st.data_editor(
    st.session_state[_k("chem_df")],
    key=_k("chem_editor"),
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    disabled=["Element", "Name"],
    column_config={
        "%": st.column_config.NumberColumn(
            "%", min_value=0.0, max_value=600.0, step=0.0001, format="%.4f"
        ),
        "Below detection limit": st.column_config.CheckboxColumn("Below detection limit"),
    },
)
chemistry: dict[str, tuple[float, bool]] = {}
for _idx, row in edited.iterrows():
    pct = row.get("%")
    if pct is None or pd.isna(pct) or float(pct) <= 0:
        continue
    sym = str(row["Element"])
    chemistry[sym] = (float(pct), bool(row.get("Below detection limit")) and sym != "SF")


def _pct(sym: str) -> float:
    return chemistry.get(sym, (0.0, False))[0]


sf_calc = round(_pct("Fe") + 2.0 * _pct("Mn") + 3.0 * _pct("Cr"), 2)
st.caption(
    "SF system calculated (Fe + 2×Mn + 3×Cr): "
    + (f"**{sf_calc:.2f}**" if sf_calc > 0 else "enter Fe, Mn, Cr")
    + (f" · entered SF **{_pct('SF'):.2f}**" if _pct("SF") > 0 else "")
)

specs = db.get_alloy_specs(alloy_id) if alloy_id is not None else {}
out_of_spec = []
for sym, (pct, below) in chemistry.items():
    spec = specs.get(sym)
    if not spec:
        continue
    mn, mx = spec.get("Min_percent"), spec.get("Max_percent")
    bad = (mx not in (None, "") and pct > float(mx)) if below else (
        (mn not in (None, "") and pct <= float(mn))
        or (mx not in (None, "") and pct >= float(mx))
    )
    if bad:
        out_of_spec.append(
            f"{sym} {('<' if below else '')}{pct:g} (spec {mn if mn is not None else '—'}"
            f" – {mx if mx is not None else '—'})"
        )
if out_of_spec:
    st.warning(
        "Outside this alloy's spec (Alloy_Master_spec): " + "; ".join(out_of_spec)
        + ". You can still save if this is what the spectrometer reported."
    )

# ── What will be saved ───────────────────────────────────────────────────────
header = {
    "Alloy_id": alloy_id,
    "Melting_team": melter,
    "Production_supervisor": supervisor,
    "Notes": notes,
    "Degassing_time": degassing,
    "Sampled_pcs": sampled,
    "Defect_pcs": defect,
    **sample_values,
    "Top_Sample_Remarks": remarks["Top"],
    "Middle_Sample_Remarks": remarks["Middle"],
    "Bottom_Sample_Remarks": remarks["Bottom"],
    "Top_Sample_datetime": sample_dts["Top"],
    "Middle_Sample_datetime": sample_dts["Middle"],
    "Bottom_Sample_datetime": sample_dts["Bottom"],
}

st.markdown("#### Changes to be saved")
changes = db.production_batch_correction_changes(
    expected_batch, expected_chem, header, chemistry
)
problems: list[str] = []
if not melter:
    problems.append("Select the melter name.")
if not supervisor:
    problems.append("Select the production supervisor.")
gaps = db.production_batch_completion_gaps(
    degassing_time=degassing,
    sampled_pcs=sampled,
    defect_pcs=defect,
    top_sample=sample_values["Top_Sample"],
    middle_sample=sample_values["Middle_Sample"],
    bottom_sample=sample_values["Bottom_Sample"],
    vacum_sample=sample_values["Vacum_Sample"],
    top_sample_datetime=sample_dts["Top"],
    middle_sample_datetime=sample_dts["Middle"],
    bottom_sample_datetime=sample_dts["Bottom"],
    chemistry_count=len(chemistry),
    charge_line_count=len(charges),
)
if gaps:
    problems.append(
        "A Completed heat must still have everything Mark as Completed needs: "
        + "; ".join(gaps)
    )

if not changes:
    st.info("No changes yet. Edit a field or a chemistry value above.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "Section": c["Section"],
                    "Field": c["Field_name"],
                    "Old value": c["Old_value"],
                    "New value": c["New_value"],
                }
                for c in changes
            ]
        )
    )
    if alloy_id is not None and saved_alloy not in (None, "") and int(saved_alloy) != int(alloy_id):
        st.info(
            "The alloy changes: this heat's product output lines move to the new "
            "alloy" + (
                " and its Finished Goods bundle is updated."
                if batch.get("Output_status") == db.BATCH_STATUS_COMPLETED
                else "."
            )
        )
for problem in problems:
    st.error(problem)

st.markdown("#### Reason for the correction")
r1, r2 = st.columns([1, 2])
with r1:
    found = st.selectbox(
        "Found during (optional)",
        ["—"] + db.PRODUCTION_BATCH_CORRECTION_FOUND_DURING,
        key=_k("found"),
    )
with r2:
    reason = st.text_area(
        "Reason *",
        key=_k("reason"),
        placeholder="What was wrong, and how was the right value confirmed?",
        height=80,
    )

st.caption(f"Saved as **{db.get_acting_user()}** at the time you click Save (IST).")
if st.button(
    "Save correction",
    type="primary",
    key=_k("save"),
    disabled=bool(problems) or not changes or not reason.strip(),
    help="Needs at least one change, a reason, and every Mark as Completed rule met.",
):
    try:
        correction_id = db.correct_production_batch(
            bid,
            header=header,
            chemistry=chemistry,
            expected_batch=expected_batch,
            expected_chemistry=expected_chem,
            reason=reason,
            found_during=None if found == "—" else found,
        )
    except Exception as exc:
        st.error(str(exc))
    else:
        st.session_state[f"pbc_ver_{bid}"] = version + 1
        st.cache_data.clear()
        st.session_state[_FLASH_KEY] = (
            f"Correction #{correction_id} saved for **{bid}** "
            f"({len(changes)} change{'s' if len(changes) != 1 else ''} logged)."
        )
        st.rerun()

# ── Audit history ────────────────────────────────────────────────────────────
st.divider()
st.subheader(f"Correction history for {bid}")
history = db.list_production_batch_corrections(bid)
if history:
    _render_history(history, show_heat=False)
else:
    st.caption("No corrections have been saved for this heat.")

with st.expander("Recent production batch corrections on all heats"):
    recent = db.list_production_batch_corrections(limit=50)
    if recent:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Correction": r["Correction_id"],
                        "Batch ID": r["Batch_ID"],
                        "Heat no": r.get("Heat_no"),
                        "Corrected at": format_ui_date(
                            r.get("Corrected_datetime"), with_time=True
                        ),
                        "Corrected by": r.get("Corrected_by"),
                        "Found during": r.get("Found_during") or "",
                        "Changes": int(r.get("Changes") or 0),
                        "Reason": r.get("Reason") or "",
                    }
                    for r in recent
                ]
            )
        )
    else:
        st.caption("No corrections yet.")
