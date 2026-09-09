import streamlit as st
import database as db
from datetime import datetime
from pages_common import CHEM_PERCENT_FORMAT, CHEM_PERCENT_STEP, _optional_percent, df_from_rows, empty_percent_input, format_ui_date, show_dataframe, to_storage_date


@st.dialog("All elements — alloy specification", width="large")
def dialog_all_element_specs(
    state_key: str,
    sync_min_keys: dict[str, str] | None = None,
    sync_max_keys: dict[str, str] | None = None,
    elements: list[dict] | None = None,
) -> None:
    """Popup to enter min/max % for other Element_Master rows (Serial_no 16–36)."""
    stored = st.session_state.get(state_key) or {}
    elements = elements if elements is not None else db.list_other_spec_elements()
    st.caption(
        f"Enter min / max % for **{len(elements)}** other elements "
        f"(Serial_no {db.OTHER_SPEC_SERIAL_MIN}–{db.OTHER_SPEC_SERIAL_MAX}). "
        "Click **Apply & close** to use these ranges on Create alloy."
    )
    values: dict[str, tuple[float | None, float | None]] = {}
    for el in elements:
        sym = el["Element_Symbol"]
        prev = stored.get(sym) or (None, None)
        try:
            prev_min, prev_max = prev[0], prev[1]
        except (TypeError, IndexError, ValueError):
            prev_min, prev_max = None, None
        c1, c2, c3 = st.columns([1.2, 2, 2])
        c1.markdown(f"**{sym}**")
        with c2:
            mn = empty_percent_input(
                f"{sym} min",
                key=f"{state_key}_dlg_min_{sym}",
                default=prev_min,
                step=CHEM_PERCENT_STEP,
                format=CHEM_PERCENT_FORMAT,
            )
        with c3:
            mx = empty_percent_input(
                f"{sym} max",
                key=f"{state_key}_dlg_max_{sym}",
                default=prev_max,
                step=CHEM_PERCENT_STEP,
                format=CHEM_PERCENT_FORMAT,
            )
        values[sym] = (mn, mx)

    b1, b2 = st.columns(2)
    with b1:
        apply = st.button("Apply & close", type="primary", use_container_width=True)
    with b2:
        cancel = st.button("Cancel", use_container_width=True)
    if apply:
        st.session_state[state_key] = {
            sym: (_optional_percent(mn), _optional_percent(mx))
            for sym, (mn, mx) in values.items()
            if _optional_percent(mn) or _optional_percent(mx)
        }
        if sync_min_keys:
            for sym, widget_key in sync_min_keys.items():
                st.session_state[widget_key] = _optional_percent(
                    (values.get(sym) or (None, None))[0]
                )
        if sync_max_keys:
            for sym, widget_key in sync_max_keys.items():
                st.session_state[widget_key] = _optional_percent(
                    (values.get(sym) or (None, None))[1]
                )
        st.rerun()
    if cancel:
        st.rerun()


def merge_spec_ranges(
    page_specs: dict[str, tuple[float | None, float | None]],
    full_state_key: str,
) -> dict[str, tuple[float | None, float | None]]:
    """Merge main-page specs over an optional full Element_Master dialog map."""
    merged: dict[str, tuple[float | None, float | None]] = {}
    for sym, pair in (st.session_state.get(full_state_key) or {}).items():
        try:
            mn, mx = pair
        except (TypeError, ValueError):
            continue
        merged[sym] = (mn if mn and mn > 0 else None, mx if mx and mx > 0 else None)
    merged.update(page_specs)
    return merged


st.title("Alloy Master & Spec")
cust_opts = {
    f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"]
    for c in db.list_customer_codes()
}

entry_elements = db.list_batch_chem_elements()
sync_min = {el["Element_Symbol"]: f"amin_{el['Element_Symbol']}" for el in entry_elements}
sync_max = {el["Element_Symbol"]: f"amax_{el['Element_Symbol']}" for el in entry_elements}
full_specs = st.session_state.get("alloy_full_specs") or {}

a1, a2 = st.columns(2)
with a1:
    aname = st.text_input(
        "Alloy name *", placeholder="e.g. ADC12, LM6", key="alloy_name"
    )
    family = st.text_input(
        "Alloy family", placeholder="e.g. Al-Si-Cu", key="alloy_family"
    )
    alloy_group = st.text_input(
        "Alloy group", placeholder="e.g. ADC, LM", key="alloy_group"
    )
    created_by = st.text_input("Created by", value="operator", key="alloy_created_by")
with a2:
    customer = st.selectbox(
        "Customer", [""] + list(cust_opts.keys()), key="alloy_customer"
    )
    colour = st.text_input(
        "Colour code", placeholder="e.g. Red, #FF0000", key="alloy_colour"
    )
    bis_desig = st.text_input(
        "BIS designation", placeholder="e.g. IS 617", key="alloy_bis"
    )
    rev_dt = st.text_input(
        "Revision datetime",
        value=format_ui_date(datetime.now(), with_time=True),
        key="alloy_rev_dt",
    )
    remarks = st.text_area("Remarks", key="alloy_remarks")
    alloy_status = st.selectbox("Status", db.ACTIVE_STATUS, key="alloy_status")

st.markdown("#### Spec range (%)")
st.caption(
    f"First {db.ENTRY_CHEM_ELEMENT_LIMIT} elements by Serial_no, "
    "plus **OE**, **OT**, and **SF**. "
    "Use **Open other elements** for Serial_no "
    f"{db.OTHER_SPEC_SERIAL_MIN}–{db.OTHER_SPEC_SERIAL_MAX}."
)

specs: dict[str, tuple[float | None, float | None]] = {}
for el in entry_elements:
    sym = el["Element_Symbol"]
    prev = full_specs.get(sym) or (None, None)
    try:
        dmin, dmax = prev[0], prev[1]
    except (TypeError, IndexError, ValueError):
        dmin, dmax = None, None
    sc1, sc2, sc3 = st.columns([1, 2, 2])
    sc1.markdown(f"**{sym}**")
    with sc2:
        mn = empty_percent_input(
            f"{sym} min",
            key=f"amin_{sym}",
            default=dmin,
            step=CHEM_PERCENT_STEP,
            format=CHEM_PERCENT_FORMAT,
        )
    with sc3:
        mx = empty_percent_input(
            f"{sym} max",
            key=f"amax_{sym}",
            default=dmax,
            step=CHEM_PERCENT_STEP,
            format=CHEM_PERCENT_FORMAT,
        )
    specs[sym] = (
        mn if mn and mn > 0 else None,
        mx if mx and mx > 0 else None,
    )

ab1, ab2 = st.columns([2, 3])
with ab1:
    if st.button(
        "Open other elements",
        key="alloy_open_all_elements",
        help=(
            "Enter min/max for Element_Master symbols with "
            f"Serial_no {db.OTHER_SPEC_SERIAL_MIN}–{db.OTHER_SPEC_SERIAL_MAX}"
        ),
        use_container_width=True,
    ):
        dialog_all_element_specs(
            "alloy_full_specs",
            sync_min_keys=sync_min,
            sync_max_keys=sync_max,
            elements=db.list_other_spec_elements(),
        )
with ab2:
    full_n = len(
        [
            1
            for pair in full_specs.values()
            if pair and ((pair[0] or 0) > 0 or (pair[1] or 0) > 0)
        ]
    )
    if full_n:
        st.caption(f"Other element specs applied ({full_n} element(s) with ranges).")

if st.button("Create alloy", type="primary", key="alloy_create"):
    if not aname.strip():
        st.error("Alloy name is required.")
    else:
        aid = db.add_alloy(
            cust_code=cust_opts[customer] if customer else None,
            alloy_name=aname.strip(),
            family=family.strip(),
            alloy_group=alloy_group.strip() or None,
            created_by=created_by.strip(),
            specs=merge_spec_ranges(specs, "alloy_full_specs"),
            colour_code=colour.strip() or None,
            bis_designation=bis_desig.strip() or None,
            revision_datetime=to_storage_date(rev_dt.strip(), with_time=True) if rev_dt.strip() else None,
            remarks=remarks.strip() or None,
            status=alloy_status,
        )
        st.session_state.pop("alloy_full_specs", None)
        st.success(f"Created alloy **{aname}** (ID {aid}).")
st.subheader("Alloys")
show_dataframe(df_from_rows(db.list_alloys()))

aid_view = st.number_input("View specs for Alloy ID", min_value=0, step=1, value=0)
if aid_view > 0:
    specs_df = df_from_rows(
        db.fetch_all(
            """
            SELECT s.Element_symbol AS "Element_symbol",
                   s.Min_percent AS "Min_percent",
                   s.Max_percent AS "Max_percent"
            FROM Alloy_Master_spec s
            LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
            WHERE s.Alloy_id = ?
            ORDER BY COALESCE(_el.Serial_no, 9999), s.Element_symbol
            """,
            (int(aid_view),),
        )
    )
    show_dataframe(specs_df)
