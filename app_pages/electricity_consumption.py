import streamlit as st
import database as db
from datetime import date
import pandas as pd
from pages_common import df_from_rows, empty_percent_input, format_ui_date, show_dataframe, ui_date_input


st.title("Electricity Consumption")
st.caption(
    "Select **EB Line 1** or **EB Line 2**, then enter that line's opening and "
    "closing power readings. Units consumed = closing reading − opening reading. "
    "Each line's opening reading is filled from that line's previous closing reading. "
    "Saving the same date and line updates that row."
)

line = st.radio(
    "Electricity line *",
    db.ELECTRICITY_LINES,
    horizontal=True,
    key="elec_line",
    help="The plant has two EB connections. Readings are stored separately for each line.",
)

month_tot = db.electricity_month_totals(date.today().year, date.today().month)
by_line = month_tot.get("by_line") or {}
latest = db.list_electricity_consumption(limit=1, line=line)
last_close = latest[0]["Closing_reading"] if latest else None
m1, m2, m3, m4 = st.columns(4)
m1.metric(
    f"{db.ELECTRICITY_LINE_1} this month",
    f"{float(by_line.get(db.ELECTRICITY_LINE_1) or 0):,.1f}",
)
m2.metric(
    f"{db.ELECTRICITY_LINE_2} this month",
    f"{float(by_line.get(db.ELECTRICITY_LINE_2) or 0):,.1f}",
)
m3.metric("Both lines this month", f"{month_tot['consumed']:,.1f}")
m4.metric(
    f"Last closing ({line})",
    f"{float(last_close):,.1f}" if last_close is not None else "—",
)

cons_date = ui_date_input(
    "Consumption date *", value=date.today(), key="elec_date"
)
day = cons_date.isoformat()
existing = db.get_electricity_consumption(day, line)
prev_close = db.get_previous_electricity_closing(day, line)
loaded_key = f"{day}|{line}"
if st.session_state.get("elec_loaded_key") != loaded_key:
    st.session_state["elec_loaded_key"] = loaded_key
    if existing:
        st.session_state["elec_open"] = float(existing["Opening_reading"])
        st.session_state["elec_close"] = float(existing["Closing_reading"])
        st.session_state["elec_notes"] = existing.get("Notes") or ""
    else:
        st.session_state["elec_open"] = (
            float(prev_close) if prev_close is not None else None
        )
        st.session_state["elec_close"] = None
        st.session_state["elec_notes"] = ""

r1, r2 = st.columns(2)
with r1:
    opening = empty_percent_input(
        "Opening power reading *",
        key="elec_open",
        max_value=None,
        step=0.1,
        format="%.1f",
        help=(
            f"Filled from {line} previous closing ({float(prev_close):,.1f})."
            if prev_close is not None and not existing
            else f"Meter reading for {line} at the start of the day."
        ),
    )
with r2:
    closing = empty_percent_input(
        "Closing power reading *",
        key="elec_close",
        max_value=None,
        step=0.1,
        format="%.1f",
        help=f"Meter reading for {line} at the end of the day.",
    )
notes = st.text_input("Notes", key="elec_notes")

try:
    open_val = float(opening) if opening is not None else None
    close_val = float(closing) if closing is not None else None
except (TypeError, ValueError):
    open_val = close_val = None
units = (
    close_val - open_val
    if open_val is not None and close_val is not None
    else None
)
u1, u2, u3 = st.columns(3)
u1.metric("Opening", f"{open_val:,.1f}" if open_val is not None else "—")
u2.metric("Closing", f"{close_val:,.1f}" if close_val is not None else "—")
if units is None:
    u3.metric("Units consumed", "—")
elif units < 0:
    u3.metric("Units consumed", f"{units:,.1f}")
    st.error("Closing reading must be greater than or equal to the opening reading.")
else:
    u3.metric("Units consumed", f"{units:,.1f}")

if st.button("Save electricity reading", type="primary", key="elec_save"):
    if open_val is None:
        st.error("Enter the opening power reading.")
    elif close_val is None:
        st.error("Enter the closing power reading.")
    else:
        try:
            saved_units = db.add_electricity_consumption(
                consumption_date=day,
                opening_reading=open_val,
                closing_reading=close_val,
                notes=notes.strip() or None,
                line=line,
            )
            st.success(
                f"Saved **{saved_units:,.1f} units** on **{line}** "
                f"for {format_ui_date(cons_date)} "
                f"(opening {open_val:,.1f} → closing {close_val:,.1f})."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

st.subheader("Daily readings")
rows = df_from_rows(db.list_electricity_consumption())
if rows.empty:
    st.info("No electricity readings yet. Select a line and save the day's readings.")
else:
    for col in ("Opening_reading", "Closing_reading", "Units_consumed"):
        if col in rows.columns:
            rows[col] = pd.to_numeric(rows[col], errors="coerce").round(1)
    show_dataframe(rows)

