import streamlit as st
import database as db
from datetime import date
import pandas as pd
from pages_common import df_from_rows, show_dataframe


st.title("Cost of Conversion")
st.caption(
    "Enter the finalized conversion rates for one calendar month. "
    "Saving the same month updates that row. "
    "**Total conversion rate per kg** is stored as the sum of the six rates "
    "and is added to every batch output's overall ₹/kg when outputs are saved "
    "(production month if that month is on file, otherwise the previous available month)."
)

today = date.today()
ycol, mcol = st.columns(2)
with ycol:
    year = st.selectbox(
        "Expense year *",
        options=list(range(today.year - 5, today.year + 2)),
        index=5,
        key="coc_year",
    )
with mcol:
    month = st.selectbox(
        "Expense month *",
        options=list(range(1, 13)),
        format_func=lambda m: date(2000, m, 1).strftime("%B"),
        index=today.month - 1,
        key="coc_month",
    )
expense = date(int(year), int(month), 1)
existing = db.get_cost_of_conversion(expense)
loaded_key = expense.isoformat()
if st.session_state.get("coc_loaded_key") != loaded_key:
    st.session_state["coc_loaded_key"] = loaded_key
    for field, _label in db.CONVERSION_RATE_FIELDS:
        st.session_state[f"coc_{field}"] = (
            float(existing[field]) if existing else 0.0
        )

st.markdown(f"#### Rates for **{expense.strftime('%B %Y')}**")
rate_values: dict[str, float] = {}
cols = st.columns(3)
for idx, (field, label) in enumerate(db.CONVERSION_RATE_FIELDS):
    with cols[idx % 3]:
        rate_values[field] = st.number_input(
            f"{label} *",
            min_value=0.0,
            step=0.0001,
            format="%.4f",
            key=f"coc_{field}",
        )
total = round(sum(float(v or 0) for v in rate_values.values()), 4)
t1, t2 = st.columns(2)
t1.metric("Total conversion rate (₹/kg)", f"{total:,.4f}")
t2.caption(
    "Oil + electricity + labour + salaries + consumables + overheads. "
    "This total is stored on save for pricing queries."
)

if st.button("Save conversion rates", type="primary", key="coc_save"):
    try:
        saved_total = db.save_cost_of_conversion(
            expense_month=expense,
            **rate_values,
        )
        st.success(
            f"Saved conversion rates for **{expense.strftime('%B %Y')}**. "
            f"Total **{saved_total:,.4f}** per kg."
        )
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

st.subheader("Saved months")
rows = df_from_rows(db.list_cost_of_conversion())
if rows.empty:
    st.info("No conversion rates yet. Enter the six rates for a month and save.")
else:
    rate_cols = [field for field, _label in db.CONVERSION_RATE_FIELDS] + [
        "total_conversion_rate_per_kg"
    ]
    for col in rate_cols:
        if col in rows.columns:
            rows[col] = pd.to_numeric(rows[col], errors="coerce").round(4)
    show_dataframe(rows)

