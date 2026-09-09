import streamlit as st
import database as db
from datetime import date
from pages_common import df_from_rows, empty_percent_input, format_ui_date, show_dataframe, ui_date_input


st.title("Furnace Oil Consumption")
st.caption(
    "Production team: enter the overall furnace oil used for the day. "
    "Saving the same date updates that day's row. "
    "Inventory is rebuilt from purchases minus consumption."
)

stock = db.get_furnace_oil_stock()
month_tot = db.furnace_oil_month_totals(date.today().year, date.today().month)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Current stock (L)", f"{stock:,.1f}")
m2.metric("Purchased this month (L)", f"{month_tot['purchased']:,.1f}")
m3.metric("Consumed this month (L)", f"{month_tot['consumed']:,.1f}")
m4.metric("Unit", "Litre")

with st.expander("Set opening stock", expanded=stock <= 0 and not db.list_furnace_oil_inventory(limit=1)):
    st.caption(
        "Use this once to load the tank balance before the first purchase. "
        "It is stored as an **Opening** receipt, not a vendor invoice."
    )
    o1, o2 = st.columns(2)
    with o1:
        open_date = ui_date_input(
            "Opening date", value=date.today(), key="fo_open_date"
        )
    with o2:
        open_qty = empty_percent_input(
            "Opening stock (litres) *",
            key="fo_open_qty",
            max_value=None,
            step=1.0,
        )
    if st.button("Save opening stock", key="fo_open_save"):
        if float(open_qty or 0) <= 0:
            st.error("Opening stock (litres) must be greater than zero.")
        else:
            try:
                db.add_furnace_oil_purchase(
                    vendor_code=None,
                    invoice="OPENING",
                    invoice_date=open_date.isoformat(),
                    received_date=open_date.isoformat(),
                    quantity=float(open_qty),
                    purchase_type="Opening",
                    notes="Opening stock",
                )
                st.success(
                    f"Opening stock set to **{float(open_qty):,.1f} L** "
                    f"on {format_ui_date(open_date)}."
                )
                st.session_state["fo_open_qty"] = None
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save opening stock: {exc}")

st.markdown("#### Daily consumption")
c1, c2 = st.columns(2)
with c1:
    cons_date = ui_date_input(
        "Consumption date *", value=date.today(), key="fo_cons_date"
    )
with c2:
    cons_qty = empty_percent_input(
        "Quantity consumed (litres) *",
        key="fo_cons_qty",
        max_value=None,
        step=1.0,
    )
cons_notes = st.text_input("Notes", key="fo_cons_notes")

if st.button("Save consumption", type="primary", key="fo_cons_save"):
    qty_val = float(cons_qty or 0)
    if qty_val <= 0:
        st.error("Quantity consumed (litres) must be greater than zero.")
    else:
        try:
            db.add_furnace_oil_consumption(
                consumption_date=cons_date.isoformat(),
                quantity=qty_val,
                notes=cons_notes.strip() or None,
            )
            st.success(
                f"Saved **{qty_val:,.1f} L** for {format_ui_date(cons_date)}. "
                f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
            )
            st.session_state["fo_cons_qty"] = None
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

st.subheader("Inventory ledger")
ledger = df_from_rows(db.list_furnace_oil_inventory())
if ledger.empty:
    st.info("No furnace oil inventory yet. Add an opening stock or a purchase.")
else:
    show_dataframe(ledger)

st.subheader("Recent consumption")
used = df_from_rows(db.list_furnace_oil_consumption())
if used.empty:
    st.info("No consumption entries yet.")
else:
    show_dataframe(used)

