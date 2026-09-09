import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


if not db.is_admin_user():
    st.error("This page is available only to Admin users.")
    st.stop()
st.title("Cancel issued certificate")
st.caption(
    "Issue is the final dispatch step. Use this page only to reverse a "
    "shipment that should not have been issued. Cancelling voids the "
    "certificate, sets the packing list back to **In-Progress**, and "
    "returns packed kg and pieces to finished-goods inventory."
)
try:
    issued_rows = db.list_issued_certificates()
except Exception as exc:
    st.error(str(exc))
    issued_rows = []
if not issued_rows:
    st.info("There are no issued test certificates to cancel.")
    st.stop()

show_dataframe(
    df_from_rows(
        [
            {
                "Packing_list_id": row.get("Packing_list_id"),
                "Certificate_no": row.get("Certificate_no"),
                "Issued_date": row.get("Issued_date"),
                "Invoice": row.get("Invoice_number"),
                "Customer": row.get("Customer_name"),
                "Alloy": row.get("Alloy_name"),
                "Packed kg": row.get("Source_weight"),
                "Packed pieces": row.get("Source_pieces"),
                "Issued by": row.get("Last_updated_by"),
            }
            for row in issued_rows
        ]
    )
)
opts = {
    (
        f"{row.get('Certificate_no') or '—'}  |  "
        f"PL #{row.get('Packing_list_id')}  |  "
        f"{row.get('Customer_name') or '—'}  |  "
        f"{row.get('Invoice_number') or '—'}"
    ): int(row["Packing_list_id"])
    for row in issued_rows
}
pick = st.selectbox(
    "Issued certificate",
    options=list(opts.keys()),
    key="admin_cancel_issued_pick",
)
packing_list_id = opts[pick]
chosen = next(
    (row for row in issued_rows if int(row["Packing_list_id"]) == packing_list_id),
    None,
)
if chosen:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Certificate", chosen.get("Certificate_no") or "—")
    c2.metric("Packed kg", f"{float(chosen.get('Source_weight') or 0):,.2f}")
    c3.metric("Packed pieces", f"{int(float(chosen.get('Source_pieces') or 0)):,}")
    c4.metric("Customer", chosen.get("Customer_name") or "—")
    header = db.get_packing_list(packing_list_id)
    if header:
        st.markdown("##### Packed batches to return")
        show_dataframe(
            df_from_rows(
                [
                    {
                        "Batch_ID": row.get("Batch_ID"),
                        "Heat_no": row.get("Heat_no"),
                        "Weight": row.get("Weight"),
                        "Pieces": row.get("Pieces"),
                    }
                    for row in (header.get("batches") or [])
                ]
            )
        )
confirm = st.checkbox(
    "I confirm this issued certificate should be cancelled and packed "
    "quantity returned to finished goods.",
    key="admin_cancel_issued_confirm",
)
if st.button(
    "Cancel issued certificate",
    type="primary",
    key="admin_cancel_issued_go",
    disabled=not confirm,
):
    try:
        voided = db.cancel_issued_test_certificate(packing_list_id)
        st.success(
            f"Cancelled **{voided.get('Certificate_no')}**. "
            "The packing list is In-Progress and packed quantity is back "
            "in finished goods."
        )
        st.rerun()
    except Exception as exc:
        st.error(str(exc))
