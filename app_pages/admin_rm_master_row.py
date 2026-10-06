import json

import pandas as pd
import streamlit as st

import database as db
from pages_common import format_ui_date, show_dataframe

st.title("Remove raw material master row")
st.caption(
    "For a duplicate or wrong row in **Raw Material Master**. Pick the raw material, "
    "compare its rows, and remove one. It runs in one step (all or nothing) and is "
    "logged with the full row and its chemistry, so nothing is lost. If another row "
    "has the same effective date (an exact copy), only the chosen row goes and the "
    "chemistry stays with the copy that remains; otherwise that effective date's "
    "chemistry rows go with it. A raw material always keeps at least one row. Stock "
    "lots, charges and purchases are not touched."
)

if not db.is_admin_user():
    st.error("Only an Admin can remove a Raw Material Master row.")
    st.stop()

flash = st.session_state.pop("rmrow_flash", None)
if flash:
    st.success(flash)

dups = db.raw_material_master_duplicate_names()
if dups:
    st.markdown(
        "**Raw materials with more than one row:** "
        + ", ".join(f"{d['Raw_Material_Name']} ({int(d['Rows'])})" for d in dups)
    )
names = [r["Raw_Material_Name"] for r in db.fetch_all(
    'SELECT DISTINCT Raw_Material_Name AS "Raw_Material_Name" FROM Raw_Material_Master '
    "ORDER BY Raw_Material_Name"
)]
default = names.index(dups[0]["Raw_Material_Name"]) if dups else None
name = st.selectbox(
    "Raw material", names, index=default, key="rmrow_name", placeholder="Choose raw material"
)

if name:
    rows = db.raw_material_master_rows(name)
    view = pd.DataFrame(
        [
            {
                "Row": i + 1,
                "Effective date": r["Effective_date"],
                "Status": r["Status"],
                "Recovery %": r["Recovery"],
                "Vendor code": r["Vendor_code"],
                "ISRI code": r["ISRI_CODE"],
                "Availability": r["Availability_class"],
                "Photo": r["Photo"] or "",
                "Chemistry rows": r["Spec_rows"],
                "Rows with this date": r["Same_date_rows"],
                "Last updated by": r["Last_updated_by"],
                "Last updated": r["Last_updated_datetime"],
            }
            for i, r in enumerate(rows)
        ]
    )
    show_dataframe(view)
    if len(rows) < 2:
        st.info(f"{name} has only one row; there is nothing to remove.")
    else:
        pick = st.selectbox(
            "Row to remove *",
            list(range(len(rows))),
            index=None,
            format_func=lambda i: (
                f"Row {i + 1}: effective {rows[i]['Effective_date']}, "
                f"{rows[i]['Status']}, recovery {rows[i]['Recovery']}, "
                f"updated by {rows[i]['Last_updated_by'] or '—'}"
            ),
            key=f"rmrow_pick_{name}",
            placeholder="Choose the row to remove",
        )
        reason = st.text_input(
            "Reason *", key=f"rmrow_reason_{name}", placeholder="e.g. Duplicate of row 1"
        )
        if pick is not None:
            r = rows[pick]
            if r["Same_date_rows"] > 1:
                st.caption(
                    "This row is an exact copy (same effective date); its chemistry "
                    "stays with the row that remains."
                )
            elif r["Spec_rows"]:
                st.warning(
                    f"This row's {r['Spec_rows']} chemistry row(s) for effective date "
                    f"{r['Effective_date']} are removed with it (kept in the log)."
                )
        sure = st.checkbox(
            "Remove this row from Raw Material Master", key=f"rmrow_sure_{name}"
        )
        if st.button(
            "Remove row",
            type="primary",
            key=f"rmrow_go_{name}",
            disabled=pick is None or not reason.strip() or not sure,
        ):
            try:
                res = db.remove_raw_material_master_row(
                    name, rows[pick]["Row_ref"], reason=reason
                )
            except Exception as exc:
                st.error(str(exc))
            else:
                for k in (f"rmrow_pick_{name}", f"rmrow_reason_{name}", f"rmrow_sure_{name}"):
                    st.session_state.pop(k, None)
                st.session_state["rmrow_flash"] = (
                    f"Removed a {name} row (effective {res['effective_date']}). "
                    + (
                        "It was an exact copy; chemistry kept. "
                        if res["exact_copy"]
                        else f"{res['spec_rows_removed']} chemistry row(s) removed with it. "
                    )
                    + f"{res['rows_left']} row(s) left."
                )
                st.cache_data.clear()
                st.rerun()

st.divider()
st.subheader("Rows removed")
done = db.list_raw_material_master_removals()
if not done:
    st.caption("None yet.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "When": format_ui_date(d.get("Deleted_datetime"), with_time=True),
                    "By": d.get("Deleted_by"),
                    "Raw material": d["Raw_Material_Name"],
                    "Effective date": d.get("Effective_date"),
                    "Exact copy": d.get("Exact_copy"),
                    "Chemistry rows": len(json.loads(d.get("Spec_rows") or "[]")),
                    "Reason": d.get("Reason") or "",
                }
                for d in done
            ]
        )
    )
