import streamlit as st
import database as db
from pages_common import df_from_rows, show_dataframe


st.title("Masters Overview")
tab1, tab2, tab3, tab4 = st.tabs(
    ["Elements", "Raw material master", "Raw material specs", "Schema info"]
)
with tab1:
    show_dataframe(df_from_rows(db.list_elements()))
with tab2:
    show_dataframe(df_from_rows(db.list_raw_material_master()))
with tab3:
    st.caption(
        "Each specification row belongs to a **Raw Material Master** grade "
        "(name + effective date)."
    )
    show_dataframe(
        df_from_rows(
            db.fetch_all(
                """
                SELECT s.Raw_Material_Name AS "Raw_Material_Name",
                       s.Effective_date AS "Effective_date",
                       s.Element_symbol AS "Element_symbol",
                       s.Percentage AS "Percentage"
                FROM Raw_Material_Spec s
                LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
                WHERE LOWER(s.Element_symbol) NOT IN ('oe', 'ot', 'sf')
                ORDER BY s.Raw_Material_Name,
                         s.Effective_date DESC,
                         COALESCE(_el.Serial_no, 9999),
                         s.Element_symbol
                LIMIT 200
                """
            )
        )
    )
with tab4:
    st.markdown(
        """
        **Tables created automatically**

        | # | Table | Purpose |
        |---|-------|---------|
        | 1 | Customer_Master | Customers |
        | 2 | Vendor_Master | Vendors (auto-serial Vendor_code PK) |
        | 3 | Element_Master | 36 chemistry elements (seeded) |
        | 4 | Raw_Material_Master | Material grades |
        | 5 | Raw_Material_Spec | Grade chemistry (child of Raw_Material_Master) |
        | 6 | Raw_Material_Purchase | Vendor invoices / receipts (document, vehicle photo, weighment slip photo) |
        | 7 | Raw_Material_Inventory | Lots / remaining stock (child of purchase) |
        | 8 | Alloy_Master | Alloys |
        | 9 | Alloy_Master_spec | Alloy min/max % |
        | 10 | Furnace_Master | Furnaces (1–4 seeded) |
        | 11 | Crucible_Master | Crucibles (Crucible_no PK; furnace and Vendor_name FKs) |
        | 12 | Melter_Master | Melter operators |
        | 13 | Trolley_Master | Trolleys (name, colour, weight) |
        | 14 | State_City_Master | States and cities |
        | 15 | Production_batch | Melts / heats |
        | 16 | batch_input | Charge sheets |
        | 17 | Batch_Chemical_Composition | Ladle chemistry |
        | 18 | Build_of_Material | BOM |
        | 19 | Purchase_Order | Customer POs + attached PO document (PDF/Word/Excel); key is Customer_PO_No + Alloy_Id |
        | 20 | ISRI_CODE_TABLE | ISRI scrap specification codes |
        | 21 | Finished_Goods_Inventory | Product-alloy output from batch_output (Available → Dispatched via Packing List) |
        | 22 | Furnace_Oil_Purchase | Furnace oil receipts and opening stock |
        | 23 | Furnace_Oil_Consumption | Daily furnace oil use (one row per day) |
        | 24 | Furnace_Oil_Inventory | Daily opening / purchase / consumption / closing ledger |
        | 25 | Electricity_Consumption | Daily opening/closing power readings per EB Line 1 / EB Line 2 |
        | 26 | Cost_of_conversion | Monthly conversion rates per kg (oil, electricity, labour, salaries, consumables, overheads) |
        | 27 | Packing_list | Dispatch header (invoice, PO, customer, alloy, vehicle; status In-Progress / Verified) |
        | 28 | Packing_list_batch | Batch IDs on a packing list; Verified lists subtract packed kg/pieces from FG |
        | 29 | Packing_list_certificate | Test-certificate header (Draft / Issued / Void); 1:1 with a Verified packing list |
        | 30 | Packing_list_certificate_line | Printed TC lines (may merge heats; weight may round up ≤ 0.15%) |
        | 31 | Packing_list_certificate_source | Maps each printed TC line back to packing_list_batch |
        | 32 | Packing_list_visual_inspection | OK / NOT OK + Verified checks required before generating a test certificate |
        | 33 | Company_profile | Our company (issuer) — legal, contact, GST/CIN/MSME, and bank details |

        Extra production columns: sample fields, `Production_supervisor`.
        """
    )
