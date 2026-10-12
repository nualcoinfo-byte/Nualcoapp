"""Purchase and Inventory dashboard (Overview section).

Every figure comes from the Dashboard materialized views (mv_pi_* plus the
existing stock / batch production views), refreshed every 4 hours with the
Production Dashboard or on demand with **Refresh now**. On SQLite the same
SQL runs live.

The From / To dates pick purchases received, raw material corrections made
and production batches made in that period. Stock on hand (by material, by
vendor and by lot) is as of the last refresh, not the period.
"""
from __future__ import annotations

import io
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
_EPS = 0.05  # kg
SRC_PURCHASE = "Purchase segregation"
SRC_PRODUCTION = "Production charge return"
SOURCE_LABEL = {SRC_PURCHASE: "Purchase segregation", SRC_PRODUCTION: "Production"}
CUSTOMER_CHARS = 12  # Alloy-wise consumption shows the customer cut to this length
GROUP_NOT_SET = "Not set"
AGE_BUCKETS = [(0, 30, "0–30 days"), (31, 60, "31–60 days"), (61, 90, "61–90 days"),
               (91, 180, "91–180 days"), (181, None, "Over 180 days")]


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rate(value: float, kg: float) -> float | None:
    return value / kg if kg > _EPS else None


def _pct(part: float, whole: float) -> float | None:
    return part / whole * 100.0 if whole > _EPS else None


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:,.1f}%"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"₹{value:,.2f}"


def _kg_cols(df: pd.DataFrame) -> dict:
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


def _frame(rows: list[dict], numeric: tuple[str, ...] = ()) -> pd.DataFrame:
    df = pd.DataFrame([dict(r) for r in rows or []])
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _hbar(values: pd.Series, value_title: str, *, fmt: str = ",.0f") -> None:
    """Ranked horizontal bars, largest first, one brand colour, hover tooltip."""
    values = values[values.abs() > 0]
    if values.empty:
        return
    df = pd.DataFrame({"Label": values.index.astype(str), "Value": values.values})
    chart = (
        alt.Chart(df)
        .mark_bar(color=BRAND_ORANGE, cornerRadiusEnd=4, size=18)
        .encode(
            x=alt.X("Value:Q", title=value_title, axis=alt.Axis(format=fmt, grid=True)),
            y=alt.Y("Label:N", title=None, sort="-x", axis=alt.Axis(labelLimit=260)),
            tooltip=[alt.Tooltip("Label:N", title=""),
                     alt.Tooltip("Value:Q", title=value_title, format=fmt)],
        )
        .properties(height=max(120, 28 * len(df)))
    )
    st.altair_chart(chart, use_container_width=True)


@st.cache_data(ttl=600, show_spinner=False)
def _load(start: date, end: date, refreshed_key: str) -> dict:
    """Everything the page reads. `refreshed_key` is the views' last refresh,
    so a refresh (scheduled or Refresh now) invalidates the cache."""
    del refreshed_key
    return {
        "purchases": db.pi_purchase_lots(start, end),
        "corrections": db.pi_corrections(start, end),
        "scrap": db.pi_scrap(start, end),
        "consumption": db.pi_production_consumption(start, end),
        "production": db.pi_production_totals(start, end),
        "stock_summary": db.list_raw_material_stock_summary(),
        "stock_by_vendor": db.list_raw_material_stock_by_vendor(),
        "lots": db.pi_inventory_lots(),
    }


@st.cache_data(ttl=600, show_spinner=False)
def _load_alloy_consumption(start: date, end: date, completed_only: bool,
                            refreshed_key: str) -> dict:
    del refreshed_key
    return db.pi_alloy_consumption(start, end, completed_only=completed_only)


def _short_customer(name: object) -> str:
    """First CUSTOMER_CHARS characters of the customer name, then "..."."""
    text = str(name or "").strip()
    if not text:
        return "—"
    return text if len(text) <= CUSTOMER_CHARS else text[:CUSTOMER_CHARS] + "..."


# ── Header, refresh, period ──────────────────────────────────────────────────
st.title("Purchase and Inventory dashboard")
st.caption(
    "Purchases received, raw material corrections made and production batches made "
    "between the **From** and **To** dates. Stock on hand by material, by vendor and by lot is "
    "as of the last data refresh, not the period. Data refreshes every 4 hours with "
    "the Production Dashboard; use **Refresh now** to pull in the latest."
)
_render_dashboard_refresh_bar(key_prefix="pi")

today = db.today_ist()
f1, f2, f3 = st.columns([1, 1, 2], vertical_alignment="bottom")
with f1:
    start = ui_date_input("From date", value=today - timedelta(days=89), key="pi_from")
with f2:
    end = ui_date_input("To date", value=today, key="pi_to")
with f3:
    include_conversion = st.checkbox(
        "Include conversion receipts",
        key="pi_include_conversion",
        help="Brakes India toll-conversion receipts are not bought from a vendor at a "
        "market rate, so they are left out of the purchase figures unless ticked.",
    )
if start > end:
    st.error("The From date is after the To date.")
    st.stop()
period_days = (end - start).days + 1

try:
    last = db.get_dashboard_last_refreshed() if db.IS_POSTGRES else None
    refreshed_key = last.isoformat() if last else "live"
    data = _load(start, end, refreshed_key)
except Exception as exc:  # noqa: BLE001
    _show_db_connection_error(exc)
    st.stop()

purch = _frame(
    data["purchases"],
    ("Received_kg", "Costed_kg", "Purchase_value", "Processing_loss_kg",
     "Processing_loss_value", "Remaining_kg"),
)
if not purch.empty:
    purch["Cost_per_kg"] = pd.to_numeric(purch["Cost_per_kg"], errors="coerce")
if not purch.empty and not include_conversion:
    purch = purch[purch["Receipt_type"] != db.RECEIPT_TYPE_CONVERSION]
corr = _frame(data["corrections"], ("Weight", "Value"))
scrap = _frame(data["scrap"], ("Weight", "Value", "Uncosted_kg"))
cons = _frame(data["consumption"], ("Charged_kg", "Charged_value", "Uncosted_kg"))
prod = data["production"]

# ── Headline KPIs ────────────────────────────────────────────────────────────
bought_kg = purch["Received_kg"].sum() if not purch.empty else 0.0
bought_value = purch["Purchase_value"].sum() if not purch.empty else 0.0
costed_kg = purch["Costed_kg"].sum() if not purch.empty else 0.0
uncosted_kg = bought_kg - costed_kg
n_invoices = purch["Purchase_id"].nunique() if not purch.empty else 0
n_vendors = purch["Vendor_code"].nunique() if not purch.empty else 0
scrap_kg = scrap["Weight"].sum() if not scrap.empty else 0.0
scrap_value = scrap["Value"].sum() if not scrap.empty else 0.0

st.markdown(
    f"**{format_ui_date(start)} to {format_ui_date(end)}** · {period_days} day(s)"
)
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Purchased (kg)", f"{bought_kg:,.0f}")
k2.metric("Purchase value (₹)", f"{bought_value:,.0f}")
k3.metric("Avg ₹/kg", _fmt_rate(_rate(bought_value, costed_kg)))
k4.metric("Invoices", f"{n_invoices:,}")
k5.metric("Vendors", f"{n_vendors:,}")
k6.metric(
    "Charged to production (kg)",
    f"{prod['Input_kg']:,.0f}",
    help=f"{prod['Batches']:,} batch(es) made in the period.",
)
k7, k8, k9, k10, k11, k12 = st.columns(6)
k7.metric("Scrap generated (kg)", f"{scrap_kg:,.0f}")
k8.metric("Scrap value loss (₹)", f"{scrap_value:,.0f}")
k9.metric("Scrap % of purchased", _fmt_pct(_pct(
    scrap.loc[scrap["Source_type"] == SRC_PURCHASE, "Weight"].sum() if not scrap.empty else 0.0,
    bought_kg,
)), help="Purchase segregation scrap found in the period ÷ kg purchased in the period.")
k10.metric("Production yield", _fmt_pct(_pct(prod["Output_kg"], prod["Input_kg"])),
           help="All output kg (including Broken Ingot / Furnace Empty / Not Ok Ingot) "
           "÷ kg charged, for batches made in the period.")
k11.metric("Corrections", f"{len(corr):,}")
k12.metric(
    "Cost not entered (kg)",
    f"{uncosted_kg:,.0f}",
    help="Purchased kg whose lots have no cost per kg yet (invoice not yet costed). "
    "Left out of values and average rates.",
)

if purch.empty:
    st.info("No purchases were received in this period.")

tabs = st.tabs([
    "Vendors",
    "Vendors by raw material",
    "Cost trend",
    "Stock by material",
    "Stock by vendor",
    "Inventory by lot",
    "Scrap",
    "Scrap value loss",
    "Corrections & consumption",
    "Alloy-wise consumption",
])

# ── Top vendors / suppliers ──────────────────────────────────────────────────
with tabs[0]:
    st.subheader("Top vendors / suppliers")
    st.caption(
        "Ranked by purchase value (received kg × invoice rate) for purchases received "
        "in the period. Avg ₹/kg leaves out lots with no cost yet."
    )
    if purch.empty:
        st.info("No purchases in this period.")
    else:
        by_vendor = (
            purch.groupby("Vendor_name", dropna=False)
            .agg(
                Invoices=("Purchase_id", "nunique"),
                Materials=("Raw_Material_Name", "nunique"),
                Received=("Received_kg", "sum"),
                Costed=("Costed_kg", "sum"),
                Value=("Purchase_value", "sum"),
                Last=("Received_date", "max"),
            )
            .reset_index()
            .sort_values(["Value", "Received"], ascending=False)
        )
        total_value = by_vendor["Value"].sum()
        total_kg = by_vendor["Received"].sum()
        top_n = st.slider(
            "Vendors to show", 3, max(3, min(50, len(by_vendor))),
            min(10, max(3, len(by_vendor))), key="pi_top_vendors",
        ) if len(by_vendor) > 3 else len(by_vendor)
        top = by_vendor.head(top_n)

        shares = by_vendor["Value"] / total_value * 100 if total_value > 0 else None
        c1, c2, c3 = st.columns(3)
        c1.metric("Largest vendor", str(by_vendor.iloc[0]["Vendor_name"]))
        c2.metric("Largest vendor's share of value",
                  _fmt_pct(None if shares is None else float(shares.iloc[0])))
        c3.metric("Top 3 vendors' share of value",
                  _fmt_pct(None if shares is None else float(shares.head(3).sum())))

        _hbar(top.set_index("Vendor_name")["Value"], "Purchase value (₹)")
        vendor_df = pd.DataFrame({
            "Rank": range(1, len(top) + 1),
            "Vendor": top["Vendor_name"],
            "Invoices": top["Invoices"],
            "Raw materials": top["Materials"],
            "Received (kg)": top["Received"],
            "Purchase value (₹)": top["Value"],
            "Avg ₹/kg": [_rate(v, k) for v, k in zip(top["Value"], top["Costed"])],
            "Share of value %": (top["Value"] / total_value * 100) if total_value > 0 else None,
            "Share of kg %": (top["Received"] / total_kg * 100) if total_kg > 0 else None,
            "Last received date": top["Last"],
        })
        show_dataframe(vendor_df, hide_index=True, column_config=_kg_cols(vendor_df))

# ── Top vendors by raw material ──────────────────────────────────────────────
with tabs[1]:
    st.subheader("Top vendors / suppliers by raw material")
    st.caption(
        "Who supplied each raw material in the period, ranked by kg received. "
        "Split lots count under the material they were split into."
    )
    if purch.empty:
        st.info("No purchases in this period.")
    else:
        vm = (
            purch.groupby(["Raw_Material_Name", "Vendor_name"], dropna=False)
            .agg(
                Received=("Received_kg", "sum"),
                Costed=("Costed_kg", "sum"),
                Value=("Purchase_value", "sum"),
                Lots=("Lot_id", "count"),
                Min_rate=("Cost_per_kg", "min"),
                Max_rate=("Cost_per_kg", "max"),
                Last=("Received_date", "max"),
            )
            .reset_index()
        )
        mat_totals = vm.groupby("Raw_Material_Name")["Received"].sum().sort_values(ascending=False)
        pick = st.selectbox(
            "Raw material", list(mat_totals.index), key="pi_vendor_material",
            help="Sorted by kg received in the period.",
        )
        one = vm[vm["Raw_Material_Name"] == pick].sort_values("Received", ascending=False)
        mat_kg = one["Received"].sum()
        mat_value = one["Value"].sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Received (kg)", f"{mat_kg:,.0f}")
        m2.metric("Purchase value (₹)", f"{mat_value:,.0f}")
        m3.metric("Avg ₹/kg", _fmt_rate(_rate(mat_value, one["Costed"].sum())))
        m4.metric("Vendors", f"{len(one):,}")
        _hbar(one.set_index("Vendor_name")["Received"], "Received (kg)")
        one_df = pd.DataFrame({
            "Rank": range(1, len(one) + 1),
            "Vendor": one["Vendor_name"],
            "Lots": one["Lots"],
            "Received (kg)": one["Received"],
            "Share of kg %": one["Received"] / mat_kg * 100 if mat_kg > _EPS else None,
            "Purchase value (₹)": one["Value"],
            "Avg ₹/kg": [_rate(v, k) for v, k in zip(one["Value"], one["Costed"])],
            "Lowest ₹/kg": one["Min_rate"],
            "Highest ₹/kg": one["Max_rate"],
            "Last received date": one["Last"],
        })
        show_dataframe(one_df, hide_index=True, column_config=_kg_cols(one_df))

        with st.expander("Every raw material and its top vendor"):
            best = vm.sort_values(["Raw_Material_Name", "Received"], ascending=[True, False])
            first = best.groupby("Raw_Material_Name").head(1).set_index("Raw_Material_Name")
            all_df = pd.DataFrame({
                "Raw material": mat_totals.index,
                "Received (kg)": mat_totals.values,
                "Vendors": vm.groupby("Raw_Material_Name")["Vendor_name"].nunique()
                .reindex(mat_totals.index).values,
                "Top vendor": first["Vendor_name"].reindex(mat_totals.index).values,
                "Top vendor (kg)": first["Received"].reindex(mat_totals.index).values,
            })
            all_df["Top vendor share %"] = all_df["Top vendor (kg)"] / all_df["Received (kg)"].where(
                all_df["Received (kg)"] > _EPS
            ) * 100
            show_dataframe(all_df, hide_index=True, column_config=_kg_cols(all_df))

# ── Raw material cost trend ──────────────────────────────────────────────────
with tabs[2]:
    st.subheader("Raw material cost trend")
    st.caption(
        "Weighted average invoice rate (purchase value ÷ costed kg) per raw material, "
        "for purchases received in the period. A gap means nothing was bought then."
    )
    costed = purch[purch["Costed_kg"] > _EPS] if not purch.empty else purch
    if costed.empty:
        st.info("No costed purchases in this period.")
    else:
        ranked = (
            costed.groupby("Raw_Material_Name")["Costed_kg"].sum().sort_values(ascending=False)
        )
        t1, t2 = st.columns([3, 1], vertical_alignment="bottom")
        with t1:
            trend_pick = st.multiselect(
                "Raw materials (up to 8)",
                list(ranked.index),
                default=list(ranked.index[:5]),
                max_selections=8,
                key="pi_trend_materials",
            )
        with t2:
            grain = st.radio(
                "Group by", ["Week", "Month"], horizontal=True,
                index=1 if period_days > 62 else 0, key="pi_trend_grain",
            )
        if not trend_pick:
            st.info("Pick at least one raw material.")
        else:
            tr = costed[costed["Raw_Material_Name"].isin(trend_pick)].copy()
            tr["day"] = pd.to_datetime(tr["Received_date"], errors="coerce")
            tr = tr.dropna(subset=["day"])
            tr["Period"] = tr["day"].dt.to_period("W-SUN" if grain == "Week" else "M").dt.start_time
            grouped = (
                tr.groupby(["Period", "Raw_Material_Name"])[["Purchase_value", "Costed_kg"]]
                .sum()
                .reset_index()
            )
            grouped["Rate"] = grouped["Purchase_value"] / grouped["Costed_kg"]
            pivot = grouped.pivot(index="Period", columns="Raw_Material_Name", values="Rate")
            pivot = pivot.reindex(columns=[m for m in trend_pick if m in pivot.columns])
            st.line_chart(pivot, x_label=f"{grain} starting", y_label="₹/kg", height=360)

            summary = []
            for mat in trend_pick:
                g = grouped[grouped["Raw_Material_Name"] == mat].sort_values("Period")
                if g.empty:
                    continue
                first_rate = float(g["Rate"].iloc[0])
                last_rate = float(g["Rate"].iloc[-1])
                summary.append({
                    "Raw material": mat,
                    "Costed (kg)": float(g["Costed_kg"].sum()),
                    "Avg ₹/kg": float(g["Purchase_value"].sum() / g["Costed_kg"].sum()),
                    f"First {grain.lower()} ₹/kg": first_rate,
                    f"Latest {grain.lower()} ₹/kg": last_rate,
                    "Change %": (last_rate - first_rate) / first_rate * 100 if first_rate else None,
                    "Lowest ₹/kg": float(g["Rate"].min()),
                    "Highest ₹/kg": float(g["Rate"].max()),
                })
            sdf = pd.DataFrame(summary)
            show_dataframe(sdf, hide_index=True, column_config=_kg_cols(sdf))
            with st.expander(f"₹/kg by {grain.lower()}"):
                table = pivot.copy()
                table.index = [format_ui_date(p.date()) for p in table.index]
                st.dataframe(table.style.format("{:,.2f}", na_rep="—"), use_container_width=True)

# ── Raw material stock by material (moved from the Dashboard) ────────────────
consumed_by_mat = (
    cons.groupby("Raw_Material_Name")["Charged_kg"].sum() if not cons.empty else pd.Series(dtype=float)
)
with tabs[3]:
    st.subheader("Raw material stock by material")
    st.caption(
        "Stock on hand as of the last data refresh (not the period). One row per raw "
        "material across all lots (lots on cancelled invoices excluded). **Charged** is "
        "what production batches have used, net of returns to inventory. **Remaining** "
        "is split by lot status. Avg ₹/kg is stock value ÷ the remaining kg that has a "
        "cost. **Days of cover** is remaining kg ÷ the average kg charged per day in the "
        "chosen period."
    )
    stock_summary = data["stock_summary"]
    if not stock_summary:
        st.info("No raw material inventory yet.")
    else:
        sf1, sf2 = st.columns([3, 1], vertical_alignment="bottom")
        with sf1:
            stock_pick = st.multiselect(
                "Look up raw material",
                sorted(str(r["Raw_Material_Name"]) for r in stock_summary),
                placeholder="All raw materials",
                key="pi_stock_materials",
            )
        with sf2:
            stock_show_empty = st.checkbox(
                "Include materials with no stock", key="pi_stock_empty"
            )
        stock_rows = [
            r
            for r in stock_summary
            if (not stock_pick or r["Raw_Material_Name"] in stock_pick)
            and (stock_show_empty or stock_pick or _num(r.get("Remaining_kg")) > _EPS)
        ]
        show_other = any(_num(r.get("Other_status_kg")) > _EPS for r in stock_rows)
        show_loss = any(_num(r.get("Processing_loss_kg")) > _EPS for r in stock_rows)

        def _avg_cost(row: dict) -> float | None:
            costed_kg_ = _num(row.get("Remaining_kg")) - _num(row.get("Uncosted_kg"))
            return _num(row.get("Stock_value")) / costed_kg_ if costed_kg_ > _EPS else None

        def _cover(row: dict) -> float | None:
            per_day = _num(consumed_by_mat.get(row["Raw_Material_Name"], 0)) / period_days
            return _num(row.get("Remaining_kg")) / per_day if per_day > _EPS else None

        stock_df = pd.DataFrame(
            [
                {
                    # Column order as asked by the user: stock on hand first, then movement,
                    # value, the status split, lots and cover.
                    "Raw material": r["Raw_Material_Name"],
                    "Remaining (kg)": _num(r.get("Remaining_kg")),
                    "Charged (kg)": _num(r.get("Charged_kg")),
                    "Received (kg)": _num(r.get("Received_kg")),
                    **(
                        {"Processing loss (kg)": _num(r.get("Processing_loss_kg"))}
                        if show_loss
                        else {}
                    ),
                    "Stock value (₹)": _num(r.get("Stock_value")),
                    "Avg ₹/kg": _avg_cost(r),
                    "Ready for melt (kg)": _num(r.get("Ready_for_melt_kg")),
                    "Awaiting assay (kg)": _num(r.get("Awaiting_assay_kg")),
                    "Not ready (kg)": _num(r.get("Not_ready_kg")),
                    **(
                        {"Other status (kg)": _num(r.get("Other_status_kg"))}
                        if show_other
                        else {}
                    ),
                    "Open lots": int(r.get("Open_lots") or 0),
                    "Days of cover": _cover(r),
                }
                for r in stock_rows
            ]
        )
        if stock_df.empty:
            st.info("No raw material matches.")
        else:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Remaining (kg)", f"{stock_df['Remaining (kg)'].sum():,.1f}")
            s2.metric("Ready for melt (kg)", f"{stock_df['Ready for melt (kg)'].sum():,.1f}")
            s3.metric("Awaiting assay (kg)", f"{stock_df['Awaiting assay (kg)'].sum():,.1f}")
            s4.metric("Stock value (₹)", f"{stock_df['Stock value (₹)'].sum():,.0f}")
            config = _kg_cols(stock_df)
            config["Days of cover"] = st.column_config.NumberColumn(format="%.0f")
            show_dataframe(stock_df, hide_index=True, column_config=config)
            if show_other:
                st.caption(
                    "**Other status** is stock on lots whose status is not Ready For "
                    "Melt, Awaiting Assay or Not Ready for Melt."
                )

# ── Raw material stock by vendor ─────────────────────────────────────────────
with tabs[4]:
    st.subheader("Raw material stock by vendor")
    st.caption(
        "Stock on hand as of the last data refresh (not the period), split by the vendor "
        "each lot was bought from; the same figures as **Stock by material**, which this "
        "adds up to. Lots on cancelled invoices are excluded. Remelt lots made from "
        "production output (Broken Ingot, Furnace Empty, Not Ok Ingot) are shown as "
        "**Internal remelt**. Avg ₹/kg is stock value ÷ the remaining kg that has a cost."
    )
    by_vendor_rows = data["stock_by_vendor"]
    if not by_vendor_rows:
        st.info("No raw material inventory yet.")
    else:
        vf1, vf2, vf3 = st.columns([2, 2, 1], vertical_alignment="bottom")
        with vf1:
            vendor_pick = st.multiselect(
                "Vendor",
                sorted({str(r["Vendor_name"]) for r in by_vendor_rows}),
                placeholder="All vendors",
                key="pi_sv_vendors",
            )
        with vf2:
            vmat_pick = st.multiselect(
                "Raw material",
                sorted({str(r["Raw_Material_Name"]) for r in by_vendor_rows}),
                placeholder="All raw materials",
                key="pi_sv_materials",
            )
        with vf3:
            vendor_show_empty = st.checkbox(
                "Include vendors with no stock", key="pi_sv_empty"
            )
        rows_v = [
            r
            for r in by_vendor_rows
            if (not vendor_pick or r["Vendor_name"] in vendor_pick)
            and (not vmat_pick or r["Raw_Material_Name"] in vmat_pick)
            and (vendor_show_empty or _num(r.get("Remaining_kg")) > _EPS)
        ]
        if not rows_v:
            st.info("No stock matches.")
        else:
            measures = {
                "Remaining (kg)": "Remaining_kg",
                "Charged (kg)": "Charged_kg",
                "Received (kg)": "Received_kg",
                "Processing loss (kg)": "Processing_loss_kg",
                "Stock value (₹)": "Stock_value",
                "Ready for melt (kg)": "Ready_for_melt_kg",
                "Awaiting assay (kg)": "Awaiting_assay_kg",
                "Not ready (kg)": "Not_ready_kg",
                "Other status (kg)": "Other_status_kg",
                "Open lots": "Open_lots",
                "_uncosted": "Uncosted_kg",
            }
            detail = pd.DataFrame(
                [
                    {
                        "Vendor": r["Vendor_name"],
                        "Raw material": r["Raw_Material_Name"],
                        **{label: _num(r.get(col)) for label, col in measures.items()},
                    }
                    for r in rows_v
                ]
            )
            detail["Open lots"] = detail["Open lots"].astype(int)
            show_loss_v = detail["Processing loss (kg)"].abs().sum() > _EPS
            show_other_v = detail["Other status (kg)"].abs().sum() > _EPS

            def _avg_rate(df: pd.DataFrame) -> pd.Series:
                costed = df["Remaining (kg)"] - df["_uncosted"]
                return (df["Stock value (₹)"] / costed.where(costed > _EPS)).astype(float)

            # Same column order as Stock by material (Days of cover is per material only).
            order = [
                "Remaining (kg)", "Charged (kg)", "Received (kg)",
                *(["Processing loss (kg)"] if show_loss_v else []),
                "Stock value (₹)", "Avg ₹/kg",
                "Ready for melt (kg)", "Awaiting assay (kg)", "Not ready (kg)",
                *(["Other status (kg)"] if show_other_v else []),
                "Open lots",
            ]

            per_vendor = detail.groupby("Vendor", as_index=False).sum(numeric_only=True)
            per_vendor["Raw materials"] = (
                detail[detail["Remaining (kg)"] > _EPS].groupby("Vendor")["Raw material"]
                .nunique().reindex(per_vendor["Vendor"]).fillna(0).astype(int).values
            )
            per_vendor["Avg ₹/kg"] = _avg_rate(per_vendor)
            total_value = per_vendor["Stock value (₹)"].sum()
            per_vendor["Share of stock value %"] = (
                per_vendor["Stock value (₹)"] / total_value * 100 if total_value > 0 else None
            )
            per_vendor = per_vendor.sort_values(
                ["Remaining (kg)", "Vendor"], ascending=[False, True]
            )

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Vendors with stock", f"{int((per_vendor['Remaining (kg)'] > _EPS).sum()):,}")
            v2.metric("Remaining (kg)", f"{per_vendor['Remaining (kg)'].sum():,.1f}")
            v3.metric("Stock value (₹)", f"{total_value:,.0f}")
            top_v = per_vendor.sort_values("Stock value (₹)", ascending=False).iloc[0]
            v4.metric(
                "Largest vendor's share of value",
                _fmt_pct(_pct(top_v["Stock value (₹)"], total_value)),
                help=f"{top_v['Vendor']}",
            )

            _hbar(per_vendor.set_index("Vendor")["Remaining (kg)"].head(15), "Remaining (kg)")

            st.markdown("**By vendor**")
            vendor_df = per_vendor[["Vendor", *order[:order.index("Open lots") + 1],
                                    "Raw materials", "Share of stock value %"]]
            show_dataframe(vendor_df, hide_index=True, column_config=_kg_cols(vendor_df))

            st.markdown("**By vendor and raw material**")
            detail["Avg ₹/kg"] = _avg_rate(detail)
            detail = detail.sort_values(
                ["Vendor", "Remaining (kg)", "Raw material"], ascending=[True, False, True]
            )
            detail_df = detail[["Vendor", "Raw material", *order]]
            show_dataframe(detail_df, hide_index=True, column_config=_kg_cols(detail_df))
            if show_other_v:
                st.caption(
                    "**Other status** is stock on lots whose status is not Ready For "
                    "Melt, Awaiting Assay or Not Ready for Melt."
                )

# ── Inventory on hand by Lot_id (moved from the Dashboard) ───────────────────
with tabs[5]:
    st.subheader("Inventory on hand by Lot_id")
    st.caption(
        "Every lot with stock left, as of the last data refresh. Age is days since the "
        "lot was received (or, for remelt lots, since the batch that made it). "
        "Stock value is remaining kg × the lot's cost per usable kg. Supplier invoice no "
        "and date are from the purchase the lot came in on (blank for remelt lots)."
    )
    lots = _frame(data["lots"], ("Remaining_Weight", "Received_weight", "Stock_value"))
    if lots.empty:
        st.info("No inventory lots with remaining weight.")
    else:
        received = pd.to_datetime(lots["Received_date"], errors="coerce")
        lots["Age_days"] = (pd.Timestamp(today) - received).dt.days

        def _bucket(age: object) -> str:
            if pd.isna(age):
                return "Unknown"
            for lo, hi, label in AGE_BUCKETS:
                if age >= lo and (hi is None or age <= hi):
                    return label
            return "Unknown"

        lots["Age"] = lots["Age_days"].map(_bucket)
        l1, l2, l3 = st.columns(3)
        with l1:
            lot_mats = st.multiselect(
                "Raw material", sorted(lots["Raw_Material_Name"].dropna().unique()),
                placeholder="All raw materials", key="pi_lot_materials",
            )
        with l2:
            lot_status = st.multiselect(
                "Status", sorted(lots["Raw_Material_Status"].dropna().unique()),
                placeholder="All statuses", key="pi_lot_status",
            )
        with l3:
            lot_age = st.multiselect(
                "Age", [b[2] for b in AGE_BUCKETS] + ["Unknown"],
                placeholder="Any age", key="pi_lot_age",
            )
        view = lots
        if lot_mats:
            view = view[view["Raw_Material_Name"].isin(lot_mats)]
        if lot_status:
            view = view[view["Raw_Material_Status"].isin(lot_status)]
        if lot_age:
            view = view[view["Age"].isin(lot_age)]

        a = st.columns(len(AGE_BUCKETS))
        for col, (_lo, _hi, label) in zip(a, AGE_BUCKETS):
            kg = view.loc[view["Age"] == label, "Remaining_Weight"].sum()
            col.metric(f"{label} (kg)", f"{kg:,.0f}")
        old_kg = view.loc[view["Age_days"] > 90, "Remaining_Weight"].sum()
        st.caption(
            f"{len(view):,} lot(s) · {view['Remaining_Weight'].sum():,.1f} kg · "
            f"₹{view['Stock_value'].sum():,.0f} · "
            f"{_fmt_pct(_pct(old_kg, view['Remaining_Weight'].sum()))} of the kg is over 90 days old"
        )
        lot_df = pd.DataFrame({
            "Lot_id": view["Lot_id"],
            "Raw material": view["Raw_Material_Name"],
            "Status": view["Raw_Material_Status"],
            "Remaining (kg)": view["Remaining_Weight"],
            "Received (kg)": view["Received_weight"],
            "Cost ₹/kg": pd.to_numeric(view["Cost_per_kg"], errors="coerce"),
            "Stock value (₹)": view["Stock_value"],
            "Received date": view["Received_date"],
            "Age (days)": view["Age_days"],
            "Vendor": view["Vendor_name"],
            "Supplier invoice no": view.get("Supplier_Invoice"),
            "Supplier invoice date": view.get("Supplier_invoice_date"),
            "Receipt type": view["Receipt_type"],
            "Usable %": pd.to_numeric(view["Usable_pct"], errors="coerce"),
            "Storage bay": view["Storage_bay"],
            "Purchase_id": view["Purchase_id"],
            "Source batch": view["Source_Batch_ID"],
            "Origin alloy": view["Origin_Alloy_name"],
        })
        config = _kg_cols(lot_df)
        config["Age (days)"] = st.column_config.NumberColumn(format="%d")
        show_dataframe(lot_df, hide_index=True, column_config=config)

# ── Scrap generated ──────────────────────────────────────────────────────────
with tabs[6]:
    st.subheader("Total scrap generated from purchase and production")
    st.caption(
        "**Purchase segregation** is contaminant (iron, plastic, rubber, dirt …) taken out "
        "of purchased lots on Raw Material Purchase Correction, dated when it was found. "
        "**Production** is charge material returned from a batch as scrap, dated by the "
        "batch's production date."
    )
    if scrap.empty:
        st.info("No scrap was recorded in this period.")
    else:
        seg = scrap[scrap["Source_type"] == SRC_PURCHASE]
        prd = scrap[scrap["Source_type"] == SRC_PRODUCTION]
        x1, x2, x3, x4 = st.columns(4)
        x1.metric("Total scrap (kg)", f"{scrap_kg:,.1f}")
        x2.metric("From purchase segregation (kg)", f"{seg['Weight'].sum():,.1f}")
        x3.metric("From production (kg)", f"{prd['Weight'].sum():,.1f}")
        x4.metric(
            "Production scrap % of charged",
            _fmt_pct(_pct(prd["Weight"].sum(), prod["Input_kg"])),
        )
        by_cat = (
            scrap.assign(Source=scrap["Source_type"].map(SOURCE_LABEL).fillna(scrap["Source_type"]))
            .pivot_table(index="Scrap_category", columns="Source", values="Weight",
                         aggfunc="sum", fill_value=0)
        )
        by_cat = by_cat.loc[by_cat.sum(axis=1).sort_values(ascending=False).index]
        st.markdown("**Scrap kg by category**")
        long = by_cat.reset_index().melt(
            id_vars="Scrap_category", var_name="Source", value_name="Kg"
        )
        order = list(by_cat.index)
        st.altair_chart(
            alt.Chart(long[long["Kg"] > 0])
            .mark_bar(size=18, stroke="white", strokeWidth=2)
            .encode(
                x=alt.X("Kg:Q", title="Scrap (kg)", stack="zero", axis=alt.Axis(format=",.0f")),
                y=alt.Y("Scrap_category:N", title=None, sort=order),
                color=alt.Color(
                    "Source:N",
                    scale=alt.Scale(domain=["Purchase segregation", "Production"],
                                    range=[BRAND_ORANGE, "#2F6DB5"]),
                    legend=alt.Legend(orient="top", title=None),
                ),
                tooltip=["Scrap_category:N", "Source:N",
                         alt.Tooltip("Kg:Q", title="kg", format=",.1f")],
            )
            .properties(height=max(120, 32 * len(order))),
            use_container_width=True,
        )

        by_mat = (
            scrap.groupby(["Raw_Material_Name", "Source_type"])
            .agg(Kg=("Weight", "sum"), Entries=("Scrap_id", "count"))
            .reset_index()
            .pivot_table(index="Raw_Material_Name", columns="Source_type", values="Kg",
                         aggfunc="sum", fill_value=0)
        )
        for col in (SRC_PURCHASE, SRC_PRODUCTION):
            if col not in by_mat.columns:
                by_mat[col] = 0.0
        bought_by_mat = (
            purch.groupby("Raw_Material_Name")["Received_kg"].sum()
            if not purch.empty else pd.Series(dtype=float)
        )
        mat_df = pd.DataFrame({
            "Raw material": by_mat.index,
            "Purchase segregation (kg)": by_mat[SRC_PURCHASE].values,
            "Production (kg)": by_mat[SRC_PRODUCTION].values,
        })
        mat_df["Total (kg)"] = mat_df["Purchase segregation (kg)"] + mat_df["Production (kg)"]
        purchased = mat_df["Raw material"].map(bought_by_mat).fillna(0.0)
        charged = mat_df["Raw material"].map(consumed_by_mat).fillna(0.0)
        mat_df["Segregation % of purchased"] = (
            mat_df["Purchase segregation (kg)"] / purchased.where(purchased > _EPS) * 100
        )
        mat_df["Production % of charged"] = (
            mat_df["Production (kg)"] / charged.where(charged > _EPS) * 100
        )
        mat_df = mat_df.sort_values("Total (kg)", ascending=False)
        st.markdown("**Scrap by raw material**")
        show_dataframe(mat_df, hide_index=True, column_config=_kg_cols(mat_df))

        with st.expander("Every scrap entry in the period"):
            entries = pd.DataFrame({
                "Scrap_id": scrap["Scrap_id"],
                "Date": scrap["Scrap_date"],
                "Source": scrap["Source_type"].map(SOURCE_LABEL).fillna(scrap["Source_type"]),
                "Category": scrap["Scrap_category"],
                "Raw material": scrap["Raw_Material_Name"],
                "Lot_id": scrap["Lot_id"],
                "Batch": scrap["Batch_ID"],
                "Vendor": scrap["Vendor_name"],
                "Weight (kg)": scrap["Weight"],
                "Cost ₹/kg": pd.to_numeric(scrap["Cost_per_kg"], errors="coerce"),
                "Value (₹)": scrap["Value"],
            }).sort_values("Date", ascending=False)
            show_dataframe(entries, hide_index=True, column_config=_kg_cols(entries))

# ── Scrap value loss ─────────────────────────────────────────────────────────
with tabs[7]:
    st.subheader("Scrap value loss")
    st.caption(
        "Scrap kg × the lot's cost per usable kg: what was paid for material that "
        "turned out to be scrap. Scrap from a lot with no cost yet is counted in kg "
        "but not in ₹."
    )
    if scrap.empty:
        st.info("No scrap was recorded in this period.")
    else:
        seg = scrap[scrap["Source_type"] == SRC_PURCHASE]
        prd = scrap[scrap["Source_type"] == SRC_PRODUCTION]
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Scrap value loss (₹)", f"{scrap_value:,.0f}")
        v2.metric("Purchase segregation (₹)", f"{seg['Value'].sum():,.0f}")
        v3.metric("Production (₹)", f"{prd['Value'].sum():,.0f}")
        v4.metric("Loss % of purchase value",
                  _fmt_pct(_pct(seg["Value"].sum(), bought_value)),
                  help="Purchase segregation scrap value ÷ purchase value, both in the period.")
        if scrap["Uncosted_kg"].sum() > _EPS:
            st.caption(
                f"{scrap['Uncosted_kg'].sum():,.1f} kg of scrap is on lots with no cost "
                "yet and is not in the ₹ figures."
            )

        by_vendor = (
            scrap.groupby("Vendor_name")
            .agg(Kg=("Weight", "sum"), Value=("Value", "sum"))
            .sort_values("Value", ascending=False)
        )
        bought_vendor = (
            purch.groupby("Vendor_name")[["Received_kg", "Purchase_value"]].sum()
            if not purch.empty else pd.DataFrame(columns=["Received_kg", "Purchase_value"])
        )
        st.markdown("**Scrap value loss by vendor**")
        _hbar(by_vendor.head(15)["Value"], "Scrap value loss (₹)")
        vrec = bought_vendor["Received_kg"].reindex(by_vendor.index).fillna(0.0)
        vval = bought_vendor["Purchase_value"].reindex(by_vendor.index).fillna(0.0)
        vdf = pd.DataFrame({
            "Vendor": by_vendor.index,
            "Scrap (kg)": by_vendor["Kg"].values,
            "Scrap value loss (₹)": by_vendor["Value"].values,
            "Purchased in period (kg)": vrec.values,
            "Scrap % of purchased kg": (by_vendor["Kg"] / vrec.where(vrec > _EPS) * 100).values,
            "Loss % of purchase value": (by_vendor["Value"] / vval.where(vval > 0) * 100).values,
        })
        show_dataframe(vdf, hide_index=True, column_config=_kg_cols(vdf))

        by_cat_v = (
            scrap.groupby(["Source_type", "Scrap_category"])
            .agg(Kg=("Weight", "sum"), Value=("Value", "sum"))
            .reset_index()
            .sort_values("Value", ascending=False)
        )
        cdf = pd.DataFrame({
            "Source": by_cat_v["Source_type"].map(SOURCE_LABEL).fillna(by_cat_v["Source_type"]),
            "Category": by_cat_v["Scrap_category"],
            "Scrap (kg)": by_cat_v["Kg"],
            "Scrap value loss (₹)": by_cat_v["Value"],
            "Share of loss %": by_cat_v["Value"] / scrap_value * 100 if scrap_value > 0 else None,
        })
        st.markdown("**By source and category**")
        show_dataframe(cdf, hide_index=True, column_config=_kg_cols(cdf))

    st.markdown("#### Other material losses in the period")
    st.caption(
        "Not scrap, shown alongside for the full picture. **Processing loss** is the part "
        "of processed purchases (e.g. BIL BORING at a usable %) that does not reach "
        "production. **Melt loss** is kg charged minus all kg output for batches made in "
        "the period, valued at those batches' average charge cost."
    )
    proc_kg = purch["Processing_loss_kg"].sum() if not purch.empty else 0.0
    proc_value = purch["Processing_loss_value"].sum() if not purch.empty else 0.0
    melt_kg = max(0.0, prod["Input_kg"] - prod["Output_kg"])
    melt_value = prod["Input_value"] * melt_kg / prod["Input_kg"] if prod["Input_kg"] > _EPS else 0.0
    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Processing loss (kg)", f"{proc_kg:,.0f}")
    o2.metric("Processing loss (₹)", f"{proc_value:,.0f}")
    o3.metric("Melt loss (kg)", f"{melt_kg:,.0f}",
              help=f"{_fmt_pct(_pct(melt_kg, prod['Input_kg']))} of kg charged.")
    o4.metric("Melt loss (₹, est.)", f"{melt_value:,.0f}")

# ── Corrections and consumption ──────────────────────────────────────────────
with tabs[8]:
    st.subheader("Raw material corrections")
    st.caption(
        "Splits, scrap and returns made on Raw Material Purchase Correction in the period. "
        "Value is kg × the lot's invoice rate."
    )
    if corr.empty:
        st.info("No corrections were made in this period.")
    else:
        ctype = (
            corr.groupby("Correction_type")
            .agg(Count=("Correction_id", "count"), Kg=("Weight", "sum"), Value=("Value", "sum"))
            .reindex(["Split", "Scrap", "Return"])
            .fillna(0.0)
        )
        cc = st.columns(3)
        for col, kind, label in zip(
            cc, ["Split", "Scrap", "Return"],
            ["Reclassified by split", "Scrapped", "Returned to vendor"],
        ):
            col.metric(
                f"{label} (kg)",
                f"{ctype.loc[kind, 'Kg']:,.1f}",
                help=f"{int(ctype.loc[kind, 'Count'])} correction(s) · "
                f"₹{ctype.loc[kind, 'Value']:,.0f}",
            )
        cv = (
            corr.pivot_table(index="Vendor_name", columns="Correction_type", values="Weight",
                             aggfunc="sum", fill_value=0)
            .reindex(columns=["Split", "Scrap", "Return"], fill_value=0)
        )
        cv["Total"] = cv.sum(axis=1)
        cv = cv.sort_values("Total", ascending=False)
        cv_df = pd.DataFrame({
            "Vendor": cv.index,
            "Split (kg)": cv["Split"].values,
            "Scrap (kg)": cv["Scrap"].values,
            "Return (kg)": cv["Return"].values,
            "Total corrected (kg)": cv["Total"].values,
        })
        st.markdown("**Corrections by vendor**")
        show_dataframe(cv_df, hide_index=True, column_config=_kg_cols(cv_df))
        with st.expander("Every correction in the period"):
            log = pd.DataFrame({
                "Correction_id": corr["Correction_id"],
                "Date": corr["Correction_date"],
                "Type": corr["Correction_type"],
                "Lot_id": corr["Lot_id"],
                "Raw material": corr["Raw_Material_Name"],
                "Vendor": corr["Vendor_name"],
                "Weight (kg)": corr["Weight"],
                "Value (₹)": corr["Value"],
                "Reason": corr["Reason"],
                "By": corr["Corrected_by"],
            }).sort_values("Date", ascending=False)
            show_dataframe(log, hide_index=True, column_config=_kg_cols(log))

    st.subheader("Raw material consumed by production")
    st.caption(
        "Charge lines of batches made in the period, net of returns to inventory, "
        "at each lot's cost per usable kg."
    )
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Batches", f"{prod['Batches']:,}")
    p2.metric("Charged (kg)", f"{prod['Input_kg']:,.0f}")
    p3.metric("Charged value (₹)", f"{prod['Input_value']:,.0f}")
    p4.metric("Output (kg)", f"{prod['Output_kg']:,.0f}")
    p5.metric("To remelt (kg)", f"{prod['Remelt_kg']:,.0f}",
              help="Output booked as Broken Ingot, Furnace Empty or Not Ok Ingot.")
    if cons.empty:
        st.info("No raw material was charged in this period.")
    else:
        cm = (
            cons.groupby("Raw_Material_Name")
            .agg(Kg=("Charged_kg", "sum"), Value=("Charged_value", "sum"),
                 Uncosted=("Uncosted_kg", "sum"))
            .sort_values("Kg", ascending=False)
        )
        bought_mat = (
            purch.groupby("Raw_Material_Name")["Received_kg"].sum()
            if not purch.empty else pd.Series(dtype=float)
        )
        total_charged = cm["Kg"].sum()
        cm_df = pd.DataFrame({
            "Raw material": cm.index,
            "Charged (kg)": cm["Kg"].values,
            "Share of charge %": (cm["Kg"] / total_charged * 100).values if total_charged else None,
            "Charged value (₹)": cm["Value"].values,
            "Avg ₹/kg": [_rate(v, k - u) for v, k, u in zip(cm["Value"], cm["Kg"], cm["Uncosted"])],
            "Purchased in period (kg)": bought_mat.reindex(cm.index).fillna(0.0).values,
        })
        cm_df["Purchased − charged (kg)"] = cm_df["Purchased in period (kg)"] - cm_df["Charged (kg)"]
        show_dataframe(cm_df, hide_index=True, column_config=_kg_cols(cm_df))
        st.caption(
            "**Purchased − charged** above zero means stock of that material built up "
            "over the period; below zero means production drew down existing stock."
        )

# ── Alloy-wise raw material consumption ──────────────────────────────────────
with tabs[9]:
    st.subheader("Raw material consumption by alloy")
    st.caption(
        "Raw material charged to heats made in the period, one row per alloy (each alloy "
        "belongs to one customer), net of charge returned to inventory. Alloys are in "
        "**alloy group** order, then by alloy name; raw materials run left to right from "
        "most to least used. The **Percentage** table shows each alloy's charge mix "
        "(its row adds up to 100%); its Total row is the mix across all alloys. "
        "Alloy group comes from the **Alloy Master**."
    )
    a1, a2 = st.columns([1, 2], vertical_alignment="bottom")
    with a1:
        completed_only = st.checkbox(
            "Completed heats only",
            key="pi_alloy_completed_only",
            help="Leave out heats whose input is still In-Progress, since their charge "
            "is not final yet.",
        )
    try:
        ac = _load_alloy_consumption(start, end, bool(completed_only), refreshed_key)
    except Exception as exc:  # noqa: BLE001
        _show_db_connection_error(exc)
        st.stop()
    alloy_df = _frame(ac["alloys"], ("Heats", "Input_kg", "Output_kg"))
    line_df = _frame(ac["lines"], ("Charged_kg", "Charged_value", "Uncosted_kg"))
    if alloy_df.empty or line_df.empty:
        st.info("No raw material was charged to heats in this period.")
    else:
        alloy_df["Alloy_group"] = (
            alloy_df["Alloy_group"].fillna("").astype(str).str.strip()
            .replace("", GROUP_NOT_SET)
        )
        alloy_df["Alloy_name"] = alloy_df["Alloy_name"].fillna("Not set").astype(str)
        groups_all = sorted(
            alloy_df["Alloy_group"].unique(),
            key=lambda g: (g == GROUP_NOT_SET, g.casefold()),
        )
        with a2:
            pick_groups = st.multiselect(
                "Alloy group", groups_all, key="pi_alloy_groups",
                placeholder="All alloy groups",
            )
        show_subtotals = st.checkbox(
            "Show alloy group subtotals", value=True, key="pi_alloy_subtotals",
        )
        if pick_groups:
            alloy_df = alloy_df[alloy_df["Alloy_group"].isin(pick_groups)]
            line_df = line_df[line_df["Alloy_id"].isin(alloy_df["Alloy_id"])]

        kg = line_df.pivot_table(index="Alloy_id", columns="Raw_Material_Name",
                                 values="Charged_kg", aggfunc="sum", fill_value=0.0)
        materials = list(kg.sum().sort_values(ascending=False).index)
        kg = kg.reindex(columns=materials, fill_value=0.0)
        money = line_df.groupby("Alloy_id").agg(
            Value=("Charged_value", "sum"), Uncosted=("Uncosted_kg", "sum"),
        )
        alloy_df = alloy_df[alloy_df["Alloy_id"].isin(kg.index)].copy()
        alloy_df["_group_unset"] = alloy_df["Alloy_group"] == GROUP_NOT_SET
        alloy_df["_group_key"] = alloy_df["Alloy_group"].str.casefold()
        alloy_df["_name_key"] = alloy_df["Alloy_name"].str.casefold()
        alloy_df["_cust_key"] = alloy_df["Customer_name"].fillna("").astype(str).str.casefold()
        alloy_df = alloy_df.sort_values(["_group_unset", "_group_key", "_name_key", "_cust_key"])

        total_kg_all = float(kg.values.sum())
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Alloys", f"{len(alloy_df):,}")
        m2.metric("Heats", f"{int(alloy_df['Heats'].sum()):,}")
        m3.metric("Charged (kg)", f"{total_kg_all:,.0f}")
        m4.metric("Raw materials used", f"{len(materials):,}")

        def _row(kind: str, group: str, alloy: str, customer: str, full_customer: str,
                 heats: float, mat_kg: pd.Series, output_kg: float, value: float,
                 uncosted: float) -> dict:
            total = float(mat_kg.sum())
            return {
                "_kind": kind,
                "Alloy group": group,
                "Alloy": alloy,
                "Customer": customer,
                "_customer_full": full_customer,
                "Heats": int(heats),
                **{m: float(mat_kg.get(m, 0.0)) for m in materials},
                "Total (kg)": total,
                "Output (kg)": output_kg,
                "Yield %": _pct(output_kg, total),
                "Charge ₹/kg": _rate(value, total - uncosted),
            }

        def _sum_row(kind, group, label, ids):
            return _row(
                kind, group, label, "", "",
                alloy_df.loc[alloy_df["Alloy_id"].isin(ids), "Heats"].sum(),
                kg.loc[ids].sum(),
                float(alloy_df.loc[alloy_df["Alloy_id"].isin(ids), "Output_kg"].sum()),
                float(money.reindex(ids)["Value"].sum()),
                float(money.reindex(ids)["Uncosted"].sum()),
            )

        rows: list[dict] = []
        for group, members in alloy_df.groupby("Alloy_group", sort=False):
            for a in members.itertuples(index=False):
                rows.append(_row(
                    "alloy", group, a.Alloy_name, _short_customer(a.Customer_name),
                    str(a.Customer_name or ""), a.Heats, kg.loc[a.Alloy_id],
                    float(a.Output_kg), float(money.loc[a.Alloy_id, "Value"]),
                    float(money.loc[a.Alloy_id, "Uncosted"]),
                ))
            if show_subtotals and len(members) > 1:
                rows.append(_sum_row("subtotal", group, f"{group} subtotal",
                                     list(members["Alloy_id"])))
        rows.append(_sum_row("total", "", "Total", list(alloy_df["Alloy_id"])))
        actual = pd.DataFrame(rows)

        pct = actual.copy()
        denom = pct["Total (kg)"].where(pct["Total (kg)"] > _EPS)
        for m in materials:
            pct[m] = pct[m] / denom * 100
        pct["Total %"] = (pct["Total (kg)"] > _EPS).map({True: 100.0, False: None})

        kinds = actual["_kind"]
        shade = {"subtotal": "background-color: rgba(241, 90, 34, 0.10)",
                 "total": "background-color: rgba(241, 90, 34, 0.20)"}

        def _row_shade(row: pd.Series) -> list[str]:
            css = shade.get(kinds.iloc[row.name], "")
            return [css] * len(row)

        def _blank_zero(fmt: str):
            return lambda v: "" if v is None or pd.isna(v) or abs(v) < 0.005 else fmt.format(v)

        id_cols = ["Alloy group", "Alloy", "Customer"]
        act_cols = id_cols + ["Heats"] + materials + [
            "Total (kg)", "Output (kg)", "Yield %", "Charge ₹/kg"]
        act_view = actual[act_cols]
        act_style = (
            act_view.style
            .format(_blank_zero("{:,.0f}"), subset=materials + ["Total (kg)", "Output (kg)"])
            .format(_blank_zero("{:,.0f}"), subset=["Heats"])
            .format(_blank_zero("{:.1f}"), subset=["Yield %"])
            .format(_blank_zero("{:,.2f}"), subset=["Charge ₹/kg"])
            .apply(_row_shade, axis=1)
        )
        st.markdown("**Actual (kg)**")
        show_dataframe(act_style, hide_index=True)
        st.caption(
            "**Yield %** = all output kg (including Broken Ingot / Furnace Empty / Not Ok "
            "Ingot) ÷ kg charged. **Charge ₹/kg** = charged value ÷ costed kg (lots whose "
            "invoice is not yet costed are left out of it). Heats still In-Progress have "
            "charge but little or no output yet, so tick **Completed heats only** for a fair yield."
        )

        pct_cols = id_cols + materials + ["Total %"]
        pct_view = pct[pct_cols]

        def _heat(col: pd.Series) -> list[str]:
            out = []
            for i, v in col.items():
                if kinds.iloc[i] != "alloy":
                    out.append(shade.get(kinds.iloc[i], ""))
                elif pd.notna(v) and v >= 0.05:
                    out.append(
                        f"background-color: rgba(241, 90, 34, {0.08 + 0.5 * min(v, 100) / 100:.2f})"
                    )
                else:
                    out.append("")
            return out

        pct_style = (
            pct_view.style
            .format(_blank_zero("{:.1f}%"), subset=materials + ["Total %"])
            .apply(_heat, subset=materials)
            .apply(lambda c: [shade.get(kinds.iloc[i], "") for i in c.index],
                   subset=id_cols + ["Total %"])
        )
        st.markdown("**Percentage of each alloy's charge**")
        show_dataframe(pct_style, hide_index=True)
        st.caption("Darker cells are a bigger share of that alloy's charge.")

        def _excel() -> bytes:
            """Both tables with the full customer name, as in the sample sheet."""
            full = actual["_customer_full"]
            xa = act_view.copy()
            xa["Customer"] = full.where(full != "", xa["Customer"])
            xp = pct_view.copy()
            xp["Customer"] = xa["Customer"]
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                xa.to_excel(writer, sheet_name="Actual (kg)", index=False, startrow=1)
                xp.to_excel(writer, sheet_name="Percentage", index=False, startrow=1)
                title = (f"Alloy wise raw material consumption, {format_ui_date(start)} "
                         f"to {format_ui_date(end)}")
                for name, sheet in writer.sheets.items():
                    sheet["A1"] = f"{title} ({name})"
                    sheet.freeze_panes = "D3"
            return buf.getvalue()

        st.download_button(
            "Download Excel",
            data=_excel(),
            file_name=f"alloy_wise_consumption_{start.isoformat()}_{end.isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="pi_alloy_excel",
        )
