import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Customer Master")
states = db.list_states()
if not states:
    st.warning("No states found. Load **State_City_Master** before saving customers.")

c1, c2 = st.columns(2)
with c1:
    code = st.text_input("Customer code (PK) *", placeholder="e.g. CUST_0026", key="cust_code")
    name = st.text_input("Customer name *", key="cust_name")
    gst = st.text_input("GST", key="cust_gst")
    pan = st.text_input("PAN", key="cust_pan")
    contact1 = st.text_input("Contact 1 name", key="cust_contact1")
    phone1 = st.text_input("Phone 1", key="cust_phone1")
    contact2 = st.text_input("Contact 2 name", key="cust_contact2")
    phone2 = st.text_input("Phone 2", key="cust_phone2")
    email = st.text_input("Email", key="cust_email")
    website = st.text_input("Website", key="cust_website")
    status = st.selectbox("Status", db.ACTIVE_STATUS, key="cust_status")
with c2:
    address = st.text_input("Address", key="cust_address")
    state = st.selectbox(
        "State *",
        options=[""] + states,
        key="cust_state_sel",
    )
    cities = db.list_cities(state) if state else []
    city = st.selectbox(
        "City *",
        options=[""] + cities,
        key="cust_city_sel",
        disabled=not bool(state),
    )
    pincode = st.text_input("Pincode", key="cust_pincode")
    country = st.text_input("Country", value="India", key="cust_country")
    bank_account = st.text_input("Bank account", key="cust_bank_account")
    ifsc_code = st.text_input("IFSC code", key="cust_ifsc")
    bank_name = st.text_input("Bank name", key="cust_bank_name")
    branch_category = st.text_input("Branch", key="cust_branch")

if st.button("Save customer", type="primary", key="cust_save"):
    if not code.strip() or not name.strip():
        st.error("Customer code and name are required.")
    elif not state or not city:
        st.error("State and City must be selected from the master list.")
    else:
        db.upsert_customer(
            {
                "Cust_code": code.strip(),
                "Customer_name": name.strip(),
                "GST": gst,
                "PAN": pan,
                "Address": address,
                "City": city,
                "State": state,
                "Pincode": pincode,
                "Country": country,
                "Contact1_name": contact1,
                "Phone1": phone1,
                "Contact_name2": contact2,
                "Phone2": phone2,
                "Email": email,
                "Website": website,
                "Bank_account": bank_account,
                "IFSC_code": ifsc_code,
                "Bank_name": bank_name,
                "Branch_category": branch_category,
                "Status": status,
            }
        )
        st.success(f"Saved customer **{name.strip()}**.")

show_dataframe(df_from_rows(db.get_all_records("Customer_Master", order_by="Cust_code")))

