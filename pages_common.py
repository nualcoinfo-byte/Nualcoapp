"""
Shared, page-agnostic helpers used by multiple pages (see app.py's shell and
app_pages/*.py). Extracted verbatim from app.py during the Phase 1 multipage
migration -- function bodies are unchanged, only their home moved.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import database as db

CHEM_PERCENT_STEP = 0.0001
CHEM_PERCENT_FORMAT = "%.4f"


def df_from_rows(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _show_db_connection_error(exc: BaseException) -> None:
    st.error(f"Could not load data from the database: {exc}")


def photo_bytes(uploaded) -> bytes | None:
    if uploaded is None:
        return None
    return uploaded.getvalue()


def _as_photo_bytes(value: object) -> bytes | None:
    if value is None or value == "":
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    elif isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes) and value:
        return value
    return None


def _output_rows_for_table(rows: list[dict]) -> list[dict]:
    skip = {"Weighment_scale_photo", "Output_photo"}
    out: list[dict] = []
    for row in rows:
        item = {k: v for k, v in row.items() if k not in skip}
        avg = db.alloy_piece_avg_kg(item.get("Weight"), item.get("Pieces"))
        ordered: dict = {}
        for key, value in item.items():
            ordered[key] = value
            if key == "Pieces":
                ordered["Avg_piece_kg"] = avg
        if "Avg_piece_kg" not in ordered:
            ordered["Avg_piece_kg"] = avg
        out.append(ordered)
    return out


def _add_avg_piece_column(data: pd.DataFrame) -> pd.DataFrame:
    """Add Avg_piece_kg from Weight/Pieces or Output_Weight/Output_pieces."""
    if data is None or data.empty:
        return data
    out = data.copy()
    if "Avg_piece_kg" in out.columns:
        return out
    weight_col = next(
        (c for c in ("Weight", "Weight (kg)", "Output_Weight") if c in out.columns),
        None,
    )
    pieces_col = next(
        (c for c in ("Pieces", "Output_pieces") if c in out.columns),
        None,
    )
    if not weight_col or not pieces_col:
        return out
    avgs = [
        db.alloy_piece_avg_kg(w, p)
        for w, p in zip(out[weight_col], out[pieces_col])
    ]
    insert_at = list(out.columns).index(pieces_col) + 1
    out.insert(insert_at, "Avg_piece_kg", avgs)
    return out


def _pandas_styler(data: pd.DataFrame):
    """Return a pandas Styler, or None when jinja2 is not installed."""
    try:
        return data.style
    except (AttributeError, ImportError):
        return None


def _style_avg_piece_column(data: pd.DataFrame):
    """Highlight product-alloy piece averages outside 5.6–6.1 kg in red."""
    if data is None or data.empty or "Avg_piece_kg" not in data.columns:
        return data
    styler = _pandas_styler(data)
    if styler is None:
        return data
    alloy_ids = data["Alloy_id"] if "Alloy_id" in data.columns else None

    def _color(col: pd.Series) -> list[str]:
        if col.name != "Avg_piece_kg":
            return [""] * len(col)
        styles: list[str] = []
        for i, val in enumerate(col):
            aid = alloy_ids.iloc[i] if alloy_ids is not None else None
            if db.alloy_piece_avg_out_of_range(val, aid):
                styles.append("color: #c62828; font-weight: 700")
            else:
                styles.append("")
        return styles

    def _fmt_avg(val: object) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "—"
        try:
            return f"{float(val):.2f}"
        except (TypeError, ValueError):
            return "—"

    return styler.apply(_color, axis=0).format(
        {"Avg_piece_kg": _fmt_avg},
        na_rep="—",
    )


def _style_recovery_variance_column(data: pd.DataFrame):
    """Green above +1%, red below -1%, on Recovery_Variance_pct."""
    if data is None or data.empty or "Recovery_Variance_pct" not in data.columns:
        return data
    styler = _pandas_styler(data)
    if styler is None:
        return data

    def _color(col: pd.Series) -> list[str]:
        if col.name != "Recovery_Variance_pct":
            return [""] * len(col)
        styles: list[str] = []
        for val in col:
            if val is None or (isinstance(val, float) and pd.isna(val)):
                styles.append("")
                continue
            try:
                v = float(val)
            except (TypeError, ValueError):
                styles.append("")
                continue
            if v > 1.0:
                styles.append("color: #2e7d32; font-weight: 700")
            elif v < -1.0:
                styles.append("color: #c62828; font-weight: 700")
            else:
                styles.append("")
        return styles

    def _fmt_pct(val: object) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "—"
        try:
            return f"{float(val):+.2f}%"
        except (TypeError, ValueError):
            return "—"

    return styler.apply(_color, axis=0).format(
        {"Recovery_Variance_pct": _fmt_pct},
        na_rep="—",
    )


def render_avg_piece_weight(
    net_weight: object,
    pieces: object,
    *,
    alloy_id: object = None,
) -> None:
    """Show net weight ÷ pieces; red when a product alloy is outside 5.6–6.1 kg."""
    avg = db.alloy_piece_avg_kg(net_weight, pieces)
    lo, hi = db.ALLOY_PIECE_KG_MIN, db.ALLOY_PIECE_KG_MAX
    sidestream = db.is_sidestream_alloy(alloy_id) if alloy_id is not None else False
    if avg is None:
        st.markdown(
            '<p class="avg-piece-label">Avg piece (kg)</p>'
            '<p class="avg-piece">—</p>',
            unsafe_allow_html=True,
        )
        if not sidestream:
            st.caption(f"Net weight ÷ pieces. Typical range {lo:g}–{hi:g} kg.")
        return
    out_of_range = db.alloy_piece_avg_out_of_range(avg, alloy_id)
    css = "avg-piece-bad" if out_of_range else "avg-piece"
    st.markdown(
        f'<p class="avg-piece-label">Avg piece (kg)</p>'
        f'<p class="{css}">{avg:.2f}</p>',
        unsafe_allow_html=True,
    )
    if out_of_range:
        st.caption(
            f"Outside {lo:g}–{hi:g} kg. Check the pieces count."
        )
    elif not sidestream:
        st.caption(f"Typical range {lo:g}–{hi:g} kg.")


def _optional_percent(value: object, *, allow_zero: bool = False) -> float | None:
    """Treat blank / 0 as empty so percentage fields can start without 0.00."""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if allow_zero:
        return num if num >= 0 else None
    return num if num > 0 else None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def empty_percent_input(
    label: str,
    *,
    key: str,
    default: object = None,
    min_value: float = 0.0,
    max_value: float | None = 100.0,
    step: float = 0.01,
    help: str | None = None,
    disabled: bool = False,
    format: str | None = None,
    allow_zero: bool = False,
) -> float | None:
    """Number input that starts blank instead of 0.00."""
    if key not in st.session_state:
        st.session_state[key] = _optional_percent(default, allow_zero=allow_zero)
    kwargs: dict[str, object] = {
        "min_value": min_value,
        "step": step,
        "key": key,
        "help": help,
        "disabled": disabled,
        "placeholder": "",
    }
    if max_value is not None:
        kwargs["max_value"] = max_value
    if format:
        kwargs["format"] = format
    return st.number_input(label, **kwargs)


def empty_int_input(
    label: str,
    *,
    key: str,
    default: object = None,
    help: str | None = None,
    disabled: bool = False,
) -> int | None:
    """Whole-number input that starts blank instead of 0."""
    if key not in st.session_state:
        st.session_state[key] = _optional_int(default)
    elif st.session_state[key] not in (None, ""):
        try:
            st.session_state[key] = int(st.session_state[key])
        except (TypeError, ValueError):
            st.session_state[key] = None
    return st.number_input(
        label,
        min_value=0,
        step=1,
        format="%d",
        key=key,
        help=help,
        placeholder="",
        disabled=disabled,
    )


def _alloy_output_label(alloy: dict) -> str:
    aid = int(alloy["Alloy_id"])
    name = alloy.get("Alloy_name") or f"Alloy {aid}"
    if db.is_sidestream_alloy(aid):
        return f"{aid} — {name} (non-spec)"
    return f"{aid} — {name}"


def render_batch_output_editor(batch: dict, *, key_prefix: str) -> None:
    """Enter output lines for one batch: product alloy plus 78/79/80."""
    bid = batch["Batch_ID"]
    if (batch.get("Production_status") or "") != db.BATCH_STATUS_COMPLETED:
        status = batch.get("Production_status") or "In-Progress"
        gaps = []
        try:
            gaps = db.production_batch_completion_gaps_for_id(bid)
        except Exception:
            gaps = []
        if gaps:
            st.warning(
                f"**{bid}** is still **{status}** — saving the chemistry form does not "
                "complete the heat. Open **Production Batch & Chemistry**, fix: "
                + "; ".join(gaps)
                + ", then click **Mark as Completed**."
            )
        else:
            st.warning(
                f"**{bid}** is still **{status}**. Required fields are filled — open "
                "**Production Batch & Chemistry** and click **Mark as Completed**. "
                "Save changes alone does not complete the heat."
            )
        saved = db.get_batch_outputs(bid)
        if saved:
            st.caption("Saved outputs (read-only until Completed).")
            show_dataframe(
                df_from_rows(_output_rows_for_table(saved)),
                highlight_avg_piece=True,
            )
        return

    output_completed = (
        batch.get("Output_status") or db.BATCH_STATUS_IN_PROGRESS
    ) == db.BATCH_STATUS_COMPLETED
    is_admin = db.is_admin_user()
    unlock_key = f"{key_prefix}_{bid}_unlock_output"
    locked = output_completed and not (
        is_admin and st.session_state.get(unlock_key)
    )
    output_gaps: list[str] = []
    try:
        output_gaps = db.batch_output_completion_gaps_for_id(bid)
    except Exception:
        output_gaps = []

    status_col, unlock_col = st.columns([2, 2])
    with status_col:
        st.markdown(
            "**Output status:** `"
            f"{batch.get('Output_status') or db.BATCH_STATUS_IN_PROGRESS}`"
        )
    with unlock_col:
        if output_completed and is_admin:
            st.checkbox(
                "Correct history (unlock completed output)",
                key=unlock_key,
                help=(
                    "Admin only. Check this to edit output after it is Completed. "
                    "Finished Goods Inventory is re-synced on save; status stays "
                    "Completed."
                ),
            )
        elif output_completed:
            st.info(
                "Output for this heat is **Completed** and locked. Ask an Admin "
                "to unlock it if history needs correction."
            )

    product_id = batch.get("Alloy_id")
    alloys = db.list_batch_output_alloys(product_id)
    if not alloys:
        st.error(
            "Define the batch alloy and non-spec outputs 78 (Broken Ingot), "
            "79 (Furnace Empty), and 80 (Not Ok Ingot) under **Alloys**."
        )
        return

    label_to_id = {_alloy_output_label(a): int(a["Alloy_id"]) for a in alloys}
    id_to_label = {v: k for k, v in label_to_id.items()}
    labels = list(label_to_id.keys())
    default_label = labels[0]
    if product_id:
        default_label = id_to_label.get(int(product_id), default_label)

    def _k(name: str, idx: int) -> str:
        return f"{key_prefix}_{bid}_{name}_{idx}"

    state_key = f"{key_prefix}_n_{bid}"
    loaded_key = f"{key_prefix}_loaded_{bid}"
    if not st.session_state.get(loaded_key):
        existing = db.get_batch_outputs(bid, include_photos=True)
        st.session_state[state_key] = max(len(existing), 1)
        for idx, row in enumerate(existing):
            st.session_state[_k("alloy", idx)] = id_to_label.get(
                int(row["Alloy_id"]), default_label
            )
            scale = row.get("Weighment_scale_weight")
            st.session_state[_k("scale", idx)] = (
                float(scale) if scale not in (None, "") else None
            )
            stand = row.get("Stand_weight")
            st.session_state[_k("stand", idx)] = (
                float(stand) if stand not in (None, "") else None
            )
            st.session_state[_k("wt", idx)] = float(row["Weight"] or 0)
            st.session_state[_k("pcs", idx)] = _optional_int(row.get("Pieces"))
            st.session_state[_k("notes", idx)] = row.get("Notes") or ""
            scale_photo = _as_photo_bytes(row.get("Weighment_scale_photo"))
            if scale_photo:
                st.session_state[_k("wsp_bytes", idx)] = scale_photo
            out_photo = _as_photo_bytes(row.get("Output_photo"))
            if out_photo:
                st.session_state[_k("out_bytes", idx)] = out_photo
        if not existing and default_label:
            st.session_state[_k("alloy", 0)] = default_label
        st.session_state[loaded_key] = True

    n_lines = int(st.session_state.get(state_key) or 1)
    st.markdown("#### Batch outputs")
    st.caption(
        "Record metal that left this heat. Net weight is **weighment scale − stand**. "
        "Avg piece is **net weight ÷ pieces**. Product alloy pieces are typically "
        f"**{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg**; outside that "
        "range the average is shown in red so you can check the piece count. "
        "The product alloy is the one selected on the batch. "
        "**Broken Ingot**, **Furnace Empty**, and **Not Ok Ingot** (alloy IDs 78–80) "
        "are for samples and portions taken out so they do not spoil the chemistry. "
        "They have no spec. Stand weight must be entered on every line, **0** "
        "included, so a forgotten stand cannot inflate the net weight."
    )

    collected: list[dict] = []
    missing_stand: list[int] = []
    for idx in range(n_lines):
        st.markdown(f"**Output line {idx + 1}**")
        c1, c2, c3 = st.columns([2.4, 1.2, 2.4])
        with c1:
            alloy_label = st.selectbox(
                "Output alloy *",
                options=labels,
                key=_k("alloy", idx),
                disabled=locked,
            )
        with c2:
            pieces = empty_int_input(
                "Pieces",
                key=_k("pcs", idx),
                help="Whole number of pieces. No decimals.",
                disabled=locked,
            )
        with c3:
            notes = st.text_input("Notes", key=_k("notes", idx), disabled=locked)

        w1, w2, w3, w4 = st.columns(4)
        with w1:
            scale_w = empty_percent_input(
                "Weighment scale weight (kg) *",
                key=_k("scale", idx),
                max_value=None,
                step=1.0,
                disabled=locked,
            )
            wsp_open_key = _k("wsp_open", idx)
            if st.button(
                "📷 Weighment scale photo",
                key=_k("wsp_btn", idx),
                help="Open camera or choose a photo from the phone gallery",
                use_container_width=True,
                disabled=locked,
            ):
                st.session_state[wsp_open_key] = not bool(
                    st.session_state.get(wsp_open_key)
                )
                st.rerun()
            scale_photo_bytes: bytes | None = None
            if st.session_state.get(wsp_open_key):
                st.caption("Capture with camera or pick from gallery")
                wsp_cam = st.camera_input(
                    "Scale camera",
                    key=_k("wsp_cam", idx),
                    help="Uses the phone camera when available.",
                )
                wsp_file = st.file_uploader(
                    "Scale gallery / files",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=_k("wsp_file", idx),
                    help="Choose an existing photo from the device gallery.",
                )
                scale_photo_bytes = photo_bytes(wsp_cam) or photo_bytes(wsp_file)
                if scale_photo_bytes:
                    st.session_state[_k("wsp_bytes", idx)] = scale_photo_bytes
                    st.success("Weighment scale photo ready to save with this line.")
            else:
                scale_photo_bytes = _as_photo_bytes(
                    st.session_state.get(_k("wsp_bytes", idx))
                )
                if scale_photo_bytes:
                    st.caption("Weighment scale photo attached.")
        scale_val = float(scale_w or 0)
        with w2:
            stand_w = empty_percent_input(
                "Stand weight (kg) *",
                key=_k("stand", idx),
                max_value=None,
                step=1.0,
                allow_zero=True,
                help=(
                    "Required on every output line. Enter 0 when the metal was "
                    "weighed without a stand — it cannot be left blank."
                ),
                disabled=locked,
            )
            if scale_val > 0 and stand_w is None:
                st.markdown(
                    '<p class="chem-spec-bad">Enter the stand weight (0 if none).</p>',
                    unsafe_allow_html=True,
                )
        stand_val = float(stand_w or 0)
        net_w = max(scale_val - stand_val, 0.0) if scale_val > 0 else 0.0
        if (scale_val > 0 or net_w > 0) and stand_w is None:
            missing_stand.append(idx + 1)
        net_key = _k("wt", idx)
        st.session_state[net_key] = float(net_w)
        with w3:
            st.number_input(
                "Net weight (kg)",
                min_value=0.0,
                step=0.1,
                disabled=True,
                key=net_key,
                help="Auto: weighment scale weight − stand weight.",
            )
            out_open_key = _k("out_open", idx)
            if st.button(
                "📷 Output photo",
                key=_k("out_btn", idx),
                help="Open camera or choose a photo of the output",
                use_container_width=True,
                disabled=locked,
            ):
                st.session_state[out_open_key] = not bool(
                    st.session_state.get(out_open_key)
                )
                st.rerun()
        with w4:
            render_avg_piece_weight(
                net_w,
                pieces,
                alloy_id=label_to_id[alloy_label],
            )

        output_photo_bytes: bytes | None = None
        if st.session_state.get(out_open_key):
            st.caption(f"Output line {idx + 1} — output photo (camera or gallery)")
            out_cam = st.camera_input(
                "Output camera",
                key=_k("out_cam", idx),
                help="Uses the phone camera when available.",
            )
            out_file = st.file_uploader(
                "Output gallery / files",
                type=["png", "jpg", "jpeg", "webp"],
                key=_k("out_file", idx),
                help="Choose an existing photo from the device gallery.",
            )
            output_photo_bytes = photo_bytes(out_cam) or photo_bytes(out_file)
            if output_photo_bytes:
                st.session_state[_k("out_bytes", idx)] = output_photo_bytes
                st.success("Output photo ready to save with this line.")
        else:
            output_photo_bytes = _as_photo_bytes(
                st.session_state.get(_k("out_bytes", idx))
            )
            if output_photo_bytes:
                st.caption(f"Output line {idx + 1}: output photo attached.")

        collected.append(
            {
                "Alloy_id": label_to_id[alloy_label],
                "Weight": net_w,
                "Weighment_scale_weight": scale_val,
                "Stand_weight": stand_w,
                "Pieces": pieces,
                "Notes": notes,
                "Weighment_scale_photo": scale_photo_bytes,
                "Output_photo": output_photo_bytes,
            }
        )

    add_c, rem_c, save_c, complete_c = st.columns([1, 1, 1.6, 1.8])
    if add_c.button(
        "Add output line", key=f"{key_prefix}_{bid}_add", disabled=locked
    ):
        st.session_state[state_key] = n_lines + 1
        st.rerun()
    if (
        rem_c.button(
            "Remove last line", key=f"{key_prefix}_{bid}_rem", disabled=locked
        )
        and n_lines > 1
    ):
        st.session_state[state_key] = n_lines - 1
        st.rerun()
    save_label = "Save history correction" if output_completed else "Save outputs"
    if save_c.button(
        save_label,
        type="primary",
        key=f"{key_prefix}_{bid}_save",
        disabled=locked,
    ):
        if missing_stand:
            lines = ", ".join(str(n) for n in missing_stand)
            st.error(
                f"Stand weight is missing on output line {lines}. Enter the stand "
                "weight — use **0** if the metal was weighed without a stand."
            )
        else:
            try:
                db.save_batch_outputs(
                    bid, collected, allow_completed=output_completed
                )
                st.session_state.pop(loaded_key, None)
                st.success(
                    f"Saved history correction for **{bid}**."
                    if output_completed
                    else f"Saved outputs for **{bid}**."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    if complete_c.button(
        "Mark Output as Completed",
        type="primary",
        key=f"{key_prefix}_{bid}_complete_output",
        disabled=locked or output_completed or bool(output_gaps),
        help=(
            "Locks output entry for this heat and posts it to Finished Goods "
            "Inventory. Requires at least one output line."
        ),
    ):
        try:
            db.complete_batch_output(bid)
            st.success(
                f"Output for **{bid}** is **Completed** and locked. It is now "
                "posted to **Finished Goods Inventory**."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if not output_completed and output_gaps:
        st.caption(
            "**Mark Output as Completed** stays disabled until: "
            + "; ".join(output_gaps)
        )

    saved = db.get_batch_outputs(bid)
    if saved:
        show_dataframe(
            df_from_rows(_output_rows_for_table(saved)),
            highlight_avg_piece=True,
        )
        cost = db.compute_batch_production_cost(bid)
        first = saved[0]
        material_kg = first.get("cost_of_production_per_kg")
        overall_kg = first.get("cost_of_production_overall_per_kg")
        conv_rate = first.get("conversion_rate_applied")
        conv_month = first.get("conversion_expense_month")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric(
            "Charge material cost",
            f"{float(cost['input_cost_total'] or 0):,.2f}",
        )
        k2.metric(
            "Material ₹/kg",
            f"{float(material_kg):,.4f}" if material_kg is not None else "—",
        )
        k3.metric(
            "Conversion ₹/kg",
            f"{float(conv_rate):,.4f}" if conv_rate is not None else "—",
        )
        k4.metric(
            "Overall ₹/kg",
            f"{float(overall_kg):,.4f}" if overall_kg is not None else "—",
        )
        if conv_month:
            prod_date = batch.get("Production_Date")
            conv_key = (
                conv_month.isoformat()[:7]
                if hasattr(conv_month, "isoformat")
                else str(conv_month)[:7]
            )
            prod_key = (
                prod_date.isoformat()[:7]
                if hasattr(prod_date, "isoformat")
                else str(prod_date or "")[:7]
            )
            if prod_key and conv_key == prod_key:
                conv_note = (
                    f"Overall adds the production-month conversion rate "
                    f"(**{format_ui_date(conv_month)}**)."
                )
            else:
                conv_note = (
                    "Production-month conversion was not on file yet, "
                    f"so the previous available month was used "
                    f"(**{format_ui_date(conv_month)}**)."
                )
            st.caption(
                "Material ₹/kg is total charge cost ÷ total output kg. "
                f"{conv_note} "
                "The same unit costs are stored on every output line of this batch."
            )
        else:
            st.caption(
                "Material ₹/kg is total charge cost ÷ total output kg. "
                "No Cost of Conversion row exists for the production month or any "
                "earlier month, so overall cost is material only. Save conversion "
                "rates and these rows will pick up the production month, or the "
                "previous month if that month is not in yet."
            )


@st.dialog("All elements — chemical composition (%)", width="large")
def dialog_all_element_percentages(
    state_key: str,
    defaults: dict[str, float] | None = None,
    sync_keys: dict[str, str] | None = None,
    elements: list[dict] | None = None,
    caption: str | None = None,
) -> None:
    """Popup to enter percentages for Element_Master rows."""
    defaults = defaults or {}
    stored = st.session_state.get(state_key) or {}
    elements = elements if elements is not None else db.list_elements()
    st.caption(
        caption
        or (
            f"Enter assay / chemistry % for all **{len(elements)}** elements "
            "in Element_Master (Serial_no order). Click **Apply & close** to use these values."
        )
    )
    values: dict[str, float | None] = {}
    cols = st.columns(4)
    for i, el in enumerate(elements):
        sym = el["Element_Symbol"]
        with cols[i % 4]:
            values[sym] = empty_percent_input(
                f"{sym} %",
                key=f"{state_key}_dlg_{sym}",
                default=stored.get(sym, defaults.get(sym)),
                step=CHEM_PERCENT_STEP,
                format=CHEM_PERCENT_FORMAT,
                help=el["Element_Name"],
            )
    b1, b2 = st.columns(2)
    with b1:
        apply = st.button("Apply & close", type="primary", use_container_width=True)
    with b2:
        cancel = st.button("Cancel", use_container_width=True)
    if apply:
        st.session_state[state_key] = {
            sym: val for sym, val in values.items() if val
        }
        if sync_keys:
            for sym, widget_key in sync_keys.items():
                st.session_state[widget_key] = _optional_percent(values.get(sym))
        st.rerun()
    if cancel:
        st.rerun()


UI_DATE_FORMAT = "%d-%b-%Y"  # 23-AUG-2026
UI_DATE_WIDGET_FORMAT = "DD-MM-YYYY"


def _is_blank_date(value: object) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _column_is_datetime(name: object) -> bool:
    lowered = str(name).lower()
    return lowered.endswith("datetime") or lowered.endswith("_time") or lowered.endswith(" time")


def _column_is_date(name: object) -> bool:
    lowered = str(name).lower()
    return (
        lowered.endswith("date")
        or lowered.endswith("expense_month")
        or _column_is_datetime(name)
    )


def parse_any_date(value: object) -> date | datetime | None:
    """Parse ISO, widget, or DD-MON-YYYY values into a date or datetime."""
    if _is_blank_date(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    text = str(value).strip()
    if not text or text in {"—", "-", "None", "NaT", "nat"}:
        return None
    date_part, time_part = text, ""
    if "T" in text:
        date_part, time_part = text.split("T", 1)
    elif " " in text and not text[2:3] == " ":
        date_part, time_part = text.split(" ", 1)
    time_part = time_part.replace("Z", "").split(".")[0]
    parsed_date = None
    if len(date_part) >= 11 and date_part[2] == "-" and date_part[6] == "-":
        bits = date_part[:11].split("-")
        try:
            parsed_date = datetime.strptime(
                f"{bits[0]}-{bits[1].title()}-{bits[2]}", UI_DATE_FORMAT
            ).date()
        except ValueError:
            parsed_date = None
    if parsed_date is None:
        try:
            parsed_date = date.fromisoformat(date_part[:10])
        except ValueError:
            return None
    if time_part:
        try:
            parsed_time = datetime.strptime(time_part[:8], "%H:%M:%S").time()
        except ValueError:
            try:
                parsed_time = datetime.strptime(time_part[:5], "%H:%M").time()
            except ValueError:
                return parsed_date
        return datetime.combine(parsed_date, parsed_time)
    return parsed_date


def format_ui_date(value: object, *, with_time: bool | None = None, empty: str = "") -> str:
    """Format a stored date for the UI as DD-MON-YYYY (optional time)."""
    parsed = parse_any_date(value)
    if parsed is None:
        return empty if _is_blank_date(value) else str(value)
    include_time = with_time
    if include_time is None:
        include_time = isinstance(parsed, datetime) and (
            parsed.hour or parsed.minute or parsed.second
        )
    if isinstance(parsed, datetime) and include_time:
        return parsed.strftime(f"{UI_DATE_FORMAT} %H:%M:%S").upper()
    day = parsed.date() if isinstance(parsed, datetime) else parsed
    return day.strftime(UI_DATE_FORMAT).upper()


def to_storage_date(value: object, *, with_time: bool = False) -> str | None:
    """Convert a UI or ISO date back to the ISO string stored in the database."""
    parsed = parse_any_date(value)
    if parsed is None:
        return None
    if with_time:
        if isinstance(parsed, datetime):
            return parsed.isoformat(timespec="seconds")
        return datetime.combine(parsed, datetime.min.time()).isoformat(timespec="seconds")
    if isinstance(parsed, datetime):
        return parsed.date().isoformat()
    return parsed.isoformat()


def format_df_dates(data: pd.DataFrame) -> pd.DataFrame:
    if data is None or data.empty:
        return data
    out = data.copy()
    for col in out.columns:
        if not _column_is_date(col):
            continue
        with_time = _column_is_datetime(col)
        out[col] = out[col].map(
            lambda v, t=with_time: None if _is_blank_date(v) else format_ui_date(v, with_time=t)
        )
    return out


def show_dataframe(data, **kwargs):
    """Display a table with date columns as DD-MON-YYYY."""
    highlight_avg = kwargs.pop("highlight_avg_piece", False)
    highlight_recovery = kwargs.pop("highlight_recovery_variance", False)
    if isinstance(data, pd.DataFrame):
        if highlight_avg:
            data = _add_avg_piece_column(data)
        data = format_df_dates(data)
        if highlight_avg:
            data = _style_avg_piece_column(data)
        elif highlight_recovery:
            data = _style_recovery_variance_column(data)
    kwargs.setdefault("use_container_width", True)
    kwargs.setdefault("hide_index", True)
    return st.dataframe(data, **kwargs)


def ui_date_input(label: str, value="today", **kwargs):
    kwargs.setdefault("format", UI_DATE_WIDGET_FORMAT)
    return st.date_input(label, value=value, **kwargs)


def _parse_master_date(value: object) -> date:
    parsed = parse_any_date(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    if isinstance(parsed, date):
        return parsed
    return date.today()


def merge_percent_composition(
    page_values: dict[str, float | None],
    full_state_key: str,
) -> dict[str, float]:
    """Merge main-page entry values over an optional full Element_Master dialog map."""
    merged: dict[str, float] = {}
    for source in (st.session_state.get(full_state_key) or {}, page_values):
        for sym, val in source.items():
            num = _optional_percent(val)
            if num is not None:
                merged[sym] = num
            elif sym in merged:
                del merged[sym]
    return merged


_IST = timezone(timedelta(hours=5, minutes=30))


def _render_dashboard_refresh_bar(*, key_prefix: str) -> None:
    """Last-refreshed time + a manual refresh button for a materialized-view page.

    The views refresh on their own every 4 hours (db.start_dashboard_refresh_
    scheduler, started once per process), so a page load normally does not
    trigger a refresh. The staleness check here (db.dashboard_data_is_stale)
    is only a safety net for the rare case the background scheduler has
    stalled; the button underneath is for refreshing on demand regardless
    of age. No-op on SQLite, which has no materialized views to refresh.
    """
    if not db.IS_POSTGRES:
        return
    if db.dashboard_data_is_stale():
        with st.spinner("Refreshing dashboard data…"):
            db.refresh_dashboard_materialized_views()

    bar_l, bar_r = st.columns([4, 1])
    with bar_l:
        last = db.get_dashboard_last_refreshed()
        if last:
            st.caption(
                f"Data last refreshed: **{format_ui_date(last.astimezone(_IST), with_time=True)} IST**"
            )
        else:
            st.caption("Data last refreshed: —")
    with bar_r:
        if st.button("Refresh now", key=f"{key_prefix}_refresh_now"):
            with st.spinner("Refreshing dashboard data…"):
                db.refresh_dashboard_materialized_views()
            st.rerun()

