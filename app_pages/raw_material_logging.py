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

flash = st.session_state.pop("rm_log_flash", None)
if flash:
    st.success(flash)

# Every widget key on this form carries this token. Saving bumps it, so the
# vendor invoice and its raw material rows all come back blank for the next
# invoice (Streamlit keeps a widget's value while its key stays the same).
if "rm_log_token" not in st.session_state:
    st.session_state.rm_log_token = 0
form_token = st.session_state.rm_log_token

st.markdown("#### Vendor invoice")
vendor_label = st.selectbox(
    "Vendor name *",
    options=[""] + list(vendor_opts.keys()),
    key=f"rm_log_vendor_{form_token}",
)
inv1, inv2, inv3 = st.columns(3)
with inv1:
    invoice_date = ui_date_input(
        "Supplier invoice date *", value=db.today_ist(), key=f"rm_log_invoice_date_{form_token}"
    )
with inv2:
    invoice = st.text_input(
        "Vendor invoice *",
        placeholder="e.g. INV-2026-001",
        key=f"rm_log_invoice_{form_token}",
    )
with inv3:
    received = ui_date_input(
        "Received date", value=db.today_ist(), key=f"rm_log_received_{form_token}"
    )

rec1, rec2, rec3 = st.columns(3)
with rec1:
    storage = st.text_input(
        "Storage bay", placeholder="e.g. Bay-A1", key=f"rm_log_storage_{form_token}"
    )
with rec2:
    inv_status = st.selectbox(
        "Inventory status", db.INVENTORY_STATUS, index=1, key=f"rm_log_inv_status_{form_token}"
    )
with rec3:
    invoice_doc = st.file_uploader(
        "Invoice document",
        type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
        help="Optional. Stored once on the vendor invoice, not on each lot.",
        key=f"rm_log_invoice_doc_{form_token}",
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
            key=f"rm_log_vphoto_cam_{form_token}",
            help="Uses the phone camera when available.",
        )
        vp_file = st.file_uploader(
            "Vehicle gallery / files",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"rm_log_vphoto_file_{form_token}",
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
            key=f"rm_log_wslip_cam_{form_token}",
            help="Uses the phone camera when available.",
        )
        ws_file = st.file_uploader(
            "Weighment slip gallery / files",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"rm_log_wslip_file_{form_token}",
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
line_token = form_token

WEIGHT_TOLERANCE_PCT = 0.12  # max allowed variance between invoice and weighbridge weight

collected_lines: list[dict] = []
for idx, _line in enumerate(st.session_state.rm_invoice_lines):
    st.markdown(f"**Row {idx + 1}**")
    n1, n2, n3 = st.columns([2.0, 1.0, 1.0])
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
        invoice_weight = empty_percent_input(
            "Invoice weight (kg) *",
            key=f"rm_line_invoice_weight_{line_token}_{idx}",
            max_value=None,
            step=1.0,
        )
    with n3:
        cost = empty_percent_input(
            "Cost per kg",
            key=f"rm_line_cost_{line_token}_{idx}",
            max_value=None,
            step=0.01,
        )

    invoice_weight_val = float(invoice_weight) if invoice_weight else None

    n4, n5 = st.columns([1.0, 1.0])
    with n4:
        wslip_weight = empty_percent_input(
            "Weighment slip weight (kg)",
            key=f"rm_line_wslip_weight_{line_token}_{idx}",
            max_value=None,
            step=1.0,
            help="Actual weight received at the factory, per the weighbridge slip.",
        )
    wslip_weight_val = float(wslip_weight) if wslip_weight else None

    recv_min = 0.0
    recv_max = invoice_weight_val
    recv_help = "Actual weight taken into inventory. Cannot exceed the invoice weight."
    if (
        wslip_weight_val is not None
        and invoice_weight_val is not None
        and wslip_weight_val < invoice_weight_val
    ):
        recv_min = wslip_weight_val
        recv_help = (
            f"Weighbridge weight ({wslip_weight_val:g} kg) is below the invoice weight "
            f"({invoice_weight_val:g} kg) — enter a value between the two."
        )
    with n5:
        weight = empty_percent_input(
            "Received weight (kg) *",
            key=f"rm_line_weight_{line_token}_{idx}",
            min_value=recv_min,
            max_value=recv_max,
            step=1.0,
            help=recv_help,
        )

    diff_col, comments_col = st.columns([1.4, 2.6])
    with diff_col:
        if invoice_weight_val and wslip_weight_val:
            diff = wslip_weight_val - invoice_weight_val
            tolerance = invoice_weight_val * WEIGHT_TOLERANCE_PCT / 100
            diff_pct = diff / invoice_weight_val * 100
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
            "invoice_weight": float(invoice_weight) if invoice_weight else None,
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

save_col, accounts_col = st.columns([1, 1])
with save_col:
    submitted = st.button("Save invoice lots", type="primary", key="rm_log_save")
with accounts_col:
    submit_to_accounts = st.button("Submit to Accounts", key="rm_log_submit_accounts")

if submitted or submit_to_accounts:
    target_status = (
        "Pending with accounts" if submit_to_accounts else "Pending with purchase"
    )
    vendor_code = vendor_opts[vendor_label] if vendor_label else None
    invoice_no = (invoice or "").strip()
    complete = [
        ln
        for ln in collected_lines
        if ln["name"] and ln["weight"] > 0 and (ln["invoice_weight"] or 0) > 0
    ]
    incomplete = [
        ln
        for ln in collected_lines
        if ln["name"] and (ln["weight"] <= 0 or not (ln["invoice_weight"] or 0) > 0)
    ]
    out_of_range = []
    for ln in complete:
        if ln["weight"] > ln["invoice_weight"] + 1e-9:
            out_of_range.append(ln)
        elif (
            ln["weighment_slip_weight"] is not None
            and ln["weighment_slip_weight"] < ln["invoice_weight"]
            and ln["weight"] < ln["weighment_slip_weight"] - 1e-9
        ):
            out_of_range.append(ln)
    if not vendors:
        st.error("Create a vendor first.")
    elif not vendor_code:
        st.error("Select a vendor name.")
    elif not invoice_no:
        st.error("Vendor invoice is required.")
    elif incomplete:
        st.error(
            "Each raw material row needs an invoice weight and a received weight, "
            "both greater than zero."
        )
    elif not complete:
        st.error("Add at least one raw material with a name, invoice weight, and received weight.")
    elif out_of_range:
        st.error(
            "Received weight must be at or below the invoice weight, and at or above the "
            "weighment slip weight when it is lower than the invoice weight. "
            f"Check: {', '.join(ln['name'] for ln in out_of_range)}."
        )
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
                        "invoice_weight": ln["invoice_weight"],
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
                invoice_status=target_status,
            )
            names = ", ".join(ln["name"] for ln in complete)
            st.session_state["rm_log_flash"] = (
                f"Saved invoice **{invoice_no}** (purchase #{purchase_id}) "
                f"with {len(lot_ids)} lot(s) ({names}). "
                f"Lot IDs: {', '.join(str(i) for i in lot_ids)}. "
                f"Invoice status: **{target_status}**."
            )
            st.session_state.rm_invoice_lines = [
                {"name": "", "cost": 0.0, "weight": 0.0}
            ]
            st.session_state.rm_log_token = int(line_token) + 1
            st.session_state.pop("rm_log_vphoto_bytes", None)
            st.session_state.pop("rm_log_vphoto_open", None)
            st.session_state.pop("rm_log_wslip_bytes", None)
            st.session_state.pop("rm_log_wslip_open", None)
            st.session_state["rm_log_last_purchase_id"] = purchase_id
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Could not save: {exc}")

last_purchase_id = st.session_state.get("rm_log_last_purchase_id")
if last_purchase_id:
    last_purchase = db.get_raw_material_purchase(int(last_purchase_id))
    if not last_purchase:
        st.session_state.pop("rm_log_last_purchase_id", None)
    else:
        st.markdown("---")
        st.markdown(
            f"**Last saved invoice:** #{last_purchase['Purchase_id']} "
            f"({last_purchase['Supplier_Invoice']}) — "
            f"status: **{last_purchase['Invoice_status']}**"
        )
        if last_purchase["Invoice_status"] == "Pending with purchase":
            if st.button("Cancel Invoice", key="rm_log_cancel_invoice"):
                try:
                    db.set_raw_material_purchase_invoice_status(
                        int(last_purchase["Purchase_id"]), "Cancelled"
                    )
                    st.success(f"Invoice #{last_purchase['Purchase_id']} cancelled.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not cancel: {exc}")

