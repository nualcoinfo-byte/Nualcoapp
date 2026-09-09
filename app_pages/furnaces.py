import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Furnace Master")
with st.form("furn_form", clear_on_submit=True):
    fname = st.text_input("Furnace ID *", placeholder="e.g. 1, 2, 3, 4")
    fstatus = st.selectbox("Status", db.ACTIVE_STATUS)
    if st.form_submit_button("Save furnace", type="primary"):
        if not fname.strip():
            st.error("Furnace ID is required.")
        else:
            db.upsert_furnace(fname.strip(), fstatus)
            st.success(f"Saved furnace **{fname.strip()}**.")

show_dataframe(df_from_rows(db.get_all_records("Furnace_Master", order_by="Furnace")))

