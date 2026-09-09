import streamlit as st
import database as db
from pages_common import CHEM_PERCENT_FORMAT, CHEM_PERCENT_STEP, _optional_percent, _parse_master_date, df_from_rows, dialog_all_element_percentages, empty_percent_input, format_ui_date, merge_percent_composition, photo_bytes, show_dataframe, ui_date_input


def _option_label(options: dict, value: object) -> str:
    if value in (None, ""):
        return ""
    for label, stored in options.items():
        if stored == value or str(stored) == str(value):
            return label
    return ""


st.title("Raw Material Master")
st.caption(
    "Modify an existing raw material or add a new name. "
    "New names are checked for duplicates without regard to letter case. "
    "A new grade also needs a specification stored in **Raw_Material_Spec**."
)

vendors = db.list_vendors()
vendor_opts = {
    f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors
}
isri_opts = {
    f"{c['ISRI_CODE']} — {c['Description']}": c["ISRI_CODE"]
    for c in db.list_isri_codes()
}
existing_names = db.list_raw_materials(active_only=False)
entry_elements = db.list_raw_material_spec_elements(entry_only=True)
if not vendors:
    st.warning("Add at least one vendor under **Vendors** before saving a grade.")

action = st.radio(
    "What do you want to do?",
    ["Modify existing raw material", "Add new raw material"],
    horizontal=True,
    key="rmm_action",
)
is_modify = action.startswith("Modify")
prefix = "rmm_mod" if is_modify else "rmm_add"
full_spec_key = f"{prefix}_full_spec"
spec_defaults = db.omit_raw_material_spec_hidden(
    st.session_state.get(full_spec_key) or {}
)

if is_modify:
    if not existing_names:
        st.info("No raw materials in the database yet. Choose **Add new raw material**.")
        rm_name = ""
    else:
        rm_name = st.selectbox(
            "Raw material name *",
            options=existing_names,
            key="rmm_existing_name",
            help="Only names already stored in the database can be modified.",
        )
        row = db.get_raw_material_master(rm_name) or {}
        stored_availability = row.get("Availability_class")
        stale_availability = bool(
            stored_availability
            and st.session_state.get("rmm_mod_availability") != stored_availability
            and stored_availability not in db.RAW_MATERIAL_AVAILABILITY
        )
        if rm_name and (
            rm_name != st.session_state.get("rmm_loaded_name") or stale_availability
        ):
            loaded_spec = db.get_raw_material_master_spec(
                rm_name, row.get("Effective_date")
            )
            st.session_state["rmm_loaded_name"] = rm_name
            st.session_state["rmm_mod_isri"] = _option_label(
                isri_opts, row.get("ISRI_CODE")
            )
            availability = row.get("Availability_class")
            st.session_state["rmm_mod_availability"] = availability or db.RAW_MATERIAL_AVAILABILITY[0]
            st.session_state["rmm_mod_vendor"] = _option_label(
                vendor_opts, row.get("Vendor_code")
            )
            st.session_state["rmm_mod_effective"] = _parse_master_date(
                row.get("Effective_date")
            )
            st.session_state["rmm_mod_recovery"] = _optional_percent(
                row.get("Recovery")
            )
            status = row.get("Status")
            st.session_state["rmm_mod_status"] = (
                status if status in db.ACTIVE_STATUS else db.ACTIVE_STATUS[0]
            )
            st.session_state[full_spec_key] = loaded_spec
            for el in entry_elements:
                sym = el["Element_Symbol"]
                st.session_state[f"{prefix}_spec_{sym}"] = _optional_percent(
                    loaded_spec.get(sym)
                )
            st.rerun()
else:
    rm_name = st.text_input(
        "Raw material name *",
        placeholder="e.g. Tense, Taint/Tabor, UBC, Pure Al",
        key="rmm_add_name",
    )

show_form = bool(rm_name) if is_modify else True
if show_form:
    availability_opts = list(db.RAW_MATERIAL_AVAILABILITY)
    current_availability = st.session_state.get(f"{prefix}_availability")
    if current_availability and current_availability not in availability_opts:
        availability_opts = [current_availability] + availability_opts
    c1, c2 = st.columns(2)
    with c1:
        isri_label = st.selectbox(
            "ISRI code",
            options=[""] + list(isri_opts.keys()),
            key=f"{prefix}_isri",
        )
        availability = st.selectbox(
            "Availability class",
            availability_opts,
            key=f"{prefix}_availability",
        )
    with c2:
        vendor_label = st.selectbox(
            "Vendor",
            options=[""] + list(vendor_opts.keys()),
            key=f"{prefix}_vendor",
        )
        effective = ui_date_input(
            "Effective date *",
            key=f"{prefix}_effective",
        )
        recovery = empty_percent_input(
            "Expected recovery %",
            key=f"{prefix}_recovery",
            step=0.1,
        )
        rm_status = st.selectbox(
            "Status", db.ACTIVE_STATUS, key=f"{prefix}_status"
        )
        photo = st.file_uploader(
            "Photo",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{prefix}_photo",
        )

    st.markdown("#### Raw material specification (%)")
    if is_modify:
        st.caption(
            "Master-grade chemistry stored in **Raw_Material_Spec** "
            "(name + effective date). Update the percentages below if needed."
        )
    else:
        st.caption(
            "Required for a new raw material. Values are stored in "
            "**Raw_Material_Spec** as the grade specification."
        )

    spec_values: dict[str, float | None] = {}
    spec_cols = st.columns(6)
    for i, el in enumerate(entry_elements):
        sym = el["Element_Symbol"]
        with spec_cols[i % 6]:
            spec_values[sym] = empty_percent_input(
                f"{sym} %",
                key=f"{prefix}_spec_{sym}",
                default=spec_defaults.get(sym),
                step=CHEM_PERCENT_STEP,
                format=CHEM_PERCENT_FORMAT,
                help=el["Element_Name"],
            )

    sync_spec = {
        el["Element_Symbol"]: f"{prefix}_spec_{el['Element_Symbol']}"
        for el in entry_elements
    }
    all_spec_elements = db.list_raw_material_spec_elements()
    sb1, sb2 = st.columns([2, 3])
    with sb1:
        if st.button(
            "Open all elements",
            key=f"{prefix}_open_spec",
            use_container_width=True,
        ):
            dialog_all_element_percentages(
                full_spec_key,
                defaults=spec_defaults,
                sync_keys=sync_spec,
                elements=all_spec_elements,
                caption=(
                    f"Enter specification % for **{len(all_spec_elements)}** "
                    "elements. **OE**, **OT**, and **SF** are not used on raw material specs. "
                    "Click **Apply & close** to use these values."
                ),
            )
    with sb2:
        extra_n = len(
            [
                1
                for sym, val in spec_defaults.items()
                if _optional_percent(val)
                and sym not in spec_values
            ]
        )
        if extra_n:
            st.caption(f"Other element percentages applied ({extra_n}).")

    save_label = "Update raw material" if is_modify else "Add raw material"
    if st.button(save_label, type="primary", key=f"{prefix}_save"):
        name_clean = (rm_name or "").strip()
        composition = merge_percent_composition(spec_values, full_spec_key)
        if not name_clean:
            st.error("Raw material name is required.")
        elif not is_modify and (existing := db.find_raw_material_name(name_clean)):
            st.error(
                f"A raw material named **{existing}** already exists. "
                "Names are not case-sensitive."
            )
        elif not is_modify and not composition:
            st.error(
                "Enter at least one specification percentage for the new raw material."
            )
        else:
            try:
                saved_name = db.add_raw_material_master(
                    name=name_clean,
                    effective_date=effective.isoformat(),
                    vendor_code=vendor_opts[vendor_label] if vendor_label else None,
                    availability_class=availability,
                    recovery=recovery,
                    status=rm_status,
                    photo=photo_bytes(photo),
                    isri_code=isri_opts[isri_label] if isri_label else None,
                    create_new=not is_modify,
                )
                db.set_raw_material_master_spec(
                    saved_name, composition, effective.isoformat()
                )
                verb = "Updated" if is_modify else "Added"
                st.success(
                    f"{verb} raw material **{saved_name}** "
                    f"(effective {format_ui_date(effective)})."
                )
                st.session_state.pop(full_spec_key, None)
                if is_modify:
                    st.session_state["rmm_loaded_name"] = None
                st.cache_data.clear()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

rows = db.list_raw_material_master()
st.subheader(f"Raw material grades ({len(rows)})")
if not rows:
    st.info("No raw material grades yet. Add one using the form above.")
else:
    show_dataframe(df_from_rows(rows))
