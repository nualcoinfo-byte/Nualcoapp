import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Trolley Master")
with st.form("trolley_form", clear_on_submit=True):
    t1, t2 = st.columns(2)
    with t1:
        tname = st.text_input("Trolley name *", placeholder="e.g. Trolley-01")
        colour = st.text_input("Colour", placeholder="e.g. Red")
    with t2:
        weight = st.number_input("Weight (kg)", min_value=0.0, value=0.0, step=0.1)
        tstatus = st.selectbox("Status", db.ACTIVE_STATUS)
    if st.form_submit_button("Save trolley", type="primary"):
        if not tname.strip():
            st.error("Trolley name is required.")
        else:
            db.upsert_trolley(
                tname.strip(),
                colour.strip() or None,
                weight if weight > 0 else None,
                tstatus,
            )
            st.success(f"Saved trolley **{tname.strip()}**.")

show_dataframe(df_from_rows(db.get_all_records("Trolley_Master", order_by="Trolley_name")))

