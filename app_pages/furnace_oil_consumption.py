import streamlit as st
import database as db
from datetime import date
from pages_common import (
    df_from_rows,
    empty_percent_input,
    format_ui_date,
    show_dataframe,
    tank_reading_rows,
    ui_date_input,
)


# Clearing a form after a save has to happen before its widgets are created: Streamlit
# refuses to change a widget's value later in the same run.
if st.session_state.pop("fo_cons_reset", False):
    for _key in list(st.session_state):
        if _key == "fo_cons_notes" or _key.startswith(
            ("foc_tank_type_", "foc_tank_start_", "foc_tank_end_")
        ):
            del st.session_state[_key]
    st.session_state["foc_tank_rows"] = 1
if st.session_state.pop("fo_open_reset", False):
    st.session_state.pop("fo_open_qty", None)

st.title("Furnace Oil Consumption")
st.caption(
    "Production team: enter each tank's dip reading at the start and at the end of the day. "
    "The quantity consumed is calculated from those readings; it cannot be typed in. "
    "Saving the same date updates that day's row. "
    "Inventory is rebuilt from purchases minus consumption."
)
for _flash_key in ("fo_open_saved_message", "fo_cons_saved_message"):
    _flash = st.session_state.pop(_flash_key, None)
    if _flash:
        st.success(_flash)

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
                st.session_state["fo_open_saved_message"] = (
                    f"Opening stock set to **{float(open_qty):,.1f} L** "
                    f"on {format_ui_date(open_date)}."
                )
                st.session_state["fo_open_reset"] = True
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
    qty_slot = st.empty()  # filled in below, once the tank readings are known
st.markdown("##### Tank readings")
st.caption(
    "Enter the dip reading before and after use (10 KL tank in cm, service oil tank in inch). "
    "The furnace draws mainly from the service oil tank, so it is selected first. "
    "Litres consumed = litres at the starting reading minus litres at the ending reading, "
    "from that tank's measurement chart."
)
tank_inputs, tank_fill = tank_reading_rows(
    "foc_tank",
    default_tank=db.FURNACE_OIL_CONSUMPTION_DEFAULT_TANK,
    consumption=True,
)
tank_ok_rows = [r for r in tank_fill["rows"] if r["status"] == "ok"]
qty_slot.text_input(
    "Quantity consumed (litres)",
    value=f"{tank_fill['total']:,.1f}" if tank_ok_rows else "",
    placeholder="Calculated from the tank readings",
    disabled=True,
    help="Total litres consumed from the tanks below. It is calculated, not typed in.",
)
cons_notes = st.text_input("Notes", key="fo_cons_notes")

if st.button("Save consumption", type="primary", key="fo_cons_save"):
    problems = [r["error"] for r in tank_fill["rows"] if r["status"] != "ok"]
    if problems:
        st.error("Complete or correct the tank readings before saving: " + " ".join(problems))
    elif not tank_ok_rows:
        st.error("Enter the tank readings; the quantity consumed is calculated from them.")
    else:
        try:
            qty_val = db.add_furnace_oil_consumption(
                consumption_date=cons_date.isoformat(),
                tank_readings=tank_inputs,
                notes=cons_notes.strip() or None,
            )
            # Shown after the rerun below; the form is cleared at the top of the next run.
            st.session_state["fo_cons_saved_message"] = (
                f"Saved **{qty_val:,.1f} L** for {format_ui_date(cons_date)}. "
                f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
            )
            st.session_state["fo_cons_reset"] = True
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
