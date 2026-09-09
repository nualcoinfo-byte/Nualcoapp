import streamlit as st
import database as db
from datetime import date, datetime, timedelta
from pages_common import df_from_rows, format_ui_date, show_dataframe, ui_date_input


st.title("Daily Batch Summary")
st.caption(
    "One row per batch on the selected date — Batch ID, alloy, total input, "
    "total output, and both statuses — so Input and Output can be reviewed "
    "and marked **Completed** promptly. Aim to clear the previous day's "
    "batches before noon."
)

default_day = (
    date.today() - timedelta(days=1) if datetime.now().hour < 12 else date.today()
)
summary_date = ui_date_input("Date", value=default_day, key="dbs_date")

rows = db.list_batches(production_date=summary_date)
if not rows:
    st.info(f"No batches on {format_ui_date(summary_date)}.")
else:
    pending_input = [
        r for r in rows if r.get("Production_status") != db.BATCH_STATUS_COMPLETED
    ]
    pending_output = [
        r for r in rows if r.get("Output_status") != db.BATCH_STATUS_COMPLETED
    ]

    c1, c2, c3 = st.columns(3)
    c1.metric("Batches", len(rows))
    c2.metric("Input pending", len(pending_input))
    c3.metric("Output pending", len(pending_output))

    table_rows = [
        {
            "Batch_ID": r["Batch_ID"],
            "Shift": r.get("Shift") or "—",
            "Melt": r.get("Melt_No") or "—",
            "Alloy": r.get("Alloy_name") or "—",
            "Total input (kg)": float(r.get("Input_Weight") or 0),
            "Total output (kg)": float(r.get("Output_Weight") or 0),
            "Input status": r.get("Production_status") or "—",
            "Output status": r.get("Output_status") or "—",
        }
        for r in rows
    ]
    show_dataframe(df_from_rows(table_rows))

    if not pending_input and not pending_output:
        st.success("Every batch on this date is Completed for both input and output.")
    else:
        st.subheader("Needs action")
        for r in rows:
            needs_input = r.get("Production_status") != db.BATCH_STATUS_COMPLETED
            needs_output = (
                not needs_input
                and r.get("Output_status") != db.BATCH_STATUS_COMPLETED
            )
            if not (needs_input or needs_output):
                continue
            rc1, rc2 = st.columns([3, 1])
            rc1.markdown(
                f"**{r['Batch_ID']}** — Shift {r.get('Shift') or '—'}, "
                f"Melt {r.get('Melt_No') or '—'} — {r.get('Alloy_name') or '—'} "
                f"(Furnace {r.get('Furnace') or '—'}) — "
                f"{'Input' if needs_input else 'Output'} pending"
            )
            with rc2:
                if needs_input:
                    if st.button(
                        "Complete input", key=f"dbs_input_{r['Batch_ID']}"
                    ):
                        st.session_state["batch_working_furnace"] = r.get("Furnace")
                        st.session_state["_pb_select_batch"] = r["Batch_ID"]
                        st.session_state.nav_page = "Production Batch & Chemistry"
                        st.rerun()
                else:
                    if st.button(
                        "Complete output", key=f"dbs_output_{r['Batch_ID']}"
                    ):
                        st.session_state["bo_furnace"] = (
                            r.get("Furnace") or "All furnaces"
                        )
                        st.session_state["bo_batch"] = (
                            f"{r['Batch_ID']}  |  Shift {r.get('Shift') or '—'}  |  "
                            f"Melt {r.get('Melt_No') or '—'}  |  "
                            f"{r.get('Alloy_name') or '—'}  |  "
                            f"in={float(r.get('Input_Weight') or 0):.0f} kg  |  "
                            f"out={float(r.get('Output_Weight') or 0):.0f} kg"
                        )
                        st.session_state.nav_page = "Batch Output"
                        st.rerun()
