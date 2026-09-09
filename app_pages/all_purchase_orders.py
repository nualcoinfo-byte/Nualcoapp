import streamlit as st
import database as db
import pandas as pd
from pages_common import format_ui_date, show_dataframe


st.title("All Purchase Orders")
st.caption(
    "Summary of every customer PO line (one row per PO number and alloy). "
    "Filter, review details, change status, and attach or download documents. "
    "Create new POs under **Purchase Orders**."
)

pos = db.list_purchase_orders()
if not pos:
    st.info("No purchase orders yet. Create one under **Purchase Orders**.")
else:
    open_n = sum(1 for p in pos if (p.get("Purchase_Order_Status") or "Open") == "Open")
    closed_n = sum(1 for p in pos if p.get("Purchase_Order_Status") == "Closed")
    cancel_n = sum(1 for p in pos if p.get("Purchase_Order_Status") == "Cancelled")
    with_doc = sum(1 for p in pos if int(p.get("Has_Document") or 0) == 1)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total POs", len(pos))
    c2.metric("Open", open_n)
    c3.metric("Closed", closed_n)
    c4.metric("Cancelled", cancel_n)
    c5.metric("With document", with_doc)

    f1, f2, f3 = st.columns([1.2, 1.5, 1.5])
    with f1:
        status_filter = st.selectbox(
            "Status",
            options=["All"] + db.PURCHASE_ORDER_STATUS,
            key="apo_status_filter",
        )
    with f2:
        def _po_cust_label(p: dict) -> str:
            code = p.get("Cust_code") or ""
            name = p.get("Customer_name") or ""
            if code and name:
                return f"{code} — {name}"
            return name or code or "—"

        customers_in_pos = sorted({_po_cust_label(p) for p in pos})
        cust_filter = st.selectbox(
            "Customer",
            options=["All"] + customers_in_pos,
            key="apo_cust_filter",
        )
    with f3:
        search = st.text_input(
            "Search PO / alloy",
            placeholder="PO number or alloy…",
            key="apo_search",
        ).strip().lower()

    filtered = []
    for p in pos:
        st_val = p.get("Purchase_Order_Status") or "Open"
        if status_filter != "All" and st_val != status_filter:
            continue
        if cust_filter != "All" and _po_cust_label(p) != cust_filter:
            continue
        if search:
            blob = " ".join(
                str(p.get(k) or "")
                for k in (
                    "Customer_PO_No",
                    "Customer_name",
                    "Cust_code",
                    "Alloy_name",
                    "Alloy_Id",
                )
            ).lower()
            if search not in blob:
                continue
        filtered.append(p)

    summary_rows = [
        {
            "PO No": p["Customer_PO_No"],
            "Customer": p.get("Customer_name") or p.get("Cust_code") or "—",
            "Alloy": p.get("Alloy_name")
            or (f"#{p['Alloy_Id']}" if p.get("Alloy_Id") else "—"),
            "Order date": format_ui_date(p.get("Order_Date"), empty="—"),
            "Delivery": format_ui_date(p.get("Delivery_Date"), empty="—"),
            "Qty": float(p["Order_Qty"] or 0),
            "Rate": float(p["Rate"] or 0) if p.get("Rate") is not None else None,
            "Status": p.get("Purchase_Order_Status") or "Open",
            "Document": "Yes" if int(p.get("Has_Document") or 0) == 1 else "No",
        }
        for p in filtered
    ]
    st.subheader(f"Purchase orders ({len(summary_rows)})")
    if not summary_rows:
        st.warning("No purchase orders match the current filters.")
    else:
        show_dataframe(pd.DataFrame(summary_rows))

    st.divider()
    st.markdown("#### Actions on a purchase order")
    action_opts = {
        f"{p['Customer_PO_No']}"
        + (f" — {p['Customer_name']}" if p.get("Customer_name") else "")
        + " / "
        + (
            p.get("Alloy_name")
            or (f"#{p['Alloy_Id']}" if p.get("Alloy_Id") else "no alloy")
        )
        + f" · {p.get('Purchase_Order_Status') or 'Open'}": (
            p["Customer_PO_No"],
            p.get("Alloy_Id"),
        )
        for p in filtered or pos
    }
    pick = st.selectbox(
        "Select purchase order line",
        options=list(action_opts.keys()),
        key="apo_action_pick",
    )
    po_no_sel, alloy_id_sel = action_opts[pick]
    selected = next(
        p
        for p in pos
        if p["Customer_PO_No"] == po_no_sel and p.get("Alloy_Id") == alloy_id_sel
    )

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("PO No", selected["Customer_PO_No"])
    d2.metric("Customer", selected.get("Customer_name") or "—")
    d3.metric(
        "Alloy",
        selected.get("Alloy_name")
        or (f"#{selected['Alloy_Id']}" if selected.get("Alloy_Id") else "—"),
    )
    d4.metric("Qty", f"{float(selected.get('Order_Qty') or 0):,.0f}")

    with st.expander("Order details", expanded=False):
        st.write(
            {
                "Order date": format_ui_date(selected.get("Order_Date"), empty="—"),
                "Delivery date": format_ui_date(selected.get("Delivery_Date"), empty="—"),
                "Rate": selected.get("Rate"),
                "Status": selected.get("Purchase_Order_Status") or "Open",
                "Billing": ", ".join(
                    x
                    for x in [
                        selected.get("Billing_Address"),
                        selected.get("Billing_City"),
                        selected.get("Billing_state"),
                        selected.get("Billing_Pincode"),
                        selected.get("Billing_country"),
                    ]
                    if x
                )
                or "—",
                "Shipping": ", ".join(
                    x
                    for x in [
                        selected.get("Shipping_address"),
                        selected.get("Shipping_City"),
                        selected.get("Shipping_state"),
                        selected.get("Shipping_Pincode"),
                        selected.get("Shipping_country"),
                    ]
                    if x
                )
                or "—",
                "Document": selected.get("PO_Document_name") or "None",
            }
        )

    st.markdown("##### Update status")
    cur_status = selected.get("Purchase_Order_Status") or "Open"
    try:
        cur_idx = db.PURCHASE_ORDER_STATUS.index(cur_status)
    except ValueError:
        cur_idx = 0
    new_status = st.selectbox(
        "Purchase order status",
        options=db.PURCHASE_ORDER_STATUS,
        index=cur_idx,
        key="apo_status_edit",
    )
    if st.button("Save status", type="primary", key="apo_save_status"):
        try:
            db.update_purchase_order_status(po_no_sel, new_status, alloy_id_sel)
            st.success(
                f"PO **{po_no_sel}** / alloy **{selected.get('Alloy_name') or alloy_id_sel}** "
                f"set to **{new_status}**."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    st.markdown("##### Document")
    uploaded = st.file_uploader(
        "Upload / replace PO document",
        type=["pdf", "doc", "docx", "xls", "xlsx"],
        key="apo_attach",
    )
    u1, u2, _ = st.columns([1, 1, 3])
    if u1.button("Save document", type="primary", disabled=uploaded is None, key="apo_save_doc"):
        try:
            db.save_po_document(
                customer_po_no=po_no_sel,
                file_bytes=uploaded.getvalue(),
                filename=uploaded.name,
                content_type=getattr(uploaded, "type", None),
                alloy_id=alloy_id_sel,
            )
            st.success(f"Saved **{uploaded.name}** on PO {po_no_sel}.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    doc = db.get_po_document(po_no_sel, alloy_id_sel)
    if doc and doc.get("PO_Document"):
        st.download_button(
            "Download attached document",
            data=bytes(doc["PO_Document"]),
            file_name=doc.get("PO_Document_name") or f"{po_no_sel}.bin",
            mime=doc.get("PO_Document_type") or "application/octet-stream",
            key="apo_download",
        )
        if u2.button("Remove document", key="apo_remove_doc"):
            db.clear_po_document(po_no_sel, alloy_id_sel)
            st.success("Document removed.")
            st.rerun()
    else:
        st.caption("No document attached to this PO yet.")

