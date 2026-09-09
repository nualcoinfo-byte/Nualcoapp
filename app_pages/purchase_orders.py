import streamlit as st
import database as db
from datetime import date
from pages_common import ui_date_input


st.title("Purchase Orders")
st.caption(
    "Create or update a customer purchase order. One Customer PO No can include "
    "several alloys; each alloy line has its own rate and order qty. "
    "Optionally attach the PO document (PDF, Word, or Excel). "
    "Review all PO lines under **All Purchase Orders**."
)

customers = db.list_customer_codes()
cust_opts = {
    f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"] for c in customers
}

if not customers:
    st.warning("Add at least one customer under **Customers** before creating a PO.")
else:
    st.markdown("#### New / update purchase order")
    cust_label = st.selectbox(
        "Customer *",
        options=[""] + list(cust_opts.keys()),
        key="po_customer_sel",
    )
    cust = db.get_customer(cust_opts[cust_label]) if cust_label else None
    cust_code = cust["Cust_code"] if cust else None
    alloys = [
        a
        for a in db.list_alloys()
        if cust_code and a.get("Cust_code") == cust_code
    ]
    alloy_opts = {
        f"{a['Alloy_id']} — {a['Alloy_name']}": a["Alloy_id"] for a in alloys
    }
    if cust and not alloy_opts:
        st.info(
            f"No alloys linked to customer **{cust['Customer_name']}**. "
            "Add alloys under **Alloys** with this customer’s code."
        )

    if "po_alloy_lines" not in st.session_state:
        st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
    if "po_line_token" not in st.session_state:
        st.session_state.po_line_token = 0
    if st.session_state.get("po_lines_cust") != cust_code:
        st.session_state.po_lines_cust = cust_code
        st.session_state.po_line_token = int(st.session_state.po_line_token) + 1
        st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
    line_token = st.session_state.po_line_token

    h1, h2, h3 = st.columns(3)
    with h1:
        po_no = st.text_input("Customer PO No *", placeholder="e.g. PO-2026-001")
        order_date = ui_date_input("Order date", value=date.today())
        delivery_date = ui_date_input("Delivery date", value=date.today())
    with h2:
        st.text_input(
            "Customer name",
            value=cust["Customer_name"] if cust else "",
            disabled=True,
        )
        st.caption("Filled from Customer Master when a customer is selected.")
        po_status = st.selectbox(
            "Purchase order status *",
            options=db.PURCHASE_ORDER_STATUS,
            index=0,
            help="Open, Closed, or Cancelled. Applied to every alloy line on this PO.",
        )
    with h3:
        st.caption("One customer PO can list multiple alloys below.")

    st.markdown("##### Alloy lines")
    st.caption(
        "Each row is one alloy on this Customer PO No, with that alloy’s rate and order qty."
    )
    add_col, rem_col, _ = st.columns([1, 1, 4])
    if add_col.button("Add alloy", key="po_add_alloy_line"):
        st.session_state.po_alloy_lines.append({"qty": 0.0, "rate": 0.0})
        st.rerun()
    if rem_col.button("Remove last alloy", key="po_rem_alloy_line") and len(
        st.session_state.po_alloy_lines
    ) > 1:
        st.session_state.po_alloy_lines.pop()
        st.rerun()

    collected_lines: list[dict] = []
    for idx, _line in enumerate(st.session_state.po_alloy_lines):
        st.markdown(f"**Alloy {idx + 1}**")
        a1, a2, a3 = st.columns([2.2, 1.2, 1.2])
        with a1:
            alloy_label = st.selectbox(
                "Alloy *",
                options=["— select alloy —"] + list(alloy_opts.keys()),
                disabled=not bool(cust),
                key=f"po_line_alloy_{line_token}_{idx}",
                help="Required. The same Customer PO No can include each alloy once.",
            )
        with a2:
            order_qty = st.number_input(
                "Order qty *",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key=f"po_line_qty_{line_token}_{idx}",
            )
        with a3:
            rate = st.number_input(
                "Rate",
                min_value=0.0,
                value=0.0,
                step=0.01,
                key=f"po_line_rate_{line_token}_{idx}",
            )
        collected_lines.append(
            {
                "alloy_label": alloy_label,
                "qty": float(order_qty or 0.0),
                "rate": float(rate or 0.0),
            }
        )

    filled_lines = [
        ln for ln in collected_lines if ln["alloy_label"] != "— select alloy —"
    ]
    total_qty = sum(ln["qty"] for ln in filled_lines)
    po_value = sum(ln["qty"] * ln["rate"] for ln in filled_lines)
    gst_value = po_value * 0.18
    grand_total = po_value + gst_value
    t1, t2, t3, t4, t5 = st.columns(5)
    t1.metric("Alloys", len(filled_lines))
    t2.metric("Total order qty", f"{total_qty:,.1f}")
    t3.metric("PO value", f"{po_value:,.2f}")
    t4.metric("GST value (18%)", f"{gst_value:,.2f}")
    t5.metric("Total value", f"{grand_total:,.2f}")

    st.markdown("##### Billing address")
    b1, b2 = st.columns(2)
    with b1:
        bill_addr = st.text_input(
            "Billing address",
            value=(cust.get("Address") or "") if cust else "",
        )
        bill_city = st.text_input(
            "Billing city",
            value=(cust.get("City") or "") if cust else "",
        )
        bill_state = st.text_input(
            "Billing state",
            value=(cust.get("State") or "") if cust else "",
        )
    with b2:
        bill_pin = st.text_input(
            "Billing pincode",
            value=(cust.get("Pincode") or "") if cust else "",
        )
        bill_country = st.text_input(
            "Billing country",
            value=(cust.get("Country") or "India") if cust else "India",
        )
        copy_ship = st.checkbox("Copy billing address to shipping", value=True)

    st.markdown("##### Shipping address")
    s1, s2 = st.columns(2)
    with s1:
        ship_addr = st.text_input(
            "Shipping address",
            value=(cust.get("Address") or "") if cust and copy_ship else "",
        )
        ship_city = st.text_input(
            "Shipping city",
            value=(cust.get("City") or "") if cust and copy_ship else "",
        )
        ship_state = st.text_input(
            "Shipping state",
            value=(cust.get("State") or "") if cust and copy_ship else "",
        )
    with s2:
        ship_pin = st.text_input(
            "Shipping pincode",
            value=(cust.get("Pincode") or "") if cust and copy_ship else "",
        )
        ship_country = st.text_input(
            "Shipping country",
            value=(cust.get("Country") or "India") if cust and copy_ship else "India",
        )

    po_file = st.file_uploader(
        "PO document (optional)",
        type=["pdf", "doc", "docx", "xls", "xlsx"],
        help="PDF, Word, or Excel. Attached to every alloy line on this PO.",
    )
    save_po = st.button("Save purchase order", type="primary", key="po_save")

    if save_po:
        if not po_no.strip():
            st.error("Customer PO No is required.")
        elif not cust_label or not cust:
            st.error("Select a customer.")
        elif not alloy_opts:
            st.error("This customer has no alloys. Add alloys under **Alloys** first.")
        elif not filled_lines:
            st.error("Add at least one alloy line.")
        elif any(ln["qty"] <= 0 for ln in filled_lines):
            st.error("Each alloy line needs an order qty greater than zero.")
        else:
            labels = [ln["alloy_label"] for ln in filled_lines]
            if len(labels) != len(set(labels)):
                st.error("Each alloy can appear only once on the same Customer PO No.")
            else:
                try:
                    if copy_ship:
                        ship_addr, ship_city, ship_state = bill_addr, bill_city, bill_state
                        ship_pin, ship_country = bill_pin, bill_country
                    header = {
                        "Customer_PO_No": po_no.strip(),
                        "Cust_code": cust["Cust_code"],
                        "Customer_name": cust["Customer_name"],
                        "Order_Date": order_date.isoformat(),
                        "Delivery_Date": delivery_date.isoformat(),
                        "Billing_Address": bill_addr.strip() or None,
                        "Billing_City": bill_city.strip() or None,
                        "Billing_state": bill_state.strip() or None,
                        "Billing_Pincode": bill_pin.strip() or None,
                        "Billing_country": bill_country.strip() or None,
                        "Shipping_address": ship_addr.strip() or None,
                        "Shipping_City": ship_city.strip() or None,
                        "Shipping_state": ship_state.strip() or None,
                        "Shipping_Pincode": ship_pin.strip() or None,
                        "Shipping_country": ship_country.strip() or None,
                        "Purchase_Order_Status": po_status,
                    }
                    line_rows = [
                        {
                            "Alloy_Id": alloy_opts[ln["alloy_label"]],
                            "Order_Qty": ln["qty"],
                            "Rate": ln["rate"] if ln["rate"] > 0 else None,
                        }
                        for ln in filled_lines
                    ]
                    alloy_ids = db.upsert_purchase_order_lines(header, line_rows)
                    if po_file is not None:
                        file_bytes = po_file.getvalue()
                        filename = po_file.name
                        content_type = getattr(po_file, "type", None)
                        for alloy_id in alloy_ids:
                            db.save_po_document(
                                customer_po_no=po_no.strip(),
                                file_bytes=file_bytes,
                                filename=filename,
                                content_type=content_type,
                                alloy_id=alloy_id,
                            )
                    names = ", ".join(ln["alloy_label"] for ln in filled_lines)
                    st.success(
                        f"Saved purchase order **{po_no.strip()}** with "
                        f"{len(filled_lines)} alloy line(s): {names}. "
                        "Open **All Purchase Orders** to review or take further actions."
                    )
                    st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
                    st.session_state.po_line_token = int(line_token) + 1
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

