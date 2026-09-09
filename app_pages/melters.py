import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Melter Master")
with st.form("melter_form", clear_on_submit=True):
    mname = st.text_input("Melter name *", placeholder="e.g. Sachin")
    mstatus = st.selectbox("Status", db.ACTIVE_STATUS)
    if st.form_submit_button("Save melter", type="primary"):
        if not mname.strip():
            st.error("Melter name is required.")
        else:
            db.upsert_melter(mname.strip(), mstatus)
            st.success(f"Saved melter **{mname.strip()}**.")

show_dataframe(df_from_rows(db.get_all_records("Melter_Master", order_by="Melter_Name")))

