import streamlit as st
import database as db
from datetime import date
from pages_common import df_from_rows, format_ui_date, show_dataframe, ui_date_input


st.title("Build of Material (BOM)")
cust_opts = {
    f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"]
    for c in db.list_customer_codes()
}
materials = db.list_raw_materials()
alloys = [a["Alloy_name"] for a in db.list_alloys()]

with st.form("bom_form", clear_on_submit=True):
    b1, b2 = st.columns(2)
    with b1:
        bom_id = st.number_input("BOM ID *", min_value=1.0, value=1.0, step=1.0)
        eff = ui_date_input("Effective date", value=date.today())
        customer = st.selectbox("Customer", [""] + list(cust_opts.keys()))
        alloy_name = st.selectbox("Alloy name", [""] + alloys)
    with b2:
        rm = st.selectbox("Raw material", [""] + materials)
        qty = st.number_input("Quantity", min_value=0.0, value=1.0, step=0.1)
        seq = st.number_input("Sequence order", min_value=0.0, value=1.0, step=1.0)
        notes = st.text_input("Notes")
    if st.form_submit_button("Save BOM line", type="primary"):
        db.add_bom_line(
            bom_id=bom_id,
            effective_date=eff.isoformat(),
            cust_code=cust_opts[customer] if customer else None,
            alloy_name=alloy_name or None,
            raw_material=rm or None,
            quantity=qty,
            sequence=seq,
            notes=notes,
        )
        st.success(f"Saved BOM {bom_id} / {format_ui_date(eff)}.")

show_dataframe(
    df_from_rows(
        db.fetch_all(
            """
            SELECT b.BOMID AS "BOMID", b.Effective_date AS "Effective_date",
                   b.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name",
                   b.Alloy_Name AS "Alloy_Name",
                   b.Raw_Material_Name AS "Raw_Material_Name", b.Quantity AS "Quantity",
                   b.Sequence_Order AS "Sequence_Order", b.notes AS "notes"
            FROM Build_of_Material b
            LEFT JOIN Customer_Master c ON c.Cust_code = b.Cust_code
            ORDER BY b.BOMID, b.Sequence_Order
            """
        )
    )
)

