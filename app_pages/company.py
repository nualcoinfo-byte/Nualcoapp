import streamlit as st
import database as db
from datetime import date
from pages_common import df_from_rows, show_dataframe


st.title("Company")
st.caption(
    "Nualco is the **issuer** on packing lists, invoices, and test certificates. "
    "These details stay in **Company_profile**, not Customer Master."
)
try:
    profile = db.get_company_profile()
except Exception as exc:
    st.error(str(exc))
    profile = dict(db.DEFAULT_COMPANY_PROFILE)

def _co_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return date(2017, 11, 8)

states = db.list_states()
saved_state = str(profile.get("State") or "")
state_options = [""] + states
if saved_state and saved_state not in state_options:
    state_options.append(saved_state)

c1, c2 = st.columns(2)
with c1:
    company_name = st.text_input(
        "Company name *",
        value=str(profile.get("Company_name") or ""),
        key="co_name",
    )
    contact_person = st.text_input(
        "Contact person",
        value=str(profile.get("Contact_person") or ""),
        key="co_contact",
    )
    phone1 = st.text_input(
        "Contact no 1",
        value=str(profile.get("Phone1") or ""),
        key="co_phone1",
    )
    phone2 = st.text_input(
        "Contact no 2",
        value=str(profile.get("Phone2") or ""),
        key="co_phone2",
    )
    email1 = st.text_input(
        "E-mail 1",
        value=str(profile.get("Email1") or ""),
        key="co_email1",
    )
    email2 = st.text_input(
        "E-mail 2",
        value=str(profile.get("Email2") or ""),
        key="co_email2",
    )
    pan = st.text_input("PAN", value=str(profile.get("PAN") or ""), key="co_pan")
    gst = st.text_input("GST", value=str(profile.get("GST") or ""), key="co_gst")
    cin = st.text_input(
        "CIN (Corporate Identity Number)",
        value=str(profile.get("CIN") or ""),
        key="co_cin",
    )
    msme = st.text_input(
        "MSME UAM",
        value=str(profile.get("MSME_UAM") or ""),
        key="co_msme",
    )
    hsn = st.text_input(
        "HSN code",
        value=str(profile.get("HSN_code") or ""),
        key="co_hsn",
    )
with c2:
    address = st.text_input(
        "Address",
        value=str(profile.get("Address") or ""),
        key="co_address",
    )
    state = st.selectbox(
        "State",
        options=state_options,
        index=state_options.index(saved_state) if saved_state in state_options else 0,
        key="co_state",
    )
    cities = db.list_cities(state) if state else []
    saved_city = str(profile.get("City") or "")
    city_options = [""] + cities
    if saved_city and saved_city not in city_options:
        city_options.append(saved_city)
    city = st.selectbox(
        "City",
        options=city_options,
        index=city_options.index(saved_city) if saved_city in city_options else 0,
        key="co_city",
        disabled=not bool(state),
    )
    pincode = st.text_input(
        "Pincode",
        value=str(profile.get("Pincode") or ""),
        key="co_pincode",
    )
    country = st.text_input(
        "Country",
        value=str(profile.get("Country") or "India"),
        key="co_country",
    )
    incorporation = st.date_input(
        "Date of incorporation",
        value=_co_date(profile.get("Incorporation_date")),
        key="co_inc",
    )
    iec = st.text_input(
        "IEC code",
        value=str(profile.get("IEC_code") or ""),
        key="co_iec",
    )
    bank_name = st.text_input(
        "Bank",
        value=str(profile.get("Bank_name") or ""),
        key="co_bank",
    )
    branch = st.text_input(
        "Branch",
        value=str(profile.get("Branch") or ""),
        key="co_branch",
    )
    bank_account = st.text_input(
        "Account number",
        value=str(profile.get("Bank_account") or ""),
        key="co_account",
    )
    ifsc = st.text_input(
        "IFSC",
        value=str(profile.get("IFSC_code") or ""),
        key="co_ifsc",
    )

if st.button("Save company details", type="primary", key="co_save"):
    try:
        saved = db.save_company_profile(
            {
                "Company_name": company_name,
                "Address": address,
                "City": city,
                "State": state,
                "Pincode": pincode,
                "Country": country,
                "Contact_person": contact_person,
                "Phone1": phone1,
                "Phone2": phone2,
                "Email1": email1,
                "Email2": email2,
                "PAN": pan,
                "GST": gst,
                "CIN": cin,
                "MSME_UAM": msme,
                "HSN_code": hsn,
                "Incorporation_date": incorporation.isoformat()
                if incorporation
                else None,
                "IEC_code": iec,
                "Bank_name": bank_name,
                "Branch": branch,
                "Bank_account": bank_account,
                "IFSC_code": ifsc,
            }
        )
        st.success(f"Saved **{saved.get('Company_name')}**.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

show_dataframe(df_from_rows([db.get_company_profile()]))

