import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Production Batches")
st.caption(
    "Review existing production batches. "
    "New batches are created on **Production Batch & Chemistry**. "
    "Input is charge weight; output is entered on **Batch Output** after the heat "
    "is marked **Completed** "
    "(product alloy plus Broken Ingot / Furnace Empty / Not Ok Ingot)."
)
st.subheader("Existing batches")
batches_df = df_from_rows(db.list_batches())
if batches_df.empty:
    st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
else:
    show_dataframe(batches_df)

