import streamlit as st
import database as db
from datetime import date
from pages_common import empty_percent_input, photo_bytes, ui_date_input


@st.cache_data(ttl=60, show_spinner=False)
def _cached_vendors() -> list[dict]:
    """Vendor dropdown options, cached so picking a vendor doesn't re-query
    on every rerun. A new vendor added on the Vendors page appears here
    within the TTL; cleared immediately on a successful save below."""
    return db.list_vendors()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_raw_materials() -> list[str]:
    return db.list_raw_materials(active_only=False)


st.title("Raw Material Logging")
st.caption(
    "Start with the vendor and invoice, then add every raw material on that invoice. "
    "The invoice is stored once; each material becomes a stock lot. "
    "Define grades under **Raw Material Master**. "
    "Browse lots under **Raw Material Inventory**."
)

vendors = _cached_vendors()
vendor_opts = {f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors}
existing_materials = _cached_raw_materials()
if not vendors:
    st.warning("Add at least one vendor under **Vendors** before logging material.")
if not existing_materials:
    st.info("No grades in Raw Material Master yet — you can type a new name on each row.")

st.markdown("#### Vendor invoice")
vendor_label = st.selectbox(
    "Vendor name *",
    options=[""] + list(vendor_opts.keys()),
    key="rm_log_vendor",
)
inv1, inv2, inv3 = st.columns(3)
with inv1:
    invoice_date = ui_date_input(
        "Supplier invoice date *", value=date.today(), key="rm_log_invoice_date"
    )
with inv2:
    invoice = st.text_input(
        "Vendor invoice *",
        placeholder="e.g. INV-2026-001",
        key="rm_log_invoice",
    )
with inv3:
    received = ui_date_input(
        "Received date", value=date.today(), key="rm_log_received"
    )

rec1, rec2, rec3 = st.columns(3)
with rec1:
    storage = st.text_input(
        "Storage bay", placeholder="e.g. Bay-A1", key="rm_log_storage"
    )
with rec2:
    inv_status = st.selectbox(
        "Inventory status", db.INVENTORY_STATUS, index=1, key="rm_log_inv_status"
    )
with rec3:
    invoice_doc = st.file_uploader(
        "Invoice document",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
        help="Optional. Stored once on the vendor invoice, not on each lot.",
        key="rm_log_invoice_doc",
    )

vp_open_key = "rm_log_vphoto_open"
ws_open_key = "rm_log_wslip_open"
photo_col, slip_col = st.columns(2)
with photo_col:
    if st.button(
        "📷 Vehicle photo",
        key="rm_log_vphoto_btn",
        help="Photo of the delivery vehicle. Saved once on this invoice.",
        use_container_width=True,
    ):
        st.session_state[vp_open_key] = not bool(st.session_state.get(vp_open_key))
        st.rerun()
    vehicle_photo_bytes: bytes | None = None
    if st.session_state.get(vp_open_key):
        st.caption("Capture the vehicle with camera or pick a photo from the gallery.")
        vp_cam = st.camera_input(
            "Vehicle camera",
            key="rm_log_vphoto_cam",
            help="Uses the phone camera when available.",
        )
        vp_file = st.file_uploader(
            "Vehicle gallery / files",
            type=["png", "jpg", "jpeg", "webp"],
            key="rm_log_vphoto_file",
            help="Choose an existing vehicle photo from the device gallery.",
        )
        vehicle_photo_bytes = photo_bytes(vp_cam) or photo_bytes(vp_file)
        if vehicle_photo_bytes:
            st.session_state["rm_log_vphoto_bytes"] = vehicle_photo_bytes
            st.success("Vehicle photo ready to save with this invoice.")
    else:
        vehicle_photo_bytes = st.session_state.get("rm_log_vphoto_bytes")
        if vehicle_photo_bytes:
            st.caption("Vehicle photo attached.")
with slip_col:
    if st.button(
        "📷 Weighment slip photo",
        key="rm_log_wslip_btn",
        help="Photo of the weighment slip. Saved once on this invoice.",
        use_container_width=True,
    ):
        st.session_state[ws_open_key] = not bool(st.session_state.get(ws_open_key))
        st.rerun()
    weighment_slip_photo_bytes: bytes | None = None
    if st.session_state.get(ws_open_key):
        st.caption(
            "Capture the weighment slip with camera or pick a photo from the gallery."
        )
        ws_cam = st.camera_input(
            "Weighment slip camera",
            key="rm_log_wslip_cam",
            help="Uses the phone camera when available.",
        )
        ws_file = st.file_uploader(
            "Weighment slip gallery / files",
            type=["png", "jpg", "jpeg", "webp"],
            key="rm_log_wslip_file",
            help="Choose an existing weighment slip photo from the device gallery.",
        )
        weighment_slip_photo_bytes = photo_bytes(ws_cam) or photo_bytes(ws_file)
        if weighment_slip_photo_bytes:
            st.session_state["rm_log_wslip_bytes"] = weighment_slip_photo_bytes
            st.success("Weighment slip photo ready to save with this invoice.")
    else:
        weighment_slip_photo_bytes = st.session_state.get("rm_log_wslip_bytes")
        if weighment_slip_photo_bytes:
            st.caption("Weighment slip photo attached.")

st.markdown("#### Raw materials on this invoice")
st.caption(
    "One vendor invoice can include multiple raw materials. "
    "Add a row for each: name, cost per kg, and received weight."
)

if "rm_invoice_lines" not in st.session_state:
    st.session_state.rm_invoice_lines = [{"name": "", "cost": 0.0, "weight": 0.0}]
if "rm_log_token" not in st.session_state:
    st.session_state.rm_log_token = 0
line_token = st.session_state.rm_log_token

WEIGHT_TOLERANCE_PCT = 0.12  # max allowed variance between invoice and weighbridge weight

collected_lines: list[dict] = []
for idx, _line in enumerate(st.session_state.rm_invoice_lines):
    st.markdown(f"**Row {idx + 1}**")
    n1, n2, n3, n4 = st.columns([2.0, 1.0, 1.0, 1.0])
    with n1:
        if existing_materials:
            name = st.selectbox(
                "Raw material name *",
                options=existing_materials,
                index=None,
                accept_new_options=True,
                placeholder="Select or type a name",
                key=f"rm_line_name_{line_token}_{idx}",
            )
        else:
            name = st.text_input(
                "Raw material name *",
                placeholder="e.g. Tense, UBC",
                key=f"rm_line_name_{line_token}_{idx}",
            )
    with n2:
        cost = empty_percent_input(
            "Cost per kg",
            key=f"rm_line_cost_{line_token}_{idx}",
            max_value=None,
            step=0.01,
        )
    with n3:
        weight = empty_percent_input(
            "Received weight (kg) *",
            key=f"rm_line_weight_{line_token}_{idx}",
            max_value=None,
            step=1.0,
        )
    with n4:
        wslip_weight = empty_percent_input(
            "Weighment slip weight (kg)",
            key=f"rm_line_wslip_weight_{line_token}_{idx}",
            max_value=None,
            step=1.0,
            help="Actual weight received at the factory, per the weighbridge slip.",
        )

    diff_col, comments_col = st.columns([1.4, 2.6])
    with diff_col:
        if weight and wslip_weight:
            diff = float(wslip_weight) - float(weight)
            tolerance = float(weight) * WEIGHT_TOLERANCE_PCT / 100
            diff_pct = (diff / float(weight) * 100) if weight else 0.0
            diff_text = f"Weight difference: {diff:+.2f} kg ({diff_pct:+.2f}%)"
            if diff > tolerance:
                st.markdown(
                    f"<span style='color:#2e7d32; font-weight:700'>{diff_text}</span>",
                    unsafe_allow_html=True,
                )
            elif diff < -tolerance:
                st.markdown(
                    f"<span style='color:#c62828; font-weight:700'>{diff_text}</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption(diff_text)
    with comments_col:
        comments = st.text_input(
            "Comments",
            placeholder="Reason for the weight variance, if any",
            key=f"rm_line_comments_{line_token}_{idx}",
        )
    collected_lines.append(
        {
            "name": (name or "").strip(),
            "cost": float(cost or 0.0),
            "weight": float(weight or 0.0),
            "weighment_slip_weight": float(wslip_weight) if wslip_weight else None,
            "comments": (comments or "").strip() or None,
        }
    )

total_weight = sum(ln["weight"] for ln in collected_lines if ln["name"])
total_value = sum(ln["weight"] * ln["cost"] for ln in collected_lines if ln["name"])
gst_value = total_value * 0.18
grand_total = total_value + gst_value
t1, t2, t3, t4, t5 = st.columns(5)
t1.metric("Materials", sum(1 for ln in collected_lines if ln["name"]))
t2.metric("Total weight (kg)", f"{total_weight:,.1f}")
t3.metric("Invoice value", f"{total_value:,.2f}")
t4.metric("GST value (18%)", f"{gst_value:,.2f}")
t5.metric("Total value", f"{grand_total:,.2f}")

add_col, rem_col, _ = st.columns([1, 1, 4])
if add_col.button("Add raw material", key="rm_log_add_line"):
    st.session_state.rm_invoice_lines.append(
        {"name": "", "cost": 0.0, "weight": 0.0}
    )
    st.rerun()
if rem_col.button("Remove last row", key="rm_log_rem_line") and len(
    st.session_state.rm_invoice_lines
) > 1:
    st.session_state.rm_invoice_lines.pop()
    st.rerun()

submitted = st.button("Save invoice lots", type="primary", key="rm_log_save")

if submitted:
    vendor_code = vendor_opts[vendor_label] if vendor_label else None
    invoice_no = (invoice or "").strip()
    complete = [ln for ln in collected_lines if ln["name"] and ln["weight"] > 0]
    incomplete = [ln for ln in collected_lines if ln["name"] and ln["weight"] <= 0]
    if not vendors:
        st.error("Create a vendor first.")
    elif not vendor_code:
        st.error("Select a vendor name.")
    elif not invoice_no:
        st.error("Vendor invoice is required.")
    elif incomplete:
        st.error("Each raw material row needs a received weight greater than zero.")
    elif not complete:
        st.error("Add at least one raw material with a name and received weight.")
    else:
        try:
            doc_bytes = photo_bytes(invoice_doc)
            doc_name = invoice_doc.name if invoice_doc else None
            doc_type = getattr(invoice_doc, "type", None) if invoice_doc else None
            lines: list[dict] = []
            for ln in complete:
                material_name = db.find_raw_material_name(ln["name"])
                if not material_name:
                    material_name = db.add_raw_material_master(
                        name=ln["name"],
                        effective_date=invoice_date.isoformat(),
                        vendor_code=vendor_code,
                        availability_class=db.RAW_MATERIAL_AVAILABILITY[0],
                        recovery=None,
                        status="Active",
                        create_new=True,
                    )
                lines.append(
                    {
                        "material": material_name,
                        "cost": ln["cost"],
                        "weight": ln["weight"],
                        "weighment_slip_weight": ln["weighment_slip_weight"],
                        "comments": ln["comments"],
                    }
                )
            purchase_id, lot_ids = db.save_raw_material_invoice(
                vendor_code=vendor_code,
                invoice=invoice_no,
                received_date=received.isoformat(),
                lines=lines,
                storage_bay=storage.strip(),
                status=inv_status,
                supplier_invoice_date=invoice_date.isoformat(),
                invoice_document=doc_bytes,
                invoice_document_name=doc_name,
                invoice_document_type=doc_type,
                vehicle_photo=vehicle_photo_bytes,
                weighment_slip_photo=weighment_slip_photo_bytes,
            )
            names = ", ".join(ln["name"] for ln in complete)
            st.success(
                f"Saved invoice **{invoice_no}** (purchase #{purchase_id}) "
                f"with {len(lot_ids)} lot(s) ({names}). "
                f"Lot IDs: {', '.join(str(i) for i in lot_ids)}."
            )
            st.session_state.rm_invoice_lines = [
                {"name": "", "cost": 0.0, "weight": 0.0}
            ]
            st.session_state.rm_log_token = int(line_token) + 1
            st.session_state.pop("rm_log_vphoto_bytes", None)
            st.session_state.pop("rm_log_vphoto_open", None)
            st.session_state.pop("rm_log_wslip_bytes", None)
            st.session_state.pop("rm_log_wslip_open", None)
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

