import streamlit as st
import database as db
from pages_common import df_from_rows, empty_percent_input, format_ui_date, photo_bytes, show_dataframe, ui_date_input


@st.cache_data(ttl=60, show_spinner=False)
def _quick_input_reference_data() -> dict:
    """Cached reference lists; they change only on their own Master pages.

    raw_material_master_by_name keeps the newest Effective_date row per
    material (list_raw_material_master sorts newest first), the same rule
    Production Batch & Chemistry uses for Recovery.
    """
    master_by_name: dict[str, dict] = {}
    for row in db.list_raw_material_master():
        master_by_name.setdefault(str(row["Raw_Material_Name"]).lower(), row)
    return {
        "furnaces": db.list_furnaces(),
        "alloys": db.list_alloys(include_sidestream=False),
        "all_alloys": db.list_alloys(),
        "raw_materials": db.list_raw_materials(),
        "trolleys": db.list_trolleys(active_only=True),
        "raw_material_master_by_name": master_by_name,
    }


def _alloy_label(alloy: dict) -> str:
    return f"{alloy['Alloy_name']}" + (
        f" ({alloy['Customer_name']})" if alloy.get("Customer_name") else ""
    )


def _trolley_label(trolley: dict) -> str:
    return (
        f"{trolley['Trolley_name']}"
        + (f" ({trolley['Colour']})" if trolley.get("Colour") else "")
        + f" — {float(trolley['Weight'] or 0):.1f} kg"
    )


def _fmt_pct(value: object) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _photo_picker(label: str, key: str) -> bytes | None:
    """Toggle that opens the camera / gallery; keeps the photo once taken."""
    bytes_key = f"{key}_bytes"
    if st.toggle(label, key=f"{key}_open"):
        cam = st.camera_input("Camera", key=f"{key}_cam")
        upload = st.file_uploader(
            "Or choose from gallery",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{key}_file",
        )
        taken = photo_bytes(cam) or photo_bytes(upload)
        if taken:
            st.session_state[bytes_key] = taken
    saved = st.session_state.get(bytes_key)
    if saved:
        st.caption(f"{label.split(' ', 1)[-1]} attached.")
    return saved


st.title("Quick Batch Input")
st.caption(
    "Fast raw material entry for a heat, made for phones. Enter the production date, "
    "furnace, shift and melt no: an existing batch opens, otherwise a new batch is "
    "created (In-Progress) with the first charge line you save. Each **Save** stores "
    "one trolley weighing. Degassing, samples, chemistry, melter and supervisor are "
    "entered on **Production Batch & Chemistry**."
)

flash = st.session_state.pop("_qbi_flash", None)
if flash:
    st.success(flash)

ref = _quick_input_reference_data()
if not ref["furnaces"]:
    st.error("Define at least one furnace under **Furnaces**.")
    st.stop()

prod_date = ui_date_input("Production date *", value=None, key="qbi_date")
furnace = st.selectbox(
    "Furnace *",
    ref["furnaces"],
    index=None,
    placeholder="Select furnace",
    key="qbi_furnace",
)
c_shift, c_melt = st.columns(2)
with c_shift:
    shift = st.selectbox(
        "Shift *", db.SHIFTS, index=None, placeholder="Shift", key="qbi_shift"
    )
with c_melt:
    melt_no = st.selectbox(
        "Melt no *", db.MELT_NOS, index=None, placeholder="Melt", key="qbi_melt"
    )

if prod_date is None or furnace is None or shift is None or melt_no is None:
    st.info("Enter production date, furnace, shift and melt no to open the batch.")
    st.stop()

batch_id = db.find_batch_id_for_identity(furnace, prod_date, shift, melt_no)
batch = db.get_batch(batch_id) if batch_id else None

alloy_id = None
with st.container(border=True):
    if batch:
        status = batch.get("Production_status") or db.BATCH_STATUS_IN_PROGRESS
        alloy_name = next(
            (
                _alloy_label(a)
                for a in ref["all_alloys"]
                if str(a["Alloy_id"]) == str(batch.get("Alloy_id"))
            ),
            "—",
        )
        alloy_id = batch.get("Alloy_id")
        st.markdown(
            f"**Batch ID:** `{batch_id}`  \n"
            f"**Heat no:** `{batch.get('Heat_no') or '—'}`  \n"
            f"**Alloy:** {alloy_name}  \n"
            f"**Production status:** `{status}`"
        )
    else:
        try:
            preview_id = db.build_production_batch_id(
                furnace, prod_date, shift, melt_no
            )
            heat_preview = db.preview_next_heat_no(furnace, prod_date)
        except Exception as exc:
            st.error(str(exc))
            st.stop()
        st.markdown(
            "**New production batch**  \n"
            f"**Batch ID:** `{preview_id}`  \n"
            f"**Heat no:** `{heat_preview}` (assigned on save)  \n"
            "**Production status:** not created yet"
        )
        crucible = db.get_available_crucible(furnace)
        if not crucible:
            st.error("No crucible available for the respective furnace.")
            st.stop()
        alloy_by_label = {_alloy_label(a): a["Alloy_id"] for a in ref["alloys"]}
        alloy_pick = st.selectbox(
            "Alloy *",
            list(alloy_by_label.keys()),
            index=None,
            placeholder="Select alloy",
            key="qbi_alloy",
        )
        alloy_id = alloy_by_label.get(alloy_pick) if alloy_pick else None

saved_lines = db.get_batch_inputs(batch_id) if batch else []

if batch and batch.get("Production_status") == db.BATCH_STATUS_COMPLETED:
    st.warning(
        "This heat is **Completed** and locked, so no more raw material can be "
        "added. An Admin can unlock it on **Production Batch & Chemistry**."
    )
else:
    # Bumping the generation after a save gives every charge-line widget a fresh
    # key, so the form comes back blank for the next trolley.
    gen = st.session_state.setdefault("qbi_gen", 0)

    def _k(name: str) -> str:
        return f"qbi_{gen}_{name}"

    st.markdown("#### Add raw material")
    mat = st.selectbox(
        "Raw material *",
        ref["raw_materials"],
        index=None,
        placeholder="Select raw material",
        key=_k("mat"),
    )
    open_lots: list[dict] = []
    recovery = None
    if mat:
        master = ref["raw_material_master_by_name"].get(mat.lower(), {})
        recovery = master.get("Recovery")
        open_lots = sorted(
            (
                dict(lot, Remaining_Weight=float(lot.get("Remaining_Weight") or 0))
                for lot in db.list_inventory_lots(material=mat)
            ),
            key=lambda lot: int(lot["Lot_id"]),
        )
        open_lots = [lot for lot in open_lots if lot["Remaining_Weight"] > 1e-9]
        stock = sum(lot["Remaining_Weight"] for lot in open_lots)
        with st.container(border=True):
            st.markdown(
                f"**Recovery: {_fmt_pct(recovery)}**  \n"
                f"In stock: **{stock:,.1f} kg** in {len(open_lots)} lot(s)"
            )
            details = ["lots used oldest first (FIFO)"]
            if master.get("ISRI_CODE"):
                details.append(f"ISRI {master['ISRI_CODE']}")
            if master.get("Availability_class"):
                details.append(f"Class {master['Availability_class']}")
            if master.get("Effective_date"):
                details.append(
                    f"master effective {format_ui_date(master['Effective_date'])}"
                )
            st.caption(" · ".join(details))
            if recovery is None:
                st.caption("No recovery set on **Raw Material Master** for this material.")

    trolley_by_label = {_trolley_label(t): t for t in ref["trolleys"]}
    if not trolley_by_label:
        st.error("Define at least one active trolley under **Trolleys**.")
    trolley_pick = st.selectbox(
        "Trolley *",
        list(trolley_by_label.keys()),
        index=None,
        placeholder="Select trolley",
        key=_k("trolley"),
    )
    trolley = trolley_by_label.get(trolley_pick) if trolley_pick else None
    tare = float(trolley["Weight"] or 0) if trolley else 0.0

    scale_w = empty_percent_input(
        "Weighment weight (kg) *",
        key=_k("scale"),
        max_value=None,
        step=1.0,
    )
    scale_val = float(scale_w or 0)
    net_w = max(scale_val - tare, 0.0) if trolley and scale_val > 0 else 0.0

    weights = (
        f"Trolley {tare:,.1f} kg" if trolley else "Trolley —"
    ) + f"  ·  **Net weight {net_w:,.1f} kg**"
    if net_w > 0 and recovery is not None:
        weights += f"  ·  est. output {net_w * float(recovery) / 100.0:,.1f} kg"
    st.info(weights)

    problems: list[str] = []
    fifo_parts: list[dict] = []
    if trolley and scale_val > 0 and net_w <= 0:
        problems.append("Weighment weight must be more than the trolley weight.")
    if mat and net_w > 0:
        fifo_parts, short = db.allocate_fifo(open_lots, net_w)
        if short > 1e-6:
            problems.append(
                f"Not enough {mat} in stock: {short:,.1f} kg short. "
                "Check the weighment or the inventory."
            )
    for problem in problems:
        st.error(problem)

    scale_photo = _photo_picker("📷 Weighment photo", _k("wsp"))
    input_photo = _photo_picker("📷 Raw material photo", _k("inp"))

    save_label = "Save charge line" if batch else "Save and create batch"
    if st.button(save_label, type="primary", use_container_width=True, key=_k("save")):
        missing = [
            name
            for name, value in (
                ("Alloy", batch or alloy_id),
                ("Raw material", mat),
                ("Trolley", trolley),
                ("Weighment weight", scale_val > 0),
            )
            if not value
        ]
        if missing:
            st.error("Enter " + ", ".join(missing) + ".")
        elif problems or net_w <= 0 or not fifo_parts:
            st.error("Fix the weight above before saving.")
        else:
            line = {
                "Raw_Material_Name": mat,
                # No lot: the save splits this line across lots FIFO.
                "Lot_id": None,
                "Weight": net_w,
                "Weighment_scale_weight": scale_val,
                "Trolley_weight": tare,
                "Trolley_name": trolley["Trolley_name"],
                "Notes": "",
                "Weighment_scale_photo": scale_photo,
                "Input_photo": input_photo,
                "Charge_time": db.now_ist().isoformat(timespec="seconds"),
            }
            try:
                if batch:
                    db.add_batch_charge_lines(batch_id, [line])
                    saved_to = batch_id
                    st.session_state["_qbi_flash"] = (
                        f"Saved **{net_w:,.1f} kg** of **{mat}** to batch **{saved_to}**."
                    )
                else:
                    saved_to = db.create_batch(
                        furnace=furnace,
                        alloy_id=alloy_id,
                        production_date=prod_date.isoformat(),
                        shift=shift,
                        melt_no=melt_no,
                        melting_team=None,
                        notes="",
                        inputs=[line],
                        composition={},
                    )
                    created = db.get_batch(saved_to) or {}
                    st.session_state["_qbi_flash"] = (
                        f"Created batch **{saved_to}** (heat no "
                        f"**{created.get('Heat_no') or '—'}**, "
                        f"{db.BATCH_STATUS_IN_PROGRESS}) with **{net_w:,.1f} kg** "
                        f"of **{mat}**."
                    )
                st.session_state["qbi_gen"] = gen + 1
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

if batch:
    st.markdown("#### Saved charge lines")
    if not saved_lines:
        st.caption("No charge lines saved on this batch yet.")
    else:
        total = sum(float(r.get("Weight") or 0) for r in saved_lines)
        st.metric(
            "Total net input (kg)",
            f"{total:,.1f}",
            help=f"{len(saved_lines)} saved line(s). A weighing split across lots shows once per lot.",
        )
        show_dataframe(
            df_from_rows(
                [
                    {
                        "Time": format_ui_date(r.get("Charge_time"), with_time=True),
                        "Raw material": r.get("Raw_Material_Name"),
                        "Trolley": r.get("Trolley_name"),
                        "Net (kg)": float(r.get("Weight") or 0),
                        "Saved by": r.get("Saved_by"),
                    }
                    for r in reversed(saved_lines)
                ]
            ),
            column_config={
                "Net (kg)": st.column_config.NumberColumn(format="%.1f"),
            },
        )
