import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Finished Goods Inventory")
st.caption(
    "Product-alloy output posts here as **Available** once you click "
    "**Mark Output as Completed** on **Batch Output** — draft output does not "
    "appear here. Dispatch stock by entering a **Packing List**. "
    "Avg piece is output weight ÷ pieces; typical product alloy pieces are "
    f"**{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** and show in red "
    "if outside that range so you can check the piece count."
)
try:
    db.backfill_finished_goods_from_output()
except Exception as exc:
    st.warning(f"Could not refresh finished goods from batch output: {exc}")

all_fg = db.list_finished_goods()
available_n = sum(
    1 for r in all_fg if r.get("Finished_Goods_Status") == db.FG_STATUS_AVAILABLE
)
dispatched_n = sum(
    1 for r in all_fg if r.get("Finished_Goods_Status") == db.FG_STATUS_DISPATCHED
)
m1, m2, m3 = st.columns(3)
m1.metric("Bundles", len(all_fg))
m2.metric("Available", available_n)
m3.metric("Dispatched", dispatched_n)
if all_fg:
    show_dataframe(df_from_rows(all_fg), highlight_avg_piece=True)
else:
    st.info(
        "No finished goods yet. Mark a heat **Completed**, save product-alloy "
        "output on **Batch Output**, then click **Mark Output as Completed**."
    )

