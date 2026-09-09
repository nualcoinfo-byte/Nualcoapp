import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


if not db.is_admin_user():
    st.error("This page is available only to Admin users.")
    st.stop()
st.title("Roles & permissions")
st.caption(
    "Admin is the super user and always has every section. "
    "Other roles follow the navigation you save here."
)
roles = db.list_roles()
granted = db.list_role_permissions()
if not roles:
    st.warning("No roles in the database yet.")
else:
    rows = []
    for role in roles:
        rid = int(role["role_id"])
        keys = granted.get(rid) or list(
            db.default_sections_for_role(rid, role.get("role_name"))
        )
        rows.append(
            {
                "Role ID": rid,
                "Role": role.get("role_name"),
                "Sections": ", ".join(
                    db.NAV_SECTION_LABELS[k]
                    for k in db.ALL_NAV_SECTION_KEYS
                    if k in keys
                ),
            }
        )
    show_dataframe(df_from_rows(rows))

    st.subheader("Edit access")
    for role in roles:
        rid = int(role["role_id"])
        rname = str(role.get("role_name") or "")
        locked = db.role_is_admin(rname, rid)
        current = set(granted.get(rid) or db.default_sections_for_role(rid, rname))
        with st.expander(f"{rname}  (Role ID {rid})", expanded=False):
            if locked:
                st.caption("Admin always has full access.")
            chosen: list[str] = []
            cols = st.columns(2)
            for i, (key, label) in enumerate(db.NAV_SECTION_DEFS):
                with cols[i % 2]:
                    checked = st.checkbox(
                        label,
                        value=True if locked else key in current,
                        disabled=locked,
                        key=f"role_perm_{rid}_{key}",
                    )
                if locked or checked:
                    chosen.append(key)
            if st.button("Save permissions", key=f"save_role_perm_{rid}", disabled=locked):
                try:
                    db.save_role_sections(rid, chosen)
                    st.success(f"Saved permissions for {rname}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

st.subheader("Add role")
with st.form("add_role_form"):
    new_name = st.text_input("Role name")
    new_keys: list[str] = []
    cols = st.columns(2)
    for i, (key, label) in enumerate(db.NAV_SECTION_DEFS):
        with cols[i % 2]:
            if st.checkbox(label, value=key == "overview", key=f"new_role_{key}"):
                new_keys.append(key)
    add_clicked = st.form_submit_button("Add role")
if add_clicked:
    try:
        db.add_role(new_name, new_keys)
        st.success(f"Added role **{new_name}**.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

if st.button("Reset all roles to company defaults"):
    try:
        db.reset_role_permissions_to_defaults()
        st.success("Role permissions restored to the company defaults.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))
