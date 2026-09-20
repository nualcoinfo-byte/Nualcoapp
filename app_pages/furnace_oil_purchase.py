import streamlit as st
import database as db
from datetime import date
from pages_common import df_from_rows, empty_percent_input, photo_bytes, show_dataframe, ui_date_input


# Clearing the form after a save has to happen before its widgets are created: Streamlit
# refuses to change a widget's value later in the same run.
if st.session_state.pop("fo_pur_reset", False):
    for _key in list(st.session_state):
        if _key in ("fo_pur_qty", "fo_pur_weight_kg", "fo_pur_rate") or _key.startswith(
            ("fo_tank_type_", "fo_tank_start_", "fo_tank_end_")
        ):
            del st.session_state[_key]
    st.session_state["fo_tank_rows"] = 1

st.title("Furnace Oil Purchase")
st.caption(
    "Purchase team: log furnace oil receipts against a vendor invoice. "
    "Stock is added to **Furnace Oil Inventory**. "
    "Production records daily use on **Furnace Oil Consumption**."
)
_saved_message = st.session_state.pop("fo_pur_saved_message", None)
if _saved_message:
    st.success(_saved_message)

vendors = db.list_vendors()
vendor_opts = {f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors}
if not vendors:
    st.warning("Add at least one vendor under **Vendors** before logging a purchase.")

stock = db.get_furnace_oil_stock()
s1, s2 = st.columns(2)
s1.metric("Current stock (L)", f"{stock:,.1f}")
s2.metric("Unit", "Litre")

st.markdown("#### Purchase details")
vendor_label = st.selectbox(
    "Vendor name *",
    options=[""] + list(vendor_opts.keys()),
    key="fo_pur_vendor",
)
p1, p2, p3 = st.columns(3)
with p1:
    invoice_date = ui_date_input(
        "Supplier invoice date *", value=date.today(), key="fo_pur_invoice_date"
    )
with p2:
    invoice = st.text_input(
        "Vendor invoice *",
        placeholder="e.g. FO-INV-2026-001",
        key="fo_pur_invoice",
    )
with p3:
    received = ui_date_input(
        "Received date *", value=date.today(), key="fo_pur_received"
    )
q1, q2, q3, q4 = st.columns(4)
with q1:
    qty = empty_percent_input(
        "Quantity (litres) *",
        key="fo_pur_qty",
        max_value=None,
        step=1.0,
    )
with q2:
    weight_kg = empty_percent_input(
        "Weight in kgs",
        key="fo_pur_weight_kg",
        max_value=None,
        step=0.1,
    )
with q3:
    rate = empty_percent_input(
        "Rate per litre",
        key="fo_pur_rate",
        max_value=None,
        step=0.01,
    )
with q4:
    tank = st.text_input(
        "Storage tank",
        placeholder="e.g. Tank-1",
        key="fo_pur_tank",
    )
st.markdown("#### Tank readings")
st.caption(
    "Optional check on the quantity. Enter each tank's dip reading before and after "
    "filling (10 KL tank in cm, service oil tank in inch); the litres filled are worked "
    "out from that tank's measurement chart and compared with the quantity above."
)


def _tank_option(tank_type: str) -> str:
    if not tank_type:
        return "Select tank"
    return f"{db.FURNACE_OIL_TANK_LABELS[tank_type]} ({tank_type})"


def _add_tank_row() -> None:
    n = st.session_state.get("fo_tank_rows", 1)
    st.session_state["fo_tank_rows"] = min(n + 1, len(db.FURNACE_OIL_TANK_TYPES))


def _remove_tank_row() -> None:
    n = st.session_state.get("fo_tank_rows", 1)
    if n > 1:
        for prefix in ("fo_tank_type_", "fo_tank_start_", "fo_tank_end_"):
            st.session_state.pop(f"{prefix}{n - 1}", None)
        st.session_state["fo_tank_rows"] = n - 1


tank_row_count = st.session_state.setdefault("fo_tank_rows", 1)
tank_inputs: list[dict] = []
for i in range(tank_row_count):
    chosen_above = {st.session_state.get(f"fo_tank_type_{j}") for j in range(i)}
    own_choice = st.session_state.get(f"fo_tank_type_{i}")
    tank_options = [""] + [
        t for t in db.FURNACE_OIL_TANK_TYPES if t not in chosen_above or t == own_choice
    ]
    r1, r2, r3 = st.columns([4, 2, 2])
    with r1:
        tank_type = st.selectbox(
            "Oil tank type",
            options=tank_options,
            format_func=_tank_option,
            key=f"fo_tank_type_{i}",
        )
    unit = db.FURNACE_OIL_TANK_READING_UNITS.get(tank_type, "")
    unit_label = f" ({unit})" if unit else ""
    with r2:
        tank_start = empty_percent_input(
            f"Starting reading{unit_label}",
            key=f"fo_tank_start_{i}",
            max_value=None,
            step=0.5,
        )
    with r3:
        tank_end = empty_percent_input(
            f"Ending reading{unit_label}",
            key=f"fo_tank_end_{i}",
            max_value=None,
            step=0.5,
        )
    tank_inputs.append(
        {
            "Oil_tank_type": tank_type,
            "Starting_reading": tank_start,
            "Ending_reading": tank_end,
        }
    )
add_col, remove_col, _spacer = st.columns([1, 1, 4])
add_col.button(
    "Add tank row",
    key="fo_tank_add",
    on_click=_add_tank_row,
    disabled=tank_row_count >= len(db.FURNACE_OIL_TANK_TYPES),
)
remove_col.button(
    "Remove last row",
    key="fo_tank_remove",
    on_click=_remove_tank_row,
    disabled=tank_row_count <= 1,
)

tank_fill = db.calculate_furnace_oil_tank_fill(tank_inputs)
tank_ok_rows = [r for r in tank_fill["rows"] if r["status"] == "ok"]
for tank_row in tank_fill["rows"]:
    if tank_row["status"] == "error":
        st.error(tank_row["error"])
    elif tank_row["status"] == "incomplete":
        st.caption(f"Waiting for input: {tank_row['error']}")
if tank_ok_rows:
    show_dataframe(
        df_from_rows(
            [
                {
                    "Oil tank": _tank_option(r["Oil_tank_type"]),
                    "Starting litres": r["Starting_litres"],
                    "Ending litres": r["Ending_litres"],
                    "Litres filled": r["Litres_in_tank"],
                }
                for r in tank_ok_rows
            ]
        )
    )
    tank_total = tank_fill["total"]
    qty_entered = float(qty or 0)
    m1, m2 = st.columns(2)
    m1.metric("Total litres filled (tanks)", f"{tank_total:,.1f}")
    m2.metric("Quantity entered (L)", f"{qty_entered:,.1f}")
    if qty_entered > 0:
        # Quantity at or above the tank total: red. Below it: green.
        qty_at_or_above = qty_entered + 1e-9 >= tank_total
        colour, tint = ("#dc2626", "#fee2e2") if qty_at_or_above else ("#16a34a", "#dcfce7")
        st.markdown(
            f"""
            <style>
            .st-key-fo_pur_qty [data-testid="stNumberInputContainer"],
            .st-key-fo_pur_qty [data-baseweb="input"] {{
                border: 2px solid {colour} !important;
                background-color: {tint} !important;
            }}
            .st-key-fo_pur_qty input {{ background-color: {tint} !important; }}
            </style>
            """,
            unsafe_allow_html=True,
        )
        if qty_at_or_above:
            st.caption(
                f"Quantity {qty_entered:,.1f} L is equal to or above the tank total "
                f"{tank_total:,.1f} L."
            )
        else:
            st.caption(
                f"Quantity {qty_entered:,.1f} L is below the tank total {tank_total:,.1f} L."
            )

notes = st.text_input("Notes", key="fo_pur_notes")
d1, d2 = st.columns(2)
with d1:
    invoice_doc = st.file_uploader(
        "Invoice document",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
        key="fo_pur_doc",
    )
with d2:
    weighment_slip = st.file_uploader(
        "Weighment slip",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
        key="fo_pur_weighment",
    )

qty_val = float(qty or 0)
weight_val = float(weight_kg or 0)
rate_val = float(rate or 0)
invoice_value = qty_val * rate_val
gst_value = invoice_value * 0.18
t1, t2, t3, t4, t5 = st.columns(5)
t1.metric("Quantity (L)", f"{qty_val:,.1f}")
t2.metric("Weight (kg)", f"{weight_val:,.1f}" if weight_val > 0 else "—")
t3.metric("Invoice value", f"{invoice_value:,.2f}")
t4.metric("GST value (18%)", f"{gst_value:,.2f}")
t5.metric("Total value", f"{invoice_value + gst_value:,.2f}")

if st.button("Save furnace oil purchase", type="primary", key="fo_pur_save"):
    vendor_code = vendor_opts[vendor_label] if vendor_label else None
    if not vendors:
        st.error("Create a vendor first.")
    elif not vendor_code:
        st.error("Select a vendor name.")
    elif not (invoice or "").strip():
        st.error("Vendor invoice is required.")
    elif qty_val <= 0:
        st.error("Quantity (litres) must be greater than zero.")
    elif any(r["status"] != "ok" for r in tank_fill["rows"]):
        st.error(
            "Complete or correct the tank readings (or clear them) before saving: "
            + " ".join(r["error"] for r in tank_fill["rows"] if r["status"] != "ok")
        )
    else:
        try:
            pid = db.add_furnace_oil_purchase(
                vendor_code=vendor_code,
                invoice=invoice.strip(),
                invoice_date=invoice_date.isoformat(),
                received_date=received.isoformat(),
                quantity=qty_val,
                weight_in_kgs=weight_val if weight_val > 0 else None,
                rate_per_litre=rate_val if rate_val > 0 else None,
                storage_tank=tank.strip() or None,
                notes=notes.strip() or None,
                invoice_document=photo_bytes(invoice_doc),
                invoice_document_name=invoice_doc.name if invoice_doc else None,
                invoice_document_type=getattr(invoice_doc, "type", None) if invoice_doc else None,
                weighment_slip=photo_bytes(weighment_slip),
                weighment_slip_name=weighment_slip.name if weighment_slip else None,
                weighment_slip_type=(
                    getattr(weighment_slip, "type", None) if weighment_slip else None
                ),
                tank_readings=tank_inputs,
            )
            tank_note = (
                f" Tank litres filled: {tank_fill['total']:,.1f} L."
                if tank_ok_rows
                else ""
            )
            # Shown after the rerun below; the form is cleared at the top of the next run.
            st.session_state["fo_pur_saved_message"] = (
                f"Saved furnace oil purchase **#{pid}** "
                f"({qty_val:,.1f} L, invoice {invoice.strip()}).{tank_note} "
                f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
            )
            st.session_state["fo_pur_reset"] = True
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

st.subheader("Recent purchases")
purchases = df_from_rows(db.list_furnace_oil_purchases())
if purchases.empty:
    st.info("No furnace oil purchases yet.")
else:
    show_dataframe(purchases)

