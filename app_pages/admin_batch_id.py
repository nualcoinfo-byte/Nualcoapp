import json

import pandas as pd
import streamlit as st

import database as db
from pages_common import format_ui_date, show_dataframe

st.title("Correct batch ID")
st.caption(
    "For a batch saved under the wrong shift or melt no. The Batch ID is built from "
    "production date + furnace + shift + melt, so it changes with them. Pick the "
    "batch and its correct shift and melt: the page shows every table that holds the "
    "old Batch ID and how many rows would move, and anything that blocks the change. "
    "**Change Batch ID** then moves all of it to the new ID in one step (all or "
    "nothing) and logs who did it and why. Heat no, weights, costs, finished goods, "
    "packing lists and certificates are otherwise unchanged."
)

if not db.is_admin_user():
    st.error("Only an Admin can change a Batch ID.")
    st.stop()

flash = st.session_state.pop("abid_flash", None)
if flash:
    st.success(flash)

token = int(st.session_state.get("abid_token", 0))
old_id = st.text_input(
    "Batch ID to correct *", key=f"abid_old_{token}", placeholder="e.g. 0510262A3"
).strip()
if not old_id:
    st.info("Enter the Batch ID that was saved under the wrong shift or melt.")
else:
    batch = db.get_batch(old_id)
    if not batch:
        st.error(f"Batch {old_id} was not found.")
    else:
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Production date", format_ui_date(batch.get("Production_Date")) or "—")
        m2.metric("Furnace", batch.get("Furnace") or "—")
        m3.metric("Shift", batch.get("Shift") or "—")
        m4.metric("Melt no", batch.get("Melt_No") or "—")
        m5.metric("Heat no", batch.get("Heat_no") or "—")
        m6.metric(
            "Status",
            f"{batch.get('Production_status') or '—'} / {batch.get('Output_status') or '—'}",
        )
        c1, c2 = st.columns(2)
        with c1:
            new_shift = st.selectbox(
                "Correct shift *",
                db.SHIFTS,
                index=db.SHIFTS.index(str(batch.get("Shift") or "A").upper())
                if str(batch.get("Shift") or "").upper() in db.SHIFTS
                else 0,
                key=f"abid_shift_{old_id}",
            )
        with c2:
            new_melt = st.number_input(
                "Correct melt no *",
                min_value=1,
                max_value=99,
                step=1,
                value=int(batch.get("Melt_No") or 1),
                key=f"abid_melt_{old_id}",
            )
        try:
            preview = db.batch_id_change_preview(old_id, new_shift, new_melt)
        except ValueError as exc:
            st.error(str(exc))
            preview = None
        if preview:
            st.markdown(
                f"#### {preview['old_batch_id']} → **{preview['new_batch_id']}**"
            )
            for msg in preview["blockers"]:
                st.error(msg)
            impact = pd.DataFrame(preview["impact"])
            moving = impact[impact["Rows"] > 0] if not impact.empty else impact
            st.markdown("**Rows that will move to the new Batch ID**")
            if moving.empty:
                st.caption("No other table holds this Batch ID; only the batch itself changes.")
            else:
                show_dataframe(moving.reset_index(drop=True))
            with st.expander(f"All {len(impact)} tables checked"):
                show_dataframe(impact)
            reason = st.text_input(
                "Reason *",
                key=f"abid_reason_{old_id}",
                placeholder="e.g. Entered as shift A melt 3; it was shift B melt 1",
            )
            sure = st.checkbox(
                f"Change {preview['old_batch_id']} to {preview['new_batch_id']} everywhere",
                key=f"abid_sure_{old_id}",
            )
            if st.button(
                "Change Batch ID",
                type="primary",
                key=f"abid_go_{old_id}",
                disabled=bool(preview["blockers"]) or not reason.strip() or not sure,
            ):
                try:
                    result = db.rename_batch_id(old_id, new_shift, new_melt, reason=reason)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    total = sum(result["moved"].values())
                    st.session_state["abid_flash"] = (
                        f"Batch {result['old']} is now **{result['new']}** "
                        f"({total} row{'s' if total != 1 else ''} in "
                        f"{len(result['moved'])} table{'s' if len(result['moved']) != 1 else ''} moved)."
                    )
                    st.session_state["abid_token"] = token + 1  # clear the form
                    st.rerun()

st.divider()
st.subheader("Batch ID changes")
changes = db.list_batch_id_changes()
if not changes:
    st.caption("No Batch ID has been changed yet.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "When": format_ui_date(c.get("Changed_datetime"), with_time=True),
                    "By": c.get("Changed_by"),
                    "Old Batch ID": c["Old_batch_id"],
                    "New Batch ID": c["New_batch_id"],
                    "Shift": f"{c.get('Old_shift') or '—'} → {c.get('New_shift') or '—'}",
                    "Melt": f"{c.get('Old_melt_no') or '—'} → {c.get('New_melt_no') or '—'}",
                    "Rows moved": ", ".join(
                        f"{k}: {v}" for k, v in (json.loads(c.get("Rows_moved") or "{}")).items()
                    ),
                    "Reason": c.get("Reason") or "",
                }
                for c in changes
            ]
        )
    )
