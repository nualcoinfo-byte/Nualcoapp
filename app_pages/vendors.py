import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Vendor Master")
st.caption("Vendor code is auto-generated serially when a new vendor is created.")
states = db.list_states()
if not states:
    st.warning("No states found. Load **State_City_Master** before saving vendors.")

c1, c2 = st.columns(2)
with c1:
    name = st.text_input("Vendor name *", key="vend_name")
    gst = st.text_input("GST", key="vend_gst")
    pan = st.text_input("PAN", key="vend_pan")
    status = st.selectbox("Status", db.ACTIVE_STATUS, key="vend_status")
with c2:
    address = st.text_input("Address", key="vend_address")
    state = st.selectbox(
        "State *",
        options=[""] + states,
        key="vend_state_sel",
    )
    cities = db.list_cities(state) if state else []
    city = st.selectbox(
        "City *",
        options=[""] + cities,
        key="vend_city_sel",
        disabled=not bool(state),
    )
    pincode = st.text_input("Pincode", key="vend_pincode")
    country = st.text_input("Country", value="India", key="vend_country")

st.markdown("#### Contacts")
k1, k2 = st.columns(2)
with k1:
    contact1 = st.text_input("Contact person 1", key="vend_contact1")
    phone1 = st.text_input("Phone 1", key="vend_phone1")
    email = st.text_input("Email", key="vend_email")
with k2:
    contact2 = st.text_input("Contact person 2", key="vend_contact2")
    phone2 = st.text_input("Phone 2", key="vend_phone2")
    website = st.text_input("Website", key="vend_website")

st.markdown("#### Commercial & bank details")
b1, b2 = st.columns(2)
with b1:
    credit_period = st.number_input(
        "Credit period (days)", min_value=0, value=0, step=1, key="vend_credit"
    )
    bank_account = st.text_input("Bank account no.", key="vend_bank_account")
    bank_name = st.text_input("Bank name", key="vend_bank_name")
with b2:
    branch = st.text_input("Branch", key="vend_branch")
    ifsc = st.text_input("IFSC code", key="vend_ifsc")

if st.button("Save vendor", type="primary", key="vend_save"):
    if not name.strip():
        st.error("Vendor name is required.")
    elif not state or not city:
        st.error("State and City must be selected from the master list.")
    else:
        db.upsert_supplier(
            {
                "Vendor_name": name.strip(),
                "GST": gst,
                "PAN": pan,
                "Address": address,
                "City": city,
                "State": state,
                "Pincode": pincode,
                "Country": country,
                "Contact1": contact1.strip(),
                "Phone1": phone1.strip(),
                "Contact2": contact2.strip(),
                "Phone2": phone2.strip(),
                "Email": email.strip(),
                "Website": website.strip(),
                "Credit_period": int(credit_period),
                "Bank_account": bank_account.strip(),
                "Branch": branch.strip(),
                "IFSC_code": ifsc.strip().upper(),
                "Bank_name": bank_name.strip(),
                "Status": status,
            }
        )
        st.success(f"Saved vendor **{name.strip()}**.")

show_dataframe(df_from_rows(db.get_all_records("Vendor_Master", order_by="Vendor_code")))

