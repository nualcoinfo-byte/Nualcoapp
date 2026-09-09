import streamlit as st
import database as db


st.title("Crucible Master")
st.caption(
    "Add or delete crucibles for a furnace, and set each crucible to "
    "**Available** or **Damaged**. A furnace can have only one "
    "**Available** crucible at a time."
)
furnaces = db.list_furnaces()
vendors = db.list_suppliers()
if not furnaces:
    st.warning("Add at least one furnace under **Furnaces** before managing crucibles.")
if not vendors:
    st.warning("Add at least one vendor under **Vendors** if you want to record the supplier.")

furnace = st.selectbox(
    "Furnace *",
    furnaces,
    key="crucible_furnace",
    disabled=not furnaces,
    help="All add, status, and delete actions apply to this furnace.",
)

available = db.get_available_crucible(furnace) if furnace else None
if available:
    st.info(
        f"Available on furnace **{furnace}**: **{available['Crucible_no']}**. "
        "Mark it Damaged before another crucible can be Available."
    )
elif furnace:
    st.info(f"No Available crucible on furnace **{furnace}** yet.")

st.markdown("#### Add crucible")
add_default = "Damaged" if available else "Available"
with st.form("crucible_add_form", clear_on_submit=True):
    a1, a2, a3 = st.columns(3)
    with a1:
        cno = st.text_input("Crucible no *", placeholder="e.g. C-01")
    with a2:
        vendor_name = st.selectbox("Vendor name", [""] + vendors)
    with a3:
        cstatus = st.selectbox(
            "Crucible status",
            db.CRUCIBLE_STATUS,
            index=db.CRUCIBLE_STATUS.index(add_default),
        )
    if st.form_submit_button("Add crucible", type="primary"):
        if not furnace:
            st.error("Select a furnace first.")
        elif not cno.strip():
            st.error("Crucible no is required.")
        else:
            try:
                db.upsert_crucible(
                    cno.strip(),
                    furnace,
                    cstatus,
                    vendor_name or None,
                )
                st.success(f"Added crucible **{cno.strip()}** to furnace **{furnace}**.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not add crucible: {exc}")

st.markdown(f"#### Crucibles on furnace {furnace or '—'}")
rows = db.list_crucibles(furnace=furnace) if furnace else []
if not rows:
    st.info("No crucibles for this furnace yet.")
else:
    h1, h2, h3, h4, h5 = st.columns([2, 3, 2, 1.4, 1.2])
    h1.caption("Crucible no")
    h2.caption("Vendor")
    h3.caption("Status")
    h4.caption("Update")
    h5.caption("Delete")
    for row in rows:
        cno_val = str(row["Crucible_no"])
        current = str(row["Crucible_status"] or db.CRUCIBLE_STATUS[0])
        if current not in db.CRUCIBLE_STATUS:
            current = db.CRUCIBLE_STATUS[0]
        r1, r2, r3, r4, r5 = st.columns([2, 3, 2, 1.4, 1.2])
        r1.markdown(f"**{cno_val}**")
        r2.markdown(row["Vendor_name"] or "—")
        new_status = r3.selectbox(
            "Status",
            db.CRUCIBLE_STATUS,
            index=db.CRUCIBLE_STATUS.index(current),
            key=f"cstat_{furnace}_{cno_val}",
            label_visibility="collapsed",
        )
        if r4.button("Update", key=f"cupd_{furnace}_{cno_val}", use_container_width=True):
            try:
                db.update_crucible_status(cno_val, new_status)
                st.success(f"Updated **{cno_val}** to **{new_status}**.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not update status: {exc}")
        if r5.button("Delete", key=f"cdel_{furnace}_{cno_val}", use_container_width=True):
            try:
                db.delete_crucible(cno_val)
                st.success(f"Deleted crucible **{cno_val}**.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not delete crucible: {exc}")

