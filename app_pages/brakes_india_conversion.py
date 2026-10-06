import io

import pandas as pd
import streamlit as st

import database as db
from pages_common import format_ui_date, show_dataframe

YIELD = db.TOLL_CONVERSION_YIELD_PCT
ALL = "All"
OPEN_ONLY = "Not fully returned"
RETURNED_ONLY = "Returned"

st.title("Brakes India Conversion")
st.caption(
    "Borings Brakes India sends for toll conversion into **LM25** (alloy "
    f"{db.TOLL_CONVERSION_ALLOY_ID}), returned at an agreed **{YIELD:g}%** of the "
    "weight collected. Collections are logged on **Raw Material Logging** as "
    "**Conversion** receipts; returns are **Packing Lists** of LM25 to Brakes India "
    "marked **Conversion return**, counted as returned once their test certificate "
    "is **Issued**. Returned kg is matched to the oldest collections first. "
    "Conversion and purchased borings share the yard and are valued at the same "
    "rate, so either can be melted; what matters is the LM25 still owed. Every "
    "receipt records the **Brakes India plant** it was collected from."
)

data = db.toll_conversion_tracker()
t = data["totals"]
collections = data["collections"]
returns = data["returns"]


def _as_day(value: object):
    try:
        return db._coerce_production_date(value)
    except (TypeError, ValueError):
        return None


def _kg(v: float) -> str:
    return f"{v:,.1f}"


# ── Headline ──────────────────────────────────────────────────────────────────
a1, a2, a3, a4, a5 = st.columns(5)
a1.metric("Borings collected (kg)", _kg(t["collected_kg"]))
a2.metric(f"LM25 due @{YIELD:g}% (kg)", _kg(t["target_kg"]))
a3.metric("LM25 returned (kg)", _kg(t["returned_kg"]))
a4.metric("In packing (kg)", _kg(t["in_packing_kg"]))
a5.metric(
    "Still owed (kg)",
    _kg(max(t["balance_kg"], 0.0)),
    help="LM25 due minus LM25 returned (certificate Issued).",
)
b1, b2, b3 = st.columns(3)
b1.metric(
    "Conversion borings still in yard (kg)",
    _kg(t["conversion_in_yard_kg"]),
    help="Remaining weight on conversion lots. Charging one into any heat lowers it.",
)
b2.metric(
    "LM25 finished goods available (kg)",
    _kg(t["lm25_fg_available_kg"]),
    help="Available (and Assigned) LM25 in Finished Goods Inventory, for any customer.",
)
cover = t["lm25_fg_available_kg"] - max(t["still_to_pack_kg"], 0.0)
b3.metric(
    "LM25 in stock vs still to pack (kg)",
    f"{cover:+,.1f}",
    help=(
        "LM25 finished goods available minus LM25 still owed and not yet on a "
        "packing list. Negative means more LM25 must be melted to cover the return."
    ),
)
if t["over_returned_kg"] > 0.5:
    st.warning(
        f"More LM25 has been returned than is due: **{_kg(t['over_returned_kg'])} kg** "
        "over. Check that every conversion receipt was logged, and that sales were "
        "not marked as conversion returns."
    )
elif t["still_to_pack_kg"] > 0.5 and cover < 0:
    st.info(
        f"**{_kg(-cover)} kg** more LM25 is needed to cover what is still owed to "
        "Brakes India and not yet packed."
    )

if not collections and not returns:
    st.info(
        "Nothing yet. Log Brakes India borings on **Raw Material Logging** and choose "
        "**Conversion**; they will appear here with the LM25 due."
    )
    st.stop()

# ── By plant ──────────────────────────────────────────────────────────────────
st.subheader("By Brakes India plant")
plant_view = pd.DataFrame(
    [
        {
            "Plant": r["BIL_plant"],
            "Receipts": int(r["Receipts"]),
            "Collected (kg)": round(r["Collected_kg"], 2),
            f"LM25 due @{YIELD:g}% (kg)": round(r["Due_kg"], 2),
            "Returned (kg)": round(r["Returned_kg"], 2),
            "Balance (kg)": round(r["Balance_kg"], 2),
            "Still in yard (kg)": round(r["In_yard_kg"], 2),
        }
        for r in data["by_plant"]
    ]
)
if plant_view.empty:
    st.caption("No conversion receipts yet.")
else:
    show_dataframe(
        plant_view,
        column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in plant_view.columns
            if c.endswith("(kg)")
        },
    )
    if "Not set" in set(plant_view["Plant"]):
        st.warning(
            "Some conversion receipts have no plant. An Admin can set it on "
            "**Merge BIL borings** (Admin)."
        )

# ── Collections (the Excel log) ────────────────────────────────────────────────
st.subheader("Collections")
f1, f2, f3 = st.columns([1, 1, 2])
with f1:
    status_pick = st.selectbox(
        "Show", [ALL, OPEN_ONLY, RETURNED_ONLY], key="bic_status"
    )
with f2:
    plants = [p for p in db.BIL_PLANTS if any(c.get("BIL_plant") == p for c in collections)]
    if any(not c.get("BIL_plant") for c in collections):
        plants.append("Not set")
    material_pick = st.selectbox("Plant", [ALL] + plants, key="bic_plant")
with f3:
    dates = [d for d in (_as_day(c.get("Received_date")) for c in collections) if d]
    if dates:
        picked = st.date_input(
            "Collection date range",
            value=(min(dates), max(dates)),
            format="DD-MM-YYYY",
            key="bic_range",
        )
        start, end = (
            picked if isinstance(picked, (tuple, list)) and len(picked) == 2 else (None, None)
        )
    else:
        start = end = None


def _keep(c: dict) -> bool:
    if status_pick == OPEN_ONLY and c["Status"] == "Returned":
        return False
    if status_pick == RETURNED_ONLY and c["Status"] != "Returned":
        return False
    if material_pick != ALL and (c.get("BIL_plant") or "Not set") != material_pick:
        return False
    if start and end and c.get("Received_date"):
        d = _as_day(c["Received_date"])
        if d and not (start <= d <= end):
            return False
    return True


shown = [c for c in collections if _keep(c)]
coll_view = pd.DataFrame(
    [
        {
            "Status": c["Status"],
            "Collection date": format_ui_date(c.get("Received_date")),
            "Invoice no": c.get("Supplier_Invoice") or "",
            "Plant": c.get("BIL_plant") or "Not set",
            "Collected (kg)": round(c["Received_weight"], 2),
            f"LM25 due @{YIELD:g}% (kg)": round(c["Target_return"], 2),
            "Returned (kg)": round(c["Returned_kg"], 2),
            "Balance (kg)": round(c["Balance_kg"], 2),
            "Still in yard (kg)": round(c["Remaining_Weight"], 2),
            "Receipt": c["Purchase_id"],
            "Lots": int(c.get("Lots") or 0),
        }
        for c in reversed(shown)
    ]
)
if coll_view.empty:
    st.info("No collection matches these filters.")
else:
    s1, s2, s3 = st.columns(3)
    s1.metric("Collections shown", len(coll_view))
    s2.metric("Collected (kg)", _kg(coll_view["Collected (kg)"].sum()))
    s3.metric("Balance on these (kg)", _kg(coll_view["Balance (kg)"].sum()))
    kg_cols = [c for c in coll_view.columns if c.endswith("(kg)")]
    show_dataframe(
        coll_view,
        column_config={c: st.column_config.NumberColumn(format="%.2f") for c in kg_cols},
    )

# ── Returns ───────────────────────────────────────────────────────────────────
st.subheader("Returns to Brakes India")
if returns:
    ret_view = pd.DataFrame(
        [
            {
                "Counted as": "Returned" if r["Returned"] else "In packing",
                "Packing list": r["Packing_list_id"],
                "Invoice date": format_ui_date(r.get("Invoice_date")),
                "Invoice no": r.get("Invoice_number") or "",
                "P.O. No": r.get("Customer_PO_No") or "",
                "Packing list status": r.get("Packing_list_status"),
                "Certificate": r.get("Certificate_no") or "",
                "Certificate status": r.get("Certificate_status") or "Not started",
                "Issued": format_ui_date(r.get("Issued_date")),
                "LM25 (kg)": round(r["Weight"], 2),
                "Pieces": r["Pieces"],
            }
            for r in reversed(returns)
        ]
    )
    show_dataframe(
        ret_view,
        column_config={"LM25 (kg)": st.column_config.NumberColumn(format="%.2f")},
    )
else:
    ret_view = pd.DataFrame()
    st.caption(
        "No conversion returns yet. Make a **Packing List** of LM25 for Brakes India "
        "and choose **Conversion return**."
    )

# ── Stock ─────────────────────────────────────────────────────────────────────
st.subheader("Brakes India borings in the yard")
yard = data["yard"]
if yard:
    yard_view = (
        pd.DataFrame(yard)
        .pivot_table(
            index="BIL_plant",
            columns="Receipt_type",
            values="Remaining_kg",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(columns=db.RECEIPT_TYPES, fill_value=0.0)
        .reset_index()
        .rename(
            columns={
                "BIL_plant": "Plant",
                db.RECEIPT_TYPE_PURCHASE: "Purchased (kg)",
                db.RECEIPT_TYPE_CONVERSION: "Conversion (kg)",
            }
        )
    )
    yard_view["Total (kg)"] = yard_view["Purchased (kg)"] + yard_view["Conversion (kg)"]
    show_dataframe(
        yard_view,
        column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in ("Purchased (kg)", "Conversion (kg)", "Total (kg)")
        },
    )
    st.caption(
        "Purchased and conversion borings share the yard and are valued at the same "
        "rate, so a heat may use either. The LM25 owed does not change with that."
    )
else:
    yard_view = pd.DataFrame()
    st.caption("No Brakes India borings in stock.")

st.markdown(f"**LM25 finished goods (alloy {db.TOLL_CONVERSION_ALLOY_ID})**")
fg = data["lm25_fg"]
if fg:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "Status": r["Status"],
                    "Bundles": int(r["Bundles"]),
                    "Weight (kg)": round(r["Weight"], 2),
                    "Pieces": r["Pieces"],
                }
                for r in fg
            ]
        ),
        column_config={"Weight (kg)": st.column_config.NumberColumn(format="%.2f")},
    )
else:
    st.caption("No LM25 in finished goods.")

# ── Downloads ─────────────────────────────────────────────────────────────────
summary = pd.DataFrame(
    {
        "Item": [
            "Borings collected (kg)",
            f"LM25 due @{YIELD:g}% (kg)",
            "LM25 returned, certificate Issued (kg)",
            "LM25 in packing (kg)",
            "Still owed (kg)",
            "Conversion borings still in yard (kg)",
            "LM25 finished goods available (kg)",
            "As of",
        ],
        "Value": [
            round(t["collected_kg"], 2),
            round(t["target_kg"], 2),
            round(t["returned_kg"], 2),
            round(t["in_packing_kg"], 2),
            round(max(t["balance_kg"], 0.0), 2),
            round(t["conversion_in_yard_kg"], 2),
            round(t["lm25_fg_available_kg"], 2),
            format_ui_date(db.now_ist(), with_time=True) + " IST",
        ],
    }
)


def _excel() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        if not plant_view.empty:
            plant_view.to_excel(writer, sheet_name="By plant", index=False)
        coll_view.to_excel(writer, sheet_name="Collections", index=False)
        if not ret_view.empty:
            ret_view.to_excel(writer, sheet_name="Returns", index=False)
        if not yard_view.empty:
            yard_view.to_excel(writer, sheet_name="Yard", index=False)
        for sheet in writer.sheets.values():
            for column in sheet.columns:
                width = max(len(str(c.value or "")) for c in column) + 2
                sheet.column_dimensions[column[0].column_letter].width = min(width, 45)
    return buf.getvalue()


stamp = db.today_ist().isoformat()
d1, d2, _sp = st.columns([1, 1, 3])
d1.download_button(
    "Download Excel",
    data=_excel(),
    file_name=f"brakes_india_conversion_{stamp}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="bic_xlsx",
)
d2.download_button(
    "Download CSV",
    data=coll_view.to_csv(index=False).encode("utf-8"),
    file_name=f"brakes_india_conversion_collections_{stamp}.csv",
    mime="text/csv",
    key="bic_csv",
    help="The collections table as filtered above.",
)
