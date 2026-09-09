import streamlit as st
import database as db
from datetime import date
from pages_common import df_from_rows, empty_percent_input, photo_bytes, show_dataframe, ui_date_input


st.title("Furnace Oil Purchase")
st.caption(
    "Purchase team: log furnace oil receipts against a vendor invoice. "
    "Stock is added to **Furnace Oil Inventory**. "
    "Production records daily use on **Furnace Oil Consumption**."
)

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
            )
            st.success(
                f"Saved furnace oil purchase **#{pid}** "
                f"({qty_val:,.1f} L, invoice {invoice.strip()}). "
                f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
            )
            st.session_state["fo_pur_qty"] = None
            st.session_state["fo_pur_weight_kg"] = None
            st.session_state["fo_pur_rate"] = None
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

