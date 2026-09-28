import pandas as pd
import streamlit as st

import database as db
from pages_common import (
    empty_int_input,
    empty_percent_input,
    format_ui_date,
    show_dataframe,
)


st.title("Batch Output Correction")
st.caption(
    "Correct output that was entered wrongly on a heat whose output is already "
    "**Completed** — for example when a physical audit or the **Packing List** "
    "shows a different weight or piece count. Change a line, add a missing one or "
    "remove a wrong one, then save. Every correction is logged with **who** made "
    "it, **when** (IST), the old and new value of each field, and your optional "
    "comment. Saving re-costs the heat, updates its remelt lots in **Raw Material "
    "Inventory** and its bundle in **Finished Goods Inventory**. Output that is "
    "still a draft is edited on **Batch Output** instead."
)

_FLASH_KEY = "boc_flash"
flash = st.session_state.pop(_FLASH_KEY, None)
if flash:
    st.success(flash)


def _kg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _alloy_label(alloy: dict) -> str:
    aid = int(alloy["Alloy_id"])
    name = alloy.get("Alloy_name") or f"Alloy {aid}"
    return f"{aid} — {name} (non-spec)" if db.is_sidestream_alloy(aid) else f"{aid} — {name}"


def _render_history(corrections: list[dict], *, show_heat: bool) -> None:
    lines = db.list_batch_output_correction_lines([c["Correction_id"] for c in corrections])
    by_corr: dict[int, list[dict]] = {}
    for ln in lines:
        by_corr.setdefault(int(ln["Correction_id"]), []).append(ln)
    for c in corrections:
        when = format_ui_date(c.get("Corrected_datetime"), with_time=True)
        heat = f"{c['Batch_ID']} (heat {c.get('Heat_no') or '—'}) · " if show_heat else ""
        pw_b, pw_a = _kg(c.get("Product_weight_before")), _kg(c.get("Product_weight_after"))
        title = (
            f"#{c['Correction_id']} · {heat}{when} · {c.get('Corrected_by') or '—'} · "
            f"product {pw_b:,.2f} → {pw_a:,.2f} kg"
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
            st.markdown(f"**Comments:** {c.get('Comments') or '—'}")
            t1, t2, t3 = st.columns(3)
            t1.metric(
                "Product weight (kg)",
                f"{pw_a:,.2f}",
                delta=f"{pw_a - pw_b:+,.2f}" if abs(pw_a - pw_b) >= 0.005 else None,
            )
            pp_b = int(c.get("Product_pieces_before") or 0)
            pp_a = int(c.get("Product_pieces_after") or 0)
            t2.metric("Product pieces", pp_a, delta=(pp_a - pp_b) or None)
            tw_b, tw_a = _kg(c.get("Total_weight_before")), _kg(c.get("Total_weight_after"))
            t3.metric(
                "Total output (kg)",
                f"{tw_a:,.2f}",
                delta=f"{tw_a - tw_b:+,.2f}" if abs(tw_a - tw_b) >= 0.005 else None,
            )
            rows = by_corr.get(int(c["Correction_id"]), [])
            if rows:
                show_dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Change": r["Change_type"],
                                "Output line": r.get("Output_id") or "new",
                                "Alloy": r.get("Alloy_name") or r.get("Alloy_id"),
                                "Field": r["Field_name"],
                                "Old value": r.get("Old_value") or "",
                                "New value": r.get("New_value") or "",
                            }
                            for r in rows
                        ]
                    )
                )


batches = db.list_correctable_batches()
if not batches:
    st.info(
        "No heat has **Completed** output yet. Output is entered and completed on "
        "**Batch Output**."
    )
    st.stop()

f1, f2 = st.columns([1, 2])
with f1:
    furnace_opts = ["All furnaces"] + sorted(
        {str(b["Furnace"]) for b in batches if b.get("Furnace")}
    )
    furnace = st.selectbox("Furnace", furnace_opts, key="boc_furnace")
with f2:
    search = st.text_input(
        "Find heat", key="boc_search", placeholder="Heat no or Batch ID, e.g. 26-1K005"
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
    st.info("No heat with Completed output matches.")
    st.stop()

# Options are Batch IDs (the label carries the output weight, which changes on
# save; a label as the value would make the selection jump to another heat).
labels = {
    b["Batch_ID"]: f"{b['Batch_ID']}  |  Heat {b.get('Heat_no') or '—'}  |  "
    f"{format_ui_date(b.get('Production_Date'))}  |  {b.get('Alloy_name') or '—'}  |  "
    f"{b.get('Customer_name') or '—'}  |  out={_kg(b.get('Output_Weight')):.0f} kg"
    for b in filtered
}
bid = st.selectbox(
    "Heat *", list(labels), format_func=lambda v: labels[v], key="boc_batch"
)
batch = db.get_batch(bid)
summary = next(b for b in filtered if b["Batch_ID"] == bid)
if not batch:
    st.error(f"Batch {bid} was not found.")
    st.stop()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Heat no", batch.get("Heat_no") or "—")
m2.metric("Production date", format_ui_date(batch.get("Production_Date")) or "—")
m3.metric("Furnace", batch.get("Furnace") or "—")
m4.metric("Product alloy", summary.get("Alloy_name") or "—")
m5.metric("Customer", summary.get("Customer_name") or "—")

packed = db.batch_output_packed_qty(bid)
packing_lists = db.list_batch_packing_lists(bid)
if packing_lists:
    st.warning(
        f"**{packed['weight']:,.2f} kg / {packed['pieces']} pieces** of this heat are "
        "on packing lists: "
        + ", ".join(
            f"#{p['Packing_list_id']} ({p.get('Packing_list_status')}, "
            f"{_kg(p.get('Weight')):,.2f} kg)"
            for p in packing_lists
        )
        + ". The corrected product output cannot go below that. To lower it "
        "further, reduce the heat on the packing list first."
    )

# ── Load the saved output once per heat (and again after each save) ──────────
version = int(st.session_state.get(f"boc_ver_{bid}", 0))
prefix = f"boc_{bid}_{version}"
alloys = db.list_batch_output_alloys(batch.get("Alloy_id"))
if not alloys:
    st.error(
        "Define the batch alloy and non-spec outputs 78 (Broken Ingot), 79 (Furnace "
        "Empty) and 80 (Not Ok Ingot) under **Alloys**."
    )
    st.stop()
label_to_id = {_alloy_label(a): int(a["Alloy_id"]) for a in alloys}
id_to_label = {v: k for k, v in label_to_id.items()}
alloy_labels = list(label_to_id)


def _k(name: str, idx: int) -> str:
    return f"{prefix}_{name}_{idx}"


expected_key = f"{prefix}_expected"
if expected_key not in st.session_state:
    saved = db.get_batch_outputs(bid)
    st.session_state[expected_key] = saved
    st.session_state[f"{prefix}_ids"] = [int(r["Output_id"]) for r in saved]
    for idx, row in enumerate(saved):
        aid = int(row["Alloy_id"])
        if aid not in id_to_label:
            # A saved alloy no longer allowed for this heat stays selectable so
            # the line shows what was saved.
            lbl = f"{aid} — {row.get('Alloy_name') or 'Alloy'}"
            id_to_label[aid] = lbl
            label_to_id[lbl] = aid
            alloy_labels.append(lbl)
        st.session_state[_k("alloy", idx)] = id_to_label[aid]
        scale = row.get("Weighment_scale_weight")
        st.session_state[_k("scale", idx)] = float(scale) if scale not in (None, "") else None
        stand = row.get("Stand_weight")
        st.session_state[_k("stand", idx)] = float(stand) if stand not in (None, "") else None
        st.session_state[_k("pcs", idx)] = int(row["Pieces"]) if row.get("Pieces") else None
        st.session_state[_k("notes", idx)] = row.get("Notes") or ""
        st.session_state[_k("remove", idx)] = False

expected: list[dict] = st.session_state[expected_key]
saved_ids: list[int] = st.session_state[f"{prefix}_ids"]
for row in expected:
    aid = int(row["Alloy_id"])
    if aid not in id_to_label:
        lbl = f"{aid} — {row.get('Alloy_name') or 'Alloy'}"
        id_to_label[aid] = lbl
        label_to_id[lbl] = aid
        alloy_labels.append(lbl)
n_new = int(st.session_state.get(f"{prefix}_new", 0))
product_id = int(batch["Alloy_id"]) if batch.get("Alloy_id") not in (None, "") else None

st.markdown("#### Output lines")
st.caption(
    "Net weight is **weighment scale − stand**. Enter the stand weight on every "
    "line (**0** if none). The grey line under each saved line shows what is saved "
    "now. Photos already on a line are kept."
)

lines: list[dict] = []
for idx in range(len(saved_ids) + n_new):
    is_new = idx >= len(saved_ids)
    output_id = None if is_new else saved_ids[idx]
    title = f"New line {idx - len(saved_ids) + 1}" if is_new else f"Output line {output_id}"
    st.markdown(f"**{title}**")
    if is_new and _k("alloy", idx) not in st.session_state and product_id in id_to_label:
        st.session_state[_k("alloy", idx)] = id_to_label[product_id]
    remove = bool(st.session_state.get(_k("remove", idx)))
    c1, c2, c3, c4, c5, c6, c7 = st.columns([2.4, 1.3, 1.1, 1.1, 0.9, 1.8, 0.8])
    with c1:
        alloy_label = st.selectbox(
            "Output alloy *", alloy_labels, key=_k("alloy", idx), disabled=remove
        )
    with c2:
        scale = empty_percent_input(
            "Scale weight (kg) *",
            key=_k("scale", idx),
            max_value=None,
            step=1.0,
            disabled=remove,
        )
    with c3:
        stand = empty_percent_input(
            "Stand (kg) *",
            key=_k("stand", idx),
            max_value=None,
            step=1.0,
            allow_zero=True,
            disabled=remove,
        )
    net = max(float(scale or 0) - float(stand or 0), 0.0) if scale else 0.0
    with c4:
        st.markdown(
            f"<p style='font-size:0.875rem;margin-bottom:0.25rem'>Net (kg)</p>"
            f"<p style='font-size:1.1rem;font-weight:600;margin-top:0.55rem'>{net:,.2f}</p>",
            unsafe_allow_html=True,
        )
    with c5:
        pieces = empty_int_input("Pieces", key=_k("pcs", idx), disabled=remove)
    with c6:
        notes = st.text_input("Notes", key=_k("notes", idx), disabled=remove)
    with c7:
        st.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
        st.checkbox("Remove", key=_k("remove", idx), help="Delete this output line")
    if not is_new:
        old = next(r for r in expected if int(r["Output_id"]) == output_id)
        st.caption(
            f"Saved: {old.get('Alloy_name') or old['Alloy_id']} · scale "
            f"{_kg(old.get('Weighment_scale_weight')):,.2f} kg · stand "
            f"{_kg(old.get('Stand_weight')):,.2f} kg · net {_kg(old.get('Weight')):,.2f} kg · "
            f"{old.get('Pieces') or '—'} pcs"
            + (f" · {old['Notes']}" if old.get("Notes") else "")
        )
    lines.append(
        {
            "Output_id": output_id,
            "Alloy_id": label_to_id[alloy_label],
            "Weighment_scale_weight": scale,
            "Stand_weight": stand,
            "Weight": net,
            "Pieces": pieces,
            "Notes": notes,
            "Remove": bool(st.session_state.get(_k("remove", idx))),
        }
    )

a1, a2, _sp = st.columns([1, 1, 3])
if a1.button("Add output line", key=f"{prefix}_add"):
    st.session_state[f"{prefix}_new"] = n_new + 1
    st.rerun()
if n_new and a2.button("Drop last new line", key=f"{prefix}_drop"):
    last = len(saved_ids) + n_new - 1
    for name in ("alloy", "scale", "stand", "pcs", "notes", "remove", "net"):
        st.session_state.pop(_k(name, last), None)
    st.session_state[f"{prefix}_new"] = n_new - 1
    st.rerun()

# ── Preview of what will be saved ───────────────────────────────────────────
st.markdown("#### Changes to be saved")
changes: list[dict] = []
error = None
try:
    cleaned, changes = db.batch_output_correction_changes(batch, expected, lines)
except ValueError as exc:
    error = str(exc)
    cleaned = []

if error:
    st.error(error)
elif not changes:
    st.info("No changes yet. Edit a line above, add one or tick Remove.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "Change": c["Change_type"],
                    "Output line": c["Output_id"] or "new",
                    "Alloy": c.get("Alloy_name") or c.get("Alloy_id"),
                    "Field": c["Field_name"],
                    "Old value": c["Old_value"],
                    "New value": c["New_value"],
                }
                for c in changes
            ]
        )
    )
    def _product(rows: list[dict]) -> tuple[float, int]:
        prod = [r for r in rows if product_id is not None and int(r["Alloy_id"]) == product_id]
        return (
            sum(_kg(r.get("Weight")) for r in prod),
            sum(int(r.get("Pieces") or 0) for r in prod),
        )

    old_w, old_p = _product(expected)
    new_w, new_p = _product(cleaned)
    p1, p2, p3 = st.columns(3)
    p1.metric(
        "Product weight (kg)",
        f"{new_w:,.2f}",
        delta=f"{new_w - old_w:+,.2f}" if abs(new_w - old_w) >= 0.005 else None,
    )
    p2.metric("Product pieces", new_p, delta=(new_p - old_p) or None)
    fg_left = new_w - packed["weight"]
    p3.metric("Finished goods after save (kg)", f"{max(fg_left, 0):,.2f}")
    if fg_left < -0.0005 or (packed["pieces"] and new_p < packed["pieces"]):
        error = (
            f"The corrected product output ({new_w:,.2f} kg, {new_p} pieces) is less "
            f"than what is already on packing lists ({packed['weight']:,.2f} kg, "
            f"{packed['pieces']} pieces). Reduce the heat on the packing list first."
        )
        st.error(error)

st.markdown("#### Reason for the correction")
r1, r2 = st.columns([1, 2])
with r1:
    found = st.selectbox(
        "Found during (optional)",
        ["—"] + db.BATCH_OUTPUT_CORRECTION_FOUND_DURING,
        key=f"{prefix}_found",
    )
with r2:
    comments = st.text_area(
        "Comments (optional)",
        key=f"{prefix}_comments",
        placeholder="Why was the output wrong, and how was the right figure confirmed?",
        height=80,
    )

st.caption(
    f"Saved as **{db.get_acting_user()}** at the time you click Save (IST)."
)
if st.button(
    "Save correction",
    type="primary",
    key=f"{prefix}_save",
    disabled=bool(error) or not changes,
):
    try:
        correction_id = db.correct_batch_output(
            bid,
            lines,
            expected=expected,
            found_during=None if found == "—" else found,
            comments=comments,
        )
    except Exception as exc:
        st.error(str(exc))
    else:
        st.session_state[f"boc_ver_{bid}"] = version + 1
        st.session_state[_FLASH_KEY] = (
            f"Correction #{correction_id} saved for **{bid}** "
            f"({len(changes)} change{'s' if len(changes) != 1 else ''} logged)."
        )
        st.rerun()

# ── Audit history ─────────────────────────────────────────────────────────────
st.divider()
st.subheader(f"Correction history for {bid}")
history = db.list_batch_output_corrections(bid)
if history:
    _render_history(history, show_heat=False)
else:
    st.caption("No corrections have been saved for this heat.")

with st.expander("Recent corrections on all heats"):
    recent = db.list_batch_output_corrections(limit=50)
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
                        "Product kg before": _kg(r.get("Product_weight_before")),
                        "Product kg after": _kg(r.get("Product_weight_after")),
                        "Changes": int(r.get("Changes") or 0),
                        "Comments": r.get("Comments") or "",
                    }
                    for r in recent
                ]
            ),
            column_config={
                "Product kg before": st.column_config.NumberColumn(format="%.2f"),
                "Product kg after": st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.caption("No corrections yet.")
