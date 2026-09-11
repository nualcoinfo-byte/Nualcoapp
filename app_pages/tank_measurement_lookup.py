import streamlit as st
import database as db
from pages_common import df_from_rows, empty_percent_input, show_dataframe

SERVICE_TANK_CAPACITY_L = 1500.0
TEN_KL_TANK_CAPACITY_L = 10000.0

st.title("Tank Depth Lookup")
st.caption(
    "Convert a dipstick depth reading into litres using the calibrated dip charts. "
    "Values between calibration points are linearly interpolated."
)

tab_service, tab_ten_kl = st.tabs(
    ["Service Oil Tank (1,500 L)", "10 KL Tank (10,000 L)"]
)

with tab_service:
    chart = db.list_service_oil_tank_measurement()
    if not chart:
        st.info("No dip chart loaded for the Service Oil Tank yet.")
    else:
        min_d, max_d = chart[0]["Inch"], chart[-1]["Inch"]
        st.caption(f"Chart covers {min_d:g}″ to {max_d:g}″.")
        inch = empty_percent_input(
            "Depth (inches) *",
            key="tank_lookup_inch",
            min_value=0.0,
            max_value=None,
            step=0.5,
            format="%.1f",
        )
        if inch:
            litres = db.service_oil_tank_litres(float(inch))
            if litres is None:
                st.error(
                    f"{float(inch):g}″ is outside the chart's range "
                    f"({min_d:g}″–{max_d:g}″)."
                )
            else:
                pct = min(litres / SERVICE_TANK_CAPACITY_L * 100, 100)
                c1, c2 = st.columns(2)
                c1.metric("Litres", f"{litres:,.1f} L")
                c2.metric("Tank full", f"{pct:,.1f}%")
        with st.expander("View full dip chart"):
            show_dataframe(df_from_rows(chart))

with tab_ten_kl:
    chart = db.list_ten_kl_tank_measurement()
    if not chart:
        st.info("No dip chart loaded for the 10 KL Tank yet.")
    else:
        min_d, max_d = chart[0]["Centimeter"], chart[-1]["Centimeter"]
        st.caption(f"Chart covers {min_d:g} cm to {max_d:g} cm.")
        cm = empty_percent_input(
            "Depth (centimeters) *",
            key="tank_lookup_cm",
            min_value=0.0,
            max_value=None,
            step=0.5,
            format="%.1f",
        )
        if cm:
            litres = db.ten_kl_tank_litres(float(cm))
            if litres is None:
                st.error(
                    f"{float(cm):g} cm is outside the chart's range "
                    f"({min_d:g}–{max_d:g} cm)."
                )
            else:
                pct = min(litres / TEN_KL_TANK_CAPACITY_L * 100, 100)
                c1, c2 = st.columns(2)
                c1.metric("Litres", f"{litres:,.1f} L")
                c2.metric("Tank full", f"{pct:,.1f}%")
        with st.expander("View full dip chart"):
            show_dataframe(df_from_rows(chart))
