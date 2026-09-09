import streamlit as st
import database as db
from datetime import date, timedelta
import pandas as pd
from pages_common import _render_dashboard_refresh_bar, format_ui_date, show_dataframe, ui_date_input


st.title("Production Data Analysis")
st.caption(
    "Input and output are grouped by **day, furnace, shift, and alloy** — not "
    "by individual heat — because a furnace can run several heats in a shift "
    "and leftover material is only accounted for at the end of the shift or "
    "day. The **daily snapshot** below shows one card per alloy, with the "
    "day/furnace/shift shown once as a heading rather than repeated on every "
    "row. **Estimated output** sums each raw material's charge weight × its "
    "newest **Expected recovery %** from Raw Material Master (e.g. 100 kg AK "
    "Wire at 99% + 100 kg SAF Boring at 85% → 184 kg estimated). **Recovery "
    "variance** is (actual output − estimated output) ÷ estimated output — "
    "above +1% is highlighted green, below −1% red. **Cost/kg** is charge "
    "material cost ÷ actual output, plus that production month's Cost of "
    "Conversion total rate. Select several furnaces (or all of them) to also "
    "see an overall view combined by day, shift, and alloy — useful since a "
    "shift can run across more than one furnace."
)
_render_dashboard_refresh_bar(key_prefix="pda")

def _pda_group_key(row: dict) -> tuple:
    return (
        row.get("Production_Date"),
        row.get("Furnace"),
        row.get("Shift"),
        row.get("Alloy_id"),
    )

def _pda_render_card(
    row: dict,
    materials_by_key: dict[tuple, list[dict]],
    outputs_by_key: dict[tuple, list[dict]],
    po_cache: dict,
) -> None:
    key = _pda_group_key(row)
    with st.container(border=True):
        st.markdown(f"**{row.get('Alloy_name') or '—'}**")
        out_lines = outputs_by_key.get(key, [])
        if out_lines:
            show_dataframe(
                pd.DataFrame(
                    {
                        "Output line": [
                            o["Output_Alloy_name"] for o in out_lines
                        ],
                        "Weight (kg)": [o["Weight"] for o in out_lines],
                    }
                )
            )

        io1, io2, io3 = st.columns(3)
        io1.metric("Total output (kg)", f"{row['Total_Output']:,.2f}")
        io2.metric("Total input (kg)", f"{row['Total_Input']:,.2f}")
        io3.metric(
            "Yield %",
            f"{row['Yield_pct']:.2f}%" if row["Yield_pct"] is not None else "—",
        )

        e1, e2, e3 = st.columns(3)
        e1.metric("Estimated output (kg)", f"{row['Estimated_Output']:,.2f}")
        delta = row["Total_Output"] - row["Estimated_Output"]
        e2.metric("Actual − estimated (kg)", f"{delta:+,.2f}")
        with e3:
            st.markdown(
                '<p style="font-size:0.8rem;color:rgba(49,51,63,0.6);'
                'margin-bottom:0.2rem">Recovery loss/gain</p>',
                unsafe_allow_html=True,
            )
            variance = row["Recovery_Variance_pct"]
            if variance is None:
                st.markdown(
                    '<p style="font-size:1.5rem;margin:0">—</p>',
                    unsafe_allow_html=True,
                )
            else:
                color = (
                    "#2e7d32"
                    if variance > 1
                    else ("#c62828" if variance < -1 else "inherit")
                )
                st.markdown(
                    f'<p style="font-size:1.5rem;font-weight:600;color:{color};'
                    f'margin:0">{variance:+.2f}%</p>',
                    unsafe_allow_html=True,
                )

        material_kg = row.get("Material_Cost_per_kg")
        overall_kg = row.get("Overall_Cost_per_kg")
        st.caption(
            "Material ₹/kg: "
            + (f"{material_kg:,.2f}" if material_kg is not None else "—")
            + " · Overall ₹/kg: "
            + (f"{overall_kg:,.2f}" if overall_kg is not None else "—")
        )

        alloy_id = row.get("Alloy_id")
        if alloy_id not in po_cache:
            po_cache[alloy_id] = (
                db.latest_open_po_rate(alloy_id) if alloy_id is not None else None
            )
        open_po = po_cache[alloy_id]
        po_rate = (
            float(open_po["Rate"])
            if open_po and open_po.get("Rate") is not None
            else None
        )
        if po_rate is not None:
            cost_target = po_rate / (1 + db.MIN_PROFIT_MARGIN_PCT / 100.0)
            p1, p2 = st.columns(2)
            p1.metric(
                "Open PO rate (₹/kg)",
                f"{po_rate:,.2f}",
                help=(
                    f"Latest Open PO for this alloy: "
                    f"{open_po.get('Customer_PO_No') or '—'} "
                    f"({format_ui_date(open_po.get('Order_Date')) or '—'})."
                ),
            )
            with p2:
                st.markdown(
                    '<p style="font-size:0.8rem;color:rgba(49,51,63,0.6);'
                    'margin-bottom:0.2rem">Cost Target (₹/kg)</p>',
                    unsafe_allow_html=True,
                )
                color = (
                    "inherit"
                    if overall_kg is None
                    else ("#2e7d32" if overall_kg <= cost_target else "#c62828")
                )
                st.markdown(
                    f'<p style="font-size:1.5rem;font-weight:600;'
                    f'color:{color};margin:0">{cost_target:,.2f}</p>',
                    unsafe_allow_html=True,
                )
            st.caption(
                f"Cost Target = Open PO rate ÷ (1 + {db.MIN_PROFIT_MARGIN_PCT:.0f}%) "
                f"— the ₹/kg needed to hit a minimum "
                f"{db.MIN_PROFIT_MARGIN_PCT:.0f}% profit margin on cost. Green "
                "when Overall ₹/kg is at or under target, red when over."
            )
        elif alloy_id is not None:
            st.caption(
                "No Open purchase order for this alloy — no cost target to "
                "compare the output cost against."
            )

        mats = materials_by_key.get(key, [])
        if mats:
            st.markdown("Raw material input")
            show_dataframe(
                pd.DataFrame(
                    {
                        "Raw material": [m["Raw_Material_Name"] for m in mats],
                        "Weight (kg)": [m["Weight"] for m in mats],
                        "% of input": [m["Percent_of_Input"] for m in mats],
                        "Cost ₹/kg": [m.get("Cost_per_kg") for m in mats],
                        "Recovery %": [m.get("Recovery_pct") for m in mats],
                    }
                )
            )
            total_pct = sum((m["Percent_of_Input"] or 0) for m in mats)
            st.caption(
                f"Total: {total_pct:.1f}%. Cost ₹/kg is that material's charge "
                "cost ÷ charge weight in this group; Recovery % is from the "
                "newest **Raw Material Master** row."
            )

d1, d2, d3 = st.columns([1, 1, 1.6])
with d1:
    start_date = ui_date_input(
        "From date", value=date.today() - timedelta(days=30), key="pda_start"
    )
with d2:
    end_date = ui_date_input("To date", value=date.today(), key="pda_end")
with d3:
    all_furnaces = db.list_furnaces(active_only=False)
    furnace_filter = st.multiselect(
        "Furnaces",
        all_furnaces,
        default=all_furnaces,
        key="pda_furnaces",
        help="See one furnace, several, or all of them together.",
    )

if start_date > end_date:
    st.error("From date must be on or before To date.")
elif not furnace_filter:
    st.info("Select at least one furnace.")
else:
    data = db.production_analysis(start_date.isoformat(), end_date.isoformat())
    selected = {str(f) for f in furnace_filter}
    summary = [r for r in data["summary"] if str(r.get("Furnace")) in selected]
    materials = [
        r for r in data["materials"] if str(r.get("Furnace")) in selected
    ]
    outputs = [r for r in data["outputs"] if str(r.get("Furnace")) in selected]

    if not summary:
        st.info("No production data in this date range.")
    else:
        total_input = sum(r["Total_Input"] for r in summary)
        total_output = sum(r["Total_Output"] for r in summary)
        total_estimated = sum(r["Estimated_Output"] for r in summary)
        overall_yield = (
            total_output / total_input * 100.0 if total_input > 0 else None
        )
        overall_variance = (
            (total_output - total_estimated) / total_estimated * 100.0
            if total_estimated > 0
            else None
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total input (kg)", f"{total_input:,.2f}")
        k2.metric("Total output (kg)", f"{total_output:,.2f}")
        k3.metric(
            "Overall yield %",
            f"{overall_yield:.2f}%" if overall_yield is not None else "—",
        )
        k4.metric(
            "Overall recovery variance",
            f"{overall_variance:+.2f}%" if overall_variance is not None else "—",
        )

        st.markdown("#### Daily snapshot")
        st.caption(
            "Expand a day to see each shift, grouped by alloy. When more "
            "than one furnace ran the same alloy in a shift, their cards "
            "sit side by side so cost and yield can be compared directly."
        )
        materials_by_key: dict[tuple, list[dict]] = {}
        for m in materials:
            materials_by_key.setdefault(_pda_group_key(m), []).append(m)
        outputs_by_key: dict[tuple, list[dict]] = {}
        for o in outputs:
            outputs_by_key.setdefault(_pda_group_key(o), []).append(o)
        po_cache: dict = {}

        by_date: dict = {}
        for row in summary:
            by_date.setdefault(row["Production_Date"], {}).setdefault(
                row["Shift"], {}
            ).setdefault(row["Alloy_id"], []).append(row)

        dates_sorted = sorted(by_date.keys(), key=lambda d: str(d or ""), reverse=True)
        for i, d in enumerate(dates_sorted):
            with st.expander(format_ui_date(d) or str(d), expanded=(i == 0)):
                shifts_sorted = sorted(
                    by_date[d].keys(), key=lambda s: str(s or "")
                )
                for shift in shifts_sorted:
                    st.markdown(f"**Shift {shift}**")
                    alloy_groups = by_date[d][shift]
                    alloy_ids_sorted = sorted(
                        alloy_groups.keys(),
                        key=lambda aid: str(alloy_groups[aid][0].get("Alloy_name") or ""),
                    )
                    for alloy_id_key in alloy_ids_sorted:
                        furnace_rows = sorted(
                            alloy_groups[alloy_id_key],
                            key=lambda r: (len(str(r.get("Furnace"))), str(r.get("Furnace"))),
                        )
                        alloy_name = furnace_rows[0].get("Alloy_name") or "—"
                        if len(furnace_rows) > 1:
                            st.caption(
                                f"{alloy_name} — {len(furnace_rows)} furnaces, "
                                "side by side for comparison"
                            )
                        else:
                            st.caption(alloy_name)
                        cols = st.columns(len(furnace_rows))
                        for col, row in zip(cols, furnace_rows):
                            with col:
                                st.caption(f"Furnace {row.get('Furnace') or '—'}")
                                _pda_render_card(
                                    row, materials_by_key, outputs_by_key, po_cache
                                )
