import streamlit as st
import database as db
from pages_common import df_from_rows, format_ui_date, show_dataframe

st.title("Accounts Invoice Review")
st.caption(
    "Every raw material invoice logged as **Pending with accounts**. "
    "Review each one, then approve it."
)

pending = db.list_raw_material_purchases_by_status("Pending with accounts")

if not pending:
    st.info("No invoices are pending with accounts.")
else:
    summary_rows = [
        {
            "Purchase_id": r["Purchase_id"],
            "Vendor_name": r["Vendor_name"],
            "Supplier_Invoice": r["Supplier_Invoice"],
            "Supplier_invoice_date": r["Supplier_invoice_date"],
            "Received_date": r["Received_date"],
            "Last_updated_by": r["Last_updated_by"],
            "Last_updated_datetime": r["Last_updated_datetime"],
        }
        for r in pending
    ]
    show_dataframe(df_from_rows(summary_rows))

    st.markdown("#### Review and action")
    for row in pending:
        purchase_id = int(row["Purchase_id"])
        header = (
            f"#{purchase_id} — {row['Vendor_name'] or 'Unknown vendor'} — "
            f"Invoice {row['Supplier_Invoice'] or '—'} "
            f"({format_ui_date(row['Supplier_invoice_date'])})"
        )
        with st.expander(header):
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Vendor", row["Vendor_name"] or "—")
            d2.metric("Supplier invoice", row["Supplier_Invoice"] or "—")
            d3.metric("Invoice date", format_ui_date(row["Supplier_invoice_date"]) or "—")
            d4.metric("Received date", format_ui_date(row["Received_date"]) or "—")

            docs = db.get_raw_material_purchase_documents(purchase_id) or {}
            doc_col, photo1_col, photo2_col = st.columns(3)
            with doc_col:
                if docs.get("Invoice_Document"):
                    st.download_button(
                        "Download invoice document",
                        data=bytes(docs["Invoice_Document"]),
                        file_name=docs.get("Invoice_Document_name") or f"invoice_{purchase_id}.bin",
                        mime=docs.get("Invoice_Document_type") or "application/octet-stream",
                        key=f"accounts_review_doc_{purchase_id}",
                    )
                else:
                    st.caption("No invoice document uploaded.")
            with photo1_col:
                if docs.get("Vehicle_photo"):
                    st.markdown("**Vehicle photo**")
                    st.image(bytes(docs["Vehicle_photo"]))
                else:
                    st.caption("No vehicle photo uploaded.")
            with photo2_col:
                if docs.get("Weighment_slip_photo"):
                    st.markdown("**Weighment slip photo**")
                    st.image(bytes(docs["Weighment_slip_photo"]))
                else:
                    st.caption("No weighment slip photo uploaded.")

            lines = db.list_raw_material_inventory_by_purchase(purchase_id)
            if lines:
                show_dataframe(df_from_rows(lines))

                def _totals(weight_key: str) -> tuple[float, float, float]:
                    value = sum(
                        float(ln.get(weight_key) or 0) * float(ln.get("Cost_per_kg") or 0)
                        for ln in lines
                    )
                    gst = value * 0.18
                    return value, gst, value + gst

                iw_value, iw_gst, iw_total = _totals("Invoice_weight")
                rw_value, rw_gst, rw_total = _totals("Received_weight")

                st.markdown("**Based on Invoice weight**")
                iv1, iv2, iv3 = st.columns(3)
                iv1.metric("Invoice value", f"{iw_value:,.2f}")
                iv2.metric("GST value (18%)", f"{iw_gst:,.2f}")
                iv3.metric("Total value", f"{iw_total:,.2f}")

                st.markdown("**Based on Received weight**")
                rv1, rv2, rv3 = st.columns(3)
                rv1.metric("Invoice value", f"{rw_value:,.2f}")
                rv2.metric("GST value (18%)", f"{rw_gst:,.2f}")
                rv3.metric("Total value", f"{rw_total:,.2f}")
            else:
                st.info("No raw material lines on this invoice.")

            if st.button(
                "Approve",
                key=f"accounts_review_approve_{purchase_id}",
                type="primary",
            ):
                try:
                    db.set_raw_material_purchase_invoice_status(purchase_id, "Approved")
                    st.success(f"Invoice #{purchase_id} approved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not approve: {exc}")
