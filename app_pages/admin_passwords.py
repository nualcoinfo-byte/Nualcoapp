import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


if not db.is_admin_user():
    st.error("This page is available only to Admin users.")
    st.stop()
st.title("Employee passwords")
st.caption(
    "Set or reset a password for each employee. Employees sign in with "
    "their employee ID. Passwords are stored hashed, not in plain text."
)
employees = db.list_employees(include_inactive=True)
if not employees:
    st.warning("No employees found.")
    st.stop()
show_dataframe(
    df_from_rows(
        [
            {
                "Employee ID": row.get("employee_id"),
                "Name": db.employee_display_name(row),
                "Email": row.get("email"),
                "Role ID": row.get("role_id"),
                "Role": row.get("role_name"),
                "Status": row.get("status"),
                "Password": "Set" if row.get("has_password") else "Not set",
                "Password updated": row.get("password_updated_at"),
            }
            for row in employees
        ]
    )
)
labels = {
    f"{row.get('employee_id')}  ·  {db.employee_display_name(row)}  ·  "
    f"{row.get('role_name') or '—'}  ·  "
    f"{'password set' if row.get('has_password') else 'no password'}": str(
        row.get("employee_id")
    )
    for row in employees
}
pick = st.selectbox("Employee", list(labels.keys()), key="pwd_employee_pick")
employee_id = labels[pick]
with st.form("set_employee_password"):
    password = st.text_input(
        "New password",
        type="password",
        help=f"At least {db.MIN_PASSWORD_LENGTH} characters.",
    )
    confirm = st.text_input("Confirm password", type="password")
    submitted = st.form_submit_button("Set / reset password", type="primary")
if submitted:
    if password != confirm:
        st.error("Passwords do not match.")
    else:
        try:
            db.set_employee_password(employee_id, password)
            st.success(f"Password saved for **{employee_id}**.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
