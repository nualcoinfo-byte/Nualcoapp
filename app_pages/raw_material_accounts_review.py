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
            lines = db.list_raw_material_inventory_by_purchase(purchase_id)
            if lines:
                show_dataframe(df_from_rows(lines))
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
