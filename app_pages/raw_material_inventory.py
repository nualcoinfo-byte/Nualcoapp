import streamlit as st
import database as db
import pandas as pd
from pages_common import df_from_rows, show_dataframe


st.title("Raw Material Inventory")
st.caption(
    "Review lots and remaining stock. Invoice documents live on "
    "**Raw Material Purchase**; grade chemistry comes from **Raw Material Spec**. "
    "New receipts are entered on **Raw Material Logging**. "
    "Broken Ingot, Furnace Empty, and Not Ok Ingot from **Batch Output** "
    "are stored as remelt lots linked to the source heat (`Source_Batch_ID`)."
)

recent = df_from_rows(
    db.fetch_all(
        """
        SELECT i.Lot_id AS "Lot_id", i.Purchase_id AS "Purchase_id",
               i.Raw_Material_Name AS "Raw_Material_Name",
               i.Source_Batch_ID AS "Source_Batch_ID",
               oa.Alloy_name AS "Origin_Alloy_name",
               v.Vendor_name AS "Vendor_name",
               p.Supplier_Invoice AS "Supplier_Invoice",
               p.Supplier_invoice_date AS "Supplier_invoice_date",
               COALESCE(p.Received_date, b.Production_Date) AS "Received_date",
               i.Received_weight AS "Received_weight",
               i.Remaining_Weight AS "Remaining_Weight",
               i.Cost_per_kg AS "Cost_per_kg",
               i.Storage_bay AS "Storage_bay",
               i.Raw_Material_Status AS "Raw_Material_Status",
               p.Invoice_Document_name AS "Invoice_Document_name",
               CASE WHEN p.Vehicle_photo IS NULL THEN NULL ELSE 'Yes' END
                   AS "Vehicle_photo",
               CASE WHEN p.Weighment_slip_photo IS NULL THEN NULL ELSE 'Yes' END
                   AS "Weighment_slip_photo"
        FROM Raw_Material_Inventory i
        LEFT JOIN Raw_Material_Purchase p ON p.Purchase_id = i.Purchase_id
        LEFT JOIN Vendor_Master v ON v.Vendor_code = p.Vendor_code
        LEFT JOIN Production_batch b ON b.Batch_ID = i.Source_Batch_ID
        LEFT JOIN Alloy_Master oa ON oa.Alloy_id = b.Alloy_id
        ORDER BY i.Lot_id DESC
        LIMIT 50
        """
    )
)
st.subheader("Recent inventory lots")
if recent.empty:
    st.info("No lots logged yet.")
else:
    show_dataframe(recent)

lot_pick = st.number_input("View chemistry for Lot ID", min_value=0, step=1, value=0)
if lot_pick > 0:
    chem = db.get_lot_chemistry(int(lot_pick))
    if chem:
        st.caption("Specification from **Raw Material Spec** for this lot's grade.")
        show_dataframe(
            pd.DataFrame([{"Element": k, "Percentage": v} for k, v in chem.items()]),
        )
    else:
        st.warning("No specification recorded for this lot's raw material grade.")
    vehicle_photo = db.get_inventory_vehicle_photo(int(lot_pick))
    weighment_slip_photo = db.get_inventory_weighment_slip_photo(int(lot_pick))
    if vehicle_photo or weighment_slip_photo:
        img1, img2 = st.columns(2)
        with img1:
            if vehicle_photo:
                st.markdown("**Vehicle photo**")
                st.image(vehicle_photo)
        with img2:
            if weighment_slip_photo:
                st.markdown("**Weighment slip photo**")
                st.image(weighment_slip_photo)

