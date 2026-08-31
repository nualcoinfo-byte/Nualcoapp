"""
Nualco — Secondary Aluminum Alloy Production Tracker
Streamlit application for batch, chemistry, and yield tracking.
Runs on Neon Postgres (DATABASE_URL) with local SQLite as fallback.
"""

from __future__ import annotations

import base64
import html
import os
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "nualco_logo.png"
ISO_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "iso_9001_2015.png"
_BRAND_ORANGE = "#F15A22"
_BRAND_INK = "#1A1A1A"

st.set_page_config(
    page_title="Nualco Alloy Tracker",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject Streamlit Cloud secrets into the environment BEFORE importing the
# database module, which binds SQLAlchemy's engine at import time.
if st.session_state.get("use_sqlite"):
    os.environ["NUALCO_FORCE_SQLITE"] = "1"
else:
    try:
        if "DATABASE_URL" in st.secrets:
            os.environ["DATABASE_URL"] = str(st.secrets["DATABASE_URL"]).strip().strip('"').strip("'")
            db_url = os.environ["DATABASE_URL"]
            # A leftover Neon DATABASE_URL_UNPOOLED must not override Supabase.
            if "neon.tech" not in db_url.lower() and "DATABASE_URL_UNPOOLED" in os.environ:
                os.environ.pop("DATABASE_URL_UNPOOLED", None)
    except Exception:
        pass

import database as db  # noqa: E402
import importlib

# Streamlit keeps imported modules in memory. Reload database.py when the
# file on disk is newer than the copy currently loaded in this process.
_db_mtime = Path(db.__file__).resolve().stat().st_mtime
if getattr(db, "_LOADED_MTIME", None) != _db_mtime:
    db = importlib.reload(db)
    db._LOADED_MTIME = _db_mtime
    st.cache_resource.clear()

# Reload only when switching Neon <-> SQLite. Reloading on every rerun
# drops the engine and forces a new handshake each click.
_want_sqlite = bool(st.session_state.get("use_sqlite"))
if _want_sqlite:
    if getattr(db, "IS_POSTGRES", False):
        os.environ["NUALCO_FORCE_SQLITE"] = "1"
        db = importlib.reload(db)
    st.session_state["_offline_sqlite"] = True
elif st.session_state.pop("_offline_sqlite", False):
    os.environ.pop("NUALCO_FORCE_SQLITE", None)
    db = importlib.reload(db)

# ── Theme tweaks ─────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <style>
    .block-container {{ padding-top: 1.2rem; max-width: 1200px; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.6rem; }}
    .yield-ok {{ color: #1b7a3d; font-weight: 700; font-size: 1.4rem; }}
    .yield-bad {{ color: #c62828; font-weight: 700; font-size: 1.4rem; }}
    .avg-piece-label {{
        font-size: 0.875rem; margin: 0 0 0.2rem 0; color: {_BRAND_INK};
    }}
    .avg-piece {{ font-size: 1.35rem; font-weight: 600; margin: 0; color: {_BRAND_INK}; }}
    .avg-piece-bad {{ font-size: 1.35rem; font-weight: 700; margin: 0; color: #c62828; }}
    .chem-spec {{ font-size: 0.8rem; color: {_BRAND_INK}; opacity: 0.75; margin: 0 0 0.35rem 0; }}
    .chem-spec-bad {{ font-size: 0.8rem; color: #c62828; font-weight: 700; margin: 0 0 0.35rem 0; }}
    .batch-id {{ font-family: ui-monospace, monospace; font-weight: 700; }}
    [data-testid="stSidebar"] {{
        border-right: 3px solid {_BRAND_ORANGE};
    }}
    [data-testid="stSidebar"] .stCaption {{
        color: {_BRAND_INK};
        opacity: 0.75;
    }}
    h1 {{
        color: {_BRAND_INK};
        border-bottom: 2px solid {_BRAND_ORANGE};
        padding-bottom: 0.35rem;
    }}
    [data-testid="stSidebar"] .stRadio {{
        margin-bottom: 0.15rem;
    }}
    [data-testid="stSidebar"] .stRadio [data-testid="stWidgetLabel"] {{
        display: none;
    }}
    .nav-section {{
        color: {_BRAND_ORANGE};
        font-weight: 700;
        font-size: 0.72rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        margin: 0.85rem 0 0.25rem 0;
    }}
    div[class*="st-key-prod_crew_fields"] [data-testid="stVerticalBlock"] {{
        gap: 0.35rem;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def bootstrap() -> str:
    """Open the database. Full init_db() is skipped on Neon (it can hang)."""
    if db.IS_POSTGRES:
        try:
            db._ensure_packing_list_ready()
            db._ensure_company_ready()
            return "neon"
        except Exception as exc:
            st.session_state["_neon_init_error"] = str(exc)
            db.switch_to_sqlite()
            db.init_db()
            return "sqlite"
    db.init_db()
    return "sqlite"


_db_mode = bootstrap()
if _db_mode == "sqlite":
    st.session_state["use_sqlite"] = True
    st.session_state["_offline_sqlite"] = True
    os.environ["NUALCO_FORCE_SQLITE"] = "1"


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


def _style_avg_piece_column(data: pd.DataFrame):
    """Highlight product-alloy piece averages outside 5.6–6.1 kg in red."""
    if data is None or data.empty or "Avg_piece_kg" not in data.columns:
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

    return data.style.apply(_color, axis=0).format(
        {"Avg_piece_kg": _fmt_avg},
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


CHEM_PERCENT_STEP = 0.0001
CHEM_PERCENT_FORMAT = "%.4f"


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
        "They have no spec."
    )

    collected: list[dict] = []
    for idx in range(n_lines):
        st.markdown(f"**Output line {idx + 1}**")
        c1, c2, c3 = st.columns([2.4, 1.2, 2.4])
        with c1:
            alloy_label = st.selectbox(
                "Output alloy *",
                options=labels,
                key=_k("alloy", idx),
            )
        with c2:
            pieces = empty_int_input(
                "Pieces",
                key=_k("pcs", idx),
                help="Whole number of pieces. No decimals.",
            )
        with c3:
            notes = st.text_input("Notes", key=_k("notes", idx))

        w1, w2, w3, w4 = st.columns(4)
        with w1:
            scale_w = empty_percent_input(
                "Weighment scale weight (kg) *",
                key=_k("scale", idx),
                max_value=None,
                step=1.0,
            )
            wsp_open_key = _k("wsp_open", idx)
            if st.button(
                "📷 Weighment scale photo",
                key=_k("wsp_btn", idx),
                help="Open camera or choose a photo from the phone gallery",
                use_container_width=True,
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
        with w2:
            stand_w = empty_percent_input(
                "Stand weight (kg)",
                key=_k("stand", idx),
                max_value=None,
                step=1.0,
            )
        scale_val = float(scale_w or 0)
        stand_val = float(stand_w or 0)
        net_w = max(scale_val - stand_val, 0.0) if scale_val > 0 else 0.0
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
                "Stand_weight": stand_val,
                "Pieces": pieces,
                "Notes": notes,
                "Weighment_scale_photo": scale_photo_bytes,
                "Output_photo": output_photo_bytes,
            }
        )

    add_c, rem_c, save_c = st.columns([1, 1, 3])
    if add_c.button("Add output line", key=f"{key_prefix}_{bid}_add"):
        st.session_state[state_key] = n_lines + 1
        st.rerun()
    if rem_c.button("Remove last line", key=f"{key_prefix}_{bid}_rem") and n_lines > 1:
        st.session_state[state_key] = n_lines - 1
        st.rerun()
    if save_c.button("Save outputs", type="primary", key=f"{key_prefix}_{bid}_save"):
        try:
            db.save_batch_outputs(bid, collected)
            st.session_state.pop(loaded_key, None)
            st.success(f"Saved outputs for **{bid}**.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

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


def format_cert_print_date(value: object, empty: str = "—") -> str:
    """Formal certificate dates match the QMS form: DD-MM-YYYY."""
    parsed = parse_any_date(value)
    if parsed is None:
        return empty if _is_blank_date(value) else str(value)
    day = parsed.date() if isinstance(parsed, datetime) else parsed
    return day.strftime("%d-%m-%Y")


def _image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _certificate_logo_data_uri() -> str:
    for path in (LOGO_PATH, LOGO_PATH.with_suffix(".jpg")):
        uri = _image_data_uri(path)
        if uri:
            return uri
    return ""


def _iso_logo_data_uri() -> str:
    return _image_data_uri(ISO_LOGO_PATH)


def _company_letterhead(company: dict) -> dict[str, str]:
    street = " ".join(str(company.get("Address") or "").strip().rstrip(".").split())
    city = str(company.get("City") or "").strip()
    pin = str(company.get("Pincode") or "").strip()
    pin_fmt = f"{pin[:3]} {pin[3:]}" if pin.isdigit() and len(pin) == 6 else pin
    if city and pin_fmt:
        place = f"{city} - {pin_fmt}"
    else:
        place = city or pin_fmt
    address = ", ".join(part for part in (street, place) if part)
    if address:
        address = address.rstrip(".") + "."
    phone = str(company.get("Phone1") or "").strip()
    email = str(company.get("Email1") or "").strip()
    contact_bits = []
    if phone:
        contact_bits.append(f"Phone : {phone}")
    if email:
        contact_bits.append(f"Email : {email}")
    gst = str(company.get("GST") or "").strip()
    return {
        "name": str(company.get("Company_name") or "Nualco Private Limited").strip(),
        "address": address,
        "contact": "  ".join(contact_bits),
        "gst": f"GSTIN : {gst}" if gst else "",
    }


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


def row_dates_to_storage(row: dict) -> dict:
    out = dict(row)
    for key, value in list(out.items()):
        if _column_is_date(key) and not _is_blank_date(value):
            out[key] = to_storage_date(value, with_time=_column_is_datetime(key))
    return out


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
    if isinstance(data, pd.DataFrame):
        if highlight_avg:
            data = _add_avg_piece_column(data)
        data = format_df_dates(data)
        if highlight_avg:
            data = _style_avg_piece_column(data)
    kwargs.setdefault("use_container_width", True)
    kwargs.setdefault("hide_index", True)
    return st.dataframe(data, **kwargs)


def ui_date_input(label: str, value="today", **kwargs):
    kwargs.setdefault("format", UI_DATE_WIDGET_FORMAT)
    return st.date_input(label, value=value, **kwargs)


def ui_datetime_input(label: str, **kwargs):
    kwargs.setdefault("format", UI_DATE_WIDGET_FORMAT)
    return st.datetime_input(label, **kwargs)


def _furnace_form_key(furnace: str, name: str) -> str:
    """Session/widget key scoped to one furnace so drafts never mix."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(furnace))
    return f"pb_{safe}_{name}"


def _furnace_key_prefix(furnace: str) -> str:
    return _furnace_form_key(furnace, "")


def _production_batch_option_label(batch_id: object, heat_no: object) -> str:
    bid = str(batch_id or "").strip()
    heat = str(heat_no or "").strip()
    if bid and heat:
        return f"{bid} - {heat}"
    return bid


def _is_ephemeral_widget_key(key: object) -> bool:
    """True for widgets Streamlit forbids assigning via st.session_state."""
    if not isinstance(key, str):
        return False
    return any(
        marker in key
        for marker in (
            "_btn_",
            "_cam_",
            "_file_",
            "_add_charge",
            "_rem_charge",
            "_save_batch",
            "_create_batch",
            "_complete_batch",
            "_open_all_elements",
        )
    ) or key.endswith(("_btn", "_cam", "_file"))


def _snapshot_furnace_widgets(furnace: str) -> None:
    """Keep a durable copy of one furnace's widgets.

    Streamlit deletes unused widget keys at the end of a run, so switching
    furnace would otherwise drop an in-progress draft.
    """
    if not furnace or furnace == "— select furnace —":
        return
    prefix = _furnace_key_prefix(furnace)
    store = st.session_state.setdefault("batch_drafts", {})
    store[str(furnace)] = {
        k: st.session_state[k]
        for k in list(st.session_state.keys())
        if isinstance(k, str)
        and k.startswith(prefix)
        and not _is_ephemeral_widget_key(k)
    }


def _restore_furnace_widgets(furnace: str) -> None:
    """Re-apply a stored draft for this furnace.

    Do not delete current ephemeral keys (buttons, cameras, file pickers).
    Streamlit stores a button click in session state for this run; wiping
    those keys here would swallow Save / Add charge / photo clicks.
    """
    saved = (st.session_state.get("batch_drafts") or {}).get(str(furnace)) or {}
    for k, v in saved.items():
        if _is_ephemeral_widget_key(k):
            continue
        if k not in st.session_state:
            st.session_state[k] = v


def _remember_ephemeral_clicks(furnace: str) -> None:
    """Copy this-run button clicks aside before any other session-state edits."""
    prefix = _furnace_key_prefix(furnace)
    remembered = set(st.session_state.get("_pb_click_keys") or [])
    for k in list(st.session_state.keys()):
        if (
            isinstance(k, str)
            and k.startswith(prefix)
            and _is_ephemeral_widget_key(k)
            and st.session_state.get(k) is True
        ):
            remembered.add(k)
    st.session_state["_pb_click_keys"] = remembered


def _consume_ephemeral_click(key: str) -> bool:
    remembered = set(st.session_state.get("_pb_click_keys") or [])
    if key not in remembered:
        return False
    remembered.discard(key)
    st.session_state["_pb_click_keys"] = remembered
    return True


def _button_clicked(clicked: bool, key: str) -> bool:
    """True if Streamlit reported the click or we preserved it earlier this run."""
    return _consume_ephemeral_click(key) or bool(clicked)


def _on_working_furnace_change() -> None:
    prev = st.session_state.get("_pb_prev_furnace")
    current = st.session_state.get("batch_working_furnace")
    if prev and prev != current:
        _snapshot_furnace_widgets(prev)
    st.session_state["_pb_prev_furnace"] = current


def _parse_master_date(value: object) -> date:
    parsed = parse_any_date(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    if isinstance(parsed, date):
        return parsed
    return date.today()


def _option_label(options: dict, value: object) -> str:
    if value in (None, ""):
        return ""
    for label, stored in options.items():
        if stored == value or str(stored) == str(value):
            return label
    return ""


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


def _as_widget_datetime(value: object) -> datetime | None:
    parsed = parse_any_date(value)
    if parsed is None:
        return None
    if isinstance(parsed, datetime):
        return parsed
    if isinstance(parsed, date):
        return datetime.combine(parsed, datetime.min.time())
    return None


def _clear_production_entry_fields(furnace: str, sample_blank: str) -> None:
    """Drop charge, degassing, sample, and chemistry widgets for this furnace.

    Used when the working production batch changes so a new unsaved heat does
    not keep the previous heat’s entries.
    """
    prefix = _furnace_key_prefix(furnace)
    pk = lambda name: _furnace_form_key(furnace, name)

    indices: set[int] = {0}
    drafts = st.session_state.setdefault("charge_lines_by_furnace", {})
    for i in range(max(1, len(drafts.get(furnace) or []))):
        indices.add(i)
    for k in list(st.session_state.keys()):
        if not isinstance(k, str) or not k.startswith(prefix + "mat_"):
            continue
        try:
            indices.add(int(k[len(prefix + "mat_") :]))
        except ValueError:
            continue

    # Selectboxes keep their last choice if the key is only deleted. Assign the
    # blank option before the widgets render, and refresh the furnace draft so
    # restore cannot put the previous heat’s material/trolley back.
    for idx in indices:
        st.session_state[pk(f"mat_{idx}")] = ""
        st.session_state[pk(f"lot_{idx}")] = ""
        st.session_state[pk(f"trolley_{idx}")] = ""
        st.session_state[pk(f"trolley_w_{idx}")] = 0.0
        st.session_state[pk(f"_prev_trolley_label_{idx}")] = ""
        st.session_state[pk(f"scale_w_{idx}")] = None
        st.session_state[pk(f"wt_{idx}")] = 0.0
        st.session_state[pk(f"ln_{idx}")] = ""
        for extra in (
            f"wsp_open_{idx}",
            f"wsp_bytes_{idx}",
            f"inp_open_{idx}",
            f"inp_bytes_{idx}",
        ):
            st.session_state.pop(pk(extra), None)

    for k in list(st.session_state.keys()):
        if not isinstance(k, str) or not k.startswith(prefix):
            continue
        rest = k[len(prefix) :]
        if rest in {"pending_charges", "full_chem", "create_error"} or rest.startswith(
            "bchem_"
        ):
            st.session_state.pop(k, None)

    drafts[furnace] = [{"material": "", "lot_id": None, "weight": 0.0, "notes": ""}]

    st.session_state[pk("degassing_time")] = ""
    st.session_state[pk("sampled_pcs")] = None
    st.session_state[pk("defect_pcs")] = None
    st.session_state[pk("top_sample")] = sample_blank
    st.session_state[pk("middle_sample")] = sample_blank
    st.session_state[pk("bottom_sample")] = sample_blank
    st.session_state[pk("vacum_sample")] = sample_blank
    st.session_state[pk("top_sample_remarks")] = ""
    st.session_state[pk("middle_sample_remarks")] = ""
    st.session_state[pk("bottom_sample_remarks")] = ""
    st.session_state[pk("top_sample_dt")] = None
    st.session_state[pk("middle_sample_dt")] = None
    st.session_state[pk("bottom_sample_dt")] = None
    st.session_state[pk("full_chem")] = {}
    st.session_state[pk("notes")] = ""
    _snapshot_furnace_widgets(furnace)


def _hydrate_production_batch_form(
    furnace: str,
    batch: dict | None,
    *,
    alloy_labels: dict[str, object],
    sample_blank: str,
) -> None:
    """Load a saved batch into furnace-scoped widgets when the working batch changes."""
    pk = lambda name: _furnace_form_key(furnace, name)
    loaded_key = pk("_hydrated_batch_id")
    token = str(batch["Batch_ID"]) if batch else f"new:{furnace}"
    previous = st.session_state.get(loaded_key)
    if previous == token:
        return
    st.session_state[loaded_key] = token
    # Keep a restored in-progress *new* draft on first paint of this furnace.
    # Any other working-batch change must drop the previous heat’s entries.
    if previous is not None or batch is not None:
        _clear_production_entry_fields(furnace, sample_blank)
    if not batch:
        return

    melt = batch.get("Melt_No")
    try:
        melt_i = int(melt)
    except (TypeError, ValueError):
        melt_i = None
    if melt_i in db.MELT_NOS:
        st.session_state[pk("melt_no")] = melt_i

    parsed_date = parse_any_date(batch.get("Production_Date"))
    if isinstance(parsed_date, datetime):
        st.session_state[pk("prod_date")] = parsed_date.date()
    elif isinstance(parsed_date, date):
        st.session_state[pk("prod_date")] = parsed_date

    shift = batch.get("Shift")
    if shift in db.SHIFTS:
        st.session_state[pk("shift")] = shift

    if batch.get("Melting_team"):
        st.session_state[pk("melter")] = batch["Melting_team"]
    if batch.get("Production_supervisor"):
        st.session_state[pk("supervisor")] = batch["Production_supervisor"]
    st.session_state[pk("notes")] = batch.get("Notes") or ""

    alloy_id = batch.get("Alloy_id")
    alloy_label = "— none —"
    if alloy_id not in (None, ""):
        for label, stored in alloy_labels.items():
            if stored == alloy_id or str(stored) == str(alloy_id):
                alloy_label = label
                break
    st.session_state[pk("alloy")] = alloy_label

    st.session_state[pk("degassing_time")] = batch.get("Degassing_time") or ""
    sampled = batch.get("Sampled_pcs")
    try:
        st.session_state[pk("sampled_pcs")] = (
            float(sampled) if sampled not in (None, "") else None
        )
    except (TypeError, ValueError):
        st.session_state[pk("sampled_pcs")] = None
    defect = batch.get("Defect_pcs")
    try:
        st.session_state[pk("defect_pcs")] = (
            float(defect) if defect not in (None, "") else None
        )
    except (TypeError, ValueError):
        st.session_state[pk("defect_pcs")] = None

    def _sample_choice(value: object) -> str:
        text = str(value or "").strip()
        return text if text in db.SAMPLE_OK_STATUS else sample_blank

    st.session_state[pk("top_sample")] = _sample_choice(batch.get("Top_Sample"))
    st.session_state[pk("middle_sample")] = _sample_choice(batch.get("Middle_Sample"))
    st.session_state[pk("bottom_sample")] = _sample_choice(batch.get("Bottom_Sample"))
    st.session_state[pk("vacum_sample")] = _sample_choice(batch.get("Vacum_Sample"))
    st.session_state[pk("top_sample_remarks")] = batch.get("Top_Sample_Remarks") or ""
    st.session_state[pk("middle_sample_remarks")] = (
        batch.get("Middle_Sample_Remarks") or ""
    )
    st.session_state[pk("bottom_sample_remarks")] = (
        batch.get("Bottom_Sample_Remarks") or ""
    )
    st.session_state[pk("top_sample_dt")] = _as_widget_datetime(
        batch.get("Top_Sample_datetime")
    )
    st.session_state[pk("middle_sample_dt")] = _as_widget_datetime(
        batch.get("Middle_Sample_datetime")
    )
    st.session_state[pk("bottom_sample_dt")] = _as_widget_datetime(
        batch.get("Bottom_Sample_datetime")
    )

    full: dict[str, float] = {}
    for row in db.get_batch_chemistry(batch["Batch_ID"]):
        sym = row.get("Element_symbol")
        if not sym:
            continue
        try:
            val = float(row.get("Percentage"))
        except (TypeError, ValueError):
            continue
        full[str(sym)] = val
        if str(sym) != "SF":
            st.session_state[pk(f"bchem_{sym}")] = val if val > 0 else None
    st.session_state[pk("full_chem")] = full

    drafts = st.session_state.setdefault("charge_lines_by_furnace", {})
    drafts[furnace] = [{"material": "", "lot_id": None, "weight": 0.0, "notes": ""}]


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


def _trolley_css_color(colour: str | None) -> str | None:
    """Map Trolley_Master.Colour text to a CSS colour (hex or named)."""
    if colour is None:
        return None
    raw = str(colour).strip()
    if not raw:
        return None
    if raw.startswith("#") and len(raw) in (4, 7, 9):
        return raw
    named = {
        "red": "#E53935",
        "blue": "#1E88E5",
        "green": "#43A047",
        "yellow": "#FDD835",
        "orange": "#FB8C00",
        "purple": "#8E24AA",
        "pink": "#D81B60",
        "brown": "#6D4C41",
        "black": "#212121",
        "white": "#FAFAFA",
        "grey": "#757575",
        "gray": "#757575",
        "silver": "#B0BEC5",
        "gold": "#F9A825",
        "cyan": "#00ACC1",
        "teal": "#00897B",
        "navy": "#1565C0",
        "maroon": "#C62828",
        "violet": "#7E57C2",
        "lime": "#C0CA33",
    }
    key = raw.lower()
    if key in named:
        return named[key]
    # Allow CSS colour names / values already stored in the master
    return raw


# ═══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════════
if LOGO_PATH.exists():
    st.sidebar.image(str(LOGO_PATH), use_container_width=True)
else:
    st.sidebar.title("Nualco")
st.sidebar.caption("Secondary Aluminum Alloy Manufacturing")

NAV_SECTIONS: list[tuple[str, list[str]]] = [
    ("Overview", ["Dashboard"]),
    (
        "Purchasing & inventory",
        [
            "Raw Material Logging",
            "Raw Material Inventory",
            "Purchase Orders",
            "All Purchase Orders",
            "Finished Goods Inventory",
            "Packing List",
            "Test Certificate",
            "Bill of Materials",
        ],
    ),
    (
        "Production",
        [
            "Production Batch & Chemistry",
            "Batch Output",
            "Production Batches",
            "Material Recovery & Yield",
        ],
    ),
    (
        "Utilities & conversion",
        [
            "Furnace Oil Purchase",
            "Furnace Oil Consumption",
            "Electricity Consumption",
            "Cost of Conversion",
        ],
    ),
    (
        "Masters",
        [
            "Company",
            "Customers",
            "Vendors",
            "Raw Material Master",
            "Alloys",
            "Furnaces",
            "Crucibles",
            "Melters",
            "Trolleys",
        ],
    ),
    ("Tools", ["Data Browser", "Masters Overview"]),
]
ADMIN_NAV_SECTION = "Admin"
ADMIN_PAGE_CANCEL_ISSUED = "Cancel issued certificate"
ADMIN_NAV_PAGES = [ADMIN_PAGE_CANCEL_ISSUED]
_BASE_NAV_PAGES = [page for _section, pages in NAV_SECTIONS for page in pages]
_ALL_NAV_PAGES = _BASE_NAV_PAGES + ADMIN_NAV_PAGES


def _nav_sections(*, include_admin: bool) -> list[tuple[str, list[str]]]:
    sections = list(NAV_SECTIONS)
    if include_admin:
        sections.append((ADMIN_NAV_SECTION, list(ADMIN_NAV_PAGES)))
    return sections


def _on_nav_section(section: str) -> None:
    chosen = st.session_state.get(f"nav_radio_{section}")
    if not chosen:
        return
    st.session_state.nav_page = chosen
    for other, _pages in _nav_sections(include_admin=True):
        if other != section:
            st.session_state[f"nav_radio_{other}"] = None


st.sidebar.divider()
users = db.list_access_users()
if users:
    if "acting_user" not in st.session_state or st.session_state.acting_user not in users:
        st.session_state.acting_user = users[0]
    acting = st.sidebar.selectbox(
        "Last updated by",
        users,
        index=users.index(st.session_state.acting_user),
        help="Stamped on master-table saves as Last_updated_by.",
    )
    st.session_state.acting_user = acting
    db.set_acting_user(acting)
else:
    db.set_acting_user("system")
    st.sidebar.caption("Last updated by: system (no Access_matrix users)")

is_admin = db.is_admin_user()
nav_sections = _nav_sections(include_admin=is_admin)

if st.session_state.get("nav_page") not in _ALL_NAV_PAGES:
    st.session_state.nav_page = "Dashboard"
if st.session_state.get("nav_page") in ADMIN_NAV_PAGES and not is_admin:
    st.session_state.nav_page = "Dashboard"

for section, pages in nav_sections:
    st.sidebar.markdown(
        f'<div class="nav-section">{html.escape(section)}</div>',
        unsafe_allow_html=True,
    )
    key = f"nav_radio_{section}"
    current = st.session_state.nav_page
    if current in pages:
        index = pages.index(current)
    else:
        index = None
    st.sidebar.radio(
        section,
        pages,
        index=index,
        key=key,
        on_change=_on_nav_section,
        args=(section,),
        label_visibility="collapsed",
    )

PAGE = st.session_state.nav_page

st.sidebar.markdown(
    f"**Yield target:** {db.YIELD_TARGET_PCT:.0f}%  \n"
    f"**DB:** `{db.DB_LABEL}`"
)
if st.session_state.get("use_sqlite") or os.environ.get("NUALCO_FORCE_SQLITE"):
    st.sidebar.warning(
        "Offline SQLite mode. Rows you save stay on this PC and are not "
        "written to Neon."
    )
    neon_err = st.session_state.get("_neon_init_error")
    if neon_err:
        st.sidebar.caption(f"Neon init error: {neon_err}")
    if st.sidebar.button("Reconnect to Neon"):
        st.session_state.pop("use_sqlite", None)
        st.session_state.pop("_neon_init_error", None)
        os.environ.pop("NUALCO_FORCE_SQLITE", None)
        st.cache_resource.clear()
        st.rerun()
elif not db.IS_POSTGRES:
    st.sidebar.error(
        "Not connected to the database. In Streamlit Cloud go to "
        "**Manage app → Settings → Secrets** and set:\n\n"
        '```\nDATABASE_URL = "postgresql://..."\n```\n\n'
        "Remove any leftover `DATABASE_URL_UNPOOLED` Neon URL, then reboot the app."
    )


def _packing_summary_from_saved(packing_list_id: int) -> dict | None:
    header = db.get_packing_list(packing_list_id)
    if not header:
        return None
    return {
        "packing_list_id": header.get("Packing_list_id"),
        "invoice_date": header.get("Invoice_date"),
        "invoice_number": header.get("Invoice_number") or "",
        "po_no": header.get("Customer_PO_No") or "",
        "customer_name": header.get("Customer_name") or "",
        "alloy_name": header.get("Alloy_name") or "",
        "colour_code": header.get("Colour_code") or "",
        "vehicle_no": header.get("Vehicle_no") or "",
        "status": header.get("Packing_list_status") or "",
        "batches": list(header.get("batches") or []),
    }


def _packing_summary_from_form() -> dict:
    inv_date = st.session_state.get("pl_invoice_date")
    cust_label = str(st.session_state.get("pl_cust") or "")
    customer_name = cust_label.split(" (")[0] if " (" in cust_label else cust_label
    alloy_label = str(st.session_state.get("pl_alloy") or "")
    alloy_name = alloy_label.rsplit(" (#", 1)[0] if " (#" in alloy_label else alloy_label
    alloy_id = st.session_state.get("pl_alloy_id")
    colour_code = ""
    if alloy_id not in (None, ""):
        master = db.get_alloy(alloy_id) or {}
        colour_code = master.get("Colour_code") or ""
    batches: list[dict] = []
    for bid in st.session_state.get("pl_batches") or []:
        bid = str(bid).strip()
        if not bid:
            continue
        qty = (st.session_state.get("pl_batch_qty") or {}).get(bid) or {}
        batches.append(
            {
                "Batch_ID": bid,
                "Heat_no": qty.get("Heat_no"),
                "Weight": float(qty.get("Weight") or 0),
                "Pieces": int(float(qty.get("Pieces") or 0)),
            }
        )
    return {
        "packing_list_id": st.session_state.get("pl_edit_id"),
        "invoice_date": to_storage_date(inv_date) if inv_date else None,
        "invoice_number": str(st.session_state.get("pl_invoice") or "").strip(),
        "po_no": str(st.session_state.get("pl_po") or "").strip(),
        "customer_name": customer_name,
        "alloy_name": alloy_name,
        "colour_code": colour_code,
        "vehicle_no": str(st.session_state.get("pl_vehicle") or "").strip(),
        "status": str(st.session_state.get("pl_status") or "").strip(),
        "batches": batches,
    }


def _render_packing_list_summary(summary: dict) -> None:
    st.markdown(
        f"""
        <style>
        .packing-summary-wrap {{
            border: 1px solid #ddd;
            border-radius: 6px;
            padding: 1.25rem 1.5rem;
            background: #fff;
            color: {_BRAND_INK};
        }}
        .packing-summary-title {{
            margin: 0 0 0.25rem 0;
            color: {_BRAND_INK};
            border-bottom: 2px solid {_BRAND_ORANGE};
            padding-bottom: 0.35rem;
        }}
        .packing-summary-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 0.75rem 0 0 0;
            font-size: 0.95rem;
        }}
        .packing-summary-table th,
        .packing-summary-table td {{
            border: 1px solid #bdbdbd;
            padding: 0.55rem 0.7rem;
            text-align: left;
            vertical-align: top;
        }}
        .packing-summary-meta td:first-child {{
            width: 30%;
            font-weight: 600;
            background: #f7f7f7;
        }}
        .packing-summary-batches th {{
            background: #f0f0f0;
            font-weight: 700;
        }}
        .packing-summary-batches tfoot td {{
            font-weight: 700;
            background: #fafafa;
        }}
        .packing-summary-note {{
            margin-top: 0.75rem;
            font-size: 0.85rem;
            color: #555;
        }}
        @media print {{
            [data-testid="stSidebar"],
            [data-testid="stToolbar"],
            footer,
            header,
            .no-print {{
                display: none !important;
            }}
            .block-container {{
                max-width: 100% !important;
                padding: 0.25rem !important;
            }}
            .packing-summary-wrap {{
                border: none;
                padding: 0;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    list_ref = summary.get("packing_list_id")
    title = "Packing List Summary"
    if list_ref:
        title += f" — #{list_ref}"

    inv_date = format_ui_date(summary.get("invoice_date"), empty="—")
    meta_rows = [
        ("Invoice date", inv_date),
        ("Invoice number", summary.get("invoice_number") or "—"),
        ("P.O. Number", summary.get("po_no") or "—"),
        ("Customer name", summary.get("customer_name") or "—"),
        ("Alloy name", summary.get("alloy_name") or "—"),
        ("Colour code", summary.get("colour_code") or "—"),
        ("Vehicle No", summary.get("vehicle_no") or "—"),
    ]
    if summary.get("status"):
        meta_rows.append(("Status", summary.get("status")))

    meta_html = "".join(
        f"<tr><td>{html.escape(label)}</td>"
        f"<td>{html.escape(str(value))}</td></tr>"
        for label, value in meta_rows
    )

    batches = summary.get("batches") or []
    batch_rows_html = ""
    total_w = 0.0
    total_p = 0
    for row in batches:
        bid = str(row.get("Batch_ID") or "—")
        heat = str(row.get("Heat_no") or "—")
        weight = float(row.get("Weight") or 0)
        pieces = int(float(row.get("Pieces") or 0))
        total_w += weight
        total_p += pieces
        batch_rows_html += (
            "<tr>"
            f"<td>{html.escape(bid)}</td>"
            f"<td>{html.escape(heat)}</td>"
            f"<td style='text-align:right'>{weight:,.2f}</td>"
            f"<td style='text-align:right'>{pieces:,}</td>"
            "</tr>"
        )
    if not batch_rows_html:
        batch_rows_html = (
            "<tr><td colspan='4' style='text-align:center;color:#666'>"
            "No batches selected</td></tr>"
        )
        footer_html = ""
    else:
        footer_html = (
            "<tfoot><tr>"
            "<td colspan='2'>Total</td>"
            f"<td style='text-align:right'>{total_w:,.2f}</td>"
            f"<td style='text-align:right'>{total_p:,}</td>"
            "</tr></tfoot>"
        )

    st.markdown(
        f"""
        <div class="packing-summary-wrap">
            <h2 class="packing-summary-title">{html.escape(title)}</h2>
            <table class="packing-summary-table packing-summary-meta">
                <tbody>{meta_html}</tbody>
            </table>
            <table class="packing-summary-table packing-summary-batches">
                <thead>
                    <tr>
                        <th>Batch ID</th>
                        <th>Heat No</th>
                        <th style="text-align:right">Weight (kg)</th>
                        <th style="text-align:right">Pieces</th>
                    </tr>
                </thead>
                <tbody>{batch_rows_html}</tbody>
                {footer_html}
            </table>
            <p class="packing-summary-note">
                Verify invoice details and batch quantities before dispatch.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, _c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("Back to packing list", key="pl_summary_back", type="secondary"):
            st.session_state.pop("pl_show_summary", None)
            st.session_state.pop("pl_summary_id", None)
            st.rerun()
    with c2:
        st.markdown(
            '<button class="no-print" onclick="window.print()" '
            'style="padding:0.45rem 1rem;border:1px solid #ccc;border-radius:0.5rem;'
            'background:#fff;cursor:pointer;font-size:0.9rem;">Print summary</button>',
            unsafe_allow_html=True,
        )


def _tc_sync_editor(lines: list[dict], edited) -> list[dict]:
    """Copy printed kg / heat / selection from the data editor back onto lines."""
    if edited is None:
        return lines
    by_no = {int(row.get("Line_no") or 0): row for row in edited.to_dict("records")}
    out = []
    for line in lines:
        row = by_no.get(int(line["Line_no"]), {})
        item = dict(line)
        if "Heat no" in row:
            item["Display_heat_no"] = db.certificate_display_heat_no(
                row.get("Heat no")
            )
        if "Printed kg" in row:
            item["Weight"] = float(row.get("Printed kg") or 0)
        item["_selected"] = bool(row.get("Select"))
        out.append(item)
    return out


def _pdf_safe_text(value: object) -> str:
    """Helvetica core fonts only cover Latin-1; map common Unicode first."""
    text = str(value if value is not None else "")
    text = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2026", "...")
        .replace("\u00a0", " ")
        .replace("\u2713", "Y")
        .replace("\u2714", "Y")
        .replace("\u00b7", "-")
    )
    return text.encode("latin-1", "replace").decode("latin-1")


def _certificate_pdf_bytes(payload: dict) -> bytes:
    """Build an A4 PDF of the formal test certificate."""
    from fpdf import FPDF

    orange = (241, 90, 34)
    ink = (26, 26, 26)
    fill = (247, 247, 247)
    line = (122, 122, 122)
    letterhead = _company_letterhead(payload.get("company") or {})
    heats = payload.get("heats") or []
    elements = payload.get("elements") or []
    inspection = payload.get("inspection") or []

    class _CertPDF(FPDF):
        def footer(self) -> None:
            return None

    pdf = _CertPDF(orientation="P", unit="mm", format="A4")
    # One A4 sheet: do not spill onto a second page. 10 mm keeps
    # content inside a typical desktop printer's unprintable edge.
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    pdf.set_text_color(*ink)
    pdf.set_draw_color(*line)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    left = pdf.l_margin
    y = pdf.get_y()
    side = 24

    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=left, y=y, w=20)
    if ISO_LOGO_PATH.exists():
        pdf.image(str(ISO_LOGO_PATH), x=left + page_w - 20, y=y, w=20)

    pdf.set_xy(left + side, y)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(
        page_w - 2 * side,
        6,
        _pdf_safe_text(letterhead["name"]),
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_font("Helvetica", "", 8)
    for line_text in (
        letterhead["address"],
        letterhead["contact"],
        letterhead["gst"],
    ):
        if line_text:
            pdf.set_x(left + side)
            pdf.cell(
                page_w - 2 * side,
                4,
                _pdf_safe_text(line_text),
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )
    pdf.set_y(max(pdf.get_y(), y + 24) + 2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(page_w, 7, "TEST CERTIFICATE", border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    def _fit(text: str, width: float, size: int = 7) -> str:
        pdf.set_font("Helvetica", "", size)
        out = _pdf_safe_text(text)
        while out and pdf.get_string_width(out) > width - 1.4:
            out = out[:-1]
        return out

    def _meta_row(cells: list[tuple[str, float, bool]]) -> None:
        h = 5.6
        x = left
        y0 = pdf.get_y()
        for text, width, is_label in cells:
            pdf.set_xy(x, y0)
            pdf.set_font("Helvetica", "B" if is_label else "", 8)
            if is_label:
                pdf.set_fill_color(*fill)
            shown = _pdf_safe_text(text) if is_label else _fit(str(text), width, 8)
            pdf.cell(width, h, shown, border=1, align="L", fill=is_label)
            x += width
        pdf.set_y(y0 + h)

    lw, vw = page_w * 0.16, page_w * 0.34
    meta_pairs = [
        ("Report No.", payload.get("certificate_no") or "—"),
        ("Grade", payload.get("grade") or "—"),
        ("Report Date", format_cert_print_date(payload.get("issued_date"))),
        ("Colour Code", payload.get("colour_code") or "—"),
        ("Customer", payload.get("customer_name") or "—"),
        ("Customer Reference", payload.get("cust_code") or "—"),
        ("Invoice No", payload.get("invoice_no") or "—"),
        ("Invoice Date", format_cert_print_date(payload.get("invoice_date"))),
        ("P.O No", payload.get("po_no") or "—"),
        ("P.O Date", format_cert_print_date(payload.get("po_date"))),
    ]
    for i in range(0, len(meta_pairs), 2):
        left_label, left_value = meta_pairs[i]
        right_label, right_value = meta_pairs[i + 1]
        _meta_row(
            [
                (str(left_label), lw, True),
                (str(left_value), vw, False),
                (str(right_label), lw, True),
                (str(right_value), vw, False),
            ]
        )
    doc_id = str(payload.get("document_id") or "").strip()
    total_w = f"{db._format_cert_number(payload.get('total_weight'))} Kgs"
    _meta_row(
        [
            ("Total Weight", lw, True),
            (total_w, vw, False),
            (doc_id, lw + vw, False),
        ]
    )

    def _section(title: str) -> None:
        pdf.set_fill_color(*orange)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(
            page_w,
            6,
            _pdf_safe_text(title),
            border=1,
            align="C",
            fill=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(*ink)

    _section("CHEMICAL ANALYSIS REPORT")
    heat_count = max(len(heats), 1)
    elem_w, spec_w = 42.0, 30.0
    heat_w = (page_w - elem_w - spec_w) / heat_count
    head_h = 15.0
    row_h = 5.0
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(243, 243, 243)
    pdf.rect(left, y0, elem_w, head_h, style="DF")
    pdf.rect(left + elem_w, y0, spec_w, head_h, style="DF")
    pdf.set_xy(left, y0 + 5)
    pdf.cell(elem_w, 5, "ELEMENTS", align="C")
    pdf.set_xy(left + elem_w, y0 + 5)
    pdf.cell(spec_w, 5, "SPECIFICATION %", align="C")
    for index, heat in enumerate(heats or [{"Heat_no": "—", "Kgs": "", "Pieces": ""}]):
        x = left + elem_w + spec_w + index * heat_w
        pdf.rect(x, y0, heat_w, 5, style="DF")
        pdf.rect(x, y0 + 5, heat_w, 5, style="DF")
        pdf.rect(x, y0 + 10, heat_w, 5, style="DF")
        pdf.set_xy(x, y0)
        pdf.cell(heat_w, 5, _fit(f"Heat No : {heat.get('Heat_no') or '—'}", heat_w), align="C")
        pdf.set_xy(x, y0 + 5)
        kgs = db._format_cert_number(heat.get("Kgs"))
        pcs = str(int(float(heat.get("Pieces") or 0)))
        pdf.cell(heat_w, 5, _fit(f"Kgs : {kgs}   Pcs : {pcs}", heat_w), align="C")
        pdf.set_xy(x, y0 + 10)
        pdf.cell(heat_w, 5, "ACTUAL %", align="C")
    pdf.set_y(y0 + head_h)

    if not elements:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(page_w, row_h, "No customer specification found for this alloy.", border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    for row in elements:
        y1 = pdf.get_y()
        pdf.set_font("Helvetica", "", 7)
        pdf.rect(left, y1, elem_w, row_h)
        pdf.rect(left + elem_w, y1, spec_w, row_h)
        pdf.set_xy(left, y1)
        pdf.cell(elem_w, row_h, _fit(str(row.get("Display_label") or row.get("Element_Name") or ""), elem_w))
        pdf.set_xy(left + elem_w, y1)
        pdf.cell(spec_w, row_h, _fit(str(row.get("Spec_text") or ""), spec_w), align="C")
        actuals = row.get("actuals") or ["—"] * heat_count
        for index in range(heat_count):
            x = left + elem_w + spec_w + index * heat_w
            pdf.rect(x, y1, heat_w, row_h)
            pdf.set_xy(x, y1)
            value = actuals[index] if index < len(actuals) else "—"
            pdf.cell(heat_w, row_h, _fit(str(value), heat_w), align="C")
        pdf.set_y(y1 + row_h)

    _section("INSTRUMENT DETAILS")
    inst_rows = [
        ("Analysis Method", payload.get("analysis_method") or ""),
        ("Instrument", payload.get("instrument") or ""),
        ("Instrument Make", payload.get("instrument_make") or ""),
    ]
    label_w = page_w * 0.28
    for label, value in inst_rows:
        y1 = pdf.get_y()
        pdf.set_fill_color(*fill)
        pdf.set_font("Helvetica", "B", 8)
        pdf.rect(left, y1, label_w, 6, style="DF")
        pdf.set_xy(left, y1)
        pdf.cell(label_w, 6, _pdf_safe_text(label))
        pdf.set_font("Helvetica", "", 8)
        pdf.rect(left + label_w, y1, page_w - label_w, 6)
        pdf.set_xy(left + label_w, y1)
        pdf.cell(page_w - label_w, 6, _pdf_safe_text(value))
        pdf.set_y(y1 + 6)

    _section("VISUAL INSPECTIONS : CUSTOMER REQUIREMENT STATUS")
    q_w, s_w, v_w = page_w * 0.76, page_w * 0.12, page_w * 0.12
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(243, 243, 243)
    pdf.cell(q_w, 6, "Requirement", border=1, align="C", fill=True)
    pdf.cell(s_w, 6, "STATUS", border=1, align="C", fill=True)
    pdf.cell(v_w, 6, "VERIFY", border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    insp_h = 5.3
    for row in inspection:
        y1 = pdf.get_y()
        answer = str(row.get("Answer") or "").strip()
        status = "Ok" if answer.upper() == "OK" else (answer or "-")
        verify = "Yes" if row.get("Verified") else ""
        pdf.set_font("Helvetica", "", 7)
        pdf.rect(left, y1, q_w, insp_h)
        pdf.set_xy(left, y1)
        pdf.cell(q_w, insp_h, _fit(str(row.get("Question_text") or ""), q_w, 7))
        pdf.set_font("Helvetica", "B", 7)
        pdf.rect(left + q_w, y1, s_w, insp_h)
        pdf.set_xy(left + q_w, y1)
        pdf.cell(s_w, insp_h, _pdf_safe_text(status), align="C")
        pdf.rect(left + q_w + s_w, y1, v_w, insp_h)
        pdf.set_xy(left + q_w + s_w, y1)
        pdf.cell(v_w, insp_h, _pdf_safe_text(verify), align="C")
        pdf.set_y(y1 + insp_h)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(
        page_w,
        6,
        _pdf_safe_text(f"Approved by : {payload.get('approved_by') or ''}"),
    )
    return bytes(pdf.output())


def _render_certificate_print(
    header: dict, cert: dict, lines: list[dict], inspection: list[dict] | None = None
) -> dict:
    payload = db.get_test_certificate_print_payload(
        int(header["Packing_list_id"]),
        lines=lines,
        inspection=inspection,
    )
    letterhead = _company_letterhead(payload.get("company") or {})
    logo_src = _certificate_logo_data_uri()
    iso_src = _iso_logo_data_uri()
    logo_html = (
        f'<img class="tc-logo" src="{logo_src}" alt="Nualco">'
        if logo_src
        else '<div class="tc-logo-fallback">NUALCO</div>'
    )
    iso_html = (
        f'<img class="tc-iso" src="{iso_src}" alt="ISO 9001:2015 Certified Company">'
        if iso_src
        else ""
    )
    esc = html.escape
    meta_pairs = [
        ("Report No.", payload.get("certificate_no") or "—"),
        ("Grade", payload.get("grade") or "—"),
        ("Report Date", format_cert_print_date(payload.get("issued_date"))),
        ("Colour Code", payload.get("colour_code") or "—"),
        ("Customer", payload.get("customer_name") or "—"),
        ("Customer Reference", payload.get("cust_code") or "—"),
        ("Invoice No", payload.get("invoice_no") or "—"),
        ("Invoice Date", format_cert_print_date(payload.get("invoice_date"))),
        ("P.O No", payload.get("po_no") or "—"),
        ("P.O Date", format_cert_print_date(payload.get("po_date"))),
    ]
    meta_rows_html = ""
    for i in range(0, len(meta_pairs), 2):
        left_label, left_value = meta_pairs[i]
        right_label, right_value = meta_pairs[i + 1]
        meta_rows_html += (
            "<tr>"
            f"<td class='tc-label'>{esc(left_label)}</td>"
            f"<td class='tc-value'>{esc(str(left_value))}</td>"
            f"<td class='tc-label'>{esc(right_label)}</td>"
            f"<td class='tc-value'>{esc(str(right_value))}</td>"
            "</tr>"
        )
    document_id = str(payload.get("document_id") or "").strip()
    meta_rows_html += (
        "<tr>"
        "<td class='tc-label'>Total Weight</td>"
        f"<td class='tc-value'>{esc(db._format_cert_number(payload.get('total_weight')))} Kgs</td>"
        f"<td class='tc-docid-cell' colspan='2'>{esc(document_id)}</td>"
        "</tr>"
    )
    heats = payload.get("heats") or []
    heat_count = max(len(heats), 1)
    heat_head = ""
    heat_qty = ""
    heat_actual = ""
    for heat in heats:
        heat_head += (
            f"<th class='tc-heat' colspan='1'>"
            f"Heat No : {esc(str(heat.get('Heat_no') or '—'))}</th>"
        )
        heat_qty += (
            "<th class='tc-heat-sub'>"
            f"Kgs : {esc(db._format_cert_number(heat.get('Kgs')))}<br>"
            f"Pcs : {esc(str(int(float(heat.get('Pieces') or 0))))}"
            "</th>"
        )
        heat_actual += "<th class='tc-heat-sub'>ACTUAL %</th>"
    if not heats:
        heat_head = "<th class='tc-heat'>Heat No : —</th>"
        heat_qty = "<th class='tc-heat-sub'>Kgs : — &nbsp; Pcs : —</th>"
        heat_actual = "<th class='tc-heat-sub'>ACTUAL %</th>"
    chem_body = ""
    for row in payload.get("elements") or []:
        cells = "".join(
            f"<td class='tc-actual'>{esc(str(value))}</td>"
            for value in (row.get("actuals") or ["—"] * heat_count)
        )
        chem_body += (
            "<tr>"
            f"<td class='tc-elem'>{esc(str(row.get('Display_label') or row.get('Element_Name') or ''))}</td>"
            f"<td class='tc-spec'>{esc(str(row.get('Spec_text') or ''))}</td>"
            f"{cells}"
            "</tr>"
        )
    if not chem_body:
        chem_body = (
            f"<tr><td colspan='{2 + heat_count}' class='tc-empty'>"
            "No customer specification found for this alloy.</td></tr>"
        )
    insp_body = ""
    for row in payload.get("inspection") or []:
        answer = str(row.get("Answer") or "").strip()
        status = "Ok" if answer.upper() == "OK" else (answer or "—")
        verify = "✓" if row.get("Verified") else ""
        insp_body += (
            "<tr>"
            f"<td class='tc-insp-q'>{esc(str(row.get('Question_text') or ''))}</td>"
            f"<td class='tc-insp-status'>{esc(status)}</td>"
            f"<td class='tc-insp-verify'>{verify}</td>"
            "</tr>"
        )
    st.markdown(
        f"""
        <style>
        .tc-doc {{
            background: #fff; color: {_BRAND_INK};
            border: 1px solid #cfcfcf; padding: 0.85rem 1rem 1.1rem;
            font-family: Arial, Helvetica, sans-serif; font-size: 12.5px;
        }}
        .block-container {{ max-width: 1100px !important; }}
        .tc-head {{
            display: flex; align-items: flex-start;
            gap: 0.45rem; margin: 0 0 0.2rem 0;
        }}
        .tc-head-left, .tc-head-right {{
            flex: 0 0 96px; width: 96px;
        }}
        .tc-head-left {{ text-align: left; }}
        .tc-head-right {{ text-align: center; }}
        .tc-head-center {{
            flex: 1 1 auto; text-align: center; min-width: 0;
            padding-top: 0.15rem;
        }}
        .tc-logo {{ width: 86px; height: auto; display: inline-block; }}
        .tc-logo-fallback {{
            font-weight: 800; color: {_BRAND_ORANGE}; font-size: 1.1rem;
        }}
        .tc-company-name {{
            margin: 0 auto; font-size: 1.2rem; font-weight: 800;
            letter-spacing: 0.02em; text-align: center; line-height: 1.25;
            white-space: nowrap;
        }}
        .tc-company-line {{
            margin: 0.16rem auto 0; line-height: 1.4; text-align: center;
        }}
        .tc-iso {{
            width: 88px; height: auto; display: block;
            margin: 0 auto;
        }}
        .tc-meta .tc-docid-cell {{
            font-weight: 700; text-align: center; letter-spacing: 0.01em;
        }}
        .tc-title {{
            margin: 0.7rem 0 0.45rem; text-align: center;
            font-size: 1.15rem; font-weight: 800;
            border: 1px solid {_BRAND_INK}; padding: 0.28rem 0;
            letter-spacing: 0.06em;
        }}
        .tc-table {{
            width: 100%; border-collapse: collapse; margin: 0;
        }}
        .tc-table th, .tc-table td {{
            border: 1px solid #7a7a7a; padding: 0.28rem 0.4rem;
            vertical-align: middle;
        }}
        .tc-meta .tc-label {{
            width: 16%; font-weight: 700; background: #f7f7f7; white-space: nowrap;
        }}
        .tc-meta .tc-value {{ width: 34%; font-weight: 600; }}
        .tc-section {{
            background: {_BRAND_ORANGE}; color: #fff; font-weight: 800;
            text-align: center; letter-spacing: 0.04em;
            padding: 0.28rem 0.4rem; margin: 0.55rem 0 0;
            border: 1px solid #c24a12;
        }}
        .tc-chem thead th {{
            background: #f3f3f3; font-weight: 700; text-align: center;
        }}
        .tc-chem .tc-elem {{ text-align: left; font-weight: 600; width: 24%; }}
        .tc-chem .tc-spec {{ text-align: center; width: 16%; }}
        .tc-chem .tc-actual {{ text-align: center; font-weight: 600; }}
        .tc-chem .tc-heat, .tc-chem .tc-heat-sub {{ min-width: 7.5rem; }}
        .tc-empty {{ text-align: center; color: #666; }}
        .tc-inst td:first-child {{ width: 22%; font-weight: 700; background: #f7f7f7; }}
        .tc-insp-q {{ text-align: left; }}
        .tc-insp-status, .tc-insp-verify {{
            text-align: center; font-weight: 700; width: 12%;
        }}
        .tc-approve {{
            margin: 0.85rem 0 0; font-weight: 700; font-size: 0.95rem;
        }}
        @media print {{
            [data-testid="stSidebar"], [data-testid="stToolbar"],
            footer, header, .no-print,
            .stAppToolbar, [data-testid="stHeading"],
            [data-testid="stSelectbox"], [data-testid="stCaption"] {{
                display: none !important;
            }}
            .block-container {{ max-width: 100% !important; padding: 0.2rem !important; }}
            .tc-doc {{ border: none; padding: 0; }}
        }}
        </style>
        <div class="tc-doc">
            <div class="tc-head">
                <div class="tc-head-left">{logo_html}</div>
                <div class="tc-head-center">
                    <div class="tc-company-name">{esc(letterhead["name"])}</div>
                    <div class="tc-company-line">{esc(letterhead["address"])}</div>
                    <div class="tc-company-line">{esc(letterhead["contact"])}</div>
                    <div class="tc-company-line">{esc(letterhead["gst"])}</div>
                </div>
                <div class="tc-head-right">{iso_html}</div>
            </div>
            <div class="tc-title">TEST CERTIFICATE</div>
            <table class="tc-table tc-meta">
                <tbody>{meta_rows_html}</tbody>
            </table>
            <div class="tc-section">CHEMICAL ANALYSIS REPORT</div>
            <table class="tc-table tc-chem">
                <thead>
                    <tr>
                        <th rowspan="3">ELEMENTS</th>
                        <th rowspan="3">SPECIFICATION %</th>
                        {heat_head}
                    </tr>
                    <tr>{heat_qty}</tr>
                    <tr>{heat_actual}</tr>
                </thead>
                <tbody>{chem_body}</tbody>
            </table>
            <div class="tc-section">INSTRUMENT DETAILS</div>
            <table class="tc-table tc-inst">
                <tbody>
                    <tr><td>Analysis Method</td><td>{esc(str(payload.get("analysis_method") or ""))}</td></tr>
                    <tr><td>Instrument</td><td>{esc(str(payload.get("instrument") or ""))}</td></tr>
                    <tr><td>Instrument Make</td><td>{esc(str(payload.get("instrument_make") or ""))}</td></tr>
                </tbody>
            </table>
            <div class="tc-section">VISUAL INSPECTIONS : CUSTOMER REQUIREMENT STATUS</div>
            <table class="tc-table tc-insp">
                <thead>
                    <tr>
                        <th>Requirement</th>
                        <th>STATUS</th>
                        <th>VERIFY</th>
                    </tr>
                </thead>
                <tbody>{insp_body}</tbody>
            </table>
            <p class="tc-approve">Approved by : {esc(str(payload.get("approved_by") or ""))}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return payload


def _style_spec_check_table(data: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Highlight rows whose Status starts with Out of spec."""

    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Status") or "")
        if status.startswith("Out of spec"):
            return ["background-color: #fdecea; color: #c62828; font-weight: 700"] * len(
                row
            )
        return [""] * len(row)

    return data.style.apply(_row_style, axis=1)


def _render_tc_spec_and_deviation(packing_list_id: int, *, locked: bool) -> tuple[bool, bool]:
    """Show batch chemistry vs alloy spec and the deviation-letter upload."""
    st.markdown("#### Alloy specification check")
    st.caption(
        "Each packed batch is compared to the packing-list alloy master spec. "
        "An element is out of specification when its percentage is at or below min, "
        "or at or above max."
    )
    comparison = db.list_packing_list_chemistry_vs_spec(packing_list_id)
    deviations = [row for row in comparison if row.get("Out_of_spec")]
    has_letter = db.has_packing_list_deviation_letter(packing_list_id)
    if not comparison:
        st.info(
            "No saved batch chemistry to compare against the alloy specification yet."
        )
    else:
        table_rows = [
            {
                "Batch_ID": row.get("Batch_ID"),
                "Heat_no": row.get("Heat_no"),
                "Element": row.get("Element_symbol"),
                "Actual %": row.get("Percentage"),
                "Spec": row.get("Spec"),
                "Status": (
                    f"Out of spec — {row.get('Reason')}"
                    if row.get("Out_of_spec")
                    else "Within spec"
                ),
            }
            for row in comparison
        ]
        table = df_from_rows(table_rows)
        st.dataframe(
            _style_spec_check_table(table),
            use_container_width=True,
            hide_index=True,
            height=min(560, 40 + 36 * max(len(table_rows), 3)),
        )
        if deviations:
            st.error(
                f"{len(deviations)} element(s) are outside specification. "
                "Upload the customer acceptance of deviation letter before "
                "creating or issuing the test certificate."
            )
        else:
            st.success(
                "All packed batch elements with recorded chemistry are within specification."
            )

    if deviations or has_letter:
        st.markdown("#### Customer acceptance of deviation")
        if deviations and not has_letter:
            st.warning(
                "A customer acceptance of deviation letter is required because "
                "one or more packed batches are outside the alloy specification."
            )
        u2 = None
        if not locked:
            uploaded = st.file_uploader(
                "Upload deviation letter",
                type=["pdf", "doc", "docx", "jpg", "jpeg", "png"],
                key=f"tc_dev_letter_{packing_list_id}",
                help="PDF, Word, or a scan (JPEG/PNG).",
            )
            u1, u2, _ = st.columns([1.2, 1.2, 3])
            if u1.button(
                "Save letter",
                type="primary",
                disabled=uploaded is None,
                key="tc_dev_save",
            ):
                try:
                    db.save_packing_list_deviation_letter(
                        packing_list_id,
                        uploaded.getvalue(),
                        uploaded.name,
                        content_type=getattr(uploaded, "type", None),
                    )
                    st.success(f"Saved **{uploaded.name}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        letter = db.get_packing_list_deviation_letter(packing_list_id)
        blob = _as_photo_bytes((letter or {}).get("Deviation_letter"))
        if letter and blob:
            st.caption(
                f"Attached: **{letter.get('Deviation_letter_name') or 'deviation letter'}**"
            )
            st.download_button(
                "Download letter",
                data=blob,
                file_name=letter.get("Deviation_letter_name")
                or f"deviation-letter-{packing_list_id}.bin",
                mime=letter.get("Deviation_letter_type") or "application/octet-stream",
                key="tc_dev_download",
            )
            if not locked and u2 is not None and u2.button("Remove letter", key="tc_dev_remove"):
                try:
                    db.clear_packing_list_deviation_letter(packing_list_id)
                    st.success("Deviation letter removed.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        elif not locked:
            st.caption("No deviation letter attached yet.")

    return bool(deviations), has_letter


def _kg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_date(value: object) -> date | None:
    parsed = parse_any_date(value)
    if parsed is None:
        return None
    if isinstance(parsed, datetime):
        return parsed.date()
    return parsed


_PO_PRIORITY_ORDER = {
    "Overdue": 0,
    "Due today": 1,
    "Produce": 2,
    "Covered": 3,
    "Supplied": 4,
}


def _po_priority(row: dict, *, alloy_to_produce: float, today: date) -> str:
    status = str(row.get("Purchase_Order_Status") or "Open")
    if status != "Open":
        return status
    if _kg(row.get("Balance_Qty")) <= 0.05:
        return "Supplied"
    due = _as_date(row.get("Delivery_Date"))
    if due is not None and due < today:
        return "Overdue"
    if due is not None and due == today:
        return "Due today"
    if alloy_to_produce > 0.05:
        return "Produce"
    return "Covered"


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════
if PAGE == "Dashboard":
    st.title("Production Dashboard")
    st.caption(
        "Open purchase-order quantity versus quantity already dispatched, "
        "balance still to supply, and finished-goods stock. "
        "Produce alloys with a **To produce** quantity first. "
        "Finished goods are shared across every open PO for that alloy. "
        "Dispatch is verified packing-list weight."
    )
    today = date.today()
    try:
        supply_rows = db.list_po_supply_status()
        batches = db.list_batches()
        materials = db.list_raw_materials()
        lots = db.list_inventory_lots()
        alloys = db.list_alloys()
        oil_stock = db.get_furnace_oil_stock()
        elec_month = db.electricity_month_totals(today.year, today.month)
    except Exception as exc:
        _show_db_connection_error(exc)
        supply_rows, batches, materials, lots, alloys = [], [], [], [], []
        oil_stock = 0.0
        elec_month = {"consumed": 0.0, "by_line": {}}

    open_rows = [
        r
        for r in supply_rows
        if (r.get("Purchase_Order_Status") or "Open") == "Open"
    ]

    alloy_plan: dict[int, dict] = {}
    for row in open_rows:
        try:
            alloy_id = int(row.get("Alloy_Id"))
        except (TypeError, ValueError):
            continue
        item = alloy_plan.setdefault(
            alloy_id,
            {
                "Alloy_Id": alloy_id,
                "Alloy": row.get("Alloy_name") or f"Alloy {alloy_id}",
                "Open_POs": 0,
                "Order_Qty": 0.0,
                "Dispatched_Qty": 0.0,
                "In_packing_Qty": 0.0,
                "Balance_Qty": 0.0,
                "FG_Available_Qty": _kg(row.get("FG_Available_Qty")),
                "FG_Under_Testing_Qty": _kg(row.get("FG_Under_Testing_Qty")),
            },
        )
        item["Open_POs"] += 1
        item["Order_Qty"] += _kg(row.get("Order_Qty"))
        item["Dispatched_Qty"] += _kg(row.get("Dispatched_Qty"))
        item["In_packing_Qty"] += _kg(row.get("In_packing_Qty"))
        item["Balance_Qty"] += _kg(row.get("Balance_Qty"))
        item["FG_Available_Qty"] = _kg(row.get("FG_Available_Qty"))
        item["FG_Under_Testing_Qty"] = _kg(row.get("FG_Under_Testing_Qty"))

    for item in alloy_plan.values():
        reserved = item["In_packing_Qty"] + item["FG_Available_Qty"]
        item["To_Produce_Qty"] = max(0.0, item["Balance_Qty"] - reserved)

    alloy_to_produce = {
        aid: float(item["To_Produce_Qty"]) for aid, item in alloy_plan.items()
    }
    po_view = []
    for row in open_rows:
        try:
            alloy_id = int(row.get("Alloy_Id"))
        except (TypeError, ValueError):
            alloy_id = None
        need = alloy_to_produce.get(alloy_id, 0.0) if alloy_id is not None else 0.0
        po_view.append(
            {
                **row,
                "Priority": _po_priority(
                    row, alloy_to_produce=need, today=today
                ),
            }
        )
    po_view.sort(
        key=lambda r: (
            _PO_PRIORITY_ORDER.get(str(r.get("Priority")), 9),
            _as_date(r.get("Delivery_Date")) or date.max,
            str(r.get("Customer_name") or ""),
            str(r.get("Customer_PO_No") or ""),
        )
    )

    total_order = sum(_kg(r.get("Order_Qty")) for r in open_rows)
    total_dispatched = sum(_kg(r.get("Dispatched_Qty")) for r in open_rows)
    total_balance = sum(_kg(r.get("Balance_Qty")) for r in open_rows)
    total_fg = sum(item["FG_Available_Qty"] for item in alloy_plan.values())
    total_to_produce = sum(item["To_Produce_Qty"] for item in alloy_plan.values())
    overdue_n = sum(1 for r in po_view if r.get("Priority") == "Overdue")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("PO alloy qty (kg)", f"{total_order:,.1f}")
    m2.metric("Dispatched (kg)", f"{total_dispatched:,.1f}")
    m3.metric("Balance to supply (kg)", f"{total_balance:,.1f}")
    m4.metric("FG available (kg)", f"{total_fg:,.1f}")
    m5.metric(
        "To produce (kg)",
        f"{total_to_produce:,.1f}",
        delta=f"{overdue_n} overdue PO line(s)" if overdue_n else None,
        delta_color="inverse" if overdue_n else "off",
    )

    st.subheader("What to produce")
    st.caption(
        "Balance still due on open POs, minus in-packing and available finished goods. "
        "Highest **To produce** alloys should be scheduled first."
    )
    if not alloy_plan:
        st.info("No open purchase orders. Create one under **Purchase Orders**.")
    else:
        alloy_rows = sorted(
            alloy_plan.values(),
            key=lambda r: (-r["To_Produce_Qty"], -r["Balance_Qty"], str(r["Alloy"])),
        )
        alloy_df = pd.DataFrame(
            [
                {
                    "Alloy": r["Alloy"],
                    "Open POs": r["Open_POs"],
                    "Order qty (kg)": r["Order_Qty"],
                    "Dispatched (kg)": r["Dispatched_Qty"],
                    "Balance (kg)": r["Balance_Qty"],
                    "In packing (kg)": r["In_packing_Qty"],
                    "FG available (kg)": r["FG_Available_Qty"],
                    "Under testing (kg)": r["FG_Under_Testing_Qty"],
                    "To produce (kg)": r["To_Produce_Qty"],
                }
                for r in alloy_rows
            ]
        )
        kg_cols = [
            "Order qty (kg)",
            "Dispatched (kg)",
            "Balance (kg)",
            "In packing (kg)",
            "FG available (kg)",
            "Under testing (kg)",
            "To produce (kg)",
        ]
        show_dataframe(
            alloy_df,
            column_config={
                "Open POs": st.column_config.NumberColumn(format="%d"),
                **{
                    col: st.column_config.NumberColumn(format="%.1f")
                    for col in kg_cols
                },
            },
        )

    st.subheader("Open purchase orders")
    st.caption(
        "One row per customer PO and alloy. "
        "**Overdue** / **Due today** are by delivery date. "
        "**Produce** means this alloy still has a melt shortfall."
    )
    if not po_view:
        st.info("No open purchase-order lines.")
    else:
        po_df = pd.DataFrame(
            [
                {
                    "Priority": r.get("Priority"),
                    "Customer": r.get("Customer_name") or r.get("Cust_code") or "—",
                    "PO No": r.get("Customer_PO_No"),
                    "Alloy": r.get("Alloy_name") or r.get("Alloy_Id"),
                    "Delivery date": r.get("Delivery_Date"),
                    "Order qty (kg)": _kg(r.get("Order_Qty")),
                    "Dispatched (kg)": _kg(r.get("Dispatched_Qty")),
                    "In packing (kg)": _kg(r.get("In_packing_Qty")),
                    "Balance (kg)": _kg(r.get("Balance_Qty")),
                    "FG available (kg)": _kg(r.get("FG_Available_Qty")),
                }
                for r in po_view
            ]
        )
        show_dataframe(
            po_df,
            column_config={
                "Priority": st.column_config.TextColumn("Priority"),
                "Order qty (kg)": st.column_config.NumberColumn(format="%.1f"),
                "Dispatched (kg)": st.column_config.NumberColumn(format="%.1f"),
                "In packing (kg)": st.column_config.NumberColumn(format="%.1f"),
                "Balance (kg)": st.column_config.NumberColumn(format="%.1f"),
                "FG available (kg)": st.column_config.NumberColumn(format="%.1f"),
            },
        )

    st.divider()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Batches", len(batches))
    c2.metric("Raw materials", len(materials))
    c3.metric("Active lots", len(lots))
    c4.metric("Alloys", len(alloys))
    c5.metric("Furnace oil (L)", f"{oil_stock:,.1f}")
    c6.metric("Electricity this month", f"{elec_month['consumed']:,.1f}")

    st.subheader("Recent production batches")
    bdf = df_from_rows(batches)
    if bdf.empty:
        st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
    else:
        show_dataframe(bdf)

    st.subheader("Inventory on hand")
    idf = df_from_rows(lots)
    if idf.empty:
        st.info("No inventory lots with remaining weight.")
    else:
        show_dataframe(idf)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Raw Material Logging
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Raw Material Logging":
    st.title("Raw Material Logging")
    st.caption(
        "Start with the vendor and invoice, then add every raw material on that invoice. "
        "The invoice is stored once; each material becomes a stock lot. "
        "Define grades under **Raw Material Master**. "
        "Browse lots under **Raw Material Inventory**."
    )

    vendors = db.list_vendors()
    vendor_opts = {f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors}
    existing_materials = db.list_raw_materials(active_only=False)
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
    if st.button(
        "📷 Vehicle photo",
        key="rm_log_vphoto_btn",
        help="Photo of the delivery vehicle. Saved once on this invoice.",
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

    collected_lines: list[dict] = []
    for idx, _line in enumerate(st.session_state.rm_invoice_lines):
        st.markdown(f"**Row {idx + 1}**")
        n1, n2, n3 = st.columns([2.2, 1.2, 1.4])
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
        collected_lines.append(
            {
                "name": (name or "").strip(),
                "cost": float(cost or 0.0),
                "weight": float(weight or 0.0),
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
                            cost_per_kg=ln["cost"],
                            create_new=True,
                        )
                    lines.append(
                        {
                            "material": material_name,
                            "cost": ln["cost"],
                            "weight": ln["weight"],
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
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1b. Raw Material Inventory (browse / review — kept separate for mobile logging)
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Raw Material Inventory":
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
                       AS "Vehicle_photo"
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
        if vehicle_photo:
            st.markdown("**Vehicle photo**")
            st.image(vehicle_photo)


# ═══════════════════════════════════════════════════════════════════════════════
# Furnace Oil Purchase
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Furnace Oil Purchase":
    st.title("Furnace Oil Purchase")
    st.caption(
        "Purchase team: log furnace oil receipts against a vendor invoice. "
        "Stock is added to **Furnace Oil Inventory**. "
        "Production records daily use on **Furnace Oil Consumption**."
    )

    vendors = db.list_vendors()
    vendor_opts = {f"{v['Vendor_name']} (#{v['Vendor_code']})": v["Vendor_code"] for v in vendors}
    if not vendors:
        st.warning("Add at least one vendor under **Vendors** before logging a purchase.")

    stock = db.get_furnace_oil_stock()
    s1, s2 = st.columns(2)
    s1.metric("Current stock (L)", f"{stock:,.1f}")
    s2.metric("Unit", "Litre")

    st.markdown("#### Purchase details")
    vendor_label = st.selectbox(
        "Vendor name *",
        options=[""] + list(vendor_opts.keys()),
        key="fo_pur_vendor",
    )
    p1, p2, p3 = st.columns(3)
    with p1:
        invoice_date = ui_date_input(
            "Supplier invoice date *", value=date.today(), key="fo_pur_invoice_date"
        )
    with p2:
        invoice = st.text_input(
            "Vendor invoice *",
            placeholder="e.g. FO-INV-2026-001",
            key="fo_pur_invoice",
        )
    with p3:
        received = ui_date_input(
            "Received date *", value=date.today(), key="fo_pur_received"
        )
    q1, q2, q3, q4 = st.columns(4)
    with q1:
        qty = empty_percent_input(
            "Quantity (litres) *",
            key="fo_pur_qty",
            max_value=None,
            step=1.0,
        )
    with q2:
        weight_kg = empty_percent_input(
            "Weight in kgs",
            key="fo_pur_weight_kg",
            max_value=None,
            step=0.1,
        )
    with q3:
        rate = empty_percent_input(
            "Rate per litre",
            key="fo_pur_rate",
            max_value=None,
            step=0.01,
        )
    with q4:
        tank = st.text_input(
            "Storage tank",
            placeholder="e.g. Tank-1",
            key="fo_pur_tank",
        )
    notes = st.text_input("Notes", key="fo_pur_notes")
    d1, d2 = st.columns(2)
    with d1:
        invoice_doc = st.file_uploader(
            "Invoice document",
            type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
            key="fo_pur_doc",
        )
    with d2:
        weighment_slip = st.file_uploader(
            "Weighment slip",
            type=["png", "jpg", "jpeg", "pdf", "doc", "docx", "xls", "xlsx"],
            key="fo_pur_weighment",
        )

    qty_val = float(qty or 0)
    weight_val = float(weight_kg or 0)
    rate_val = float(rate or 0)
    invoice_value = qty_val * rate_val
    gst_value = invoice_value * 0.18
    t1, t2, t3, t4, t5 = st.columns(5)
    t1.metric("Quantity (L)", f"{qty_val:,.1f}")
    t2.metric("Weight (kg)", f"{weight_val:,.1f}" if weight_val > 0 else "—")
    t3.metric("Invoice value", f"{invoice_value:,.2f}")
    t4.metric("GST value (18%)", f"{gst_value:,.2f}")
    t5.metric("Total value", f"{invoice_value + gst_value:,.2f}")

    if st.button("Save furnace oil purchase", type="primary", key="fo_pur_save"):
        vendor_code = vendor_opts[vendor_label] if vendor_label else None
        if not vendors:
            st.error("Create a vendor first.")
        elif not vendor_code:
            st.error("Select a vendor name.")
        elif not (invoice or "").strip():
            st.error("Vendor invoice is required.")
        elif qty_val <= 0:
            st.error("Quantity (litres) must be greater than zero.")
        else:
            try:
                pid = db.add_furnace_oil_purchase(
                    vendor_code=vendor_code,
                    invoice=invoice.strip(),
                    invoice_date=invoice_date.isoformat(),
                    received_date=received.isoformat(),
                    quantity=qty_val,
                    weight_in_kgs=weight_val if weight_val > 0 else None,
                    rate_per_litre=rate_val if rate_val > 0 else None,
                    storage_tank=tank.strip() or None,
                    notes=notes.strip() or None,
                    invoice_document=photo_bytes(invoice_doc),
                    invoice_document_name=invoice_doc.name if invoice_doc else None,
                    invoice_document_type=getattr(invoice_doc, "type", None) if invoice_doc else None,
                    weighment_slip=photo_bytes(weighment_slip),
                    weighment_slip_name=weighment_slip.name if weighment_slip else None,
                    weighment_slip_type=(
                        getattr(weighment_slip, "type", None) if weighment_slip else None
                    ),
                )
                st.success(
                    f"Saved furnace oil purchase **#{pid}** "
                    f"({qty_val:,.1f} L, invoice {invoice.strip()}). "
                    f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
                )
                st.session_state["fo_pur_qty"] = None
                st.session_state["fo_pur_weight_kg"] = None
                st.session_state["fo_pur_rate"] = None
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

    st.subheader("Recent purchases")
    purchases = df_from_rows(db.list_furnace_oil_purchases())
    if purchases.empty:
        st.info("No furnace oil purchases yet.")
    else:
        show_dataframe(purchases)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Production Batch & Chemistry
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Batch & Chemistry":
    st.title("Production Batch & Chemistry Input")
    st.caption(
        "Choose a furnace first, then enter header details and charge lines. "
        "Click **Save and create production batch** to store the heat as **In-Progress**. "
        "Degassing, samples, and chemistry unlock only after that. "
        "Switching furnace keeps this draft and opens a separate form — it does not "
        "remap entries to the other furnace. "
        "Batch ID is **DDMMYY + furnace + shift + melt no** from the production date "
        "(e.g. 27-Aug-2026, furnace 1, shift A, melt 1 → `2708261A1`). "
        "The same date, furnace, shift, and melt no cannot be used twice. "
        "Heat no is assigned by the system as **YY-furnace + month code + 3-digit "
        "counter** (e.g. 27-Aug-2026 on furnace 1 → `26-1H001`; September → `26-1K001`). "
        "The counter is unique per furnace and resets to `001` each month. "
        "Mark the heat **Completed** after degassing, samples, K Mold, chemistry, "
        "and at least one charge line. **Batch Output** can be entered only after that. "
        "A Completed heat is locked; only Admin can unlock it to correct history. "
        "Browse existing batches under **Production Batches**."
    )

    furnaces = db.list_furnaces()
    melters = db.list_melters()
    supervisors = db.list_production_supervisors()
    alloys = db.list_alloys(include_sidestream=False)
    alloy_labels = {
        f"{a['Alloy_id']} — {a['Alloy_name']}"
        + (f" ({a['Customer_name']})" if a["Customer_name"] else ""): a["Alloy_id"]
        for a in alloys
    }
    materials = db.list_raw_materials()

    if not furnaces:
        st.error("Define at least one furnace under **Furnaces**.")
    elif not melters:
        st.error("Define at least one melter under **Melters**.")
    elif not supervisors:
        st.error("Define at least one production supervisor (Data Browser → Production supervisors).")
    else:
        furnace_choice = st.selectbox(
            "Working furnace *",
            options=["— select furnace —"] + furnaces,
            key="batch_working_furnace",
            on_change=_on_working_furnace_change,
            help=(
                "Select the furnace this heat is running in. All fields below "
                "stay with this furnace only."
            ),
        )
        if furnace_choice == "— select furnace —":
            st.info(
                "Select a furnace to enter production data. Alloy, samples, "
                "charge lines, and batch chemistry are stored per furnace, so "
                "changing furnace later will not move this entry to another furnace."
            )
            st.stop()

        furnace = furnace_choice
        _remember_ephemeral_clicks(furnace)
        _restore_furnace_widgets(furnace)
        st.session_state["_pb_prev_furnace"] = furnace

        def _pk(name: str) -> str:
            return _furnace_form_key(furnace, name)

        flash = st.session_state.pop("_pb_flash", None)
        if flash:
            st.success(flash)

        st.info(
            f"Entering data for furnace **{furnace}** only. "
            "Crucible, heat, alloy, samples, charges, and chemistry apply to this furnace."
        )

        sample_blank = "— not set —"
        NEW_BATCH = "New production batch"
        furnace_rows = db.list_furnace_batches(furnace)
        label_to_batch_id: dict[str, str | None] = {NEW_BATCH: None}
        work_opts = [NEW_BATCH]
        for row in furnace_rows:
            bid = str(row.get("Batch_ID") or "").strip()
            if not bid:
                continue
            label = _production_batch_option_label(bid, row.get("Heat_no"))
            label_to_batch_id[label] = bid
            work_opts.append(label)
        wb_key = _pk("working_batch")
        pending_batch = st.session_state.pop("_pb_select_batch", None)
        if pending_batch:
            pending_id = str(pending_batch)
            pending_label = next(
                (lbl for lbl, bid in label_to_batch_id.items() if bid == pending_id),
                None,
            )
            if pending_label is None:
                created = db.get_batch(pending_id)
                pending_label = _production_batch_option_label(
                    pending_id, (created or {}).get("Heat_no")
                )
                if pending_label not in work_opts:
                    work_opts.append(pending_label)
                    label_to_batch_id[pending_label] = pending_id
            st.session_state[wb_key] = pending_label
        current_choice = st.session_state.get(wb_key)
        if current_choice not in work_opts:
            mapped = next(
                (
                    lbl
                    for lbl, bid in label_to_batch_id.items()
                    if bid == current_choice
                ),
                NEW_BATCH,
            )
            st.session_state[wb_key] = mapped
        working_label = st.selectbox(
            "Production batch",
            options=work_opts,
            key=wb_key,
            help=(
                "Start a new heat, or pick an existing heat shown as "
                "Batch ID - Heat no (e.g. 2708261A9 - 26-1H001)."
            ),
        )
        working_batch_id = label_to_batch_id.get(working_label)
        existing_batch = (
            None
            if working_label == NEW_BATCH or not working_batch_id
            else db.get_batch(working_batch_id)
        )
        _hydrate_production_batch_form(
            furnace,
            existing_batch,
            alloy_labels=alloy_labels,
            sample_blank=sample_blank,
        )

        is_completed = (
            existing_batch is not None
            and existing_batch.get("Production_status") == db.BATCH_STATUS_COMPLETED
        )
        is_admin = db.is_admin_user()
        unlock_key = (
            f"pb_unlock_{existing_batch['Batch_ID']}"
            if existing_batch
            else _pk("unlock_new")
        )
        locked = bool(is_completed) and not (
            is_admin and st.session_state.get(unlock_key)
        )

        if existing_batch:
            saved_melt = existing_batch.get("Melt_No")
            try:
                melt_no = int(saved_melt)
            except (TypeError, ValueError):
                melt_no = saved_melt
            parsed_date = parse_any_date(existing_batch.get("Production_Date"))
            if isinstance(parsed_date, datetime):
                prod_date = parsed_date.date()
            elif isinstance(parsed_date, date):
                prod_date = parsed_date
            else:
                prod_date = date.today()
            shift = str(existing_batch.get("Shift") or "").strip().upper()
            if shift not in db.SHIFTS:
                shift = db.SHIFTS[0]

        identity_lock_note = (
            "Taken from this heat’s Batch ID. It cannot be changed after create."
        )

        h1, h2, h3 = st.columns(3, gap="small")
        with h1:
            if existing_batch and existing_batch.get("Crucible_no"):
                available_crucible = {"Crucible_no": existing_batch["Crucible_no"]}
                st.markdown("**Crucible no**")
                st.info(str(existing_batch["Crucible_no"]))
            else:
                available_crucible = (
                    db.get_available_crucible(furnace) if furnace else None
                )
                if available_crucible:
                    st.markdown("**Crucible no**")
                    st.info(str(available_crucible["Crucible_no"]))
                else:
                    st.markdown("**Crucible no**")
                    st.error(
                        "No crucible available for the respective furnace."
                    )
            if existing_batch:
                st.markdown("**Melt no**")
                st.info(str(melt_no if melt_no not in (None, "") else "—"))
                st.caption(identity_lock_note)
            else:
                melt_no = st.selectbox(
                    "Melt no",
                    db.MELT_NOS,
                    key=_pk("melt_no"),
                    disabled=locked,
                    help=(
                        "Part of Batch ID together with production date, furnace, "
                        "and shift. Example: 27-Aug-2026, furnace 1, shift A, "
                        "melt 9 → 2708261A9."
                    ),
                )
        with h2:
            if existing_batch:
                st.markdown("**Production date**")
                st.info(
                    format_ui_date(existing_batch.get("Production_Date")) or "—"
                )
                st.caption(identity_lock_note)
            else:
                prod_date = ui_date_input(
                    "Production date",
                    value=date.today(),
                    key=_pk("prod_date"),
                    disabled=locked,
                    help=(
                        "Year and month of this date set Heat no "
                        "(e.g. Aug 2026 on furnace 1 → 26-1H001)."
                    ),
                )
            if existing_batch:
                st.markdown("**Shift**")
                st.info(shift)
                st.caption(identity_lock_note)
            else:
                shift = st.selectbox(
                    "Shift",
                    db.SHIFTS,
                    key=_pk("shift"),
                    disabled=locked,
                    help=(
                        "Part of Batch ID together with production date, furnace, "
                        "and melt no."
                    ),
                )
            notes = st.text_area(
                "Notes", height=68, key=_pk("notes"), disabled=locked
            )
        with h3:
            with st.container(gap="small", key="prod_crew_fields"):
                alloy_label = st.selectbox(
                    "Alloy",
                    options=["— none —"] + list(alloy_labels.keys()),
                    key=_pk("alloy"),
                    disabled=locked,
                )
                melting_team = st.selectbox(
                    "Melter name *", melters, key=_pk("melter"), disabled=locked
                )
                production_supervisor = st.selectbox(
                    "Production supervisor *",
                    supervisors,
                    key=_pk("supervisor"),
                    disabled=locked,
                )

        alloy_id = None if alloy_label == "— none —" else alloy_labels[alloy_label]

        preview_error = None
        duplicate_id = None
        heat_no_preview = ""
        if existing_batch:
            preview_id = str(existing_batch["Batch_ID"])
            heat_no_preview = str(existing_batch.get("Heat_no") or "")
        else:
            try:
                preview_id = db.build_production_batch_id(
                    furnace, prod_date, shift, melt_no
                )
                duplicate_id = db.find_batch_id_for_identity(
                    furnace, prod_date, shift, melt_no
                )
                if duplicate_id:
                    preview_error = (
                        f"A production batch already exists for this date, furnace, "
                        f"shift, and melt no (Batch ID {duplicate_id}). "
                        "Open that batch instead of creating a duplicate."
                    )
            except Exception as exc:
                preview_id = ""
                preview_error = str(exc)
            try:
                heat_no_preview = db.preview_next_heat_no(furnace, prod_date)
            except Exception as exc:
                heat_no_preview = ""
                if preview_error:
                    preview_error = f"{preview_error} {exc}"
                else:
                    preview_error = str(exc)

        heat_no_label = heat_no_preview or "—"
        status_col, unlock_col = st.columns([2, 2])
        with status_col:
            if existing_batch:
                status = (
                    existing_batch.get("Production_status")
                    or db.BATCH_STATUS_IN_PROGRESS
                )
                st.markdown(
                    f"**Batch ID:** `{preview_id}` &nbsp;|&nbsp; "
                    f"**Heat no:** `{heat_no_label}` &nbsp;|&nbsp; "
                    f"**Production status:** `{status}`"
                )
            elif preview_id:
                st.markdown(
                    f"**Batch ID:** `{preview_id}` &nbsp;|&nbsp; "
                    f"**Heat no:** `{heat_no_label}` &nbsp;|&nbsp; "
                    "**Production status:** not created yet"
                )
            else:
                st.markdown(
                    f"**Batch ID:** — &nbsp;|&nbsp; "
                    f"**Heat no:** `{heat_no_label}` &nbsp;|&nbsp; "
                    "**Production status:** not created yet"
                )
        with unlock_col:
            if is_completed and is_admin:
                st.checkbox(
                    "Correct history (unlock this completed batch)",
                    key=unlock_key,
                    help=(
                        "Admin only. Check this to edit a Completed heat. "
                        "Save writes a history correction; status stays Completed."
                    ),
                )
            elif is_completed:
                st.info(
                    "This heat is Completed and locked. Ask an Admin to unlock "
                    "it if history needs correction."
                )
        if preview_error:
            st.error(preview_error)

        st.markdown("#### Charge / raw material inputs")
        st.caption(
            "Select trolley (tare), enter weighment scale reading. "
            "**Net Weight = Weighment scale − Trolley weight.**"
        )

        saved_charges = db.get_batch_inputs(preview_id) if existing_batch else []
        if saved_charges:
            st.caption("Saved charge lines")
            show_dataframe(df_from_rows(saved_charges))

        trolleys = db.list_trolleys(active_only=True)
        trolley_by_name = {t["Trolley_name"]: float(t["Weight"] or 0) for t in trolleys}
        trolley_colour_by_name = {
            t["Trolley_name"]: (t.get("Colour") or "").strip() or None for t in trolleys
        }
        trolley_labels = [
            f"{t['Trolley_name']}"
            + (f" ({t['Colour']})" if t.get("Colour") else "")
            + f" — {float(t['Weight'] or 0):.1f} kg"
            for t in trolleys
        ]
        trolley_label_to_name = {
            (
                f"{t['Trolley_name']}"
                + (f" ({t['Colour']})" if t.get("Colour") else "")
                + f" — {float(t['Weight'] or 0):.1f} kg"
            ): t["Trolley_name"]
            for t in trolleys
        }

        if "charge_lines_by_furnace" not in st.session_state:
            st.session_state.charge_lines_by_furnace = {}
        drafts = st.session_state.charge_lines_by_furnace
        if furnace not in drafts:
            drafts[furnace] = [
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            ]
        furnace_charge_lines = drafts[furnace]
        if locked:
            furnace_charge_lines = []
            st.caption("Charge lines cannot be edited on a Completed heat.")
        elif existing_batch:
            st.markdown("##### Additional charge lines")
            st.caption("Saved lines above stay as-is. Use this to charge more metal.")

        if not trolleys:
            st.error("Define at least one active trolley under **Trolleys**.")

        charge_inputs: list[dict] = []
        for idx, line in enumerate(furnace_charge_lines):
            st.markdown(f"**Charge line {idx + 1}**")
            r1c1, r1c2, r1c3 = st.columns([2, 2, 2])
            with r1c1:
                mat = st.selectbox(
                    "Raw material",
                    options=[""] + materials,
                    key=_pk(f"mat_{idx}"),
                )
            lots = db.list_inventory_lots(material=mat or None) if mat else []
            lot_opts = {}
            for lot in lots:
                rem = float(lot.get("Remaining_Weight") or 0)
                status = lot.get("Raw_Material_Status") or ""
                src = lot.get("Source_Batch_ID")
                origin = lot.get("Origin_Alloy_name")
                if src:
                    origin_bit = f" {origin}" if origin else ""
                    label = (
                        f"Lot {lot['Lot_id']} — rem {rem:.1f} kg | "
                        f"from {src}{origin_bit} ({status})"
                    )
                else:
                    label = f"Lot {lot['Lot_id']} — rem {rem:.1f} kg ({status})"
                lot_opts[label] = lot["Lot_id"]
            with r1c2:
                lot_label = st.selectbox(
                    "Lot",
                    options=[""] + list(lot_opts.keys()),
                    key=_pk(f"lot_{idx}"),
                )
            with r1c3:
                # Style from current selection (session) so highlight updates on rerun
                _pending_label = st.session_state.get(_pk(f"trolley_{idx}"), "") or ""
                _pending_name = trolley_label_to_name.get(_pending_label)
                _pending_colour = (
                    trolley_colour_by_name.get(_pending_name) if _pending_name else None
                )
                _css = _trolley_css_color(_pending_colour)
                safe_colour = html.escape(str(_pending_colour)) if _pending_colour else ""
                sw, fld = st.columns([0.18, 0.82], gap="small")
                with sw:
                    if _css:
                        st.markdown(
                            f"""
                            <div title="{safe_colour}" style="
                                margin-top: 1.7rem;
                                height: 2.55rem;
                                border-radius: 8px;
                                background: {_css};
                                border: 1px solid rgba(0,0,0,0.28);
                                box-shadow: inset 0 0 0 1px rgba(255,255,255,0.25);
                            "></div>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            """
                            <div style="
                                margin-top: 1.7rem;
                                height: 2.55rem;
                                border-radius: 8px;
                                background: #ECEFF1;
                                border: 1px dashed #90A4AE;
                            "></div>
                            """,
                            unsafe_allow_html=True,
                        )
                with fld:
                    if _css:
                        st.markdown(
                            f"""
                            <div style="
                                border: 2px solid {_css};
                                border-radius: 10px;
                                padding: 0.15rem 0.35rem 0.35rem;
                                background: linear-gradient(90deg, {_css}30 0%, transparent 70%);
                                margin-bottom: 0.05rem;
                            ">
                              <div style="font-size:0.72rem;font-weight:600;opacity:0.9;">
                                {safe_colour}
                              </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                    trolley_label = st.selectbox(
                        "Trolley *",
                        options=[""] + trolley_labels,
                        key=_pk(f"trolley_{idx}"),
                        disabled=not bool(trolleys),
                    )

            trolley_name = trolley_label_to_name.get(trolley_label) if trolley_label else None
            trolley_w = float(trolley_by_name.get(trolley_name, 0)) if trolley_name else 0.0

            # Streamlit number_input ignores `value` after first render when `key` is set.
            # Sync tare whenever the selected trolley changes.
            tare_key = _pk(f"trolley_w_{idx}")
            prev_trolley_key = _pk(f"_prev_trolley_label_{idx}")
            if st.session_state.get(prev_trolley_key) != trolley_label:
                st.session_state[tare_key] = float(trolley_w)
                st.session_state[prev_trolley_key] = trolley_label
            elif tare_key not in st.session_state:
                st.session_state[tare_key] = float(trolley_w)

            r2c1, r2c2, r2c3, r2c4 = st.columns([1.5, 1.5, 1.5, 2])
            with r2c1:
                st.number_input(
                    "Trolley weight (kg)",
                    min_value=0.0,
                    step=0.1,
                    disabled=True,
                    key=tare_key,
                    help="Auto-filled from Trolley_Master when a trolley is selected.",
                )
            with r2c2:
                scale_w = empty_percent_input(
                    "Weighment Weight (kg) *",
                    key=_pk(f"scale_w_{idx}"),
                    max_value=None,
                    step=1.0,
                )
                wsp_open_key = _pk(f"wsp_open_{idx}")
                if st.button(
                    "📷 Weighment photo",
                    key=_pk(f"wsp_btn_{idx}"),
                    help="Open camera or choose a photo from the phone gallery",
                    use_container_width=True,
                ):
                    st.session_state[wsp_open_key] = not bool(
                        st.session_state.get(wsp_open_key)
                    )
                    st.rerun()

                scale_photo_bytes: bytes | None = None
                if st.session_state.get(wsp_open_key):
                    st.caption("Capture with camera or pick from gallery")
                    wsp_cam = st.camera_input(
                        "Camera",
                        key=_pk(f"wsp_cam_{idx}"),
                        help="Uses the phone camera when available.",
                    )
                    wsp_file = st.file_uploader(
                        "Gallery / files",
                        type=["png", "jpg", "jpeg", "webp"],
                        key=_pk(f"wsp_file_{idx}"),
                        help="Choose an existing photo from the device gallery.",
                    )
                    scale_photo_bytes = photo_bytes(wsp_cam) or photo_bytes(wsp_file)
                    if scale_photo_bytes:
                        st.session_state[_pk(f"wsp_bytes_{idx}")] = scale_photo_bytes
                        st.success("Weighment photo ready to save with this charge line.")
                else:
                    scale_photo_bytes = st.session_state.get(_pk(f"wsp_bytes_{idx}"))
                    if scale_photo_bytes:
                        st.caption("Weighment photo attached.")

            # Net charge = weighment scale − trolley tare (always recompute into widget state)
            tare_w = float(st.session_state.get(tare_key, trolley_w) or 0.0)
            scale_val = float(scale_w or 0)
            net_w = max(scale_val - tare_w, 0.0) if trolley_name and scale_val > 0 else 0.0
            net_key = _pk(f"wt_{idx}")
            if st.session_state.get(net_key) != float(net_w):
                st.session_state[net_key] = float(net_w)
            with r2c3:
                st.number_input(
                    "Net weight (kg)",
                    min_value=0.0,
                    step=0.1,
                    disabled=True,
                    key=net_key,
                    help="Auto: weighment scale weight − trolley weight.",
                )
            with r2c4:
                n = st.text_input("Line notes", key=_pk(f"ln_{idx}"))
                inp_open_key = _pk(f"inp_open_{idx}")
                if st.button(
                    "📷 Material Photo",
                    key=_pk(f"inp_btn_{idx}"),
                    help="Open camera or choose a photo from the phone gallery for Material Photo",
                    use_container_width=True,
                ):
                    st.session_state[inp_open_key] = not bool(
                        st.session_state.get(inp_open_key)
                    )
                    st.rerun()

            input_photo_bytes: bytes | None = None
            if st.session_state.get(inp_open_key):
                st.caption(f"Charge line {idx + 1} — Material Photo (camera or gallery)")
                inp_cam = st.camera_input(
                    "Input camera",
                    key=_pk(f"inp_cam_{idx}"),
                    help="Uses the phone camera when available.",
                )
                inp_file = st.file_uploader(
                    "Input gallery / files",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=_pk(f"inp_file_{idx}"),
                    help="Choose an existing photo from the device gallery.",
                )
                input_photo_bytes = photo_bytes(inp_cam) or photo_bytes(inp_file)
                if input_photo_bytes:
                    st.session_state[_pk(f"inp_bytes_{idx}")] = input_photo_bytes
                    st.success("Material Photo ready to save with this charge line.")
            else:
                input_photo_bytes = st.session_state.get(_pk(f"inp_bytes_{idx}"))
                if input_photo_bytes:
                    st.caption(f"Charge line {idx + 1}: Material Photo attached.")

            if mat and lot_label and trolley_name and scale_val > 0 and net_w > 0:
                charge_inputs.append(
                    {
                        "Raw_Material_Name": mat,
                        "Lot_id": lot_opts[lot_label],
                        "Weight": net_w,
                        "Weighment_scale_weight": scale_val,
                        "Trolley_weight": tare_w,
                        "Trolley_name": trolley_name,
                        "Notes": n,
                        "Weighment_scale_photo": scale_photo_bytes,
                        "Input_photo": input_photo_bytes,
                        "Charge_time": datetime.now().isoformat(timespec="seconds"),
                    }
                )

        pending_charges_key = _pk("pending_charges")
        if charge_inputs:
            st.session_state[pending_charges_key] = charge_inputs
        saved_pending_charges = st.session_state.get(pending_charges_key) or []

        add_col, rem_col, _ = st.columns([1, 1, 4])
        if _button_clicked(
            add_col.button(
                "Add charge line", key=_pk("add_charge"), disabled=locked
            ),
            _pk("add_charge"),
        ):
            drafts[furnace].append(
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            )
            st.rerun()
        if _button_clicked(
            rem_col.button(
                "Remove last line", key=_pk("rem_charge"), disabled=locked
            ),
            _pk("rem_charge"),
        ) and len(drafts[furnace]) > 1:
            drafts[furnace].pop()
            st.rerun()

        display_charges = charge_inputs or saved_pending_charges
        saved_in = sum(float(c.get("Weight") or 0) for c in saved_charges)
        extra_in = sum(float(c.get("Weight") or 0) for c in display_charges)
        total_in = saved_in + extra_in
        total_lines = len(saved_charges) + len(display_charges)
        if total_in > 0:
            st.session_state.pop(_pk("create_error"), None)
        st.info(
            f"Total net input weight: **{total_in:,.2f} kg** across {total_lines} charge line(s)."
        )

        top_save_clicked = False
        if not existing_batch:
            st.caption(
                "Create the heat to unlock degassing, samples, and chemistry. "
                "The batch is saved as **In-Progress**."
            )
            create_clicked = _button_clicked(
                st.button(
                    "Save and create production batch",
                    type="primary",
                    disabled=available_crucible is None,
                    key=_pk("create_batch"),
                    help=(
                        "Stores furnace, production date, shift, melt, alloy, and charge "
                        "lines. Assigns Batch ID (DDMMYY + furnace + shift + melt) and "
                        "Heat no (YY-furnace + month code + counter), then sets "
                        "Production_status to In-Progress."
                    ),
                ),
                _pk("create_batch"),
            )
            create_error_key = _pk("create_error")
            if available_crucible is None:
                st.error("No crucible available for the respective furnace.")
            elif not charge_inputs and not saved_pending_charges:
                st.caption("Add at least one charge line with net weight > 0.")
            if create_clicked:
                try:
                    if available_crucible is None:
                        raise ValueError(
                            "No crucible available for the respective furnace."
                        )
                    inputs_to_save = charge_inputs or list(saved_pending_charges)
                    total_save_weight = sum(
                        float(c.get("Weight") or 0) for c in inputs_to_save
                    )
                    if not inputs_to_save or total_save_weight <= 0:
                        raise ValueError(
                            "The Total net input weight must be greater than zero "
                            "to save and create a new batch."
                        )
                    if not preview_id:
                        raise ValueError(
                            preview_error or "Batch ID could not be generated."
                        )
                    if not heat_no_preview:
                        raise ValueError(
                            preview_error or "Heat no could not be generated."
                        )
                    if duplicate_id:
                        raise ValueError(
                            f"A production batch already exists for this date, "
                            f"furnace, shift, and melt no (Batch ID {duplicate_id}). "
                            "Open that batch instead of creating a duplicate."
                        )
                    bid = db.create_batch(
                        furnace=furnace,
                        alloy_id=alloy_id,
                        production_date=prod_date.isoformat(),
                        shift=shift,
                        melt_no=melt_no,
                        melting_team=melting_team,
                        notes=notes.strip(),
                        inputs=inputs_to_save,
                        composition={},
                        production_supervisor=production_supervisor,
                    )
                    drafts[furnace] = [
                        {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
                    ]
                    st.session_state.pop(pending_charges_key, None)
                    st.session_state.pop(_pk("_hydrated_batch_id"), None)
                    st.session_state.pop(create_error_key, None)
                    st.session_state["_pb_select_batch"] = bid
                    created = db.get_batch(bid) or {}
                    created_heat = created.get("Heat_no") or heat_no_preview
                    st.session_state["_pb_flash"] = (
                        f"Created batch **{bid}** with heat no **{created_heat}**. "
                        f"**Production status:** **{db.BATCH_STATUS_IN_PROGRESS}**. "
                        "Enter degassing, samples, and chemistry below."
                    )
                    st.rerun()
                except Exception as exc:
                    st.session_state[create_error_key] = str(exc)
            persist_error = st.session_state.get(create_error_key)
            if persist_error:
                st.error(persist_error)
        else:
            created_status = (
                existing_batch.get("Production_status") or db.BATCH_STATUS_IN_PROGRESS
            )
            st.success(
                f"**Batch ID:** `{preview_id}`  ·  "
                f"**Heat no:** `{heat_no_label}`  ·  "
                f"**Production status:** `{created_status}`"
            )
            if not is_completed:
                top_save_clicked = _button_clicked(
                    st.button(
                        "Save changes",
                        type="secondary",
                        disabled=locked,
                        key=_pk("save_batch_top"),
                        help=(
                            "Save alloy, notes, and new charge lines here so you "
                            "do not have to scroll past degassing, samples, and chemistry."
                        ),
                    ),
                    _pk("save_batch_top"),
                )
                st.caption(
                    "Saves header details and any new charge lines. "
                    "A matching **Save changes** button remains at the bottom."
                )

        later_locked = locked or existing_batch is None
        if later_locked and not existing_batch:
            st.info(
                "Degassing, samples, and chemistry stay locked until you "
                "**Save and create production batch**."
            )

        st.markdown("#### Degassing & piece counts")
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            degassing_time = st.text_input(
                "Degassing time",
                placeholder="e.g. 14:30 or 12 min",
                key=_pk("degassing_time"),
                disabled=later_locked,
            )
        with d2:
            sampled_pcs = empty_percent_input(
                "Sampled pcs",
                key=_pk("sampled_pcs"),
                max_value=None,
                step=1.0,
                disabled=later_locked,
            )
        with d3:
            defect_pcs = empty_percent_input(
                "Defect pcs",
                key=_pk("defect_pcs"),
                max_value=None,
                step=1.0,
                allow_zero=True,
                disabled=later_locked,
            )
        with d4:
            st.caption("K Mold Value = Defect pcs / Sampled pcs")
            if sampled_pcs and sampled_pcs > 0 and defect_pcs is not None:
                k_mold = float(defect_pcs) / float(sampled_pcs)
                css = "yield-bad" if k_mold > db.K_MOLD_MAX else "yield-ok"
                st.markdown(
                    f'<p class="{css}">K Mold Value<br>{k_mold:.3f}</p>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<p class="yield-ok">K Mold Value<br>—</p>',
                    unsafe_allow_html=True,
                )

        st.markdown("#### Sample results")
        sample_opts = [sample_blank] + db.SAMPLE_OK_STATUS
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            top_sample = st.selectbox(
                "Top sample", sample_opts, key=_pk("top_sample"), disabled=later_locked
            )
        with s2:
            middle_sample = st.selectbox(
                "Middle sample",
                sample_opts,
                key=_pk("middle_sample"),
                disabled=later_locked,
            )
        with s3:
            bottom_sample = st.selectbox(
                "Bottom sample",
                sample_opts,
                key=_pk("bottom_sample"),
                disabled=later_locked,
            )
        with s4:
            vacum_sample = st.selectbox(
                "Vacum sample",
                sample_opts,
                key=_pk("vacum_sample"),
                disabled=later_locked,
            )

        r1, r2, r3, r4 = st.columns(4)
        with r1:
            top_sample_remarks = st.text_input(
                "Remarks", key=_pk("top_sample_remarks"), disabled=later_locked
            )
        with r2:
            middle_sample_remarks = st.text_input(
                "Remarks", key=_pk("middle_sample_remarks"), disabled=later_locked
            )
        with r3:
            bottom_sample_remarks = st.text_input(
                "Remarks", key=_pk("bottom_sample_remarks"), disabled=later_locked
            )
        with r4:
            st.empty()

        d1, d2, d3, d4 = st.columns(4)
        with d1:
            top_sample_dt = ui_datetime_input(
                "Datetime",
                value=None,
                step=60,
                key=_pk("top_sample_dt"),
                help="Open the calendar icon to pick date and time.",
                disabled=later_locked,
            )
        with d2:
            middle_sample_dt = ui_datetime_input(
                "Datetime",
                value=None,
                step=60,
                key=_pk("middle_sample_dt"),
                help="Open the calendar icon to pick date and time.",
                disabled=later_locked,
            )
        with d3:
            bottom_sample_dt = ui_datetime_input(
                "Datetime",
                value=None,
                step=60,
                key=_pk("bottom_sample_dt"),
                help="Open the calendar icon to pick date and time.",
                disabled=later_locked,
            )
        with d4:
            st.empty()

        st.markdown("#### Batch chemistry (ladle / spectrometer)")
        st.caption(
            f"First {db.ENTRY_CHEM_ELEMENT_LIMIT} elements by Serial_no from Element_Master, "
            "plus **OE**, **OT**, and **SF**. "
            "Use **Open all elements…** for the full list. "
            "Each field shows this alloy’s min/max from Alloy_Master_spec. "
            "An entered % is highlighted in red if it is at or below min, or at or above max. "
            "**SF %** is calculated as Fe + 2×Mn + 3×Cr, rounded to the nearest tenth."
        )
        if not alloy_id:
            st.info("Select an alloy above to display spec ranges and validate ladle chemistry.")
        alloy_specs = db.get_alloy_specs(alloy_id) if alloy_id else {}
        entry_elements = db.list_batch_chem_elements()
        full_chem_key = _pk("full_chem")
        sync_batch_keys = {
            el["Element_Symbol"]: _pk(f"bchem_{el['Element_Symbol']}")
            for el in entry_elements
        }
        full_batch = st.session_state.get(full_chem_key) or {}
        bbtn1, bbtn2 = st.columns([2, 3])
        with bbtn1:
            if st.button(
                "Open all elements…",
                key=_pk("open_all_elements"),
                help="Enter chemistry for every row in Element_Master",
                use_container_width=True,
                disabled=later_locked,
            ):
                dialog_all_element_percentages(
                    full_chem_key,
                    defaults=full_batch,
                    sync_keys=sync_batch_keys,
                )
        with bbtn2:
            full_n = len([v for v in full_batch.values() if v and v > 0])
            if full_n:
                st.caption(f"Full Element_Master entry applied ({full_n} non-zero value(s)).")

        def _fmt_spec_pct(v: object) -> str:
            if v is None or v == "":
                return "—"
            try:
                return f"{float(v):.4f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                return "—"

        def _spec_out_of_range(value: float, spec: dict | None) -> bool:
            if not spec or value <= 0:
                return False
            mn, mx = spec.get("Min_percent"), spec.get("Max_percent")
            if mn is not None and mn != "" and value <= float(mn):
                return True
            if mx is not None and mx != "" and value >= float(mx):
                return True
            return False

        def _entered_chem(sym: str) -> float:
            if sym in batch_chem:
                try:
                    return float(batch_chem[sym] or 0.0)
                except (TypeError, ValueError):
                    return 0.0
            try:
                return float(st.session_state.get(_pk(f"bchem_{sym}")) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        chem_cols = st.columns(6)
        batch_chem: dict[str, float | None] = {}
        out_of_spec_keys: list[str] = []
        for i, el in enumerate(entry_elements):
            sym = el["Element_Symbol"]
            spec = alloy_specs.get(sym)
            with chem_cols[i % 6]:
                if sym == "SF":
                    sludge = (
                        1.0 * _entered_chem("Fe")
                        + 2.0 * _entered_chem("Mn")
                        + 3.0 * _entered_chem("Cr")
                    )
                    sf_val = round(sludge, 1)
                    st.session_state[_pk("bchem_SF")] = sf_val if sf_val > 0 else None
                    batch_chem[sym] = st.number_input(
                        "SF %",
                        min_value=0.0,
                        max_value=600.0,
                        value=None,
                        step=0.1,
                        key=_pk("bchem_SF"),
                        disabled=True,
                        placeholder="",
                        help="Auto: Sludge Factor = Fe + 2×Mn + 3×Cr, rounded to 0.1%.",
                    )
                else:
                    batch_chem[sym] = empty_percent_input(
                        f"{sym} %",
                        key=_pk(f"bchem_{sym}"),
                        default=full_batch.get(sym),
                        step=CHEM_PERCENT_STEP,
                        format=CHEM_PERCENT_FORMAT,
                        help=el["Element_Name"],
                        disabled=later_locked,
                    )
                entered = float(batch_chem[sym] or 0.0)
                bad = _spec_out_of_range(entered, spec)
                if spec:
                    spec_line = (
                        f"Spec min {_fmt_spec_pct(spec.get('Min_percent'))} / "
                        f"max {_fmt_spec_pct(spec.get('Max_percent'))}"
                    )
                elif alloy_id:
                    spec_line = "No spec for this element"
                else:
                    spec_line = "Select an alloy to see spec"
                css = "chem-spec-bad" if bad else "chem-spec"
                st.markdown(f'<p class="{css}">{spec_line}</p>', unsafe_allow_html=True)
                if bad:
                    out_of_spec_keys.append(_pk(f"bchem_{sym}"))

        if out_of_spec_keys:
            rules = "\n".join(
                f"div.st-key-{key} input {{ color: #c62828 !important; font-weight: 700; }}"
                for key in out_of_spec_keys
            )
            st.markdown(f"<style>{rules}</style>", unsafe_allow_html=True)

        def _sample_or_none(v: str) -> str | None:
            return None if v == sample_blank else v

        merged_chem = merge_percent_composition(batch_chem, full_chem_key)
        composition = {k: v for k, v in merged_chem.items() if v and v > 0}
        completion_gaps = db.production_batch_completion_gaps(
            degassing_time=degassing_time,
            sampled_pcs=sampled_pcs,
            defect_pcs=defect_pcs,
            top_sample=_sample_or_none(top_sample),
            middle_sample=_sample_or_none(middle_sample),
            bottom_sample=_sample_or_none(bottom_sample),
            vacum_sample=_sample_or_none(vacum_sample),
            top_sample_datetime=(
                top_sample_dt.isoformat(timespec="seconds") if top_sample_dt else None
            ),
            middle_sample_datetime=(
                middle_sample_dt.isoformat(timespec="seconds")
                if middle_sample_dt
                else None
            ),
            bottom_sample_datetime=(
                bottom_sample_dt.isoformat(timespec="seconds")
                if bottom_sample_dt
                else None
            ),
            chemistry_count=len(composition),
            charge_line_count=len(saved_charges) + len(charge_inputs),
        )
        if existing_batch and not is_completed:
            if completion_gaps:
                st.warning(
                    "Required before **Completed**: " + "; ".join(completion_gaps)
                )
            else:
                st.success(
                    "Required fields are filled. You can mark this heat **Completed**."
                )

        def _batch_kwargs() -> dict:
            return dict(
                alloy_id=alloy_id,
                production_date=prod_date.isoformat(),
                shift=shift,
                melt_no=melt_no,
                melting_team=melting_team,
                notes=notes.strip(),
                composition=composition,
                degassing_time=degassing_time.strip() or None,
                sampled_pcs=(
                    sampled_pcs if sampled_pcs and sampled_pcs > 0 else None
                ),
                defect_pcs=None if defect_pcs is None else float(defect_pcs),
                top_sample=_sample_or_none(top_sample),
                middle_sample=_sample_or_none(middle_sample),
                bottom_sample=_sample_or_none(bottom_sample),
                vacum_sample=_sample_or_none(vacum_sample),
                top_sample_remarks=top_sample_remarks.strip() or None,
                middle_sample_remarks=middle_sample_remarks.strip() or None,
                bottom_sample_remarks=bottom_sample_remarks.strip() or None,
                top_sample_datetime=(
                    top_sample_dt.isoformat(timespec="seconds")
                    if top_sample_dt
                    else None
                ),
                middle_sample_datetime=(
                    middle_sample_dt.isoformat(timespec="seconds")
                    if middle_sample_dt
                    else None
                ),
                bottom_sample_datetime=(
                    bottom_sample_dt.isoformat(timespec="seconds")
                    if bottom_sample_dt
                    else None
                ),
                production_supervisor=production_supervisor,
            )

        def _save_chemistry_page(*, mark_completed: bool) -> str:
            if not existing_batch:
                raise ValueError(
                    "Create the production batch before saving degassing, samples, or chemistry."
                )
            db.update_production_batch_input(
                preview_id,
                extra_inputs=charge_inputs,
                allow_completed=bool(
                    is_completed and is_admin and st.session_state.get(unlock_key)
                ),
                **_batch_kwargs(),
            )
            bid = preview_id
            if mark_completed:
                db.complete_production_batch(bid)
            drafts[furnace] = [
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            ]
            st.session_state.pop(full_chem_key, None)
            st.session_state.pop(_pk("_hydrated_batch_id"), None)
            return bid

        if existing_batch:
            b1, b2, _ = st.columns([1.4, 1.4, 2])
            save_clicked = _button_clicked(
                b1.button(
                    "Save history correction" if is_completed else "Save changes",
                    type="primary" if is_completed else "secondary",
                    disabled=locked,
                    key=_pk("save_batch"),
                ),
                _pk("save_batch"),
            ) or top_save_clicked
            complete_clicked = _button_clicked(
                b2.button(
                    "Mark as Completed",
                    type="primary",
                    disabled=locked or is_completed or bool(completion_gaps),
                    key=_pk("complete_batch"),
                    help=(
                        "Requires degassing time, sampled/defect pcs, K Mold ≤ "
                        f"{db.K_MOLD_MAX:g}, top/middle/bottom samples and datetimes, "
                        "vacum sample, batch chemistry, and at least one charge line."
                    ),
                ),
                _pk("complete_batch"),
            )
            if not is_completed and completion_gaps:
                st.caption(
                    "**Mark as Completed** stays disabled until: "
                    + "; ".join(completion_gaps)
                )
            if save_clicked:
                try:
                    bid = _save_chemistry_page(mark_completed=False)
                    if is_completed:
                        st.session_state["_pb_flash"] = (
                            f"Saved history correction for **{bid}**."
                        )
                    else:
                        st.session_state["_pb_flash"] = (
                            f"Saved **{bid}**. Production status remains "
                            f"**{existing_batch.get('Production_status') or db.BATCH_STATUS_IN_PROGRESS}**. "
                            "Mark Completed when the checklist is done, then enter **Batch Output**."
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            if complete_clicked:
                if completion_gaps:
                    st.error(
                        "Cannot mark Completed until these are entered: "
                        + "; ".join(completion_gaps)
                    )
                else:
                    try:
                        bid = _save_chemistry_page(mark_completed=True)
                        st.session_state["_pb_flash"] = (
                            f"Batch **{bid}** is **Completed** and locked. "
                            "Enter product and non-spec output on **Batch Output**."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

        _snapshot_furnace_widgets(furnace)


# ═══════════════════════════════════════════════════════════════════════════════
# 2a. Batch Output
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Batch Output":
    st.title("Batch Output")
    st.caption(
        "Enter metal that left a heat into **batch_output**. "
        "Each batch can have more than one output line: the product alloy selected "
        "on the batch, plus **Broken Ingot (78)**, **Furnace Empty (79)**, and "
        "**Not Ok Ingot (80)** for samples and portions taken out so they do not "
        "spoil the chemistry. Those three have no spec. "
        "Create and mark the batch **Completed** first on **Production Batch & Chemistry**. "
        "Saving product-alloy output posts that heat into **Finished Goods Inventory**. "
        "Dispatch those bundles on **Packing List**. "
        "Avg piece weight is net output ÷ pieces; product alloy pieces are typically "
        f"{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg and show in red if outside that range. "
        "On save, each output line stores material ₹/kg (charge lot cost ÷ total output kg) "
        "and overall ₹/kg (material + Cost of Conversion for the production month, "
        "or the previous available month if that month's rates are not in yet). "
        "Broken Ingot, Furnace Empty, and Not Ok Ingot are also stored as remelt lots "
        "in **Raw Material Inventory**, linked to this heat so they can be charged later."
    )

    batches = db.list_batches()
    if not batches:
        st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
    else:
        furnace_opts = ["All furnaces"] + sorted(
            {str(b["Furnace"]) for b in batches if b.get("Furnace")}
        )
        furnace_filter = st.selectbox("Furnace", furnace_opts, key="bo_furnace")
        filtered = [
            b
            for b in batches
            if furnace_filter == "All furnaces" or str(b.get("Furnace")) == furnace_filter
        ]
        if not filtered:
            st.info("No batches for this furnace.")
        else:
            labels = {
                f"{b['Batch_ID']}  |  {b.get('Alloy_name') or '—'}  |  "
                f"in={float(b.get('Input_Weight') or 0):.0f} kg  |  "
                f"out={float(b.get('Output_Weight') or 0):.0f} kg": b["Batch_ID"]
                for b in filtered
            }
            pick = st.selectbox("Batch *", list(labels.keys()), key="bo_batch")
            bid = labels[pick]
            batch = db.get_batch(bid)
            if not batch:
                st.error(f"Batch {bid} was not found.")
            else:
                alloy_name = next(
                    (b.get("Alloy_name") for b in filtered if b["Batch_ID"] == bid),
                    None,
                )
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Batch ID", batch["Batch_ID"])
                m2.metric("Furnace", batch.get("Furnace") or "—")
                m3.metric("Heat No", batch.get("Heat_no") or "—")
                m4.metric(
                    "Product alloy",
                    f"{batch.get('Alloy_id')} — {alloy_name or '—'}",
                )
                m5.metric(
                    "Status",
                    batch.get("Production_status") or "—",
                )
                st.caption(
                    f"Charge input: {float(batch.get('Input_Weight') or 0):,.2f} kg"
                )

                with st.expander("Charge input lines"):
                    charges = db.get_batch_inputs(bid)
                    if charges:
                        show_dataframe(df_from_rows(charges))
                    else:
                        st.caption("No charge lines on this batch.")

                render_batch_output_editor(batch, key_prefix="bo_page")

    st.subheader("Saved outputs")
    saved_all = df_from_rows(db.list_all_batch_outputs())
    if saved_all.empty:
        st.info("No output rows yet. Select a batch above and save output lines.")
    else:
        show_dataframe(saved_all, highlight_avg_piece=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 2b. Production Batches (browse — kept separate for efficient batch capture)
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Batches":
    st.title("Production Batches")
    st.caption(
        "Review existing production batches. "
        "New batches are created on **Production Batch & Chemistry**. "
        "Input is charge weight; output is entered on **Batch Output** after the heat "
        "is marked **Completed** "
        "(product alloy plus Broken Ingot / Furnace Empty / Not Ok Ingot)."
    )
    st.subheader("Existing batches")
    batches_df = df_from_rows(db.list_batches())
    if batches_df.empty:
        st.info("No batches yet. Create one under **Production Batch & Chemistry**.")
    else:
        show_dataframe(batches_df)


# ═══════════════════════════════════════════════════════════════════════════════
# Furnace Oil Consumption
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Furnace Oil Consumption":
    st.title("Furnace Oil Consumption")
    st.caption(
        "Production team: enter the overall furnace oil used for the day. "
        "Saving the same date updates that day's row. "
        "Inventory is rebuilt from purchases minus consumption."
    )

    stock = db.get_furnace_oil_stock()
    month_tot = db.furnace_oil_month_totals(date.today().year, date.today().month)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Current stock (L)", f"{stock:,.1f}")
    m2.metric("Purchased this month (L)", f"{month_tot['purchased']:,.1f}")
    m3.metric("Consumed this month (L)", f"{month_tot['consumed']:,.1f}")
    m4.metric("Unit", "Litre")

    with st.expander("Set opening stock", expanded=stock <= 0 and not db.list_furnace_oil_inventory(limit=1)):
        st.caption(
            "Use this once to load the tank balance before the first purchase. "
            "It is stored as an **Opening** receipt, not a vendor invoice."
        )
        o1, o2 = st.columns(2)
        with o1:
            open_date = ui_date_input(
                "Opening date", value=date.today(), key="fo_open_date"
            )
        with o2:
            open_qty = empty_percent_input(
                "Opening stock (litres) *",
                key="fo_open_qty",
                max_value=None,
                step=1.0,
            )
        if st.button("Save opening stock", key="fo_open_save"):
            if float(open_qty or 0) <= 0:
                st.error("Opening stock (litres) must be greater than zero.")
            else:
                try:
                    db.add_furnace_oil_purchase(
                        vendor_code=None,
                        invoice="OPENING",
                        invoice_date=open_date.isoformat(),
                        received_date=open_date.isoformat(),
                        quantity=float(open_qty),
                        purchase_type="Opening",
                        notes="Opening stock",
                    )
                    st.success(
                        f"Opening stock set to **{float(open_qty):,.1f} L** "
                        f"on {format_ui_date(open_date)}."
                    )
                    st.session_state["fo_open_qty"] = None
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save opening stock: {exc}")

    st.markdown("#### Daily consumption")
    c1, c2 = st.columns(2)
    with c1:
        cons_date = ui_date_input(
            "Consumption date *", value=date.today(), key="fo_cons_date"
        )
    with c2:
        cons_qty = empty_percent_input(
            "Quantity consumed (litres) *",
            key="fo_cons_qty",
            max_value=None,
            step=1.0,
        )
    cons_notes = st.text_input("Notes", key="fo_cons_notes")

    if st.button("Save consumption", type="primary", key="fo_cons_save"):
        qty_val = float(cons_qty or 0)
        if qty_val <= 0:
            st.error("Quantity consumed (litres) must be greater than zero.")
        else:
            try:
                db.add_furnace_oil_consumption(
                    consumption_date=cons_date.isoformat(),
                    quantity=qty_val,
                    notes=cons_notes.strip() or None,
                )
                st.success(
                    f"Saved **{qty_val:,.1f} L** for {format_ui_date(cons_date)}. "
                    f"Stock is now **{db.get_furnace_oil_stock():,.1f} L**."
                )
                st.session_state["fo_cons_qty"] = None
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

    st.subheader("Inventory ledger")
    ledger = df_from_rows(db.list_furnace_oil_inventory())
    if ledger.empty:
        st.info("No furnace oil inventory yet. Add an opening stock or a purchase.")
    else:
        show_dataframe(ledger)

    st.subheader("Recent consumption")
    used = df_from_rows(db.list_furnace_oil_consumption())
    if used.empty:
        st.info("No consumption entries yet.")
    else:
        show_dataframe(used)


# ═══════════════════════════════════════════════════════════════════════════════
# Electricity Consumption
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Electricity Consumption":
    st.title("Electricity Consumption")
    st.caption(
        "Select **EB Line 1** or **EB Line 2**, then enter that line's opening and "
        "closing power readings. Units consumed = closing reading − opening reading. "
        "Each line's opening reading is filled from that line's previous closing reading. "
        "Saving the same date and line updates that row."
    )

    line = st.radio(
        "Electricity line *",
        db.ELECTRICITY_LINES,
        horizontal=True,
        key="elec_line",
        help="The plant has two EB connections. Readings are stored separately for each line.",
    )

    month_tot = db.electricity_month_totals(date.today().year, date.today().month)
    by_line = month_tot.get("by_line") or {}
    latest = db.list_electricity_consumption(limit=1, line=line)
    last_close = latest[0]["Closing_reading"] if latest else None
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        f"{db.ELECTRICITY_LINE_1} this month",
        f"{float(by_line.get(db.ELECTRICITY_LINE_1) or 0):,.1f}",
    )
    m2.metric(
        f"{db.ELECTRICITY_LINE_2} this month",
        f"{float(by_line.get(db.ELECTRICITY_LINE_2) or 0):,.1f}",
    )
    m3.metric("Both lines this month", f"{month_tot['consumed']:,.1f}")
    m4.metric(
        f"Last closing ({line})",
        f"{float(last_close):,.1f}" if last_close is not None else "—",
    )

    cons_date = ui_date_input(
        "Consumption date *", value=date.today(), key="elec_date"
    )
    day = cons_date.isoformat()
    existing = db.get_electricity_consumption(day, line)
    prev_close = db.get_previous_electricity_closing(day, line)
    loaded_key = f"{day}|{line}"
    if st.session_state.get("elec_loaded_key") != loaded_key:
        st.session_state["elec_loaded_key"] = loaded_key
        if existing:
            st.session_state["elec_open"] = float(existing["Opening_reading"])
            st.session_state["elec_close"] = float(existing["Closing_reading"])
            st.session_state["elec_notes"] = existing.get("Notes") or ""
        else:
            st.session_state["elec_open"] = (
                float(prev_close) if prev_close is not None else None
            )
            st.session_state["elec_close"] = None
            st.session_state["elec_notes"] = ""

    r1, r2 = st.columns(2)
    with r1:
        opening = empty_percent_input(
            "Opening power reading *",
            key="elec_open",
            max_value=None,
            step=0.1,
            format="%.1f",
            help=(
                f"Filled from {line} previous closing ({float(prev_close):,.1f})."
                if prev_close is not None and not existing
                else f"Meter reading for {line} at the start of the day."
            ),
        )
    with r2:
        closing = empty_percent_input(
            "Closing power reading *",
            key="elec_close",
            max_value=None,
            step=0.1,
            format="%.1f",
            help=f"Meter reading for {line} at the end of the day.",
        )
    notes = st.text_input("Notes", key="elec_notes")

    try:
        open_val = float(opening) if opening is not None else None
        close_val = float(closing) if closing is not None else None
    except (TypeError, ValueError):
        open_val = close_val = None
    units = (
        close_val - open_val
        if open_val is not None and close_val is not None
        else None
    )
    u1, u2, u3 = st.columns(3)
    u1.metric("Opening", f"{open_val:,.1f}" if open_val is not None else "—")
    u2.metric("Closing", f"{close_val:,.1f}" if close_val is not None else "—")
    if units is None:
        u3.metric("Units consumed", "—")
    elif units < 0:
        u3.metric("Units consumed", f"{units:,.1f}")
        st.error("Closing reading must be greater than or equal to the opening reading.")
    else:
        u3.metric("Units consumed", f"{units:,.1f}")

    if st.button("Save electricity reading", type="primary", key="elec_save"):
        if open_val is None:
            st.error("Enter the opening power reading.")
        elif close_val is None:
            st.error("Enter the closing power reading.")
        else:
            try:
                saved_units = db.add_electricity_consumption(
                    consumption_date=day,
                    opening_reading=open_val,
                    closing_reading=close_val,
                    notes=notes.strip() or None,
                    line=line,
                )
                st.success(
                    f"Saved **{saved_units:,.1f} units** on **{line}** "
                    f"for {format_ui_date(cons_date)} "
                    f"(opening {open_val:,.1f} → closing {close_val:,.1f})."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

    st.subheader("Daily readings")
    rows = df_from_rows(db.list_electricity_consumption())
    if rows.empty:
        st.info("No electricity readings yet. Select a line and save the day's readings.")
    else:
        for col in ("Opening_reading", "Closing_reading", "Units_consumed"):
            if col in rows.columns:
                rows[col] = pd.to_numeric(rows[col], errors="coerce").round(1)
        show_dataframe(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Cost of Conversion
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Cost of Conversion":
    st.title("Cost of Conversion")
    st.caption(
        "Enter the finalized conversion rates for one calendar month. "
        "Saving the same month updates that row. "
        "**Total conversion rate per kg** is stored as the sum of the six rates "
        "and is added to every batch output's overall ₹/kg when outputs are saved "
        "(production month if that month is on file, otherwise the previous available month)."
    )

    today = date.today()
    ycol, mcol = st.columns(2)
    with ycol:
        year = st.selectbox(
            "Expense year *",
            options=list(range(today.year - 5, today.year + 2)),
            index=5,
            key="coc_year",
        )
    with mcol:
        month = st.selectbox(
            "Expense month *",
            options=list(range(1, 13)),
            format_func=lambda m: date(2000, m, 1).strftime("%B"),
            index=today.month - 1,
            key="coc_month",
        )
    expense = date(int(year), int(month), 1)
    existing = db.get_cost_of_conversion(expense)
    loaded_key = expense.isoformat()
    if st.session_state.get("coc_loaded_key") != loaded_key:
        st.session_state["coc_loaded_key"] = loaded_key
        for field, _label in db.CONVERSION_RATE_FIELDS:
            st.session_state[f"coc_{field}"] = (
                float(existing[field]) if existing else 0.0
            )

    st.markdown(f"#### Rates for **{expense.strftime('%B %Y')}**")
    rate_values: dict[str, float] = {}
    cols = st.columns(3)
    for idx, (field, label) in enumerate(db.CONVERSION_RATE_FIELDS):
        with cols[idx % 3]:
            rate_values[field] = st.number_input(
                f"{label} *",
                min_value=0.0,
                step=0.0001,
                format="%.4f",
                key=f"coc_{field}",
            )
    total = round(sum(float(v or 0) for v in rate_values.values()), 4)
    t1, t2 = st.columns(2)
    t1.metric("Total conversion rate (₹/kg)", f"{total:,.4f}")
    t2.caption(
        "Oil + electricity + labour + salaries + consumables + overheads. "
        "This total is stored on save for pricing queries."
    )

    if st.button("Save conversion rates", type="primary", key="coc_save"):
        try:
            saved_total = db.save_cost_of_conversion(
                expense_month=expense,
                **rate_values,
            )
            st.success(
                f"Saved conversion rates for **{expense.strftime('%B %Y')}**. "
                f"Total **{saved_total:,.4f}** per kg."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    st.subheader("Saved months")
    rows = df_from_rows(db.list_cost_of_conversion())
    if rows.empty:
        st.info("No conversion rates yet. Enter the six rates for a month and save.")
    else:
        rate_cols = [field for field, _label in db.CONVERSION_RATE_FIELDS] + [
            "total_conversion_rate_per_kg"
        ]
        for col in rate_cols:
            if col in rows.columns:
                rows[col] = pd.to_numeric(rows[col], errors="coerce").round(4)
        show_dataframe(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Material Recovery & Yield
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Material Recovery & Yield":
    st.title("Material Recovery & Yield Calculator")
    st.caption(
        f"Recovery % = (total recorded output / charge input) × 100. "
        f"Enter outputs on **Batch Output** after the heat is **Completed** "
        f"(product alloy plus Broken Ingot, Furnace Empty, and Not Ok Ingot). "
        f"Below **{db.YIELD_TARGET_PCT:.0f}%** is highlighted in red."
    )

    batches = db.list_batches()
    if not batches:
        st.info("No batches available.")
    else:
        labels = {
            f"{b['Batch_ID']}  |  {b.get('Alloy_name') or '—'}  |  "
            f"in={b.get('Input_Weight') or 0:.0f} kg  |  "
            f"out={b.get('Output_Weight') or 0:.0f} kg": b["Batch_ID"]
            for b in batches
        }
        pick = st.selectbox("Batch", list(labels.keys()))
        bid = labels[pick]
        batch = db.get_batch(bid)
        assert batch is not None

        render_batch_output_editor(batch, key_prefix="yield_out")
        batch = db.get_batch(bid)
        assert batch is not None

        input_w = float(batch.get("Input_Weight") or 0)
        output_rows = db.get_batch_outputs(bid)
        output_w = sum(float(r["Weight"] or 0) for r in output_rows)
        product_id = batch.get("Alloy_id")
        product_w = sum(
            float(r["Weight"] or 0)
            for r in output_rows
            if product_id is not None and int(r["Alloy_id"]) == int(product_id)
        )
        sidestream_w = sum(
            float(r["Weight"] or 0)
            for r in output_rows
            if db.is_sidestream_alloy(r["Alloy_id"])
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Charge input (kg)", f"{input_w:,.2f}")
        c2.metric("Product alloy (kg)", f"{product_w:,.2f}")
        c3.metric("Non-spec output (kg)", f"{sidestream_w:,.2f}")
        c4.metric("Total output (kg)", f"{output_w:,.2f}")

        if output_w > 0 and input_w > 0:
            result = db.calc_yield(input_w, output_w)
            pct = result["recovery_pct"]
            ok = pct >= db.YIELD_TARGET_PCT
            css = "yield-ok" if ok else "yield-bad"

            r1, r2, r3 = st.columns(3)
            r1.metric("Input weight", f"{result['input_weight']:,.2f} kg")
            r2.metric("Output weight", f"{result['output_weight']:,.2f} kg")
            r3.metric("Metal loss", f"{result['loss_kg']:,.2f} kg")

            st.markdown(
                f'<p class="{css}">Material recovery rate: {pct:.2f}%</p>',
                unsafe_allow_html=True,
            )
            if ok:
                st.success(f"Yield meets or exceeds the {db.YIELD_TARGET_PCT:.0f}% target.")
            else:
                st.error(
                    f"Yield is below the {db.YIELD_TARGET_PCT:.0f}% efficiency target — investigate melt loss."
                )

            bar = min(pct / 100.0, 1.0)
            st.progress(bar, text=f"Recovery {pct:.1f}% (target {db.YIELD_TARGET_PCT:.0f}%)")
        elif input_w <= 0:
            st.info("This batch has no charge input yet.")
        else:
            st.info("Save batch outputs above to calculate recovery.")


# ═══════════════════════════════════════════════════════════════════════════════
# Finished Goods Inventory
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Finished Goods Inventory":
    st.title("Finished Goods Inventory")
    st.caption(
        "Product-alloy output saved on **Batch Output** posts here as **Available**. "
        "Dispatch stock by entering a **Packing List**. "
        "Avg piece is output weight ÷ pieces; typical product alloy pieces are "
        f"**{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** and show in red "
        "if outside that range so you can check the piece count."
    )
    try:
        db.backfill_finished_goods_from_output()
    except Exception as exc:
        st.warning(f"Could not refresh finished goods from batch output: {exc}")

    all_fg = db.list_finished_goods()
    available_n = sum(
        1 for r in all_fg if r.get("Finished_Goods_Status") == db.FG_STATUS_AVAILABLE
    )
    dispatched_n = sum(
        1 for r in all_fg if r.get("Finished_Goods_Status") == db.FG_STATUS_DISPATCHED
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("Bundles", len(all_fg))
    m2.metric("Available", available_n)
    m3.metric("Dispatched", dispatched_n)
    if all_fg:
        show_dataframe(df_from_rows(all_fg), highlight_avg_piece=True)
    else:
        st.info(
            "No finished goods yet. Mark a heat **Completed**, then save product-alloy "
            "output on **Batch Output**."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Packing List
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Packing List":
    if st.session_state.get("pl_show_summary"):
        summary_id = st.session_state.get("pl_summary_id")
        if summary_id:
            summary = _packing_summary_from_saved(int(summary_id))
            if not summary:
                st.title("Packing List")
                st.error("That packing list was not found.")
                if st.button("Back to packing list", key="pl_summary_missing_back"):
                    st.session_state.pop("pl_show_summary", None)
                    st.session_state.pop("pl_summary_id", None)
                    st.rerun()
                st.stop()
        else:
            summary = _packing_summary_from_form()
        st.title("Packing List")
        _render_packing_list_summary(summary)
        st.stop()

    st.title("Packing List")
    st.caption(
        "Enter invoice details, then select **batch_id**s from Available finished goods. "
        "A heat can be packed in part: enter only the kg and pieces on this list. "
        "Saving as **Verified** subtracts that quantity from finished-goods inventory; "
        "the remainder stays Available. **Cancel packing list** returns packed qty to "
        "finished goods. After the test certificate is **Issued**, dispatch is final "
        "and only an Admin can reverse it. Avg piece is packed weight ÷ pieces; typical "
        f"product alloy pieces are **{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** "
        "and show in red if outside that range."
    )
    try:
        db.backfill_finished_goods_from_output()
    except Exception as exc:
        st.warning(f"Could not refresh finished goods from batch output: {exc}")

    def _clear_packing_form() -> None:
        keep = {"pl_load_pick"}
        for key in list(st.session_state.keys()):
            if str(key).startswith("pl_") and key not in keep:
                st.session_state.pop(key, None)

    def _cust_label(row: dict) -> str:
        return f"{row.get('Customer_name') or '—'} ({row.get('Cust_code') or '—'})"

    def _alloy_label(row: dict) -> str:
        return f"{row.get('Alloy_name') or '—'} (#{row.get('Alloy_id')})"

    existing_lists = db.list_packing_lists()
    po_numbers = db.list_packing_po_numbers()
    editing_id = st.session_state.get("pl_edit_id")
    edit_cert = (
        db.get_packing_list_certificate(int(editing_id)) if editing_id else None
    )
    issued_locked = bool(
        edit_cert and edit_cert.get("Status") == db.CERT_STATUS_ISSUED
    )

    top1, top2, top3 = st.columns([3, 1, 1])
    with top1:
        if editing_id:
            if issued_locked:
                st.warning(
                    f"Packing list **#{editing_id}** has issued test certificate "
                    f"**{edit_cert.get('Certificate_no') or ''}**. Dispatch is final. "
                    "Packed batches cannot be changed or cancelled here. An Admin "
                    "can reverse this from **Admin → Cancel issued certificate**."
                )
            else:
                st.info(
                    f"Editing packing list **#{editing_id}**. "
                    "Save to update, or start a new list."
                )
        else:
            st.markdown("#### New packing list")
    with top2:
        if st.button("View summary", key="pl_view_summary"):
            st.session_state["pl_show_summary"] = True
            st.session_state.pop("pl_summary_id", None)
            st.rerun()
    with top3:
        if st.button("New packing list", key="pl_new"):
            _clear_packing_form()
            st.rerun()

    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1:
        invoice_date = ui_date_input(
            "Invoice date *",
            value=date.today(),
            key="pl_invoice_date",
        )
    with r1c2:
        invoice_number = st.text_input("Invoice number *", key="pl_invoice")
    with r1c3:
        po_no = st.selectbox(
            "P.O. Number *",
            options=[""] + po_numbers,
            key="pl_po",
            help="Customer PO numbers from purchase_order.customer_po_no (not Cancelled).",
        )

    if st.session_state.get("pl_po_seen") != po_no:
        previous_po = st.session_state.get("pl_po_seen")
        st.session_state["pl_po_seen"] = po_no
        if previous_po is not None:
            for key in (
                "pl_cust",
                "pl_alloy",
                "pl_batches",
                "pl_batches_labels",
                "pl_batch_qty",
                "pl_cust_seen",
                "pl_alloy_seen",
            ):
                st.session_state.pop(key, None)

    customers = db.list_packing_po_customers(po_no) if po_no else []
    cust_opts = {_cust_label(c): c for c in customers}
    valid_cust = set(cust_opts) | {""}
    if st.session_state.get("pl_cust") not in valid_cust:
        wanted = st.session_state.get("pl_cust_code")
        match = next(
            (
                label
                for label, row in cust_opts.items()
                if str(row.get("Cust_code")) == str(wanted)
            ),
            "",
        )
        st.session_state["pl_cust"] = match
    if len(cust_opts) == 1 and not st.session_state.get("pl_cust"):
        st.session_state["pl_cust"] = next(iter(cust_opts))

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1:
        cust_label = st.selectbox(
            "Customer name *",
            options=[""] + list(cust_opts.keys()),
            key="pl_cust",
            help="Filled from this P.O. when it has one customer.",
        )
        cust_row = cust_opts.get(cust_label) or {}
        cust_code = cust_row.get("Cust_code")
        customer_name = cust_row.get("Customer_name")
        st.session_state["pl_cust_code"] = cust_code

        if st.session_state.get("pl_cust_seen") != cust_code:
            previous_cust = st.session_state.get("pl_cust_seen")
            st.session_state["pl_cust_seen"] = cust_code
            if previous_cust is not None:
                for key in (
                    "pl_alloy",
                    "pl_batches",
                    "pl_batches_labels",
                    "pl_batch_qty",
                    "pl_alloy_seen",
                ):
                    st.session_state.pop(key, None)

        alloys = (
            db.list_packing_po_alloys(po_no, cust_code)
            if po_no and cust_code
            else []
        )
        alloy_opts = {
            _alloy_label(a): a
            for a in alloys
            if a.get("Alloy_id") not in (None, "")
        }
        valid_alloy = set(alloy_opts) | {""}
        if st.session_state.get("pl_alloy") not in valid_alloy:
            wanted_aid = st.session_state.get("pl_alloy_id")
            match = next(
                (
                    label
                    for label, row in alloy_opts.items()
                    if str(row.get("Alloy_id")) == str(wanted_aid)
                ),
                "",
            )
            st.session_state["pl_alloy"] = match
        if len(alloy_opts) == 1 and not st.session_state.get("pl_alloy"):
            st.session_state["pl_alloy"] = next(iter(alloy_opts))

    with r2c2:
        alloy_label = st.selectbox(
            "Alloy Name *",
            options=[""] + list(alloy_opts.keys()),
            key="pl_alloy",
            help="Filled from this P.O. for the selected customer.",
        )
        alloy_row = alloy_opts.get(alloy_label) or {}
        alloy_id = alloy_row.get("Alloy_id")
        st.session_state["pl_alloy_id"] = alloy_id
        if alloy_id not in (None, ""):
            master = db.get_alloy(alloy_id) or alloy_row
            colour_code = master.get("Colour_code") or ""
        else:
            colour_code = ""
    with r2c3:
        st.text_input(
            "Colour code",
            value=colour_code,
            disabled=True,
            help="Filled from alloy_master for the selected alloy.",
        )

    r3c1, r3c2, r3c3 = st.columns(3)
    with r3c1:
        vehicle_no = st.text_input("Vehicle No", key="pl_vehicle")
    with r3c2:
        status = st.selectbox(
            "Packing list status *",
            options=db.PACKING_LIST_STATUS,
            key="pl_status",
            disabled=issued_locked,
            help="In-Progress does not take stock. Verified subtracts packed kg and pieces from finished goods.",
        )
    with r3c3:
        if editing_id and status == db.PACKING_STATUS_VERIFIED:
            if st.button("Open test certificate", key="pl_open_tc"):
                st.session_state.nav_page = "Test Certificate"
                st.session_state["tc_packing_list_id"] = int(editing_id)
                st.rerun()
        else:
            st.empty()

    if st.session_state.get("pl_alloy_seen") != alloy_id:
        previous_alloy = st.session_state.get("pl_alloy_seen")
        st.session_state["pl_alloy_seen"] = alloy_id
        if previous_alloy is not None:
            st.session_state.pop("pl_batches", None)
            st.session_state.pop("pl_batches_labels", None)
            st.session_state.pop("pl_batch_qty", None)

    st.markdown("#### Batch IDs")
    f1, f2 = st.columns(2)
    match_name = f1.checkbox(
        "Filter by alloy name",
        value=True,
        key="pl_match_name",
        help="Include heats whose alloy_name matches the packing-list alloy (or its group).",
    )
    match_group = f2.checkbox(
        "Filter by alloy group",
        value=True,
        key="pl_match_group",
        help="Include heats whose alloy_group matches the packing-list alloy.",
    )

    include_ids = [
        str(b) for b in (st.session_state.get("pl_batches") or []) if b
    ]
    dispatchable: list[dict] = []
    blocked: list[dict] = []
    if alloy_id not in (None, ""):
        try:
            dispatchable, blocked = db.list_packing_batch_candidates(
                int(alloy_id),
                match_name=bool(match_name),
                match_group=bool(match_group),
                include_batch_ids=include_ids,
                packing_list_id=int(editing_id) if editing_id else None,
                packing_list_status=status,
            )
        except Exception as exc:
            st.error(str(exc))

    selected_batch_ids: list[str] = []
    selected_lines: list[dict] = []
    by_id: dict[str, dict] = {
        str(r["Batch_ID"]): r for r in dispatchable
    }

    if not po_no:
        st.info("Select a P.O. Number to load customers and alloys.")
    elif not cust_code:
        st.info("Select the customer on this purchase order.")
    elif alloy_id in (None, ""):
        st.info("Select the alloy on this purchase order, then pick batch IDs.")
    else:
        if dispatchable:
            st.caption(
                "Tick each heat and enter the **kg** and **pieces** to pack. "
                "On-hand is what is still in finished goods. Saving as **Verified** "
                "subtracts the packed quantity; leftover kg and pieces stay in inventory. "
                f"Avg piece outside **{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** "
                "is shown in red."
            )
            saved_qty = st.session_state.get("pl_batch_qty") or {}
            pick_rows = []
            for r in dispatchable:
                bid = str(r["Batch_ID"])
                on_hand_w = float(r.get("On_hand_weight") or 0)
                on_hand_p = int(float(r.get("On_hand_pieces") or 0))
                saved = saved_qty.get(bid) or {}
                if saved.get("Weight") not in (None, ""):
                    pack_w = float(saved.get("Weight") or 0)
                elif float(r.get("Packed_Weight") or 0) > 0:
                    pack_w = float(r.get("Packed_Weight") or 0)
                else:
                    pack_w = on_hand_w
                if saved.get("Pieces") not in (None, ""):
                    pack_p = int(float(saved.get("Pieces") or 0))
                elif int(float(r.get("Packed_Pieces") or 0)) > 0:
                    pack_p = int(float(r.get("Packed_Pieces") or 0))
                else:
                    pack_p = on_hand_p
                pick_rows.append(
                    {
                        "Select": bid in set(include_ids),
                        "Batch_ID": bid,
                        "Heat_no": str(r.get("Heat_no") or "—"),
                        "On hand (kg)": on_hand_w,
                        "On hand pieces": on_hand_p,
                        "Weight (kg)": pack_w,
                        "Pieces": pack_p,
                        "Avg_piece_kg": db.alloy_piece_avg_kg(pack_w, pack_p),
                        "Alloy": r.get("Alloy_name") or "—",
                        "Alloy_id": r.get("Alloy_id"),
                    }
                )
            pick_df = pd.DataFrame(pick_rows)
            editor_key = (
                f"pl_batch_editor_{alloy_id}_"
                f"{int(bool(match_name))}_{int(bool(match_group))}_"
                f"{int(editing_id or 0)}_qty2"
            )
            prev_edit = st.session_state.get(editor_key)
            if isinstance(prev_edit, pd.DataFrame) and not prev_edit.empty:
                if {"Weight (kg)", "Pieces"}.issubset(prev_edit.columns):
                    prev_edit = prev_edit.copy()
                    prev_edit["Avg_piece_kg"] = [
                        db.alloy_piece_avg_kg(w, p)
                        for w, p in zip(prev_edit["Weight (kg)"], prev_edit["Pieces"])
                    ]
                    st.session_state[editor_key] = prev_edit
            edited = st.data_editor(
                pick_df,
                column_config={
                    "Select": st.column_config.CheckboxColumn(
                        "Select",
                        help="Include this heat on the packing list.",
                    ),
                    "On hand (kg)": st.column_config.NumberColumn(
                        format="%.2f",
                        help="Quantity still in finished-goods inventory.",
                    ),
                    "On hand pieces": st.column_config.NumberColumn(
                        format="%d",
                        help="Pieces still in finished-goods inventory.",
                    ),
                    "Weight (kg)": st.column_config.NumberColumn(
                        format="%.2f",
                        min_value=0.0,
                        help="Kilograms to pack on this list.",
                    ),
                    "Pieces": st.column_config.NumberColumn(
                        format="%d",
                        min_value=0,
                        step=1,
                        help="Whole pieces to pack on this list.",
                    ),
                    "Avg_piece_kg": st.column_config.NumberColumn(
                        "Avg piece (kg)",
                        format="%.2f",
                        help=(
                            f"Packed weight ÷ pieces. Typical range "
                            f"{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg."
                        ),
                    ),
                    "Alloy_id": None,
                },
                disabled=[
                    "Batch_ID",
                    "Heat_no",
                    "On hand (kg)",
                    "On hand pieces",
                    "Avg_piece_kg",
                    "Alloy",
                    "Alloy_id",
                ],
                hide_index=True,
                use_container_width=True,
                key=editor_key,
            )
            for row in edited.to_dict("records"):
                if not row.get("Select"):
                    continue
                bid = str(row.get("Batch_ID") or "").strip()
                if not bid:
                    continue
                selected_batch_ids.append(bid)
                selected_lines.append(
                    {
                        "Batch_ID": bid,
                        "Weight": float(row.get("Weight (kg)") or 0),
                        "Pieces": int(float(row.get("Pieces") or 0)),
                        "Alloy_id": row.get("Alloy_id") or by_id.get(bid, {}).get("Alloy_id"),
                        "Heat_no": row.get("Heat_no"),
                    }
                )
            range_warnings = []
            over_max = []
            for line in selected_lines:
                cand = by_id.get(line["Batch_ID"]) or {}
                avg = db.alloy_piece_avg_kg(line.get("Weight"), line.get("Pieces"))
                if db.alloy_piece_avg_out_of_range(avg, line.get("Alloy_id")):
                    range_warnings.append(
                        f"{line['Batch_ID']}: {avg:.2f} kg/pc"
                    )
                max_w = float(cand.get("Max_weight") or 0)
                max_p = int(float(cand.get("Max_pieces") or 0))
                if line["Weight"] - max_w > 0.0005 or line["Pieces"] > max_p:
                    over_max.append(
                        f"{line['Batch_ID']} (max {max_w:g} kg / {max_p} pieces)"
                    )
            if range_warnings:
                st.warning(
                    "Avg piece is outside "
                    f"**{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg**: "
                    + "; ".join(range_warnings)
                )
            if over_max:
                st.error(
                    "Packed quantity exceeds remaining finished goods: "
                    + "; ".join(over_max)
                )
        else:
            st.warning(
                "No Available finished-goods batches match this alloy name or group. "
                "Mark the heat **Completed**, then save product-alloy output on **Batch Output**."
            )

        st.markdown("##### Selected batches")
        if selected_lines:
            selected_df = pd.DataFrame(
                [
                    {
                        "Batch_ID": r["Batch_ID"],
                        "Heat_no": str(r.get("Heat_no") or "—"),
                        "Weight (kg)": float(r.get("Weight") or 0),
                        "Pieces": int(float(r.get("Pieces") or 0)),
                        "Avg_piece_kg": db.alloy_piece_avg_kg(
                            r.get("Weight"), r.get("Pieces")
                        ),
                        "Alloy_id": r.get("Alloy_id"),
                    }
                    for r in selected_lines
                ]
            )
            show_dataframe(selected_df, highlight_avg_piece=True)
            t1, t2, t3 = st.columns(3)
            t1.metric("Batches", len(selected_lines))
            t2.metric(
                "Total weight (kg)",
                f"{float(selected_df['Weight (kg)'].sum()):,.2f}",
            )
            t3.metric(
                "Total pieces",
                f"{int(selected_df['Pieces'].sum())}",
            )
        else:
            st.caption("Tick one or more rows above and enter packed kg and pieces.")

        if blocked:
            with st.expander(
                f"Matching heats not ready to dispatch ({len(blocked)})",
                expanded=True,
            ):
                show_dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Batch_ID": r["Batch_ID"],
                                "Heat_no": str(r.get("Heat_no") or "—"),
                                "On hand (kg)": float(r.get("On_hand_weight") or 0),
                                "On hand pieces": int(float(r.get("On_hand_pieces") or 0)),
                                "Status": r.get("Production_status") or "—",
                                "Reason": r.get("Reason") or "",
                            }
                            for r in blocked
                        ]
                    )
                )

    st.session_state["pl_batches"] = selected_batch_ids
    st.session_state["pl_batch_qty"] = {
        str(r["Batch_ID"]): {
            "Weight": r["Weight"],
            "Pieces": r["Pieces"],
            "Heat_no": r.get("Heat_no"),
        }
        for r in selected_lines
    }

    save_col, cancel_col = st.columns(2)
    can_cancel_list = bool(
        editing_id
        and not issued_locked
        and status == db.PACKING_STATUS_VERIFIED
    )
    with save_col:
        save_clicked = st.button(
            "Save packing list",
            type="primary",
            key="pl_save",
            disabled=issued_locked,
        )
    with cancel_col:
        cancel_list_clicked = st.button(
            "Cancel packing list",
            key="pl_cancel_list",
            disabled=not can_cancel_list,
            help="Returns packed kg and pieces to finished goods and sets the list to In-Progress.",
        )

    if cancel_list_clicked and editing_id:
        try:
            cancelled = db.cancel_packing_list(int(editing_id))
            st.session_state["pl_status"] = db.PACKING_STATUS_IN_PROGRESS
            st.success(
                f"Packing list **#{cancelled.get('Packing_list_id')}** cancelled. "
                "Packed quantity is back in finished goods."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if save_clicked:
        if not invoice_number.strip():
            st.error("Invoice number is required.")
        elif not po_no:
            st.error("P.O. Number is required.")
        elif not cust_code:
            st.error("Customer name is required.")
        elif alloy_id in (None, ""):
            st.error("Alloy Name is required.")
        else:
            over_max = []
            for line in selected_lines:
                cand = by_id.get(line["Batch_ID"]) or {}
                max_w = float(cand.get("Max_weight") or 0)
                max_p = int(float(cand.get("Max_pieces") or 0))
                if line["Weight"] - max_w > 0.0005 or line["Pieces"] > max_p:
                    over_max.append(
                        f"{line['Batch_ID']} (max {max_w:g} kg / {max_p} pieces)"
                    )
            if over_max:
                st.error(
                    "Packed quantity exceeds remaining finished goods: "
                    + "; ".join(over_max)
                )
            else:
                try:
                    pid = db.save_packing_list(
                        packing_list_id=int(editing_id) if editing_id else None,
                        invoice_date=to_storage_date(invoice_date),
                        invoice_number=invoice_number,
                        customer_po_no=po_no,
                        cust_code=cust_code,
                        customer_name=customer_name,
                        alloy_id=int(alloy_id),
                        colour_code=colour_code or None,
                        vehicle_no=vehicle_no,
                        packing_list_status=status,
                        batch_lines=selected_lines,
                    )
                    st.session_state["pl_edit_id"] = pid
                    st.success(
                        f"Saved packing list **#{pid}** as **{status}** "
                        f"({len(selected_batch_ids)} batch"
                        f"{'' if len(selected_batch_ids) == 1 else 'es'})."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    st.divider()
    st.markdown("#### Existing packing lists")
    if not existing_lists:
        st.info("No packing lists yet.")
    else:
        show_dataframe(df_from_rows(existing_lists))
        load_opts = {
            (
                f"#{r['Packing_list_id']}  |  {r.get('Invoice_number') or '—'}  |  "
                f"{r.get('Customer_name') or '—'}  |  {r.get('Packing_list_status')}"
                f"  |  {r.get('Certificate_status') or 'no certificate'}"
            ): int(r["Packing_list_id"])
            for r in existing_lists
        }
        load_label = st.selectbox(
            "Load packing list",
            options=[""] + list(load_opts.keys()),
            key="pl_load_pick",
        )
        load_col, summary_col = st.columns(2)
        with load_col:
            load_clicked = st.button("Load selected", key="pl_load")
        with summary_col:
            summary_clicked = st.button("View summary", key="pl_view_saved_summary")
        if summary_clicked and load_label:
            st.session_state["pl_show_summary"] = True
            st.session_state["pl_summary_id"] = load_opts[load_label]
            st.rerun()
        if load_clicked and load_label:
            header = db.get_packing_list(load_opts[load_label])
            if not header:
                st.error("That packing list was not found.")
            else:
                _clear_packing_form()
                st.session_state["pl_edit_id"] = int(header["Packing_list_id"])
                st.session_state["pl_invoice_date"] = _parse_master_date(
                    header.get("Invoice_date")
                )
                st.session_state["pl_invoice"] = header.get("Invoice_number") or ""
                po_loaded = header.get("Customer_PO_No") or ""
                st.session_state["pl_po"] = po_loaded
                st.session_state["pl_po_seen"] = po_loaded
                st.session_state["pl_cust_code"] = header.get("Cust_code")
                st.session_state["pl_cust"] = _cust_label(
                    {
                        "Customer_name": header.get("Customer_name"),
                        "Cust_code": header.get("Cust_code"),
                    }
                )
                st.session_state["pl_cust_seen"] = header.get("Cust_code")
                aid = header.get("Alloy_id")
                st.session_state["pl_alloy_id"] = aid
                st.session_state["pl_alloy"] = (
                    _alloy_label(
                        {"Alloy_name": header.get("Alloy_name"), "Alloy_id": aid}
                    )
                    if aid not in (None, "")
                    else ""
                )
                st.session_state["pl_alloy_seen"] = aid
                st.session_state["pl_vehicle"] = header.get("Vehicle_no") or ""
                st.session_state["pl_status"] = (
                    header.get("Packing_list_status") or db.PACKING_STATUS_IN_PROGRESS
                )
                loaded_batches = header.get("batches") or []
                st.session_state["pl_batches"] = [
                    str(r["Batch_ID"]) for r in loaded_batches if r.get("Batch_ID")
                ]
                st.session_state["pl_batch_qty"] = {
                    str(r["Batch_ID"]): {
                        "Weight": float(r.get("Weight") or 0),
                        "Pieces": int(float(r.get("Pieces") or 0)),
                        "Heat_no": r.get("Heat_no"),
                    }
                    for r in loaded_batches
                    if r.get("Batch_ID")
                }
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Test Certificate
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Test Certificate":
    st.title("Test Certificate")
    st.caption(
        "Print dispatch weights from a **Verified** packing list. Complete **Visual "
        "inspection** first (OK / NOT OK and Verified on every item). Merge heats onto "
        "one printed line and round **up** total kg by at most "
        f"**{db.CERT_WEIGHT_ROUND_MAX_PCT:g}%**. Pieces must stay exact. "
        "**Issue** is the final dispatch step. After that, packed inventory cannot be "
        "reversed here — only an Admin can cancel an issued certificate."
    )

    try:
        verified_lists = db.list_packing_lists_for_certificate()
    except Exception as exc:
        _show_db_connection_error(exc)
        st.stop()

    if not verified_lists:
        st.info("Verify a packing list first, then open it here.")
        st.stop()

    list_opts = {
        (
            f"#{r['Packing_list_id']}  |  {r.get('Invoice_number') or '—'}  |  "
            f"{r.get('Customer_name') or '—'}  |  "
            f"{r.get('Certificate_status') or 'no certificate'}"
        ): int(r["Packing_list_id"])
        for r in verified_lists
    }
    preset_id = st.session_state.get("tc_packing_list_id")
    labels = list(list_opts.keys())
    default_ix = 0
    if preset_id:
        for i, lab in enumerate(labels):
            if list_opts[lab] == int(preset_id):
                default_ix = i
                break
    pick = st.selectbox(
        "Verified packing list",
        options=labels,
        index=default_ix,
        key="tc_list_pick",
    )
    packing_list_id = list_opts[pick]
    if st.session_state.get("tc_packing_list_id") != packing_list_id:
        st.session_state["tc_packing_list_id"] = packing_list_id
        st.session_state.pop("tc_lines", None)
        st.session_state.pop("tc_loaded_id", None)
        st.session_state.pop("tc_show_print", None)
        st.session_state.pop("tc_show_void", None)

    header = db.get_packing_list(packing_list_id)
    if not header:
        st.error("That packing list was not found.")
        st.stop()

    cert = db.get_packing_list_certificate(packing_list_id)
    if st.session_state.get("tc_show_print") and cert:
        if (
            st.session_state.get("tc_loaded_id") != packing_list_id
            or "tc_lines" not in st.session_state
        ):
            st.session_state["tc_lines"] = [dict(row) for row in (cert.get("lines") or [])]
            st.session_state["tc_loaded_id"] = packing_list_id
        payload = _render_certificate_print(
            header,
            cert,
            st.session_state.get("tc_lines") or cert.get("lines") or [],
        )
        cert_no = str(payload.get("certificate_no") or "test-certificate").strip()
        pdf_name = re.sub(r"[^\w.-]+", "_", cert_no) + ".pdf"
        try:
            pdf_bytes = _certificate_pdf_bytes(payload)
        except Exception as exc:
            pdf_bytes = None
            st.error(f"PDF could not be created: {exc}")
        b1, b2, b3, _b4 = st.columns([1.1, 0.9, 1.3, 2])
        with b1:
            if st.button("Back to editor", key="tc_print_back"):
                st.session_state.pop("tc_show_print", None)
                st.rerun()
        with b2:
            st.markdown(
                '<button class="no-print" onclick="window.print()" '
                'style="padding:0.45rem 1rem;border:1px solid #ccc;border-radius:0.5rem;'
                'background:#fff;cursor:pointer;font-size:0.9rem;">Print</button>',
                unsafe_allow_html=True,
            )
        with b3:
            if pdf_bytes:
                st.download_button(
                    "Download PDF",
                    data=pdf_bytes,
                    file_name=pdf_name,
                    mime="application/pdf",
                    key="tc_download_pdf",
                )
        st.stop()

    packed_batches = list(header.get("batches") or [])
    packed_w = sum(float(r.get("Weight") or 0) for r in packed_batches)
    packed_p = sum(int(float(r.get("Pieces") or 0)) for r in packed_batches)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Packed kg", f"{packed_w:,.2f}")
    m2.metric("Packed pieces", f"{packed_p:,}")
    m3.metric("Invoice", header.get("Invoice_number") or "—")
    m4.metric("Alloy", header.get("Alloy_name") or "—")
    st.caption(
        f"Customer: {header.get('Customer_name') or '—'}  ·  "
        f"PO: {header.get('Customer_PO_No') or '—'}  ·  "
        f"Vehicle: {header.get('Vehicle_no') or '—'}"
    )

    insp_locked = bool(cert and cert.get("Status") == db.CERT_STATUS_ISSUED)
    try:
        inspection = db.get_visual_inspection(packing_list_id)
    except Exception as exc:
        st.error(str(exc))
        inspection = [
            {
                "Question_no": index,
                "Question_text": text,
                "Answer": "",
                "Verified": 0,
            }
            for index, text in enumerate(db.VISUAL_INSPECTION_QUESTIONS, start=1)
        ]

    st.markdown("#### Visual inspection")
    st.caption(
        "Complete every check as **OK** or **NOT OK**, then tick **Verified**. "
        "All items must be OK and Verified before the test certificate can be generated."
    )
    answer_choices = ["", *db.SAMPLE_OK_STATUS]
    inspection_rows: list[dict] = []
    for row in inspection:
        qno = int(row.get("Question_no") or 0)
        text = str(row.get("Question_text") or "")
        saved_answer = str(row.get("Answer") or "").strip()
        q_col, a_col, v_col = st.columns([5.0, 2.4, 1.8])
        q_col.markdown(f"**{qno}.** {text}")
        answer = a_col.selectbox(
            f"Answer {qno}",
            options=answer_choices,
            index=(
                answer_choices.index(saved_answer)
                if saved_answer in answer_choices
                else 0
            ),
            format_func=lambda value: "Answer" if value == "" else value,
            key=f"tc_insp_ans_{packing_list_id}_{qno}",
            disabled=insp_locked,
            label_visibility="collapsed",
        )
        verified = v_col.checkbox(
            "Verified",
            value=bool(row.get("Verified")),
            key=f"tc_insp_ver_{packing_list_id}_{qno}",
            disabled=insp_locked,
        )
        inspection_rows.append(
            {
                "Question_no": qno,
                "Question_text": text,
                "Answer": str(answer or "").strip(),
                "Verified": 1 if verified else 0,
            }
        )
    insp_errors = db.visual_inspection_errors(inspection_rows)
    insp_ready = not insp_errors
    if insp_errors:
        for msg in insp_errors:
            st.warning(msg)
    else:
        st.success("Visual inspection is complete. You can generate the test certificate.")

    if not insp_locked:
        if st.button("Save inspection", key="tc_insp_save"):
            try:
                db.save_visual_inspection(packing_list_id, inspection_rows)
                st.success("Visual inspection saved.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    has_deviations, has_letter = _render_tc_spec_and_deviation(
        packing_list_id,
        locked=insp_locked,
    )
    spec_blocked = has_deviations and not has_letter

    if cert is None or cert.get("Status") == db.CERT_STATUS_VOID:
        label = (
            "Create new draft from packed batches"
            if cert and cert.get("Status") == db.CERT_STATUS_VOID
            else "Create draft from packed batches"
        )
        if st.button(
            label,
            type="primary",
            key="tc_create",
            disabled=not insp_ready or spec_blocked,
        ):
            try:
                db.save_visual_inspection(packing_list_id, inspection_rows)
                cert = db.create_packing_list_certificate_draft(packing_list_id)
                st.session_state["tc_lines"] = cert.get("lines") or []
                st.session_state["tc_loaded_id"] = packing_list_id
                st.session_state["tc_editor_n"] = (
                    int(st.session_state.get("tc_editor_n") or 0) + 1
                )
                st.success(f"Draft **{cert.get('Certificate_no')}** created.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if not insp_ready:
            st.info("Finish visual inspection before generating the test certificate.")
        elif spec_blocked:
            st.info(
                "Upload the customer acceptance of deviation letter before "
                "generating the test certificate."
            )
        if cert is None:
            st.stop()
        st.caption(
            f"Previous certificate {cert.get('Certificate_no')} is **Void**. "
            "Create a new draft to edit again."
        )
        if cert.get("Status") == db.CERT_STATUS_VOID and not st.session_state.get("tc_show_void"):
            if st.button("View voided certificate", key="tc_view_void"):
                st.session_state["tc_show_void"] = True
                st.session_state["tc_show_print"] = True
                st.rerun()
            st.stop()

    locked = cert.get("Status") in {db.CERT_STATUS_ISSUED, db.CERT_STATUS_VOID}
    if (
        st.session_state.get("tc_loaded_id") != packing_list_id
        or "tc_lines" not in st.session_state
    ):
        st.session_state["tc_lines"] = [dict(row) for row in (cert.get("lines") or [])]
        st.session_state["tc_loaded_id"] = packing_list_id

    if "tc_cert_no" not in st.session_state or st.session_state.get("tc_cert_no_for") != packing_list_id:
        st.session_state["tc_cert_no"] = cert.get("Certificate_no") or f"TC-{packing_list_id:04d}"
        st.session_state["tc_cert_no_for"] = packing_list_id
        issued_raw = cert.get("Issued_date")
        st.session_state["tc_issued_date"] = (
            _parse_master_date(issued_raw) if issued_raw else date.today()
        )

    h1, h2, h3 = st.columns(3)
    with h1:
        certificate_no = st.text_input(
            "Certificate no",
            key="tc_cert_no",
            disabled=locked,
        )
    with h2:
        issued_date = ui_date_input(
            "Issued date",
            key="tc_issued_date",
            disabled=locked,
        )
    with h3:
        st.text_input(
            "Status",
            value=cert.get("Status") or "",
            disabled=True,
            key="tc_status_display",
        )

    lines = list(st.session_state.get("tc_lines") or [])
    editor_rows = []
    for line in lines:
        sources = line.get("sources") or []
        source_kg = sum(float(src.get("Source_weight") or 0) for src in sources)
        editor_rows.append(
            {
                "Select": bool(line.get("_selected")),
                "Line_no": int(line.get("Line_no") or 0),
                "Heat no": db.certificate_display_heat_no(line.get("Display_heat_no")),
                "Batch IDs": ", ".join(
                    str(src.get("Batch_ID") or "")
                    for src in sources
                    if src.get("Batch_ID")
                ),
                "Source kg": source_kg,
                "Printed kg": float(line.get("Weight") or 0),
                "Pieces": int(float(line.get("Pieces") or 0)),
                "Blended": "Yes" if line.get("Is_blended") else "No",
            }
        )
    editor_df = pd.DataFrame(editor_rows)
    editor_key = (
        f"tc_editor_{packing_list_id}_"
        f"{int(st.session_state.get('tc_editor_n') or 0)}_h2"
    )
    edited = st.data_editor(
        editor_df,
        column_config={
            "Select": st.column_config.CheckboxColumn(
                "Select",
                help="Tick lines to merge, or one merged line to split.",
                disabled=locked,
            ),
            "Line_no": st.column_config.NumberColumn("Line", format="%d"),
            "Heat no": st.column_config.TextColumn(
                "Heat no",
                help="Printed heat number on the test certificate.",
                disabled=locked,
            ),
            "Source kg": st.column_config.NumberColumn(format="%.2f"),
            "Printed kg": st.column_config.NumberColumn(
                format="%.2f",
                min_value=0.0,
                help="May be rounded up. Total round-up capped at 0.15%.",
                disabled=locked,
            ),
            "Pieces": st.column_config.NumberColumn(format="%d"),
        },
        disabled=["Line_no", "Batch IDs", "Source kg", "Pieces", "Blended"],
        hide_index=True,
        use_container_width=True,
        key=editor_key,
    )
    lines = _tc_sync_editor(lines, edited)
    st.session_state["tc_lines"] = lines

    printed_w = sum(float(row.get("Weight") or 0) for row in lines)
    printed_p = sum(int(float(row.get("Pieces") or 0)) for row in lines)
    summary = db.certificate_weight_summary(packed_w, printed_w)
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Printed kg", f"{printed_w:,.2f}")
    s2.metric("Printed pieces", f"{printed_p:,}")
    s3.metric(
        "Weight delta",
        f"{summary['delta_kg']:+.2f} kg",
        delta=f"{summary['delta_pct']:+.3f}%",
        delta_color="off",
    )
    s4.metric("Max extra kg", f"{summary['allowed_kg']:.2f}")
    if printed_p != packed_p:
        st.error(f"Pieces must match packed ({packed_p}). Printed is {printed_p}.")
    elif not summary["ok"]:
        st.error(
            "Weight is outside the allowed round-up. Printed kg must be at least "
            f"packed ({packed_w:.2f}) and at most "
            f"{packed_w + summary['allowed_kg']:.2f} "
            f"(+{db.CERT_WEIGHT_ROUND_MAX_PCT:g}%)."
        )
    else:
        st.success(
            f"Within cap: {summary['delta_kg']:.2f} kg extra "
            f"({summary['delta_pct']:.3f}% of packed)."
        )

    errors = db.validate_certificate_lines(packed_batches, lines)
    if errors:
        for msg in errors:
            st.warning(msg)

    selected_nos = [
        int(row["Line_no"]) for row in lines if row.get("_selected")
    ]
    if not locked:
        st.markdown("#### Merge or split printed lines")
        allow_blend = st.checkbox(
            "Allow blended chemistry (different heat numbers on one printed line)",
            key="tc_allow_blend",
            help="Required when merging batches that do not share the same Heat_no.",
        )
        merge_heat = st.text_input(
            "Merged heat no (optional)",
            key="tc_merge_heat",
            help="Leave blank to keep the shared heat, or BLEND when heats differ.",
        )
        c_merge, c_split, c_reset = st.columns(3)
        with c_merge:
            if st.button("Merge selected", key="tc_merge"):
                try:
                    st.session_state["tc_lines"] = db.merge_certificate_lines(
                        lines,
                        selected_nos,
                        display_heat_no=merge_heat,
                        allow_blend=allow_blend,
                    )
                    st.session_state["tc_editor_n"] = (
                        int(st.session_state.get("tc_editor_n") or 0) + 1
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with c_split:
            if st.button("Split selected", key="tc_split"):
                if len(selected_nos) != 1:
                    st.error("Select exactly one merged line to split.")
                else:
                    try:
                        st.session_state["tc_lines"] = db.split_certificate_line(
                            lines, selected_nos[0]
                        )
                        st.session_state["tc_editor_n"] = (
                            int(st.session_state.get("tc_editor_n") or 0) + 1
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
        with c_reset:
            if st.button("Reset to packed batches", key="tc_reset"):
                try:
                    rebuilt = db.create_packing_list_certificate_draft(
                        packing_list_id,
                        certificate_no=certificate_no,
                    )
                    st.session_state["tc_lines"] = rebuilt.get("lines") or []
                    st.session_state["tc_editor_n"] = (
                        int(st.session_state.get("tc_editor_n") or 0) + 1
                    )
                    st.success("Draft reset to one printed line per packed batch.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    if selected_nos:
        st.markdown("#### Chemistry for selected line")
        focus = next(
            (row for row in lines if int(row["Line_no"]) == selected_nos[0]),
            None,
        )
        if focus:
            chem = db.blended_certificate_chemistry(focus.get("sources") or [])
            if chem:
                label = (
                    "Weighted average (blended heats)"
                    if focus.get("Is_blended")
                    else "Heat chemistry"
                )
                st.caption(label)
                try:
                    alloy_id = int(header.get("Alloy_id") or 0)
                except (TypeError, ValueError):
                    alloy_id = 0
                specs = db.get_alloy_specs(alloy_id) if alloy_id else {}
                chem_rows = []
                for row in chem:
                    symbol = str(row.get("Element_symbol") or "").strip()
                    spec = specs.get(symbol)
                    pct = row.get("Percentage")
                    oos = bool(
                        spec
                        and db.element_percent_out_of_spec(
                            pct,
                            spec.get("Min_percent"),
                            spec.get("Max_percent"),
                        )
                    )
                    chem_rows.append(
                        {
                            "Element": symbol,
                            "Actual %": pct,
                            "Spec": (
                                db.format_alloy_spec_percent(
                                    spec.get("Min_percent"),
                                    spec.get("Max_percent"),
                                )
                                if spec
                                else "—"
                            ),
                            "Status": (
                                "Out of spec"
                                if oos
                                else ("Within spec" if spec else "No spec")
                            ),
                        }
                    )
                st.dataframe(
                    _style_spec_check_table(df_from_rows(chem_rows)),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No chemistry saved on the source batches yet.")

    a1, a2, a3 = st.columns(3)
    with a1:
        if st.button("View / print", key="tc_view_print"):
            st.session_state["tc_show_print"] = True
            st.rerun()
    with a2:
        save_clicked = st.button(
            "Save draft",
            key="tc_save",
            disabled=locked,
            type="primary" if not locked else "secondary",
        )
    with a3:
        issue_clicked = st.button(
            "Issue certificate",
            key="tc_issue",
            disabled=locked or not insp_ready or spec_blocked,
        )
    if cert.get("Status") == db.CERT_STATUS_ISSUED:
        st.info(
            "This certificate is issued. Dispatch is final. Packed inventory "
            "cannot be reversed here. An Admin can cancel it from "
            "**Admin → Cancel issued certificate**."
        )
    if spec_blocked and not locked:
        st.info(
            "Upload the customer acceptance of deviation letter before issuing "
            "the test certificate."
        )

    if save_clicked:
        try:
            saved = db.save_packing_list_certificate_draft(
                packing_list_id,
                lines,
                certificate_no=certificate_no,
                issued_date=to_storage_date(issued_date),
            )
            st.session_state["tc_lines"] = saved.get("lines") or []
            st.success(f"Saved draft **{saved.get('Certificate_no')}**.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if issue_clicked:
        try:
            db.save_visual_inspection(packing_list_id, inspection_rows)
            issued = db.issue_packing_list_certificate(
                packing_list_id,
                lines,
                certificate_no=certificate_no,
                issued_date=to_storage_date(issued_date),
            )
            st.session_state["tc_lines"] = issued.get("lines") or []
            st.success(f"Issued **{issued.get('Certificate_no')}**. Printed lines are locked.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    st.divider()
    st.markdown("#### Packed baseline (not editable)")
    show_dataframe(
        df_from_rows(
            [
                {
                    "Batch_ID": r.get("Batch_ID"),
                    "Heat_no": r.get("Heat_no"),
                    "Weight": r.get("Weight"),
                    "Pieces": r.get("Pieces"),
                }
                for r in packed_batches
            ]
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Company (issuer — not a customer)
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Company":
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


# ═══════════════════════════════════════════════════════════════════════════════
# Customers
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Customers":
    st.title("Customer Master")
    states = db.list_states()
    if not states:
        st.warning("No states found. Load **State_City_Master** before saving customers.")

    c1, c2 = st.columns(2)
    with c1:
        code = st.text_input("Customer code (PK) *", placeholder="e.g. CUST_0026", key="cust_code")
        name = st.text_input("Customer name *", key="cust_name")
        gst = st.text_input("GST", key="cust_gst")
        pan = st.text_input("PAN", key="cust_pan")
        contact1 = st.text_input("Contact 1 name", key="cust_contact1")
        phone1 = st.text_input("Phone 1", key="cust_phone1")
        contact2 = st.text_input("Contact 2 name", key="cust_contact2")
        phone2 = st.text_input("Phone 2", key="cust_phone2")
        email = st.text_input("Email", key="cust_email")
        website = st.text_input("Website", key="cust_website")
        status = st.selectbox("Status", db.ACTIVE_STATUS, key="cust_status")
    with c2:
        address = st.text_input("Address", key="cust_address")
        state = st.selectbox(
            "State *",
            options=[""] + states,
            key="cust_state_sel",
        )
        cities = db.list_cities(state) if state else []
        city = st.selectbox(
            "City *",
            options=[""] + cities,
            key="cust_city_sel",
            disabled=not bool(state),
        )
        pincode = st.text_input("Pincode", key="cust_pincode")
        country = st.text_input("Country", value="India", key="cust_country")
        bank_account = st.text_input("Bank account", key="cust_bank_account")
        ifsc_code = st.text_input("IFSC code", key="cust_ifsc")
        bank_name = st.text_input("Bank name", key="cust_bank_name")
        branch_category = st.text_input("Branch", key="cust_branch")

    if st.button("Save customer", type="primary", key="cust_save"):
        if not code.strip() or not name.strip():
            st.error("Customer code and name are required.")
        elif not state or not city:
            st.error("State and City must be selected from the master list.")
        else:
            db.upsert_customer(
                {
                    "Cust_code": code.strip(),
                    "Customer_name": name.strip(),
                    "GST": gst,
                    "PAN": pan,
                    "Address": address,
                    "City": city,
                    "State": state,
                    "Pincode": pincode,
                    "Country": country,
                    "Contact1_name": contact1,
                    "Phone1": phone1,
                    "Contact_name2": contact2,
                    "Phone2": phone2,
                    "Email": email,
                    "Website": website,
                    "Bank_account": bank_account,
                    "IFSC_code": ifsc_code,
                    "Bank_name": bank_name,
                    "Branch_category": branch_category,
                    "Status": status,
                }
            )
            st.success(f"Saved customer **{name.strip()}**.")

    show_dataframe(df_from_rows(db.get_all_records("Customer_Master", order_by="Cust_code")))


# ═══════════════════════════════════════════════════════════════════════════════
# Vendors
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Vendors":
    st.title("Vendor Master")
    st.caption("Vendor code is auto-generated serially when a new vendor is created.")
    states = db.list_states()
    if not states:
        st.warning("No states found. Load **State_City_Master** before saving vendors.")

    c1, c2 = st.columns(2)
    with c1:
        name = st.text_input("Vendor name *", key="vend_name")
        gst = st.text_input("GST", key="vend_gst")
        pan = st.text_input("PAN", key="vend_pan")
        status = st.selectbox("Status", db.ACTIVE_STATUS, key="vend_status")
    with c2:
        address = st.text_input("Address", key="vend_address")
        state = st.selectbox(
            "State *",
            options=[""] + states,
            key="vend_state_sel",
        )
        cities = db.list_cities(state) if state else []
        city = st.selectbox(
            "City *",
            options=[""] + cities,
            key="vend_city_sel",
            disabled=not bool(state),
        )
        pincode = st.text_input("Pincode", key="vend_pincode")
        country = st.text_input("Country", value="India", key="vend_country")

    st.markdown("#### Contacts")
    k1, k2 = st.columns(2)
    with k1:
        contact1 = st.text_input("Contact person 1", key="vend_contact1")
        phone1 = st.text_input("Phone 1", key="vend_phone1")
        email = st.text_input("Email", key="vend_email")
    with k2:
        contact2 = st.text_input("Contact person 2", key="vend_contact2")
        phone2 = st.text_input("Phone 2", key="vend_phone2")
        website = st.text_input("Website", key="vend_website")

    st.markdown("#### Commercial & bank details")
    b1, b2 = st.columns(2)
    with b1:
        credit_period = st.number_input(
            "Credit period (days)", min_value=0, value=0, step=1, key="vend_credit"
        )
        bank_account = st.text_input("Bank account no.", key="vend_bank_account")
        bank_name = st.text_input("Bank name", key="vend_bank_name")
    with b2:
        branch = st.text_input("Branch", key="vend_branch")
        ifsc = st.text_input("IFSC code", key="vend_ifsc")

    if st.button("Save vendor", type="primary", key="vend_save"):
        if not name.strip():
            st.error("Vendor name is required.")
        elif not state or not city:
            st.error("State and City must be selected from the master list.")
        else:
            db.upsert_supplier(
                {
                    "Vendor_name": name.strip(),
                    "GST": gst,
                    "PAN": pan,
                    "Address": address,
                    "City": city,
                    "State": state,
                    "Pincode": pincode,
                    "Country": country,
                    "Contact1": contact1.strip(),
                    "Phone1": phone1.strip(),
                    "Contact2": contact2.strip(),
                    "Phone2": phone2.strip(),
                    "Email": email.strip(),
                    "Website": website.strip(),
                    "Credit_period": int(credit_period),
                    "Bank_account": bank_account.strip(),
                    "Branch": branch.strip(),
                    "IFSC_code": ifsc.strip().upper(),
                    "Bank_name": bank_name.strip(),
                    "Status": status,
                }
            )
            st.success(f"Saved vendor **{name.strip()}**.")

    show_dataframe(df_from_rows(db.get_all_records("Vendor_Master", order_by="Vendor_code")))


# ═══════════════════════════════════════════════════════════════════════════════
# Raw Material Master
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Raw Material Master":
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
                st.session_state["rmm_mod_cost"] = _optional_percent(
                    row.get("Cost_per_kg")
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
            cost = empty_percent_input(
                "Cost per kg",
                key=f"{prefix}_cost",
                max_value=None,
                step=0.01,
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
                        cost_per_kg=cost if cost is not None else 0.0,
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


# ═══════════════════════════════════════════════════════════════════════════════
# Alloys
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Alloys":
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


# ═══════════════════════════════════════════════════════════════════════════════
# Furnaces
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Furnaces":
    st.title("Furnace Master")
    with st.form("furn_form", clear_on_submit=True):
        fname = st.text_input("Furnace ID *", placeholder="e.g. 1, 2, 3, 4")
        fstatus = st.selectbox("Status", db.ACTIVE_STATUS)
        if st.form_submit_button("Save furnace", type="primary"):
            if not fname.strip():
                st.error("Furnace ID is required.")
            else:
                db.upsert_furnace(fname.strip(), fstatus)
                st.success(f"Saved furnace **{fname.strip()}**.")

    show_dataframe(df_from_rows(db.get_all_records("Furnace_Master", order_by="Furnace")))


# ═══════════════════════════════════════════════════════════════════════════════
# Crucibles
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Crucibles":
    st.title("Crucible Master")
    st.caption(
        "Add or delete crucibles for a furnace, and set each crucible to "
        "**Available** or **Damaged**. A furnace can have only one "
        "**Available** crucible at a time."
    )
    furnaces = db.list_furnaces()
    vendors = db.list_suppliers()
    if not furnaces:
        st.warning("Add at least one furnace under **Furnaces** before managing crucibles.")
    if not vendors:
        st.warning("Add at least one vendor under **Vendors** if you want to record the supplier.")

    furnace = st.selectbox(
        "Furnace *",
        furnaces,
        key="crucible_furnace",
        disabled=not furnaces,
        help="All add, status, and delete actions apply to this furnace.",
    )

    available = db.get_available_crucible(furnace) if furnace else None
    if available:
        st.info(
            f"Available on furnace **{furnace}**: **{available['Crucible_no']}**. "
            "Mark it Damaged before another crucible can be Available."
        )
    elif furnace:
        st.info(f"No Available crucible on furnace **{furnace}** yet.")

    st.markdown("#### Add crucible")
    add_default = "Damaged" if available else "Available"
    with st.form("crucible_add_form", clear_on_submit=True):
        a1, a2, a3 = st.columns(3)
        with a1:
            cno = st.text_input("Crucible no *", placeholder="e.g. C-01")
        with a2:
            vendor_name = st.selectbox("Vendor name", [""] + vendors)
        with a3:
            cstatus = st.selectbox(
                "Crucible status",
                db.CRUCIBLE_STATUS,
                index=db.CRUCIBLE_STATUS.index(add_default),
            )
        if st.form_submit_button("Add crucible", type="primary"):
            if not furnace:
                st.error("Select a furnace first.")
            elif not cno.strip():
                st.error("Crucible no is required.")
            else:
                try:
                    db.upsert_crucible(
                        cno.strip(),
                        furnace,
                        cstatus,
                        vendor_name or None,
                    )
                    st.success(f"Added crucible **{cno.strip()}** to furnace **{furnace}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add crucible: {exc}")

    st.markdown(f"#### Crucibles on furnace {furnace or '—'}")
    rows = db.list_crucibles(furnace=furnace) if furnace else []
    if not rows:
        st.info("No crucibles for this furnace yet.")
    else:
        h1, h2, h3, h4, h5 = st.columns([2, 3, 2, 1.4, 1.2])
        h1.caption("Crucible no")
        h2.caption("Vendor")
        h3.caption("Status")
        h4.caption("Update")
        h5.caption("Delete")
        for row in rows:
            cno_val = str(row["Crucible_no"])
            current = str(row["Crucible_status"] or db.CRUCIBLE_STATUS[0])
            if current not in db.CRUCIBLE_STATUS:
                current = db.CRUCIBLE_STATUS[0]
            r1, r2, r3, r4, r5 = st.columns([2, 3, 2, 1.4, 1.2])
            r1.markdown(f"**{cno_val}**")
            r2.markdown(row["Vendor_name"] or "—")
            new_status = r3.selectbox(
                "Status",
                db.CRUCIBLE_STATUS,
                index=db.CRUCIBLE_STATUS.index(current),
                key=f"cstat_{furnace}_{cno_val}",
                label_visibility="collapsed",
            )
            if r4.button("Update", key=f"cupd_{furnace}_{cno_val}", use_container_width=True):
                try:
                    db.update_crucible_status(cno_val, new_status)
                    st.success(f"Updated **{cno_val}** to **{new_status}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not update status: {exc}")
            if r5.button("Delete", key=f"cdel_{furnace}_{cno_val}", use_container_width=True):
                try:
                    db.delete_crucible(cno_val)
                    st.success(f"Deleted crucible **{cno_val}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not delete crucible: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Melters
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Melters":
    st.title("Melter Master")
    with st.form("melter_form", clear_on_submit=True):
        mname = st.text_input("Melter name *", placeholder="e.g. Sachin")
        mstatus = st.selectbox("Status", db.ACTIVE_STATUS)
        if st.form_submit_button("Save melter", type="primary"):
            if not mname.strip():
                st.error("Melter name is required.")
            else:
                db.upsert_melter(mname.strip(), mstatus)
                st.success(f"Saved melter **{mname.strip()}**.")

    show_dataframe(df_from_rows(db.get_all_records("Melter_Master", order_by="Melter_Name")))


# ═══════════════════════════════════════════════════════════════════════════════
# Trolleys
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Trolleys":
    st.title("Trolley Master")
    with st.form("trolley_form", clear_on_submit=True):
        t1, t2 = st.columns(2)
        with t1:
            tname = st.text_input("Trolley name *", placeholder="e.g. Trolley-01")
            colour = st.text_input("Colour", placeholder="e.g. Red")
        with t2:
            weight = st.number_input("Weight (kg)", min_value=0.0, value=0.0, step=0.1)
            tstatus = st.selectbox("Status", db.ACTIVE_STATUS)
        if st.form_submit_button("Save trolley", type="primary"):
            if not tname.strip():
                st.error("Trolley name is required.")
            else:
                db.upsert_trolley(
                    tname.strip(),
                    colour.strip() or None,
                    weight if weight > 0 else None,
                    tstatus,
                )
                st.success(f"Saved trolley **{tname.strip()}**.")

    show_dataframe(df_from_rows(db.get_all_records("Trolley_Master", order_by="Trolley_name")))


# ═══════════════════════════════════════════════════════════════════════════════
# Purchase Orders
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Purchase Orders":
    st.title("Purchase Orders")
    st.caption(
        "Create or update a customer purchase order. One Customer PO No can include "
        "several alloys; each alloy line has its own rate and order qty. "
        "Optionally attach the PO document (PDF, Word, or Excel). "
        "Review all PO lines under **All Purchase Orders**."
    )

    customers = db.list_customer_codes()
    cust_opts = {
        f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"] for c in customers
    }

    if not customers:
        st.warning("Add at least one customer under **Customers** before creating a PO.")
    else:
        st.markdown("#### New / update purchase order")
        cust_label = st.selectbox(
            "Customer *",
            options=[""] + list(cust_opts.keys()),
            key="po_customer_sel",
        )
        cust = db.get_customer(cust_opts[cust_label]) if cust_label else None
        cust_code = cust["Cust_code"] if cust else None
        alloys = [
            a
            for a in db.list_alloys()
            if cust_code and a.get("Cust_code") == cust_code
        ]
        alloy_opts = {
            f"{a['Alloy_id']} — {a['Alloy_name']}": a["Alloy_id"] for a in alloys
        }
        if cust and not alloy_opts:
            st.info(
                f"No alloys linked to customer **{cust['Customer_name']}**. "
                "Add alloys under **Alloys** with this customer’s code."
            )

        if "po_alloy_lines" not in st.session_state:
            st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
        if "po_line_token" not in st.session_state:
            st.session_state.po_line_token = 0
        if st.session_state.get("po_lines_cust") != cust_code:
            st.session_state.po_lines_cust = cust_code
            st.session_state.po_line_token = int(st.session_state.po_line_token) + 1
            st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
        line_token = st.session_state.po_line_token

        h1, h2, h3 = st.columns(3)
        with h1:
            po_no = st.text_input("Customer PO No *", placeholder="e.g. PO-2026-001")
            order_date = ui_date_input("Order date", value=date.today())
            delivery_date = ui_date_input("Delivery date", value=date.today())
        with h2:
            st.text_input(
                "Customer name",
                value=cust["Customer_name"] if cust else "",
                disabled=True,
            )
            st.caption("Filled from Customer Master when a customer is selected.")
            po_status = st.selectbox(
                "Purchase order status *",
                options=db.PURCHASE_ORDER_STATUS,
                index=0,
                help="Open, Closed, or Cancelled. Applied to every alloy line on this PO.",
            )
        with h3:
            st.caption("One customer PO can list multiple alloys below.")

        st.markdown("##### Alloy lines")
        st.caption(
            "Each row is one alloy on this Customer PO No, with that alloy’s rate and order qty."
        )
        add_col, rem_col, _ = st.columns([1, 1, 4])
        if add_col.button("Add alloy", key="po_add_alloy_line"):
            st.session_state.po_alloy_lines.append({"qty": 0.0, "rate": 0.0})
            st.rerun()
        if rem_col.button("Remove last alloy", key="po_rem_alloy_line") and len(
            st.session_state.po_alloy_lines
        ) > 1:
            st.session_state.po_alloy_lines.pop()
            st.rerun()

        collected_lines: list[dict] = []
        for idx, _line in enumerate(st.session_state.po_alloy_lines):
            st.markdown(f"**Alloy {idx + 1}**")
            a1, a2, a3 = st.columns([2.2, 1.2, 1.2])
            with a1:
                alloy_label = st.selectbox(
                    "Alloy *",
                    options=["— select alloy —"] + list(alloy_opts.keys()),
                    disabled=not bool(cust),
                    key=f"po_line_alloy_{line_token}_{idx}",
                    help="Required. The same Customer PO No can include each alloy once.",
                )
            with a2:
                order_qty = st.number_input(
                    "Order qty *",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key=f"po_line_qty_{line_token}_{idx}",
                )
            with a3:
                rate = st.number_input(
                    "Rate",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    key=f"po_line_rate_{line_token}_{idx}",
                )
            collected_lines.append(
                {
                    "alloy_label": alloy_label,
                    "qty": float(order_qty or 0.0),
                    "rate": float(rate or 0.0),
                }
            )

        filled_lines = [
            ln for ln in collected_lines if ln["alloy_label"] != "— select alloy —"
        ]
        total_qty = sum(ln["qty"] for ln in filled_lines)
        po_value = sum(ln["qty"] * ln["rate"] for ln in filled_lines)
        gst_value = po_value * 0.18
        grand_total = po_value + gst_value
        t1, t2, t3, t4, t5 = st.columns(5)
        t1.metric("Alloys", len(filled_lines))
        t2.metric("Total order qty", f"{total_qty:,.1f}")
        t3.metric("PO value", f"{po_value:,.2f}")
        t4.metric("GST value (18%)", f"{gst_value:,.2f}")
        t5.metric("Total value", f"{grand_total:,.2f}")

        st.markdown("##### Billing address")
        b1, b2 = st.columns(2)
        with b1:
            bill_addr = st.text_input(
                "Billing address",
                value=(cust.get("Address") or "") if cust else "",
            )
            bill_city = st.text_input(
                "Billing city",
                value=(cust.get("City") or "") if cust else "",
            )
            bill_state = st.text_input(
                "Billing state",
                value=(cust.get("State") or "") if cust else "",
            )
        with b2:
            bill_pin = st.text_input(
                "Billing pincode",
                value=(cust.get("Pincode") or "") if cust else "",
            )
            bill_country = st.text_input(
                "Billing country",
                value=(cust.get("Country") or "India") if cust else "India",
            )
            copy_ship = st.checkbox("Copy billing address to shipping", value=True)

        st.markdown("##### Shipping address")
        s1, s2 = st.columns(2)
        with s1:
            ship_addr = st.text_input(
                "Shipping address",
                value=(cust.get("Address") or "") if cust and copy_ship else "",
            )
            ship_city = st.text_input(
                "Shipping city",
                value=(cust.get("City") or "") if cust and copy_ship else "",
            )
            ship_state = st.text_input(
                "Shipping state",
                value=(cust.get("State") or "") if cust and copy_ship else "",
            )
        with s2:
            ship_pin = st.text_input(
                "Shipping pincode",
                value=(cust.get("Pincode") or "") if cust and copy_ship else "",
            )
            ship_country = st.text_input(
                "Shipping country",
                value=(cust.get("Country") or "India") if cust and copy_ship else "India",
            )

        po_file = st.file_uploader(
            "PO document (optional)",
            type=["pdf", "doc", "docx", "xls", "xlsx"],
            help="PDF, Word, or Excel. Attached to every alloy line on this PO.",
        )
        save_po = st.button("Save purchase order", type="primary", key="po_save")

        if save_po:
            if not po_no.strip():
                st.error("Customer PO No is required.")
            elif not cust_label or not cust:
                st.error("Select a customer.")
            elif not alloy_opts:
                st.error("This customer has no alloys. Add alloys under **Alloys** first.")
            elif not filled_lines:
                st.error("Add at least one alloy line.")
            elif any(ln["qty"] <= 0 for ln in filled_lines):
                st.error("Each alloy line needs an order qty greater than zero.")
            else:
                labels = [ln["alloy_label"] for ln in filled_lines]
                if len(labels) != len(set(labels)):
                    st.error("Each alloy can appear only once on the same Customer PO No.")
                else:
                    try:
                        if copy_ship:
                            ship_addr, ship_city, ship_state = bill_addr, bill_city, bill_state
                            ship_pin, ship_country = bill_pin, bill_country
                        header = {
                            "Customer_PO_No": po_no.strip(),
                            "Cust_code": cust["Cust_code"],
                            "Customer_name": cust["Customer_name"],
                            "Order_Date": order_date.isoformat(),
                            "Delivery_Date": delivery_date.isoformat(),
                            "Billing_Address": bill_addr.strip() or None,
                            "Billing_City": bill_city.strip() or None,
                            "Billing_state": bill_state.strip() or None,
                            "Billing_Pincode": bill_pin.strip() or None,
                            "Billing_country": bill_country.strip() or None,
                            "Shipping_address": ship_addr.strip() or None,
                            "Shipping_City": ship_city.strip() or None,
                            "Shipping_state": ship_state.strip() or None,
                            "Shipping_Pincode": ship_pin.strip() or None,
                            "Shipping_country": ship_country.strip() or None,
                            "Purchase_Order_Status": po_status,
                        }
                        line_rows = [
                            {
                                "Alloy_Id": alloy_opts[ln["alloy_label"]],
                                "Order_Qty": ln["qty"],
                                "Rate": ln["rate"] if ln["rate"] > 0 else None,
                            }
                            for ln in filled_lines
                        ]
                        alloy_ids = db.upsert_purchase_order_lines(header, line_rows)
                        if po_file is not None:
                            file_bytes = po_file.getvalue()
                            filename = po_file.name
                            content_type = getattr(po_file, "type", None)
                            for alloy_id in alloy_ids:
                                db.save_po_document(
                                    customer_po_no=po_no.strip(),
                                    file_bytes=file_bytes,
                                    filename=filename,
                                    content_type=content_type,
                                    alloy_id=alloy_id,
                                )
                        names = ", ".join(ln["alloy_label"] for ln in filled_lines)
                        st.success(
                            f"Saved purchase order **{po_no.strip()}** with "
                            f"{len(filled_lines)} alloy line(s): {names}. "
                            "Open **All Purchase Orders** to review or take further actions."
                        )
                        st.session_state.po_alloy_lines = [{"qty": 0.0, "rate": 0.0}]
                        st.session_state.po_line_token = int(line_token) + 1
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# All Purchase Orders (browse / actions)
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "All Purchase Orders":
    st.title("All Purchase Orders")
    st.caption(
        "Summary of every customer PO line (one row per PO number and alloy). "
        "Filter, review details, change status, and attach or download documents. "
        "Create new POs under **Purchase Orders**."
    )

    pos = db.list_purchase_orders()
    if not pos:
        st.info("No purchase orders yet. Create one under **Purchase Orders**.")
    else:
        open_n = sum(1 for p in pos if (p.get("Purchase_Order_Status") or "Open") == "Open")
        closed_n = sum(1 for p in pos if p.get("Purchase_Order_Status") == "Closed")
        cancel_n = sum(1 for p in pos if p.get("Purchase_Order_Status") == "Cancelled")
        with_doc = sum(1 for p in pos if int(p.get("Has_Document") or 0) == 1)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total POs", len(pos))
        c2.metric("Open", open_n)
        c3.metric("Closed", closed_n)
        c4.metric("Cancelled", cancel_n)
        c5.metric("With document", with_doc)

        f1, f2, f3 = st.columns([1.2, 1.5, 1.5])
        with f1:
            status_filter = st.selectbox(
                "Status",
                options=["All"] + db.PURCHASE_ORDER_STATUS,
                key="apo_status_filter",
            )
        with f2:
            def _po_cust_label(p: dict) -> str:
                code = p.get("Cust_code") or ""
                name = p.get("Customer_name") or ""
                if code and name:
                    return f"{code} — {name}"
                return name or code or "—"

            customers_in_pos = sorted({_po_cust_label(p) for p in pos})
            cust_filter = st.selectbox(
                "Customer",
                options=["All"] + customers_in_pos,
                key="apo_cust_filter",
            )
        with f3:
            search = st.text_input(
                "Search PO / alloy",
                placeholder="PO number or alloy…",
                key="apo_search",
            ).strip().lower()

        filtered = []
        for p in pos:
            st_val = p.get("Purchase_Order_Status") or "Open"
            if status_filter != "All" and st_val != status_filter:
                continue
            if cust_filter != "All" and _po_cust_label(p) != cust_filter:
                continue
            if search:
                blob = " ".join(
                    str(p.get(k) or "")
                    for k in (
                        "Customer_PO_No",
                        "Customer_name",
                        "Cust_code",
                        "Alloy_name",
                        "Alloy_Id",
                    )
                ).lower()
                if search not in blob:
                    continue
            filtered.append(p)

        summary_rows = [
            {
                "PO No": p["Customer_PO_No"],
                "Customer": p.get("Customer_name") or p.get("Cust_code") or "—",
                "Alloy": p.get("Alloy_name")
                or (f"#{p['Alloy_Id']}" if p.get("Alloy_Id") else "—"),
                "Order date": format_ui_date(p.get("Order_Date"), empty="—"),
                "Delivery": format_ui_date(p.get("Delivery_Date"), empty="—"),
                "Qty": float(p["Order_Qty"] or 0),
                "Rate": float(p["Rate"] or 0) if p.get("Rate") is not None else None,
                "Status": p.get("Purchase_Order_Status") or "Open",
                "Document": "Yes" if int(p.get("Has_Document") or 0) == 1 else "No",
            }
            for p in filtered
        ]
        st.subheader(f"Purchase orders ({len(summary_rows)})")
        if not summary_rows:
            st.warning("No purchase orders match the current filters.")
        else:
            show_dataframe(pd.DataFrame(summary_rows))

        st.divider()
        st.markdown("#### Actions on a purchase order")
        action_opts = {
            f"{p['Customer_PO_No']}"
            + (f" — {p['Customer_name']}" if p.get("Customer_name") else "")
            + " / "
            + (
                p.get("Alloy_name")
                or (f"#{p['Alloy_Id']}" if p.get("Alloy_Id") else "no alloy")
            )
            + f" · {p.get('Purchase_Order_Status') or 'Open'}": (
                p["Customer_PO_No"],
                p.get("Alloy_Id"),
            )
            for p in filtered or pos
        }
        pick = st.selectbox(
            "Select purchase order line",
            options=list(action_opts.keys()),
            key="apo_action_pick",
        )
        po_no_sel, alloy_id_sel = action_opts[pick]
        selected = next(
            p
            for p in pos
            if p["Customer_PO_No"] == po_no_sel and p.get("Alloy_Id") == alloy_id_sel
        )

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("PO No", selected["Customer_PO_No"])
        d2.metric("Customer", selected.get("Customer_name") or "—")
        d3.metric(
            "Alloy",
            selected.get("Alloy_name")
            or (f"#{selected['Alloy_Id']}" if selected.get("Alloy_Id") else "—"),
        )
        d4.metric("Qty", f"{float(selected.get('Order_Qty') or 0):,.0f}")

        with st.expander("Order details", expanded=False):
            st.write(
                {
                    "Order date": format_ui_date(selected.get("Order_Date"), empty="—"),
                    "Delivery date": format_ui_date(selected.get("Delivery_Date"), empty="—"),
                    "Rate": selected.get("Rate"),
                    "Status": selected.get("Purchase_Order_Status") or "Open",
                    "Billing": ", ".join(
                        x
                        for x in [
                            selected.get("Billing_Address"),
                            selected.get("Billing_City"),
                            selected.get("Billing_state"),
                            selected.get("Billing_Pincode"),
                            selected.get("Billing_country"),
                        ]
                        if x
                    )
                    or "—",
                    "Shipping": ", ".join(
                        x
                        for x in [
                            selected.get("Shipping_address"),
                            selected.get("Shipping_City"),
                            selected.get("Shipping_state"),
                            selected.get("Shipping_Pincode"),
                            selected.get("Shipping_country"),
                        ]
                        if x
                    )
                    or "—",
                    "Document": selected.get("PO_Document_name") or "None",
                }
            )

        st.markdown("##### Update status")
        cur_status = selected.get("Purchase_Order_Status") or "Open"
        try:
            cur_idx = db.PURCHASE_ORDER_STATUS.index(cur_status)
        except ValueError:
            cur_idx = 0
        new_status = st.selectbox(
            "Purchase order status",
            options=db.PURCHASE_ORDER_STATUS,
            index=cur_idx,
            key="apo_status_edit",
        )
        if st.button("Save status", type="primary", key="apo_save_status"):
            try:
                db.update_purchase_order_status(po_no_sel, new_status, alloy_id_sel)
                st.success(
                    f"PO **{po_no_sel}** / alloy **{selected.get('Alloy_name') or alloy_id_sel}** "
                    f"set to **{new_status}**."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        st.markdown("##### Document")
        uploaded = st.file_uploader(
            "Upload / replace PO document",
            type=["pdf", "doc", "docx", "xls", "xlsx"],
            key="apo_attach",
        )
        u1, u2, _ = st.columns([1, 1, 3])
        if u1.button("Save document", type="primary", disabled=uploaded is None, key="apo_save_doc"):
            try:
                db.save_po_document(
                    customer_po_no=po_no_sel,
                    file_bytes=uploaded.getvalue(),
                    filename=uploaded.name,
                    content_type=getattr(uploaded, "type", None),
                    alloy_id=alloy_id_sel,
                )
                st.success(f"Saved **{uploaded.name}** on PO {po_no_sel}.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        doc = db.get_po_document(po_no_sel, alloy_id_sel)
        if doc and doc.get("PO_Document"):
            st.download_button(
                "Download attached document",
                data=bytes(doc["PO_Document"]),
                file_name=doc.get("PO_Document_name") or f"{po_no_sel}.bin",
                mime=doc.get("PO_Document_type") or "application/octet-stream",
                key="apo_download",
            )
            if u2.button("Remove document", key="apo_remove_doc"):
                db.clear_po_document(po_no_sel, alloy_id_sel)
                st.success("Document removed.")
                st.rerun()
        else:
            st.caption("No document attached to this PO yet.")


# ═══════════════════════════════════════════════════════════════════════════════
# BOM
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Bill of Materials":
    st.title("Build of Material (BOM)")
    cust_opts = {
        f"{c['Cust_code']} — {c['Customer_name']}": c["Cust_code"]
        for c in db.list_customer_codes()
    }
    materials = db.list_raw_materials()
    alloys = [a["Alloy_name"] for a in db.list_alloys()]

    with st.form("bom_form", clear_on_submit=True):
        b1, b2 = st.columns(2)
        with b1:
            bom_id = st.number_input("BOM ID *", min_value=1.0, value=1.0, step=1.0)
            eff = ui_date_input("Effective date", value=date.today())
            customer = st.selectbox("Customer", [""] + list(cust_opts.keys()))
            alloy_name = st.selectbox("Alloy name", [""] + alloys)
        with b2:
            rm = st.selectbox("Raw material", [""] + materials)
            qty = st.number_input("Quantity", min_value=0.0, value=1.0, step=0.1)
            seq = st.number_input("Sequence order", min_value=0.0, value=1.0, step=1.0)
            notes = st.text_input("Notes")
        if st.form_submit_button("Save BOM line", type="primary"):
            db.add_bom_line(
                bom_id=bom_id,
                effective_date=eff.isoformat(),
                cust_code=cust_opts[customer] if customer else None,
                alloy_name=alloy_name or None,
                raw_material=rm or None,
                quantity=qty,
                sequence=seq,
                notes=notes,
            )
            st.success(f"Saved BOM {bom_id} / {format_ui_date(eff)}.")

    show_dataframe(
        df_from_rows(
            db.fetch_all(
                """
                SELECT b.BOMID AS "BOMID", b.Effective_date AS "Effective_date",
                       b.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name",
                       b.Alloy_Name AS "Alloy_Name",
                       b.Raw_Material_Name AS "Raw_Material_Name", b.Quantity AS "Quantity",
                       b.Sequence_Order AS "Sequence_Order", b.notes AS "notes"
                FROM Build_of_Material b
                LEFT JOIN Customer_Master c ON c.Cust_code = b.Cust_code
                ORDER BY b.BOMID, b.Sequence_Order
                """
            )
        )
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Masters overview
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Data Browser":
    st.title("Data Browser")
    st.caption(
        "View, filter, analyse, and correct master data. "
        "Edit cells in the grid, then click **Save changes**."
    )

    table_opts = {t["label"]: t for t in db.EDITABLE_TABLES}
    t1, t2, t3 = st.columns([2, 2, 1])
    with t1:
        chosen_label = st.selectbox("Table", list(table_opts.keys()))
    meta = table_opts[chosen_label]
    table_key = meta["key"]
    pk_cols = meta["pk"]
    identity_cols = meta.get("identity") or []

    # Reload token so Save / Reset refreshes the grid
    state_token = f"data_browser_{table_key}_token"
    if state_token not in st.session_state:
        st.session_state[state_token] = 0

    rows = db.load_editable_table(table_key, order_by=meta["order_by"])
    full_df = df_from_rows(rows)
    if table_key == "raw_material_spec" and not full_df.empty:
        sym_col = next(
            (c for c in full_df.columns if c.lower() == "element_symbol"),
            None,
        )
        if sym_col:
            full_df = full_df[
                ~full_df[sym_col]
                .astype(str)
                .str.upper()
                .isin(db.RAW_MATERIAL_SPEC_HIDDEN_SYMBOLS)
            ].copy()
    full_df = format_df_dates(full_df)

    with t2:
        search = st.text_input(
            "Search",
            placeholder="Filter any column…",
            key=f"search_{table_key}_{st.session_state[state_token]}",
        )
    with t3:
        st.metric("Rows", len(full_df))

    if full_df.empty:
        st.info(f"No rows in **{chosen_label}** yet.")
    else:
        # Quick analysis strip
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Columns", len(full_df.columns))
        status_col = next((c for c in full_df.columns if c.lower() == "status"), None)
        if status_col is not None:
            active_n = int((full_df[status_col].astype(str).str.lower() == "active").sum())
            a2.metric("Active", active_n)
            a3.metric("Other status", len(full_df) - active_n)
        else:
            a2.metric("Primary key", ", ".join(pk_cols))
            a3.metric("Editable", "Yes")
        a4.download_button(
            "Download CSV",
            data=full_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{table_key}.csv",
            mime="text/csv",
        )

        # Optional status / categorical filter
        filter_df = full_df
        filter_cols = [
            c for c in full_df.columns
            if c.lower() in {
                "status", "cust_code", "vendor_code", "isri_code",
                "alloy_id", "availability_class", "fe", "cu", "mg",
            }
        ]
        if filter_cols:
            with st.expander("Column filters", expanded=False):
                fcols = st.columns(min(4, len(filter_cols)))
                for i, col in enumerate(filter_cols):
                    opts = sorted({str(v) for v in full_df[col].dropna().unique()})
                    with fcols[i % len(fcols)]:
                        picked = st.multiselect(
                            col,
                            opts,
                            default=[],
                            key=f"filt_{table_key}_{col}_{st.session_state[state_token]}",
                        )
                    if picked:
                        filter_df = filter_df[filter_df[col].astype(str).isin(picked)]

        if search.strip():
            q = search.strip().lower()
            mask = filter_df.apply(
                lambda r: any(q in str(v).lower() for v in r.values if v is not None),
                axis=1,
            )
            filter_df = filter_df[mask]

        st.caption(
            f"Showing **{len(filter_df)}** of **{len(full_df)}** rows. "
            f"Locked key column(s): `{', '.join(pk_cols)}`."
            + (" New rows: use the dedicated entry pages for auto-IDs." if identity_cols else "")
        )

        # Keep an unfiltered original snapshot for save (full table)
        original_key = f"data_browser_{table_key}_original_{st.session_state[state_token]}"
        st.session_state[original_key] = full_df.copy()

        disabled = [c for c in pk_cols if c in filter_df.columns]
        column_config = {}
        for c in filter_df.columns:
            if (
                c.lower() in {"percentage", "min_percent", "max_percent"}
                and table_key in {
                    "raw_material_spec",
                    "batch_chemical_composition",
                    "alloy_master_spec",
                }
            ):
                column_config[c] = st.column_config.NumberColumn(
                    c, format="%.4f", step=CHEM_PERCENT_STEP
                )
            elif filter_df[c].dtype == object:
                # Keep text columns as text (avoid Streamlit guessing badly)
                column_config[c] = st.column_config.TextColumn(c)

        edited = st.data_editor(
            filter_df,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic" if meta.get("allow_add") else "fixed",
            disabled=disabled,
            column_config=column_config or None,
            key=f"editor_{table_key}_{st.session_state[state_token]}",
        )

        b1, b2, b3 = st.columns([1, 1, 3])
        with b1:
            save = st.button("Save changes", type="primary", key=f"save_{table_key}")
        with b2:
            reset = st.button("Reset view", key=f"reset_{table_key}")

        if reset:
            st.session_state[state_token] += 1
            st.rerun()

        if save:
            try:
                # Merge edits: apply changes from filtered view onto full original by PK
                original = st.session_state[original_key]
                orig_rows = [
                    row_dates_to_storage(r) for r in original.to_dict(orient="records")
                ]
                edited_rows = [
                    row_dates_to_storage(r) for r in edited.to_dict(orient="records")
                ]

                # If user filtered, only upsert rows present in the editor
                # (plus any newly added blank-key rows when allow_add)
                result = db.save_table_edits(
                    table_name=table_key,
                    pk_cols=pk_cols,
                    original_rows=orig_rows,
                    edited_rows=edited_rows,
                    identity_cols=identity_cols,
                )
                st.success(
                    f"Saved **{chosen_label}**: "
                    f"{result['updated']} updated, {result['inserted']} inserted."
                )
                st.session_state[state_token] += 1
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save: {exc}")

        # Simple pivot-style peek for chemistry / spec tables
        if table_key in {"raw_material_spec", "alloy_master_spec"} and not filter_df.empty:
            st.subheader("Quick analysis")
            sym_col = next(
                (c for c in filter_df.columns if "element" in c.lower()),
                None,
            )
            val_col = next(
                (
                    c for c in filter_df.columns
                    if c.lower() in {"percentage", "min_percent", "max_percent"}
                ),
                None,
            )
            if sym_col:
                serial_order = {
                    e["Element_Symbol"]: e["Serial_no"] for e in db.list_elements()
                }
                counts = (
                    filter_df[sym_col]
                    .value_counts()
                    .rename_axis(sym_col)
                    .reset_index(name="rows")
                )
                counts["_ord"] = counts[sym_col].map(
                    lambda s: serial_order.get(s, 9999)
                )
                counts = counts.sort_values("_ord").drop(columns="_ord")
                c_left, c_right = st.columns(2)
                with c_left:
                    st.markdown("**Rows by element**")
                    show_dataframe(counts)
                if val_col and val_col in filter_df.columns:
                    with c_right:
                        st.markdown(f"**{val_col} summary**")
                        summary = (
                            filter_df.groupby(sym_col)[val_col]
                            .agg(["count", "min", "mean", "max"])
                            .round(3)
                            .reset_index()
                        )
                        summary["_ord"] = summary[sym_col].map(
                            lambda s: serial_order.get(s, 9999)
                        )
                        summary = summary.sort_values("_ord").drop(columns="_ord")
                        show_dataframe(summary)


elif PAGE == "Masters Overview":
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
            | 6 | Raw_Material_Purchase | Vendor invoices / receipts (document + vehicle photo) |
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


# ═══════════════════════════════════════════════════════════════════════════════
# Admin — Cancel issued certificate
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == ADMIN_PAGE_CANCEL_ISSUED:
    if not db.is_admin_user():
        st.error("This page is available only to Admin users.")
        st.stop()
    st.title("Cancel issued certificate")
    st.caption(
        "Issue is the final dispatch step. Use this page only to reverse a "
        "shipment that should not have been issued. Cancelling voids the "
        "certificate, sets the packing list back to **In-Progress**, and "
        "returns packed kg and pieces to finished-goods inventory."
    )
    try:
        issued_rows = db.list_issued_certificates()
    except Exception as exc:
        st.error(str(exc))
        issued_rows = []
    if not issued_rows:
        st.info("There are no issued test certificates to cancel.")
        st.stop()

    show_dataframe(
        df_from_rows(
            [
                {
                    "Packing_list_id": row.get("Packing_list_id"),
                    "Certificate_no": row.get("Certificate_no"),
                    "Issued_date": row.get("Issued_date"),
                    "Invoice": row.get("Invoice_number"),
                    "Customer": row.get("Customer_name"),
                    "Alloy": row.get("Alloy_name"),
                    "Packed kg": row.get("Source_weight"),
                    "Packed pieces": row.get("Source_pieces"),
                    "Issued by": row.get("Last_updated_by"),
                }
                for row in issued_rows
            ]
        )
    )
    opts = {
        (
            f"{row.get('Certificate_no') or '—'}  |  "
            f"PL #{row.get('Packing_list_id')}  |  "
            f"{row.get('Customer_name') or '—'}  |  "
            f"{row.get('Invoice_number') or '—'}"
        ): int(row["Packing_list_id"])
        for row in issued_rows
    }
    pick = st.selectbox(
        "Issued certificate",
        options=list(opts.keys()),
        key="admin_cancel_issued_pick",
    )
    packing_list_id = opts[pick]
    chosen = next(
        (row for row in issued_rows if int(row["Packing_list_id"]) == packing_list_id),
        None,
    )
    if chosen:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Certificate", chosen.get("Certificate_no") or "—")
        c2.metric("Packed kg", f"{float(chosen.get('Source_weight') or 0):,.2f}")
        c3.metric("Packed pieces", f"{int(float(chosen.get('Source_pieces') or 0)):,}")
        c4.metric("Customer", chosen.get("Customer_name") or "—")
        header = db.get_packing_list(packing_list_id)
        if header:
            st.markdown("##### Packed batches to return")
            show_dataframe(
                df_from_rows(
                    [
                        {
                            "Batch_ID": row.get("Batch_ID"),
                            "Heat_no": row.get("Heat_no"),
                            "Weight": row.get("Weight"),
                            "Pieces": row.get("Pieces"),
                        }
                        for row in (header.get("batches") or [])
                    ]
                )
            )
    confirm = st.checkbox(
        "I confirm this issued certificate should be cancelled and packed "
        "quantity returned to finished goods.",
        key="admin_cancel_issued_confirm",
    )
    if st.button(
        "Cancel issued certificate",
        type="primary",
        key="admin_cancel_issued_go",
        disabled=not confirm,
    ):
        try:
            voided = db.cancel_issued_test_certificate(packing_list_id)
            st.success(
                f"Cancelled **{voided.get('Certificate_no')}**. "
                "The packing list is In-Progress and packed quantity is back "
                "in finished goods."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
