"""Production and Profitability dashboard (Overview section).

For the production team: what was made, at what cost per kg, how that cost
compares with customer purchase order rates, and the gross margin on material
dispatched.

Every figure comes from the Dashboard materialized views (mv_pd_* plus
mv_po_supply_status), refreshed every 4 hours with the Production Dashboard or
on demand with **Refresh now**. On SQLite the same SQL runs live.

Cost of production per kg is what Batch Output stamps on each batch: charged
material cost ÷ all output kg, plus the month's conversion cost per kg.
Revenue is dispatched kg × the PO rate. Margins are gross: before freight,
taxes, finance costs and anything else not in the conversion cost.
"""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import database as db
from pages_common import (
    _render_dashboard_refresh_bar,
    _show_db_connection_error,
    format_ui_date,
    show_dataframe,
    ui_date_input,
)

BRAND_ORANGE = "#F15A22"
SECOND_BLUE = "#2F6DB5"
_EPS = 0.05  # kg


# ── Small helpers ────────────────────────────────────────────────────────────
def _rate(value: float, kg: float) -> float | None:
    return value / kg if kg > _EPS else None


def _pct(part: float, whole: float) -> float | None:
    return part / whole * 100.0 if abs(whole) > _EPS else None


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:,.1f}%"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"₹{value:,.2f}"


def _fmt_money(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}₹{abs(value):,.0f}"


def _formats(df: pd.DataFrame) -> dict:
    """Number formats by column-name suffix."""
    config = {}
    for col in df.columns:
        if col.endswith("(kg)"):
            config[col] = st.column_config.NumberColumn(format="%.1f")
        elif col.endswith("(₹)"):
            config[col] = st.column_config.NumberColumn(format="%.0f")
        elif "₹/kg" in col:
            config[col] = st.column_config.NumberColumn(format="%.2f")
        elif col.endswith("%"):
            config[col] = st.column_config.NumberColumn(format="%.1f")
    return config


def _frame(rows: list[dict], numeric: tuple[str, ...] = (), *, keep_null: tuple[str, ...] = ()) -> pd.DataFrame:
    """DataFrame with numeric columns as floats. `keep_null` columns stay NaN when missing
    (a missing cost or rate is not zero)."""
    df = pd.DataFrame([dict(r) for r in rows or []])
    for col in numeric + keep_null:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            if col in numeric:
                df[col] = df[col].fillna(0.0)
    return df


def _weighted(df: pd.DataFrame, value_col: str, weight_col: str) -> float | None:
    """Weighted average of value_col over rows where it is known."""
    known = df[df[value_col].notna() & (df[weight_col] > _EPS)]
    total = known[weight_col].sum()
    return float((known[value_col] * known[weight_col]).sum() / total) if total > _EPS else None


def _hbar(values: pd.Series, value_title: str, *, fmt: str = ",.0f", diverging: bool = False) -> None:
    """Ranked horizontal bars, largest first, hover tooltip. `diverging` colours
    negatives blue so a loss reads differently from a profit."""
    values = values[values.abs() > 0]
    if values.empty:
        return
    df = pd.DataFrame({"Label": values.index.astype(str), "Value": values.values})
    df["Sign"] = df["Value"].map(lambda v: "Loss" if v < 0 else "Profit")
    color = (
        alt.Color("Sign:N", scale=alt.Scale(domain=["Profit", "Loss"],
                                            range=[BRAND_ORANGE, SECOND_BLUE]),
                  legend=alt.Legend(orient="top", title=None))
        if diverging and (df["Value"] < 0).any()
        else alt.value(BRAND_ORANGE)
    )
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4, size=18)
        .encode(
            x=alt.X("Value:Q", title=value_title, axis=alt.Axis(format=fmt)),
            y=alt.Y("Label:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
            color=color,
            tooltip=[alt.Tooltip("Label:N", title=""),
                     alt.Tooltip("Value:Q", title=value_title, format=fmt)],
        )
        .properties(height=max(120, 28 * len(df)))
    )
    st.altair_chart(chart, use_container_width=True)


def _period_start(series: pd.Series, grain: str) -> pd.Series:
    days = pd.to_datetime(series, errors="coerce")
    return days.dt.to_period({"Day": "D", "Week": "W-SUN", "Month": "M"}[grain]).dt.start_time


@st.cache_data(ttl=600, show_spinner=False)
def _load(start: date, end: date, refreshed_key: str) -> dict:
    """Everything the page reads. `refreshed_key` is the views' last refresh,
    so a refresh (scheduled or Refresh now) invalidates the cache."""
    del refreshed_key
    return {
        "batches": db.pd_batches(start, end),
        "dispatch": db.pd_dispatches(start, end),
        "dispatch_all": db.pd_dispatches(),
        "fg": db.pd_fg_stock(),
        "po": db.list_po_supply_status(),
    }


# ── Header, refresh, period ──────────────────────────────────────────────────
st.title("Production and Profitability dashboard")
st.caption(
    "Production batches made, and material dispatched (test certificate Issued), "
    "between the **From** and **To** dates. Cost of production is each batch's "
    "material + conversion cost per kg. Revenue is dispatched kg × the PO rate, so "
    "margins are gross. Data refreshes every 4 hours with the Production Dashboard; "
    "use **Refresh now** to pull in the latest."
)
_render_dashboard_refresh_bar(key_prefix="pd")

today = db.today_ist()
f1, f2, f3 = st.columns([1, 1, 2], vertical_alignment="bottom")
with f1:
    start = ui_date_input("From date", value=today - timedelta(days=89), key="pd_from")
with f2:
    end = ui_date_input("To date", value=today, key="pd_to")
with f3:
    include_conversion = st.checkbox(
        "Include conversion returns",
        key="pd_include_conversion",
        help="Brakes India conversion returns are billed a conversion charge on "
        "material the customer supplied, so they are left out of revenue and margin "
        "unless ticked.",
    )
if start > end:
    st.error("The From date is after the To date.")
    st.stop()
period_days = (end - start).days + 1

try:
    last = db.get_dashboard_last_refreshed() if db.IS_POSTGRES else None
    data = _load(start, end, last.isoformat() if last else "live")
except Exception as exc:  # noqa: BLE001
    _show_db_connection_error(exc)
    st.stop()

batches = _frame(
    data["batches"],
    ("Input_kg", "Input_cost", "Uncosted_input_kg", "Output_kg", "Remelt_kg",
     "Product_kg", "Pieces", "Scrap_kg"),
    keep_null=("Material_cost_per_kg", "Conversion_rate", "Cost_per_kg",
               "Sampled_pcs", "Defect_pcs"),
)
DISPATCH_NUM = ("Dispatched_kg", "Packed_kg", "Uncosted_packed_kg")
DISPATCH_NULL = ("PO_rate", "Revenue", "Cost_per_kg", "Material_cost_per_kg",
                 "Conversion_rate", "Production_cost")
disp = _frame(data["dispatch"], DISPATCH_NUM, keep_null=DISPATCH_NULL)
disp_all = _frame(data["dispatch_all"], DISPATCH_NUM, keep_null=DISPATCH_NULL)
if not include_conversion:
    if not disp.empty:
        disp = disp[disp["Dispatch_type"] != db.DISPATCH_TYPE_CONVERSION_RETURN]
    if not disp_all.empty:
        disp_all = disp_all[disp_all["Dispatch_type"] != db.DISPATCH_TYPE_CONVERSION_RETURN]
fg = _frame(data["fg"], ("Bundles", "Kg", "Value", "Uncosted_kg"))
po = _frame(
    data["po"],
    ("Order_Qty", "Dispatched_Qty", "In_packing_Qty", "Balance_Qty",
     "FG_Available_Qty", "FG_Under_Testing_Qty"),
    keep_null=("Rate",),
)

# ── Headline figures ─────────────────────────────────────────────────────────
n_batches = len(batches)
input_kg = batches["Input_kg"].sum() if n_batches else 0.0
output_kg = batches["Output_kg"].sum() if n_batches else 0.0
product_kg = batches["Product_kg"].sum() if n_batches else 0.0
remelt_kg = batches["Remelt_kg"].sum() if n_batches else 0.0
# Yield and losses only make sense once a batch's output is complete.
done = batches[batches["Output_status"] == "Completed"] if n_batches else batches
done_in = done["Input_kg"].sum() if not done.empty else 0.0
done_out = done["Output_kg"].sum() if not done.empty else 0.0
avg_cost = _weighted(batches, "Cost_per_kg", "Output_kg") if n_batches else None
avg_material = _weighted(batches, "Material_cost_per_kg", "Output_kg") if n_batches else None
avg_conversion = _weighted(batches, "Conversion_rate", "Output_kg") if n_batches else None

# Margin only on dispatch rows that have both a PO rate and a production cost.
if not disp.empty:
    disp["Margin"] = disp["Revenue"] - disp["Production_cost"]
    priced = disp[disp["Revenue"].notna() & disp["Production_cost"].notna()]
else:
    priced = disp
d_kg = disp["Dispatched_kg"].sum() if not disp.empty else 0.0
p_kg = priced["Dispatched_kg"].sum() if not priced.empty else 0.0
revenue = priced["Revenue"].sum() if not priced.empty else 0.0
cost = priced["Production_cost"].sum() if not priced.empty else 0.0
margin = revenue - cost

st.markdown(f"**{format_ui_date(start)} to {format_ui_date(end)}** · {period_days} day(s)")
st.markdown("##### Production")
k = st.columns(6)
k[0].metric("Batches", f"{n_batches:,}")
k[1].metric("Charged (kg)", f"{input_kg:,.0f}")
k[2].metric("Output (kg)", f"{output_kg:,.0f}",
            help=f"Product {product_kg:,.0f} kg + to remelt {remelt_kg:,.0f} kg.")
k[3].metric("Yield", _fmt_pct(_pct(done_out, done_in)),
            help="All output kg ÷ kg charged, for batches whose output is Completed.")
k[4].metric("Cost of production ₹/kg", _fmt_rate(avg_cost),
            help=f"Material {_fmt_rate(avg_material)} + conversion {_fmt_rate(avg_conversion)}, "
            "weighted by output kg.")
k[5].metric("Product share of output", _fmt_pct(_pct(product_kg, output_kg)),
            help="Output that is not Broken Ingot / Furnace Empty / Not Ok Ingot.")
st.markdown("##### Dispatch")
k = st.columns(6)
k[0].metric("Dispatched (kg)", f"{d_kg:,.0f}")
k[1].metric("Revenue (₹)", f"{revenue:,.0f}")
k[2].metric("Cost of production (₹)", f"{cost:,.0f}")
k[3].metric("Gross margin (₹)", _fmt_money(margin))
k[4].metric("Gross margin %", _fmt_pct(_pct(margin, revenue)))
k[5].metric("Margin ₹/kg", _fmt_rate(_rate(margin, p_kg)))
if d_kg - p_kg > _EPS:
    st.caption(
        f"{d_kg - p_kg:,.1f} kg dispatched is left out of revenue and margin because its "
        "PO has no rate or its batches have no cost of production yet."
    )

tabs = st.tabs([
    "Production",
    "Cost vs purchase order",
    "Dispatch profitability",
    "Cost trend",
    "Finished goods",
    "Losses & quality",
])

# ── Production ───────────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Production in the period")
    if not n_batches:
        st.info("No production batches in this period.")
    else:
        grain = st.radio("Group by", ["Day", "Week", "Month"], horizontal=True,
                         index=0 if period_days <= 31 else (1 if period_days <= 120 else 2),
                         key="pd_prod_grain")
        b = batches.assign(Period=_period_start(batches["Production_Date"], grain))
        trend = (
            b.groupby("Period")[["Product_kg", "Remelt_kg"]].sum().reset_index()
            .rename(columns={"Product_kg": "Product", "Remelt_kg": "To remelt"})
            .melt(id_vars="Period", var_name="Output", value_name="Kg")
        )
        st.altair_chart(
            alt.Chart(trend[trend["Kg"] > 0])
            .mark_bar(stroke="white", strokeWidth=1)
            .encode(
                x=alt.X("Period:T", title=f"{grain} starting"),
                y=alt.Y("Kg:Q", title="Output (kg)", stack="zero", axis=alt.Axis(format=",.0f")),
                color=alt.Color("Output:N",
                                scale=alt.Scale(domain=["Product", "To remelt"],
                                                range=[BRAND_ORANGE, SECOND_BLUE]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("Period:T", title=grain), "Output:N",
                         alt.Tooltip("Kg:Q", format=",.0f")],
            )
            .properties(height=280),
            use_container_width=True,
        )

        def _group(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
            rows = []
            for key, g in df.groupby(keys, dropna=False):
                key = key if isinstance(key, tuple) else (key,)
                ik, ok = g["Input_kg"].sum(), g["Output_kg"].sum()
                gd = g[g["Output_status"] == "Completed"]
                rows.append({
                    **dict(zip(keys, key)),
                    "Batches": len(g),
                    "Charged (kg)": ik,
                    "Output (kg)": ok,
                    "Product (kg)": g["Product_kg"].sum(),
                    "To remelt (kg)": g["Remelt_kg"].sum(),
                    "Yield %": _pct(gd["Output_kg"].sum(), gd["Input_kg"].sum()),
                    "Material ₹/kg": _weighted(g, "Material_cost_per_kg", "Output_kg"),
                    "Conversion ₹/kg": _weighted(g, "Conversion_rate", "Output_kg"),
                    "Cost of production ₹/kg": _weighted(g, "Cost_per_kg", "Output_kg"),
                })
            return pd.DataFrame(rows).sort_values("Output (kg)", ascending=False)

        st.markdown("**By alloy**")
        by_alloy = _group(batches, ["Alloy_name"]).rename(columns={"Alloy_name": "Alloy"})
        show_dataframe(by_alloy, hide_index=True, column_config=_formats(by_alloy))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By furnace and shift**")
            by_fs = _group(batches, ["Furnace", "Shift"])
            show_dataframe(by_fs, hide_index=True, column_config=_formats(by_fs))
        with c2:
            st.markdown("**By melting team**")
            by_team = _group(batches, ["Melting_team"]).rename(columns={"Melting_team": "Melting team"})
            show_dataframe(by_team, hide_index=True, column_config=_formats(by_team))
        with st.expander("Every batch in the period"):
            every = pd.DataFrame({
                "Batch_ID": batches["Batch_ID"],
                "Production date": batches["Production_Date"],
                "Shift": batches["Shift"],
                "Furnace": batches["Furnace"],
                "Alloy": batches["Alloy_name"],
                "Heat no": batches["Heat_no"],
                "Charged (kg)": batches["Input_kg"],
                "Output (kg)": batches["Output_kg"],
                "Yield %": (batches["Output_kg"] / batches["Input_kg"].where(batches["Input_kg"] > _EPS) * 100)
                .where(batches["Output_status"] == "Completed"),
                "Material ₹/kg": batches["Material_cost_per_kg"],
                "Conversion ₹/kg": batches["Conversion_rate"],
                "Cost of production ₹/kg": batches["Cost_per_kg"],
                "Output status": batches["Output_status"],
            }).sort_values(["Production date", "Batch_ID"], ascending=False)
            show_dataframe(every, hide_index=True, column_config=_formats(every))

# ── Cost of production vs purchase order ─────────────────────────────────────
alloy_cost = {}
if n_batches:
    for alloy, g in batches.groupby("Alloy_name"):
        alloy_cost[alloy] = {
            "cost": _weighted(g, "Cost_per_kg", "Output_kg"),
            "material": _weighted(g, "Material_cost_per_kg", "Output_kg"),
            "conversion": _weighted(g, "Conversion_rate", "Output_kg"),
        }

with tabs[1]:
    st.subheader("Cost of production vs purchase order rate")
    st.caption(
        "**Current cost** is the alloy's average cost of production per kg for batches "
        "made in the period. **Dispatched cost** is the actual cost of the batches "
        "dispatched against that PO so far (any date). Margin at current cost is what "
        "the balance still to supply would earn if it is made at today's cost."
    )
    if po.empty:
        st.info("No purchase orders yet.")
    else:
        c1, c2 = st.columns([1, 2], vertical_alignment="bottom")
        with c1:
            target = st.number_input(
                "Target margin %", min_value=0.0, max_value=100.0, value=5.0, step=0.5,
                key="pd_target_margin",
                help="PO lines whose margin at current cost is under this are flagged.",
            )
        with c2:
            include_closed = st.checkbox("Include closed and cancelled POs", key="pd_po_closed")
        lines = po if include_closed else po[po["Purchase_Order_Status"] == "Open"]

        per_po = (
            disp_all.groupby(["Customer_PO_No", "Alloy_name"])
            .agg(Kg=("Dispatched_kg", "sum"), Cost=("Production_cost", "sum"),
                 Costed=("Production_cost", lambda s: s.notna().sum()))
            if not disp_all.empty else pd.DataFrame(columns=["Kg", "Cost", "Costed"])
        )
        costed_kg = (
            disp_all[disp_all["Production_cost"].notna()]
            .groupby(["Customer_PO_No", "Alloy_name"])["Dispatched_kg"].sum()
            if not disp_all.empty else pd.Series(dtype=float)
        )

        rows = []
        for _, r in lines.iterrows():
            key = (r["Customer_PO_No"], r["Alloy_name"])
            rate = r["Rate"]
            cur = (alloy_cost.get(r["Alloy_name"]) or {}).get("cost")
            ck = float(costed_kg.get(key, 0.0)) if len(costed_kg) else 0.0
            dcost = float(per_po.loc[key, "Cost"]) / ck if key in per_po.index and ck > _EPS else None
            m_now = rate - cur if pd.notna(rate) and cur is not None else None
            m_pct = _pct(m_now, rate) if m_now is not None else None
            balance = max(0.0, float(r["Balance_Qty"]))
            if m_now is None:
                flag = "No rate" if pd.isna(rate) else "No cost yet"
            elif m_now < 0:
                flag = "⚠ Below cost"
            elif m_pct is not None and m_pct < target:
                flag = "△ Under target"
            else:
                flag = "✓ OK"
            rows.append({
                "Check": flag,
                "Customer": r["Customer_name"] or r["Cust_code"],
                "PO No": r["Customer_PO_No"],
                "Alloy": r["Alloy_name"],
                "Delivery date": r["Delivery_Date"],
                "PO rate ₹/kg": rate,
                "Order (kg)": r["Order_Qty"],
                "Dispatched (kg)": r["Dispatched_Qty"],
                "Balance (kg)": balance,
                "Dispatched cost ₹/kg": dcost,
                "Margin on dispatched ₹/kg": (rate - dcost) if dcost is not None and pd.notna(rate) else None,
                "Current cost ₹/kg": cur,
                "Margin at current cost ₹/kg": m_now,
                "Margin at current cost %": m_pct,
                "Expected margin on balance (₹)": m_now * balance if m_now is not None else None,
                "Status": r["Purchase_Order_Status"],
            })
        po_df = pd.DataFrame(rows)
        if po_df.empty:
            st.info("No open purchase orders. Tick **Include closed and cancelled POs** to see the rest.")
        else:
            order = {"⚠ Below cost": 0, "△ Under target": 1, "No cost yet": 2, "No rate": 3, "✓ OK": 4}
            po_df = po_df.sort_values(
                ["Check", "Margin at current cost %"],
                key=lambda s: s.map(order) if s.name == "Check" else s,
            )
            below = po_df[po_df["Check"] == "⚠ Below cost"]
            under = po_df[po_df["Check"] == "△ Under target"]
            exp = po_df["Expected margin on balance (₹)"].dropna()
            bal = po_df.loc[po_df["Margin at current cost ₹/kg"].notna(), "Balance (kg)"]
            w_rate = _weighted(
                po_df.assign(B=po_df["Balance (kg)"]), "PO rate ₹/kg", "B"
            )
            m = st.columns(5)
            m[0].metric("PO lines", f"{len(po_df):,}")
            m[1].metric("Below cost", f"{len(below):,}",
                        help="PO rate is lower than the alloy's current cost of production.")
            m[2].metric(f"Under {target:g}% target", f"{len(under):,}")
            m[3].metric("Avg PO rate on balance ₹/kg", _fmt_rate(w_rate))
            m[4].metric("Expected margin on balance (₹)", _fmt_money(float(exp.sum())),
                        help=f"On {bal.sum():,.0f} kg still to supply that has both a rate and a "
                        "current cost.")

            st.markdown("**By alloy**")
            arows = []
            for alloy, g in po_df.groupby("Alloy"):
                ac = alloy_cost.get(alloy) or {}
                rate_w = _weighted(g.assign(B=g["Balance (kg)"].clip(lower=0) + _EPS * 2),
                                   "PO rate ₹/kg", "B")
                cur = ac.get("cost")
                arows.append({
                    "Alloy": alloy,
                    "PO lines": len(g),
                    "Balance (kg)": g["Balance (kg)"].sum(),
                    "Avg PO rate ₹/kg": rate_w,
                    "Material ₹/kg": ac.get("material"),
                    "Conversion ₹/kg": ac.get("conversion"),
                    "Current cost ₹/kg": cur,
                    "Margin ₹/kg": rate_w - cur if rate_w is not None and cur is not None else None,
                    "Margin %": _pct(rate_w - cur, rate_w) if rate_w and cur is not None else None,
                    "Expected margin on balance (₹)": g["Expected margin on balance (₹)"].sum(min_count=1),
                })
            adf = pd.DataFrame(arows).sort_values("Balance (kg)", ascending=False)
            show_dataframe(adf, hide_index=True, column_config=_formats(adf))
            chart_src = adf.dropna(subset=["Margin ₹/kg"]).set_index("Alloy")["Margin ₹/kg"]
            if not chart_src.empty:
                st.markdown("**Margin ₹/kg at current cost, by alloy**")
                _hbar(chart_src, "Margin ₹/kg", fmt=",.2f", diverging=True)

            st.markdown("**By PO line**")
            st.caption("⚠ Below cost · △ under the target margin · ✓ OK. Sorted worst first.")
            show_dataframe(po_df, hide_index=True, column_config=_formats(po_df))

# ── Dispatch profitability ───────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Profitability of material dispatched")
    st.caption(
        "Packing lists whose test certificate was Issued in the period, dated by the "
        "certificate's issued date. Revenue = kg allocated to each PO × its rate. Cost = "
        "the same kg × the weighted cost of production of the batches packed. Gross margin "
        "is before freight, taxes and finance costs."
    )
    if disp.empty:
        st.info("Nothing was dispatched in this period.")
    else:
        def _summ(df: pd.DataFrame, key: str, label: str) -> pd.DataFrame:
            out = []
            for name, g in df.groupby(key, dropna=False):
                gp = g[g["Revenue"].notna() & g["Production_cost"].notna()]
                rev, cst = gp["Revenue"].sum(), gp["Production_cost"].sum()
                out.append({
                    label: name,
                    "Invoices": g["Packing_list_id"].nunique(),
                    "Dispatched (kg)": g["Dispatched_kg"].sum(),
                    "Revenue (₹)": rev,
                    "Cost of production (₹)": cst,
                    "Gross margin (₹)": rev - cst,
                    "Gross margin %": _pct(rev - cst, rev),
                    "Avg rate ₹/kg": _rate(rev, gp["Dispatched_kg"].sum()),
                    "Avg cost ₹/kg": _rate(cst, gp["Dispatched_kg"].sum()),
                    "Margin ₹/kg": _rate(rev - cst, gp["Dispatched_kg"].sum()),
                })
            return pd.DataFrame(out).sort_values("Gross margin (₹)", ascending=False)

        grain = st.radio("Group by", ["Week", "Month"], horizontal=True,
                         index=1 if period_days > 62 else 0, key="pd_disp_grain")
        tp = priced.assign(Period=_period_start(priced["Dispatch_date"], grain))
        tr = tp.groupby("Period")[["Revenue", "Production_cost"]].sum().reset_index()
        tr_long = tr.rename(columns={"Production_cost": "Cost of production"}).melt(
            id_vars="Period", var_name="Measure", value_name="Rupees"
        )
        if not tr_long.empty:
            st.markdown(f"**Revenue and cost of production by {grain.lower()}**")
            st.altair_chart(
                alt.Chart(tr_long)
                .mark_bar(size=14, stroke="white", strokeWidth=1)
                .encode(
                    x=alt.X("Period:T", title=f"{grain} starting"),
                    xOffset="Measure:N",
                    y=alt.Y("Rupees:Q", title="₹", axis=alt.Axis(format=",.0f")),
                    color=alt.Color("Measure:N",
                                    scale=alt.Scale(domain=["Revenue", "Cost of production"],
                                                    range=[BRAND_ORANGE, SECOND_BLUE]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=[alt.Tooltip("Period:T", title=grain), "Measure:N",
                             alt.Tooltip("Rupees:Q", title="₹", format=",.0f")],
                )
                .properties(height=280),
                use_container_width=True,
            )
            tr["Gross margin (₹)"] = tr["Revenue"] - tr["Production_cost"]
            tr_tbl = pd.DataFrame({
                f"{grain} starting": [format_ui_date(p.date()) for p in tr["Period"]],
                "Revenue (₹)": tr["Revenue"],
                "Cost of production (₹)": tr["Production_cost"],
                "Gross margin (₹)": tr["Gross margin (₹)"],
                "Gross margin %": tr["Gross margin (₹)"] / tr["Revenue"].where(tr["Revenue"] > 0) * 100,
            })
            with st.expander(f"Margin by {grain.lower()}"):
                show_dataframe(tr_tbl, hide_index=True, column_config=_formats(tr_tbl))

        by_cust = _summ(disp, "Customer_name", "Customer")
        st.markdown("**Gross margin by customer**")
        _hbar(by_cust.set_index("Customer")["Gross margin (₹)"], "Gross margin (₹)", diverging=True)
        show_dataframe(by_cust, hide_index=True, column_config=_formats(by_cust))
        st.markdown("**By alloy**")
        by_al = _summ(disp, "Alloy_name", "Alloy")
        show_dataframe(by_al, hide_index=True, column_config=_formats(by_al))

        st.markdown("**By invoice**")
        inv = (
            disp.groupby(["Packing_list_id", "Dispatch_date", "Invoice_number", "Customer_name",
                          "Alloy_name"], dropna=False)
            .agg(PO=("Customer_PO_No", lambda s: ", ".join(sorted(set(map(str, s))))),
                 Kg=("Dispatched_kg", "sum"),
                 Revenue=("Revenue", lambda s: s.sum(min_count=1)),
                 Cost=("Production_cost", lambda s: s.sum(min_count=1)),
                 Cost_kg=("Cost_per_kg", "max"))
            .reset_index()
            .sort_values("Dispatch_date", ascending=False)
        )
        inv_df = pd.DataFrame({
            "Dispatch date": inv["Dispatch_date"],
            "Invoice no": inv["Invoice_number"],
            "Packing list": inv["Packing_list_id"],
            "Customer": inv["Customer_name"],
            "Alloy": inv["Alloy_name"],
            "PO No": inv["PO"],
            "Dispatched (kg)": inv["Kg"],
            "Avg rate ₹/kg": inv["Revenue"] / inv["Kg"].where(inv["Kg"] > _EPS),
            "Cost of production ₹/kg": inv["Cost_kg"],
            "Revenue (₹)": inv["Revenue"],
            "Cost of production (₹)": inv["Cost"],
            "Gross margin (₹)": inv["Revenue"] - inv["Cost"],
            "Gross margin %": (inv["Revenue"] - inv["Cost"]) / inv["Revenue"].where(inv["Revenue"] > 0) * 100,
        })
        show_dataframe(inv_df, hide_index=True, column_config=_formats(inv_df))
        losses = inv_df[inv_df["Gross margin (₹)"] < 0]
        if not losses.empty:
            st.warning(
                f"⚠ {len(losses)} invoice(s) dispatched below cost of production, "
                f"{_fmt_money(float(losses['Gross margin (₹)'].sum()))} in total."
            )

# ── Cost trend ───────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("Cost of production trend")
    st.caption("Cost of production ₹/kg per alloy, weighted by output kg, for batches made in the period.")
    costed = batches[batches["Cost_per_kg"].notna() & (batches["Output_kg"] > _EPS)] if n_batches else batches
    if costed.empty:
        st.info("No costed batches in this period.")
    else:
        ranked = costed.groupby("Alloy_name")["Output_kg"].sum().sort_values(ascending=False)
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        with c1:
            pick = st.multiselect("Alloys (up to 8)", list(ranked.index), default=list(ranked.index[:5]),
                                  max_selections=8, key="pd_trend_alloys")
        with c2:
            grain = st.radio("Group by", ["Week", "Month"], horizontal=True,
                             index=1 if period_days > 62 else 0, key="pd_trend_grain")
        if not pick:
            st.info("Pick at least one alloy.")
        else:
            t = costed[costed["Alloy_name"].isin(pick)].assign(
                Period=lambda d: _period_start(d["Production_Date"], grain),
                W=lambda d: d["Cost_per_kg"] * d["Output_kg"],
                WM=lambda d: d["Material_cost_per_kg"].fillna(0) * d["Output_kg"],
                WC=lambda d: d["Conversion_rate"].fillna(0) * d["Output_kg"],
            )
            g = t.groupby(["Period", "Alloy_name"])[["W", "WM", "WC", "Output_kg"]].sum().reset_index()
            g["Cost"] = g["W"] / g["Output_kg"]
            pivot = g.pivot(index="Period", columns="Alloy_name", values="Cost")
            pivot = pivot.reindex(columns=[a for a in pick if a in pivot.columns])
            st.line_chart(pivot, x_label=f"{grain} starting", y_label="₹/kg", height=340)
            srows = []
            for alloy in pick:
                ga = g[g["Alloy_name"] == alloy].sort_values("Period")
                if ga.empty:
                    continue
                first, latest = float(ga["Cost"].iloc[0]), float(ga["Cost"].iloc[-1])
                kg = ga["Output_kg"].sum()
                srows.append({
                    "Alloy": alloy,
                    "Output (kg)": kg,
                    "Material ₹/kg": ga["WM"].sum() / kg,
                    "Conversion ₹/kg": ga["WC"].sum() / kg,
                    "Cost of production ₹/kg": ga["W"].sum() / kg,
                    f"First {grain.lower()} ₹/kg": first,
                    f"Latest {grain.lower()} ₹/kg": latest,
                    "Change %": (latest - first) / first * 100 if first else None,
                })
            sdf = pd.DataFrame(srows)
            show_dataframe(sdf, hide_index=True, column_config=_formats(sdf))

# ── Finished goods ───────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("Finished goods on hand")
    st.caption(
        "Bundles not yet dispatched, as of the last data refresh (not the period), "
        "valued at each batch's cost of production. **Assigned** is packed on a packing "
        "list that is not dispatched yet."
    )
    if fg.empty:
        st.info("No finished goods on hand.")
    else:
        status_label = {"Available": "Available", "Under_Testing": "Under testing",
                        "Assigned": "Assigned"}
        fg = fg.assign(Status=fg["Status"].map(status_label).fillna(fg["Status"]))
        m = st.columns(4)
        m[0].metric("On hand (kg)", f"{fg['Kg'].sum():,.0f}")
        for col, s in zip(m[1:], ["Available", "Under testing", "Assigned"]):
            col.metric(f"{s} (kg)", f"{fg.loc[fg['Status'] == s, 'Kg'].sum():,.0f}")
        st.metric("Value at cost of production (₹)", f"{fg['Value'].sum():,.0f}")
        kg = fg.pivot_table(index="Alloy_name", columns="Status", values="Kg", aggfunc="sum", fill_value=0)
        kg = kg.reindex(columns=["Available", "Under testing", "Assigned"], fill_value=0)
        val = fg.groupby("Alloy_name")["Value"].sum()
        unc = fg.groupby("Alloy_name")["Uncosted_kg"].sum()
        open_bal = (
            po[po["Purchase_Order_Status"] == "Open"].groupby("Alloy_name")["Balance_Qty"].sum()
            if not po.empty else pd.Series(dtype=float)
        )
        fg_df = pd.DataFrame({
            "Alloy": kg.index,
            "Available (kg)": kg["Available"].values,
            "Under testing (kg)": kg["Under testing"].values,
            "Assigned (kg)": kg["Assigned"].values,
        })
        fg_df["On hand (kg)"] = fg_df[["Available (kg)", "Under testing (kg)", "Assigned (kg)"]].sum(axis=1)
        fg_df["Value (₹)"] = val.reindex(kg.index).values
        fg_df["Value ₹/kg"] = fg_df["Value (₹)"] / (
            fg_df["On hand (kg)"] - unc.reindex(kg.index).fillna(0).values
        ).where(lambda s: s > _EPS)
        fg_df["Open PO balance (kg)"] = open_bal.reindex(kg.index).fillna(0).clip(lower=0).values
        fg_df = fg_df.sort_values("On hand (kg)", ascending=False)
        show_dataframe(fg_df, hide_index=True, column_config=_formats(fg_df))
        no_po = fg_df[(fg_df["Open PO balance (kg)"] <= _EPS) & (fg_df["Available (kg)"] > _EPS)]
        if not no_po.empty:
            st.caption(
                f"{len(no_po)} alloy(s) have available stock but no open PO balance: "
                + ", ".join(map(str, no_po["Alloy"].head(10)))
                + ("…" if len(no_po) > 10 else "")
            )

# ── Losses & quality ─────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader("Losses and quality")
    st.caption(
        "Batches whose output is not Completed yet are left out. "
        "**Melt loss** is kg charged minus all kg output. **Remelt** is output booked to "
        "Broken Ingot / Furnace Empty / Not Ok Ingot. **Scrap returned** is charge material "
        "sent to scrap. **K-mold defect %** is defect pieces ÷ sampled pieces."
    )
    if done.empty:
        st.info("No batches with completed output in this period.")
    else:
        melt = max(0.0, done_in - done_out)
        avg_in = _rate(done["Input_cost"].sum(), done_in - done["Uncosted_input_kg"].sum())
        sampled = done["Sampled_pcs"].fillna(0).sum()
        defects = done["Defect_pcs"].fillna(0).sum()
        d_remelt = done["Remelt_kg"].sum()
        m = st.columns(5)
        m[0].metric("Melt loss (kg)", f"{melt:,.0f}", help=_fmt_pct(_pct(melt, done_in)) + " of kg charged.")
        m[1].metric("Melt loss (₹, est.)", f"{melt * (avg_in or 0):,.0f}",
                    help="Melt loss kg × the average charge cost per kg.")
        m[2].metric("Remelt (kg)", f"{d_remelt:,.0f}", help=_fmt_pct(_pct(d_remelt, done_out)) + " of output.")
        m[3].metric("Scrap returned (kg)", f"{done['Scrap_kg'].sum():,.0f}")
        m[4].metric("K-mold defect %", _fmt_pct(_pct(defects, sampled)))

        def _loss(df: pd.DataFrame, key: str) -> pd.DataFrame:
            out = []
            for name, g in df.groupby(key, dropna=False):
                ik, ok = g["Input_kg"].sum(), g["Output_kg"].sum()
                out.append({
                    key.replace("_", " "): name,
                    "Batches": len(g),
                    "Charged (kg)": ik,
                    "Melt loss (kg)": max(0.0, ik - ok),
                    "Melt loss %": _pct(max(0.0, ik - ok), ik),
                    "Remelt (kg)": g["Remelt_kg"].sum(),
                    "Remelt %": _pct(g["Remelt_kg"].sum(), ok),
                    "Scrap returned (kg)": g["Scrap_kg"].sum(),
                    "K-mold defect %": _pct(g["Defect_pcs"].fillna(0).sum(), g["Sampled_pcs"].fillna(0).sum()),
                })
            return pd.DataFrame(out).sort_values("Melt loss %", ascending=False, na_position="last")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By furnace**")
            lf = _loss(done, "Furnace")
            show_dataframe(lf, hide_index=True, column_config=_formats(lf))
        with c2:
            st.markdown("**By alloy**")
            la = _loss(done, "Alloy_name").rename(columns={"Alloy name": "Alloy"})
            show_dataframe(la, hide_index=True, column_config=_formats(la))
        st.markdown("**By melting team**")
        lt = _loss(done, "Melting_team")
        show_dataframe(lt, hide_index=True, column_config=_formats(lt))
        no_cost = batches[batches["Cost_per_kg"].isna() & (batches["Output_kg"] > _EPS)]
        if not no_cost.empty:
            st.caption(
                f"{len(no_cost)} batch(es) with output have no cost of production yet "
                "(output not costed), so they are left out of ₹/kg figures."
            )
