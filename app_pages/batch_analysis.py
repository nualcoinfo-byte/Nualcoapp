"""Batch Analysis: everything about one heat on one page, plus two sales tools.

1. Alternative customers: when the ordering customer delays dispatch, finished
   goods sit in stock. Every other customer alloy is checked against this heat's
   chemistry, so the stock can be sent to a customer whose spec it already meets
   and the first customer's order replenished later.
2. Spec comparison: pick any customer alloy and see the heat side by side with
   its spec; elements outside the limits are in red, ready for a deviation
   request (CSV / PDF download).

Read only: nothing on this page changes data. "In spec" uses the same rule as
the packing-list deviation check (db.element_percent_out_of_spec).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import database as db
import re

from pages_common import format_ui_date, show_dataframe, summary_pdf_bytes

st.title("Batch Analysis")
st.caption(
    "Find a heat by **Batch ID**, **heat no**, **customer** or **alloy** and see "
    "everything about it: production details, charge and returns, degassing, K mould, "
    "samples, chemistry, output, finished goods and dispatch. **Alternative customers** "
    "checks the heat's chemistry against every other customer's alloy spec, to divert "
    "ready stock when a customer delays dispatch. **Spec comparison** puts the heat "
    "next to one alloy spec of your choice and marks out-of-spec elements in **red** "
    "for a deviation request. A value on a spec limit counts as out of spec, as on the "
    "**Test Certificate**. This page only reads data."
)

RED = "background-color: #fdecea; color: #c62828; font-weight: 700"
AMBER = "background-color: #fff4e5; color: #8a5300"
GREEN = "color: #1b7a3d; font-weight: 600"


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct_text(value: object, less: bool = False) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{'<' if less else ''}{float(value):.4f}".rstrip("0").rstrip(".")


def _limit(value: object) -> str:
    return "—" if value in (None, "") else f"{float(value):g}"


def _pts(value: object) -> str:
    """Percentage points, up to 4 decimals without trailing zeros."""
    return f"{float(value or 0):.4f}".rstrip("0").rstrip(".")


def _off(row: dict) -> str:
    """How far an out-of-spec element is past its limit ('on the limit' when equal)."""
    if row["Status"] not in (db.SPEC_STATUS_LOW, db.SPEC_STATUS_HIGH):
        return ""
    return _pts(row["Deviation"]) if row["Deviation"] > 0 else "on the limit"


# ── Find the heat ────────────────────────────────────────────────────────────
s1, s2 = st.columns([3, 1], vertical_alignment="bottom")
with s1:
    term = st.text_input(
        "Search",
        key="ba_search",
        placeholder="Batch ID, heat no, customer or alloy, e.g. 26-1M012, LM25, Brakes",
    )
with s2:
    in_stock_only = st.checkbox(
        "Only heats with stock",
        key="ba_in_stock",
        help="Heats with finished goods still Available or Under testing.",
    )
try:
    found = db.search_batches_for_analysis(term)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not search batches: {exc}")
    st.stop()
if in_stock_only and found:
    stocked = {
        str(r["Batch_ID"])
        for r in db.fetch_all(
            """
            SELECT DISTINCT Batch_ID AS "Batch_ID" FROM Finished_Goods_Inventory
            WHERE Finished_Goods_Status IN ('Available', 'Under_Testing')
              AND COALESCE(Output_Weight, 0) > 0
            """
        )
    }
    found = [r for r in found if str(r["Batch_ID"]) in stocked]
if not found:
    st.info("No heat matches. Try part of the heat no (e.g. 1M012) or the customer name.")
    st.stop()

labels = {
    r["Batch_ID"]: f"{r['Batch_ID']}  |  Heat {r.get('Heat_no') or '—'}  |  "
    f"{format_ui_date(r.get('Production_Date'))}  |  {r.get('Alloy_name') or '—'}  |  "
    f"{r.get('Customer_name') or '—'}"
    for r in found
}
if st.session_state.get("ba_batch") not in labels:
    st.session_state.pop("ba_batch", None)
bid = st.selectbox(
    f"Heat * ({len(found)} found{', newest 300 shown' if len(found) >= 300 else ''})",
    list(labels),
    format_func=lambda v: labels[v],
    key="ba_batch",
)
batch = db.get_batch(bid)
if not batch:
    st.error(f"Batch {bid} was not found.")
    st.stop()
listed = next((r for r in found if r["Batch_ID"] == bid), {})
alloy = db.get_alloy(batch.get("Alloy_id")) or {}
chemistry = db.get_batch_chemistry(bid)
inputs = db.get_batch_inputs(bid)
returns = db.list_batch_input_returns(bid)
outputs = db.get_batch_outputs(bid)
fg = db.batch_finished_goods(bid)
dispatch = db.batch_dispatch_history(bid)

input_kg = sum(_num(r.get("Weight")) for r in inputs)
output_kg = sum(_num(r.get("Weight")) for r in outputs)
product_kg = sum(
    _num(r.get("Weight")) for r in outputs if not db.is_sidestream_alloy(r.get("Alloy_id"))
)
fg_stock_kg = sum(
    _num(r.get("Output_Weight")) for r in fg
    if r.get("Finished_Goods_Status") in ("Available", "Under_Testing")
)
yield_pct = output_kg / input_kg * 100 if input_kg > 0 else None

# ── Heading figures ──────────────────────────────────────────────────────────
st.markdown(
    f"### {bid} · Heat {batch.get('Heat_no') or '—'} · "
    f"{alloy.get('Alloy_name') or listed.get('Alloy_name') or 'No alloy'}"
    + (f" · {alloy.get('Customer_name')}" if alloy.get("Customer_name") else "")
)
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Charged (kg)", f"{input_kg:,.1f}")
k2.metric("Output (kg)", f"{output_kg:,.1f}")
k3.metric("Yield", f"{yield_pct:,.2f}%" if yield_pct is not None else "—",
          help=f"All output ÷ charge. Target {db.YIELD_TARGET_PCT:g}%.")
k4.metric("Product alloy (kg)", f"{product_kg:,.1f}")
k5.metric("In stock (kg)", f"{fg_stock_kg:,.1f}",
          help="Finished goods of this heat still Available or Under testing.")
cost = next((r.get("cost_of_production_overall_per_kg") for r in outputs
             if r.get("cost_of_production_overall_per_kg") is not None), None)
k6.metric("Cost ₹/kg", f"{float(cost):,.2f}" if cost is not None else "—",
          help="Overall cost of production (material + conversion) stored on the output.")

tabs = st.tabs([
    "Production & QA",
    "Charge",
    "Chemistry",
    "Output & dispatch",
    "Alternative customers",
    "Spec comparison",
])

# ── Production details and QA ────────────────────────────────────────────────
with tabs[0]:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Production**")
        show_dataframe(pd.DataFrame([
            ("Batch ID", bid),
            ("Heat no", batch.get("Heat_no") or "—"),
            ("Production date", format_ui_date(batch.get("Production_Date")) or "—"),
            ("Shift", batch.get("Shift") or "—"),
            ("Melt no", batch.get("Melt_No") or "—"),
            ("Furnace / crucible", f"{batch.get('Furnace') or '—'} / {batch.get('Crucible_no') or '—'}"),
            ("Alloy", alloy.get("Alloy_name") or "—"),
            ("Customer", alloy.get("Customer_name") or "—"),
            ("Melter name", batch.get("Melting_team") or "—"),
            ("Production supervisor", batch.get("Production_supervisor") or "—"),
            ("Input status", batch.get("Production_status") or "—"),
            ("Output status", batch.get("Output_status") or "—"),
            ("Notes", batch.get("Notes") or "—"),
        ], columns=["Field", "Value"]).astype(str))
    with c2:
        st.markdown("**Degassing and K mould**")
        k_mold = db.k_mold_value(batch.get("Sampled_pcs"), batch.get("Defect_pcs"))
        show_dataframe(pd.DataFrame([
            ("Degassing time", batch.get("Degassing_time") or "—"),
            ("Sampled pcs", _limit(batch.get("Sampled_pcs"))),
            ("Defect pcs", _limit(batch.get("Defect_pcs"))),
            ("K mould value", "—" if k_mold is None else
             f"{k_mold:.3f} ({'OK' if k_mold <= db.K_MOLD_MAX else 'over'} limit {db.K_MOLD_MAX:g})"),
        ], columns=["Field", "Value"]).astype(str))
        st.markdown("**Samples**")
        samples = pd.DataFrame([
            {
                "Sample": pos,
                "Result": batch.get(f"{key}_Sample") or "—",
                "Remarks": batch.get(f"{key}_Sample_Remarks") or "",
                "Time": format_ui_date(batch.get(f"{key}_Sample_datetime"), with_time=True)
                if batch.get(f"{key}_Sample_datetime") else "",
            }
            for pos, key in (("Top", "Top"), ("Middle", "Middle"), ("Bottom", "Bottom"),
                             ("Vacuum", "Vacum"))
        ])
        show_dataframe(samples.style.map(
            lambda v: RED if str(v).upper() == "NOT OK" else (GREEN if v == "OK" else ""),
            subset=["Result"],
        ))
    corrections = db.list_production_batch_corrections(bid)
    if corrections:
        with st.expander(f"Corrections to this heat ({len(corrections)})"):
            show_dataframe(pd.DataFrame([
                {"Correction": c["Correction_id"],
                 "When": format_ui_date(c.get("Corrected_datetime"), with_time=True),
                 "By": c.get("Corrected_by"), "Changes": c.get("Changes"),
                 "Reason": c.get("Reason")}
                for c in corrections
            ]))

# ── Charge and returns ───────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("**Batch input (charge lines)**")
    if inputs:
        idf = pd.DataFrame([
            {
                "Charged at": format_ui_date(r.get("Charge_time"), with_time=True),
                "Raw material": r.get("Raw_Material_Name"),
                "Lot": r.get("Lot_id"),
                "Trolley": r.get("Trolley_name") or "",
                "Scale (kg)": r.get("Weighment_scale_weight"),
                "Tare (kg)": r.get("Trolley_weight"),
                "Net (kg)": _num(r.get("Weight")),
                "Saved by": r.get("Saved_by") or "",
            }
            for r in inputs
        ])
        show_dataframe(idf, column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in ("Scale (kg)", "Tare (kg)", "Net (kg)")
        })
        by_mat = idf.groupby("Raw material")["Net (kg)"].sum().sort_values(ascending=False)
        st.caption(
            "By raw material: " + " · ".join(
                f"**{m}** {kg:,.1f} kg ({kg / input_kg * 100:,.1f}%)"
                for m, kg in by_mat.items()
            ) if input_kg > 0 else "No charge."
        )
    else:
        st.caption("No charge lines saved.")
    st.markdown("**Batch input returns**")
    if returns:
        show_dataframe(pd.DataFrame([
            {"Returned at": format_ui_date(r.get("Return_time"), with_time=True),
             "Raw material": r.get("Raw_Material_Name"), "Lot": r.get("Lot_id"),
             "Weight (kg)": _num(r.get("Weight")), "Sent to": r.get("Return_type"),
             "Notes": r.get("Notes") or "", "By": r.get("Last_updated_by") or ""}
            for r in returns
        ]), column_config={"Weight (kg)": st.column_config.NumberColumn(format="%.2f")})
        st.caption("Charge net kg above is already after returns to inventory.")
    else:
        st.caption("Nothing was returned from this heat's charge.")


def _chem_vs_spec_table(rows: list[dict]) -> "pd.io.formats.style.Styler":
    df = pd.DataFrame([
        {
            "Element": r["Element_symbol"],
            "Name": r["Element_Name"],
            "Heat %": _pct_text(r["Percentage"], r["Less_than"]),
            "Min %": _limit(r["Min_percent"]),
            "Max %": _limit(r["Max_percent"]),
            "Spec": r["Spec"],
            "Result": r["Status"],
            "Off by (% pts)": _off(r),
        }
        for r in rows
    ])
    statuses = [r["Status"] for r in rows]

    def _style(row: pd.Series) -> list[str]:
        status = statuses[row.name]
        if status in (db.SPEC_STATUS_LOW, db.SPEC_STATUS_HIGH):
            return [RED] * len(row)
        if status == db.SPEC_STATUS_MISSING:
            return [AMBER] * len(row)
        return [""] * len(row)

    return df.style.apply(_style, axis=1)


# ── Chemistry against its own alloy ──────────────────────────────────────────
with tabs[2]:
    if not chemistry:
        st.info("No chemistry has been entered for this heat.")
    else:
        own_rows = db.compare_chemistry_to_spec(
            chemistry, db.alloy_spec_rows(batch.get("Alloy_id"))
        )
        if own_rows:
            own = db.summarize_spec_comparison(own_rows)
            (st.success if own["Verdict"] == db.SPEC_MATCH_FULL else st.warning)(
                f"Against its own alloy **{alloy.get('Alloy_name') or '—'}**: "
                f"**{own['Verdict']}** ({own['Within']} within, {own['Out']} out, "
                f"{own['Missing']} not measured)."
            )
            show_dataframe(_chem_vs_spec_table(own_rows))
        else:
            st.caption("This heat's alloy has no spec in **Alloy Master**.")
        spec_syms = {r["Element_symbol"] for r in own_rows}
        extra = [r for r in chemistry if r.get("Element_symbol") not in spec_syms]
        if extra:
            st.markdown("**Other elements recorded**")
            show_dataframe(pd.DataFrame([
                {"Element": r["Element_symbol"],
                 "Heat %": _pct_text(r.get("Percentage"), bool(r.get("Less_than")))}
                for r in extra
            ]))

# ── Output, finished goods and dispatch ──────────────────────────────────────
with tabs[3]:
    st.markdown("**Batch output**")
    if outputs:
        show_dataframe(pd.DataFrame([
            {
                "Output at": format_ui_date(r.get("Output_time"), with_time=True),
                "Output alloy": r.get("Alloy_name"),
                "Scale (kg)": r.get("Weighment_scale_weight"),
                "Stand (kg)": r.get("Stand_weight"),
                "Net (kg)": _num(r.get("Weight")),
                "Pieces": r.get("Pieces"),
                "Avg piece (kg)": (_num(r.get("Weight")) / _num(r.get("Pieces")))
                if _num(r.get("Pieces")) > 0 else None,
            }
            for r in outputs
        ]), column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in ("Scale (kg)", "Stand (kg)", "Net (kg)", "Avg piece (kg)")
        })
        st.caption(
            f"Melt loss {input_kg - output_kg:,.1f} kg"
            + (f" ({(input_kg - output_kg) / input_kg * 100:,.2f}% of charge)" if input_kg > 0 else "")
            + f". Non-spec output (Broken Ingot / Furnace Empty / Not Ok Ingot) "
            f"{output_kg - product_kg:,.1f} kg."
        )
    else:
        st.caption("No output saved yet.")
    st.markdown("**Finished goods**")
    if fg:
        show_dataframe(pd.DataFrame([
            {"Bundle": r["Bundle_id"], "Status": r.get("Finished_Goods_Status"),
             "Weight (kg)": _num(r.get("Output_Weight")), "Pieces": r.get("Output_pieces")}
            for r in fg
        ]), column_config={"Weight (kg)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.caption("No finished goods (output not completed, or all of it was non-spec).")
    st.markdown("**Packing lists and test certificates**")
    if dispatch:
        show_dataframe(pd.DataFrame([
            {"Packing list": r["Packing_list_id"],
             "Invoice date": format_ui_date(r.get("Invoice_date")),
             "Invoice no": r.get("Invoice_number") or "", "Customer": r.get("Customer_name"),
             "Alloy": r.get("Alloy_name"), "Packed (kg)": _num(r.get("Weight")),
             "Pieces": r.get("Pieces"), "Packing list status": r.get("Packing_list_status"),
             "Certificate": r.get("Certificate_no") or "",
             "Certificate status": r.get("Certificate_status") or ""}
            for r in dispatch
        ]), column_config={"Packed (kg)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.caption("This heat is not on any packing list.")

# ── Alternative customer alloys ──────────────────────────────────────────────
with tabs[4]:
    st.markdown("#### Customers whose alloy spec this heat already meets")
    st.caption(
        "Every customer alloy with a spec in **Alloy Master** is checked against this "
        "heat's chemistry. **Meets spec**: every spec element was measured and is within "
        "limits, so the stock can be offered as is. **Some elements not measured**: no "
        "element is out, but the spec has elements this heat's analysis did not record; "
        "check them before offering. **Needs deviation**: listed with the elements out and "
        "by how much, closest first, for a deviation request. Open P.O.s show which "
        "customers have demand now."
    )
    if not chemistry:
        st.info("Enter this heat's chemistry first; there is nothing to compare.")
    else:
        o1, o2, o3 = st.columns([1.2, 1, 1.4], vertical_alignment="bottom")
        with o1:
            show = st.selectbox(
                "Show",
                ["Meets spec only", "Meets spec + not measured", "All, with near misses"],
                index=1, key="ba_alt_show",
            )
        with o2:
            max_out = st.number_input(
                "Near miss: elements out (max)", min_value=1, max_value=10, value=2,
                key="ba_alt_maxout", disabled=show != "All, with near misses",
            )
        with o3:
            inc_inactive = st.checkbox("Include inactive alloys", key="ba_alt_inactive")
            demand_only = st.checkbox("Only customers with open P.O.s", key="ba_alt_demand")
        matches = db.match_batch_to_alloys(bid, include_inactive=inc_inactive)
        allowed = {
            "Meets spec only": {db.SPEC_MATCH_FULL},
            "Meets spec + not measured": {db.SPEC_MATCH_FULL, db.SPEC_MATCH_UNCHECKED},
            "All, with near misses": {db.SPEC_MATCH_FULL, db.SPEC_MATCH_UNCHECKED,
                                      db.SPEC_MATCH_DEVIATION},
        }[show]
        view = [
            m for m in matches
            if m["Verdict"] in allowed
            and (m["Verdict"] != db.SPEC_MATCH_DEVIATION or m["Out"] <= int(max_out))
            and (not demand_only or m["Open_POs"] > 0)
        ]
        n_full = sum(1 for m in matches if m["Verdict"] == db.SPEC_MATCH_FULL and not m["Is_batch_alloy"])
        n_unchecked = sum(1 for m in matches if m["Verdict"] == db.SPEC_MATCH_UNCHECKED and not m["Is_batch_alloy"])
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Alloys checked", f"{len(matches):,}")
        m2.metric("Other alloys it meets", f"{n_full:,}")
        m3.metric("Meets, some not measured", f"{n_unchecked:,}")
        m4.metric("Stock to divert (kg)", f"{fg_stock_kg:,.1f}")
        if not view:
            st.info("No customer alloy matches with these settings.")
        else:
            table = pd.DataFrame([
                {
                    "Alloy": f"{m['Alloy_name']}" + ("  (this heat's alloy)" if m["Is_batch_alloy"] else ""),
                    "Customer": m.get("Customer_name") or "—",
                    "Alloy ID": m["Alloy_id"],
                    "Result": m["Verdict"],
                    "Elements in spec": m["Elements"],
                    "Out of spec": ", ".join(
                        f"{r['Element_symbol']} {_pct_text(r['Percentage'], r['Less_than'])} "
                        f"({'max' if r['Status'] == db.SPEC_STATUS_HIGH else 'min'} "
                        f"{_limit(r['Max_percent'] if r['Status'] == db.SPEC_STATUS_HIGH else r['Min_percent'])})"
                        for r in m["Out_elements"]
                    ),
                    "Not measured": ", ".join(m["Missing_elements"]),
                    "Closest to a limit": (
                        f"{m['Tightest']['Element_symbol']} ({_pts(m['Tightest']['Headroom'])} % pts)"
                    ) if m.get("Tightest") else "",
                    "Open P.O.s": m["Open_POs"],
                    "Open P.O. kg": m["Open_PO_kg"],
                    "Next delivery": format_ui_date(m.get("Next_delivery")) if m.get("Next_delivery") else "",
                }
                for m in view
            ])
            verdicts = [m["Verdict"] for m in view]
            styled = table.style.apply(
                lambda row: [
                    GREEN if verdicts[row.name] == db.SPEC_MATCH_FULL and c == "Result"
                    else AMBER if verdicts[row.name] == db.SPEC_MATCH_UNCHECKED and c == "Result"
                    else RED if verdicts[row.name] == db.SPEC_MATCH_DEVIATION and c in ("Result", "Out of spec")
                    else ""
                    for c in row.index
                ],
                axis=1,
            )
            show_dataframe(styled, column_config={
                "Open P.O. kg": st.column_config.NumberColumn(format="%.0f"),
            })
            st.caption(
                "Pick an alloy on **Spec comparison** to see it element by element and "
                "download a deviation sheet."
            )
            st.download_button(
                "Download CSV",
                data=table.to_csv(index=False).encode("utf-8"),
                file_name=f"alternative_customers_{bid}.csv",
                mime="text/csv",
                key="ba_alt_csv",
            )

# ── Side-by-side spec comparison ─────────────────────────────────────────────
with tabs[5]:
    st.markdown("#### Compare this heat with a customer's alloy spec")
    st.caption(
        "Pick any customer alloy. Elements where this heat is outside that spec are in "
        "**red** with how far off they are; elements the heat's analysis did not record "
        "are in amber. Download the sheet to send with a deviation request."
    )
    if not chemistry:
        st.info("Enter this heat's chemistry first; there is nothing to compare.")
    else:
        alloys = [a for a in db.list_alloys(include_sidestream=False)]
        specs_by_alloy = {a["Alloy_id"]: a for a in alloys}
        with_spec = [a for a in alloys if db.alloy_spec_rows(a["Alloy_id"])]
        if not with_spec:
            st.info("No alloy has a spec in **Alloy Master** yet.")
        else:
            ids = [int(a["Alloy_id"]) for a in with_spec]
            default = st.session_state.get("ba_cmp_alloy")
            if default not in ids:
                default = next((m["Alloy_id"] for m in db.match_batch_to_alloys(bid)
                                if not m["Is_batch_alloy"]), ids[0])
                st.session_state["ba_cmp_alloy"] = default if default in ids else ids[0]
            pick = st.selectbox(
                "Customer alloy *",
                ids,
                key="ba_cmp_alloy",
                format_func=lambda v: (
                    f"{specs_by_alloy[v]['Alloy_name']} — {specs_by_alloy[v].get('Customer_name') or 'no customer'}"
                    f" (ID {v})" + ("  · this heat's alloy" if str(v) == str(batch.get("Alloy_id")) else "")
                    + ("  · inactive" if (specs_by_alloy[v].get("Status") or "Active") != "Active" else "")
                ),
            )
            rows = db.compare_chemistry_to_spec(chemistry, db.alloy_spec_rows(pick))
            summary = db.summarize_spec_comparison(rows)
            target = specs_by_alloy[pick]
            if summary["Verdict"] == db.SPEC_MATCH_FULL:
                st.success(f"This heat meets **{target['Alloy_name']}** ({target.get('Customer_name') or '—'}) on every element.")
            elif summary["Verdict"] == db.SPEC_MATCH_UNCHECKED:
                st.warning(
                    f"No element is out, but **{', '.join(summary['Missing_elements'])}** "
                    "was not measured on this heat. Check before dispatching."
                )
            else:
                st.error(
                    f"**{summary['Out']}** element(s) outside **{target['Alloy_name']}**: "
                    + "; ".join(
                        f"{r['Element_symbol']} {_pct_text(r['Percentage'], r['Less_than'])}% "
                        f"({r['Status'].lower()} {_limit(r['Max_percent'] if r['Status'] == db.SPEC_STATUS_HIGH else r['Min_percent'])}, "
                        + (f"off by {_off(r)})" if r["Deviation"] > 0 else "on the limit)")
                        for r in summary["Out_elements"]
                    )
                    + ". A deviation approval is needed to dispatch this heat against it."
                )
            show_dataframe(_chem_vs_spec_table(rows))
            spec_syms = {r["Element_symbol"] for r in rows}
            extra = [r for r in chemistry if r.get("Element_symbol") not in spec_syms]
            if extra:
                st.caption(
                    "Recorded on the heat but not in this spec: "
                    + ", ".join(f"{r['Element_symbol']} {_pct_text(r.get('Percentage'), bool(r.get('Less_than')))}"
                                for r in extra)
                )

            sheet = pd.DataFrame([
                {"Element": r["Element_symbol"], "Name": r["Element_Name"],
                 "Heat %": _pct_text(r["Percentage"], r["Less_than"]),
                 "Spec": r["Spec"], "Result": r["Status"],
                 "Off by (% pts)": _off(r)}
                for r in rows
            ])
            title = f"Chemistry vs spec - Heat {batch.get('Heat_no') or bid}"
            # The PDF may go to the customer being compared with, so it names
            # only that customer: not the customer the heat was made for, and
            # not our stock.
            meta = [
                ("Heat no", str(batch.get("Heat_no") or bid)),
                ("Production date", format_ui_date(batch.get("Production_Date")) or "—"),
                ("Compared with", f"{target['Alloy_name']} ({target.get('Customer_name') or '—'})"),
                ("Result", f"{summary['Verdict']}: {summary['Within']} within, {summary['Out']} out, "
                           f"{summary['Missing']} not measured"),
            ]
            pdf_rows = [
                (
                    [r["Element_symbol"], r["Element_Name"], _pct_text(r["Percentage"], r["Less_than"]),
                     r["Spec"], r["Status"],
                     _off(r)],
                    "bad" if r["Status"] in (db.SPEC_STATUS_LOW, db.SPEC_STATUS_HIGH) else "",
                )
                for r in rows
            ]
            notes = [("A value on a spec limit counts as out of spec. '<' marks a value "
                      "below the spectrometer's detection limit.", False)]
            if summary["Out"]:
                notes.insert(0, ("Deviation approval requested for the elements marked in red.", True))
            d1, d2, _d3 = st.columns([1, 1, 2])
            with d1:
                st.download_button(
                    "Download CSV",
                    data=sheet.to_csv(index=False).encode("utf-8"),
                    file_name=f"spec_comparison_{bid}_alloy{pick}.csv",
                    mime="text/csv",
                    key="ba_cmp_csv",
                )
            with d2:
                try:
                    pdf = summary_pdf_bytes(
                        title, meta,
                        [{"heading": "Element by element",
                          "columns": [("Element", 1, "L"), ("Name", 2.2, "L"), ("Heat %", 1.4, "R"),
                                      ("Spec %", 1.8, "R"), ("Result", 1.8, "L"), ("Off by", 1.3, "R")],
                          "rows": pdf_rows, "footer": None}],
                        notes,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.caption(f"PDF not available: {exc}")
                else:
                    st.download_button(
                        "Download PDF",
                        data=pdf,
                        file_name=re.sub(
                            r'[\\/:*?"<>|]+', "-",
                            f"Spec comparison_{batch.get('Heat_no') or bid}_{target['Alloy_name']}.pdf",
                        ),
                        mime="application/pdf",
                        key="ba_cmp_pdf",
                    )
