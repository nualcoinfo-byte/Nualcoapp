"""
Nualco — Secondary Aluminum Alloy Production Tracker
Streamlit application for batch, chemistry, and yield tracking.
Runs on Neon Postgres (DATABASE_URL) with local SQLite as fallback.
"""

from __future__ import annotations

import html
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "nualco_logo.png"
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
elif not os.environ.get("DATABASE_URL"):
    try:
        if "DATABASE_URL" in st.secrets:
            os.environ["DATABASE_URL"] = str(st.secrets["DATABASE_URL"]).strip().strip('"').strip("'")
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
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def bootstrap() -> str:
    """Create tables. If Neon cannot be reached, continue on local SQLite."""
    if db.IS_POSTGRES:
        try:
            db.init_db()
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
    return [{k: v for k, v in row.items() if k not in skip} for row in rows]


def _optional_percent(value: object) -> float | None:
    """Treat blank / 0 as empty so percentage fields can start without 0.00."""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
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
) -> float | None:
    """Number input that starts blank instead of 0.00."""
    if key in st.session_state:
        if st.session_state[key] in (0, 0.0):
            st.session_state[key] = None
    else:
        st.session_state[key] = _optional_percent(default)
    kwargs: dict[str, object] = {
        "min_value": min_value,
        "value": None,
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
) -> int | None:
    """Whole-number input that starts blank instead of 0."""
    if key in st.session_state:
        raw = st.session_state[key]
        if raw in (0, 0.0):
            st.session_state[key] = None
        elif raw is not None:
            try:
                st.session_state[key] = int(raw)
            except (TypeError, ValueError):
                st.session_state[key] = None
    else:
        st.session_state[key] = _optional_int(default)
    return st.number_input(
        label,
        min_value=0,
        value=None,
        step=1,
        format="%d",
        key=key,
        help=help,
        placeholder="",
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

        w1, w2, w3 = st.columns(3)
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
        show_dataframe(df_from_rows(_output_rows_for_table(saved)))
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
    if isinstance(data, pd.DataFrame):
        data = format_df_dates(data)
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
        if isinstance(k, str) and k.startswith(prefix)
    }


def _restore_furnace_widgets(furnace: str) -> None:
    saved = (st.session_state.get("batch_drafts") or {}).get(str(furnace)) or {}
    for k, v in saved.items():
        if k not in st.session_state:
            st.session_state[k] = v


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

PAGE = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard",
        "Raw Material Logging",
        "Raw Material Inventory",
        "Furnace Oil Purchase",
        "Production Batch & Chemistry",
        "Batch Output",
        "Production Batches",
        "Furnace Oil Consumption",
        "Electricity Consumption",
        "Cost of Conversion",
        "Production Workflow Tracker",
        "Material Recovery & Yield",
        "Finished Goods Inventory",
        "Purchase Orders",
        "All Purchase Orders",
        "Customers",
        "Vendors",
        "Raw Material Master",
        "Alloys",
        "Furnaces",
        "Crucibles",
        "Melters",
        "Trolleys",
        "Bill of Materials",
        "Data Browser",
        "Masters Overview",
    ],
)

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
        "Not connected to Neon. In Streamlit Cloud go to "
        "**Manage app → Settings → Secrets** and set:\n\n"
        '```\nDATABASE_URL = "postgresql://..."\n```\n\n'
        "Then reboot the app."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════
if PAGE == "Dashboard":
    st.title("Production Dashboard")
    try:
        batches = db.list_batches()
        materials = db.list_raw_materials()
        lots = db.list_inventory_lots()
        alloys = db.list_alloys()
        oil_stock = db.get_furnace_oil_stock()
        elec_month = db.electricity_month_totals(date.today().year, date.today().month)
    except Exception as exc:
        _show_db_connection_error(exc)
        batches, materials, lots, alloys = [], [], [], []
        oil_stock = 0.0
        elec_month = {"consumed": 0.0, "by_line": {}}

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
        show = bdf.copy()
        show_dataframe(show)

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
        "New receipts are entered on **Raw Material Logging**."
    )

    recent = df_from_rows(
        db.fetch_all(
            """
            SELECT i.Lot_id AS "Lot_id", i.Purchase_id AS "Purchase_id",
                   i.Raw_Material_Name AS "Raw_Material_Name",
                   v.Vendor_name AS "Vendor_name",
                   p.Supplier_Invoice AS "Supplier_Invoice",
                   p.Supplier_invoice_date AS "Supplier_invoice_date",
                   p.Received_date AS "Received_date",
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
        "Choose a furnace first, then enter alloy, samples, charge lines, and chemistry "
        "for that furnace only. Switching furnace keeps this draft and opens a separate "
        "form — it does not remap entries to the other furnace. "
        "Batch ID is **F{Furnace}-H{Heat}** (e.g. Furnace 3 + Heat 7 → `F3-H07`). "
        "Record metal that left the furnace on **Batch Output**. "
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
        _restore_furnace_widgets(furnace)
        st.session_state["_pb_prev_furnace"] = furnace

        def _pk(name: str) -> str:
            return _furnace_form_key(furnace, name)

        st.info(
            f"Entering data for furnace **{furnace}** only. "
            "Crucible, heat, alloy, samples, charges, and chemistry apply to this furnace."
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            available_crucible = (
                db.get_available_crucible(furnace) if furnace else None
            )
            if available_crucible:
                crucible_no = str(available_crucible["Crucible_no"])
                st.markdown("**Crucible no**")
                st.info(crucible_no)
            else:
                st.markdown("**Crucible no**")
                st.error(
                    "No crucible available for the respective furnace."
                )
            heat_no = st.selectbox("Heat no *", db.HEAT_NOS, key=_pk("heat_no"))
            melt_no = st.selectbox("Melt no", db.MELT_NOS, key=_pk("melt_no"))
        with col2:
            prod_date = ui_date_input(
                "Production date",
                value=date.today(),
                key=_pk("prod_date"),
            )
            shift = st.selectbox("Shift", db.SHIFTS, key=_pk("shift"))
            melting_team = st.selectbox("Melter name *", melters, key=_pk("melter"))
        with col3:
            alloy_label = st.selectbox(
                "Alloy",
                options=["— none —"] + list(alloy_labels.keys()),
                key=_pk("alloy"),
            )
            production_supervisor = st.selectbox(
                "Production supervisor *",
                supervisors,
                key=_pk("supervisor"),
            )
            notes = st.text_area("Notes", height=68, key=_pk("notes"))
            preview_id = db.make_batch_id(furnace, heat_no)
            st.markdown(f"**Batch ID preview:** `{preview_id}`")

        alloy_id = None if alloy_label == "— none —" else alloy_labels[alloy_label]

        st.markdown("#### Degassing & piece counts")
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            degassing_time = st.text_input(
                "Degassing time",
                placeholder="e.g. 14:30 or 12 min",
                key=_pk("degassing_time"),
            )
        with d2:
            sampled_pcs = empty_percent_input(
                "Sampled pcs",
                key=_pk("sampled_pcs"),
                max_value=None,
                step=1.0,
            )
        with d3:
            defect_pcs = empty_percent_input(
                "Defect pcs",
                key=_pk("defect_pcs"),
                max_value=None,
                step=1.0,
            )
        with d4:
            st.caption("K Mold Value = Defect pcs / Sampled pcs")
            if sampled_pcs and sampled_pcs > 0:
                k_mold = float(defect_pcs or 0) / float(sampled_pcs)
                css = "yield-bad" if k_mold > 0.5 else "yield-ok"
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
        sample_blank = "— not set —"
        sample_opts = [sample_blank] + db.SAMPLE_OK_STATUS
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            top_sample = st.selectbox("Top sample", sample_opts, key=_pk("top_sample"))
        with s2:
            middle_sample = st.selectbox(
                "Middle sample", sample_opts, key=_pk("middle_sample")
            )
        with s3:
            bottom_sample = st.selectbox(
                "Bottom sample", sample_opts, key=_pk("bottom_sample")
            )
        with s4:
            vacum_sample = st.selectbox(
                "Vacum sample", sample_opts, key=_pk("vacum_sample")
            )

        r1, r2, r3, r4 = st.columns(4)
        with r1:
            top_sample_remarks = st.text_input("Remarks", key=_pk("top_sample_remarks"))
        with r2:
            middle_sample_remarks = st.text_input(
                "Remarks", key=_pk("middle_sample_remarks")
            )
        with r3:
            bottom_sample_remarks = st.text_input(
                "Remarks", key=_pk("bottom_sample_remarks")
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
            )
        with d2:
            middle_sample_dt = ui_datetime_input(
                "Datetime",
                value=None,
                step=60,
                key=_pk("middle_sample_dt"),
                help="Open the calendar icon to pick date and time.",
            )
        with d3:
            bottom_sample_dt = ui_datetime_input(
                "Datetime",
                value=None,
                step=60,
                key=_pk("bottom_sample_dt"),
                help="Open the calendar icon to pick date and time.",
            )
        with d4:
            st.empty()

        st.markdown("#### Charge / raw material inputs")
        st.caption(
            "Select trolley (tare), enter weighment scale reading. "
            "**Net Weight = Weighment scale − Trolley weight.**"
        )

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
            lot_opts = {
                f"Lot {l['Lot_id']} — rem {l['Remaining_Weight']:.1f} kg ({l['Raw_Material_Status']})": l["Lot_id"]
                for l in lots
            }
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
                    "Weighment scale weight (kg) *",
                    key=_pk(f"scale_w_{idx}"),
                    max_value=None,
                    step=1.0,
                )
                wsp_open_key = _pk(f"wsp_open_{idx}")
                if st.button(
                    "📷 Weighment scale photo",
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
                        st.success("Weighment scale photo ready to save with this charge line.")
                else:
                    scale_photo_bytes = st.session_state.get(_pk(f"wsp_bytes_{idx}"))
                    if scale_photo_bytes:
                        st.caption("Weighment scale photo attached.")

            # Net charge = weighment scale − trolley tare (always recompute into widget state)
            tare_w = float(st.session_state.get(tare_key, trolley_w) or 0.0)
            scale_val = float(scale_w or 0)
            net_w = max(scale_val - tare_w, 0.0) if trolley_name and scale_val > 0 else 0.0
            net_key = _pk(f"wt_{idx}")
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

        add_col, rem_col, _ = st.columns([1, 1, 4])
        if add_col.button("Add charge line", key=_pk("add_charge")):
            drafts[furnace].append(
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            )
            st.rerun()
        if rem_col.button("Remove last line", key=_pk("rem_charge")) and len(
            drafts[furnace]
        ) > 1:
            drafts[furnace].pop()
            st.rerun()

        total_in = sum(c["Weight"] for c in charge_inputs)
        st.info(f"Total net input weight: **{total_in:,.2f} kg** across {len(charge_inputs)} charge line(s).")

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

        if st.button(
            "Create production batch",
            type="primary",
            disabled=available_crucible is None,
            key=_pk("create_batch"),
        ):
            if available_crucible is None:
                st.error("No crucible available for the respective furnace.")
            elif not charge_inputs:
                st.error("Add at least one charge line with weight > 0.")
            else:
                try:
                    merged_chem = merge_percent_composition(batch_chem, full_chem_key)
                    bid = db.create_batch(
                        furnace=furnace,
                        heat_no=heat_no,
                        alloy_id=alloy_id,
                        production_date=prod_date.isoformat(),
                        shift=shift,
                        melt_no=melt_no,
                        melting_team=melting_team,
                        notes=notes.strip(),
                        inputs=charge_inputs,
                        composition={k: v for k, v in merged_chem.items() if v and v > 0},
                        degassing_time=degassing_time.strip() or None,
                        sampled_pcs=sampled_pcs if sampled_pcs and sampled_pcs > 0 else None,
                        defect_pcs=defect_pcs if defect_pcs and defect_pcs > 0 else None,
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
                    st.success(
                        f"Created batch **{bid}** — Heat {heat_no}, Furnace {furnace}. "
                        "Record product and non-spec output on **Batch Output**."
                    )
                    drafts[furnace] = [
                        {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
                    ]
                    st.session_state.pop(full_chem_key, None)
                    st.balloons()
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
        "Create the batch first on **Production Batch & Chemistry**. "
        "On save, each output line stores material ₹/kg (charge lot cost ÷ total output kg) "
        "and overall ₹/kg (material + Cost of Conversion for the production month, "
        "or the previous available month if that month's rates are not in yet)."
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
                    "Charge input (kg)",
                    f"{float(batch.get('Input_Weight') or 0):,.2f}",
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
        show_dataframe(saved_all)


# ═══════════════════════════════════════════════════════════════════════════════
# 2b. Production Batches (browse — kept separate for efficient batch capture)
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Batches":
    st.title("Production Batches")
    st.caption(
        "Review existing production batches. "
        "New batches are created on **Production Batch & Chemistry**. "
        "Input is charge weight; output is entered on **Batch Output** "
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
# 3. Production Workflow Tracker
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Production Workflow Tracker":
    st.title("Production Workflow Tracker")
    st.caption(
        "Move each batch through: Raw Material → Melting/Furnace → Casting → "
        "Quality Inspection → Finished Goods."
    )

    batches = db.list_batches()
    if not batches:
        st.info("No batches to track yet.")
    else:
        batch_ids = [b["Batch_ID"] for b in batches]
        selected = st.selectbox("Select Batch ID", batch_ids)
        batch = db.get_batch(selected)
        if batch:
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Batch ID", batch["Batch_ID"])
            m2.metric("Furnace", batch["Furnace"])
            m3.metric("Crucible", batch.get("Crucible_no") or "—")
            m4.metric("Heat No", batch["Heat_no"])
            m5.metric("Output kg", f"{batch['Output_Weight'] or 0:,.1f}")
            st.caption(
                f"Input **{float(batch.get('Input_Weight') or 0):,.1f} kg** from charges. "
                "Output is the sum of recorded batch outputs (not charge weight)."
            )

            st.markdown(
                f"**Current stage:** `{batch['Workflow_stage']}` &nbsp;|&nbsp; "
                f"**QA status:** `{batch['Production_status']}`"
            )

            # Stage progress
            try:
                stage_idx = db.WORKFLOW_STAGES.index(batch["Workflow_stage"])
            except ValueError:
                stage_idx = 0
            prog = (stage_idx + 1) / len(db.WORKFLOW_STAGES)
            st.progress(prog, text=" → ".join(db.WORKFLOW_STAGES))

            with st.form("workflow_form"):
                wc1, wc2 = st.columns(2)
                with wc1:
                    new_stage = st.selectbox(
                        "Workflow stage",
                        db.WORKFLOW_STAGES,
                        index=stage_idx,
                    )
                with wc2:
                    qa = st.selectbox(
                        "QA / batch status",
                        db.BATCH_QA_STATUS,
                        index=db.BATCH_QA_STATUS.index(batch["Production_status"])
                        if batch["Production_status"] in db.BATCH_QA_STATUS
                        else 0,
                    )
                save = st.form_submit_button("Update workflow", type="primary")

            if save:
                db.update_batch_workflow(
                    selected,
                    workflow_stage=new_stage,
                    qa_status=qa,
                )
                if qa == "Approved":
                    st.success(
                        f"Batch {selected} → **{new_stage}** (Approved). "
                        "Under_Testing finished-goods bundles for this batch are now **Available**."
                    )
                else:
                    st.success(f"Batch {selected} → **{new_stage}** ({qa}).")
                st.rerun()

            st.subheader("Charge details")
            show_dataframe(df_from_rows(db.get_batch_inputs(selected)))
            st.subheader("Chemistry")
            chem_df = df_from_rows(db.get_batch_chemistry(selected))
            if chem_df.empty:
                st.caption("No chemistry recorded.")
            else:
                show_dataframe(chem_df)
            render_batch_output_editor(batch, key_prefix="wf_out")

        st.divider()
        st.subheader("All batches by stage")
        overview = df_from_rows(batches)
        if not overview.empty:
            cols = [
                "Batch_ID",
                "Production_Date",
                "Furnace",
                "Heat_no",
                "Workflow_stage",
                "Production_status",
                "Input_Weight",
                "Output_Weight",
                "Output_pieces",
                "Alloy_name",
            ]
            show_dataframe(overview[[c for c in cols if c in overview.columns]])


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Material Recovery & Yield
# ═══════════════════════════════════════════════════════════════════════════════
elif PAGE == "Material Recovery & Yield":
    st.title("Material Recovery & Yield Calculator")
    st.caption(
        f"Recovery % = (total recorded output / charge input) × 100. "
        f"Enter outputs on **Batch Output** (product alloy plus Broken Ingot, "
        f"Furnace Empty, and Not Ok Ingot). "
        f"Below **{db.YIELD_TARGET_PCT:.0f}%** is highlighted in red."
    )

    batches = db.list_batches()
    if not batches:
        st.info("No batches available.")
    else:
        labels = {
            f"{b['Batch_ID']}  |  stage={b['Workflow_stage']}  |  "
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

        if st.button("Advance to Casting if output recorded", key="yield_advance"):
            stage = batch["Workflow_stage"]
            if output_w > 0 and stage in ("Raw Material", "Melting/Furnace"):
                db.update_batch_workflow(bid, workflow_stage="Casting")
                st.info("Batch stage advanced to **Casting**.")
                st.rerun()
            elif output_w <= 0:
                st.warning("Save at least one output line with weight > 0 first.")
            else:
                st.info(f"Batch is already at **{stage}**.")

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
        "New bundles are created as **Under_Testing** and cannot be assigned. "
        "When the linked production batch is set to **Approved**, those bundles "
        "automatically become **Available**."
    )

    batches = db.list_batches()
    batch_ids = [b["Batch_ID"] for b in batches]
    batch_map = {b["Batch_ID"]: b for b in batches}

    st.markdown("#### Add bundle")
    if not batch_ids:
        st.info("Create a production batch first.")
    else:
        with st.form("fg_add_form", clear_on_submit=True):
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                fg_batch = st.selectbox("Batch ID *", batch_ids)
            linked = batch_map.get(fg_batch, {})
            product_id = linked.get("Alloy_id")
            product_out = 0.0
            product_pcs = 0
            for r in db.get_batch_outputs(fg_batch):
                if product_id is None or int(r["Alloy_id"]) != int(product_id):
                    continue
                product_out += float(r["Weight"] or 0)
                product_pcs += int(r.get("Pieces") or 0)
            with fc2:
                fg_w = st.number_input(
                    "Output weight (kg)",
                    min_value=0.0,
                    value=float(product_out or 0),
                    step=1.0,
                    help="Defaults to this batch’s product-alloy output (excludes 78/79/80).",
                )
            with fc3:
                fg_pcs = st.number_input(
                    "Output pieces",
                    min_value=0,
                    value=int(product_pcs or 0),
                    step=1,
                    format="%d",
                    help="Whole number of pieces. No decimals.",
                )
            st.caption(
                f"Status will be locked to **{db.FG_STATUS_UNDER_TESTING}** "
                f"(batch QA: `{linked.get('Production_status', '—')}`)."
            )
            if st.form_submit_button("Create bundle", type="primary"):
                try:
                    bid = db.add_finished_goods_bundle(
                        batch_id=fg_batch,
                        output_weight=fg_w if fg_w > 0 else None,
                        output_pieces=int(fg_pcs) if fg_pcs > 0 else None,
                    )
                    st.success(
                        f"Created bundle **{bid}** for batch {fg_batch} "
                        f"(status {db.FG_STATUS_UNDER_TESTING})."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    st.divider()
    st.markdown("#### All bundles")
    all_fg = db.list_finished_goods()
    show_dataframe(df_from_rows(all_fg))

    locked = [r for r in all_fg if r["Finished_Goods_Status"] == db.FG_STATUS_UNDER_TESTING]
    if locked:
        st.warning(
            f"{len(locked)} bundle(s) locked as **Under_Testing** — "
            "approve the production batch to release them."
        )

    st.markdown("#### Assignable stock (Available only)")
    available = db.list_finished_goods(assignable_only=True)
    if not available:
        st.info("No Available bundles. Under_Testing stock stays locked until batch approval.")
    else:
        assign_opts = {
            f"Bundle {r['Bundle_id']} | batch {r['Batch_ID']} | "
            f"{r['Output_Weight'] or 0:.1f} kg / {r['Output_pieces'] or 0:.0f} pcs": r["Bundle_id"]
            for r in available
        }
        pick = st.selectbox("Select Available bundle to assign", list(assign_opts.keys()))
        if st.button("Mark as Assigned", type="primary"):
            try:
                db.assign_finished_goods_bundle(assign_opts[pick])
                st.success(f"Bundle {assign_opts[pick]} marked **Assigned**.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


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
            | 21 | Finished_Goods_Inventory | Bundles (Under_Testing → Available → Assigned / Dispatched / Rejected) |
            | 22 | Furnace_Oil_Purchase | Furnace oil receipts and opening stock |
            | 23 | Furnace_Oil_Consumption | Daily furnace oil use (one row per day) |
            | 24 | Furnace_Oil_Inventory | Daily opening / purchase / consumption / closing ledger |
            | 25 | Electricity_Consumption | Daily opening/closing power readings per EB Line 1 / EB Line 2 |
            | 26 | Cost_of_conversion | Monthly conversion rates per kg (oil, electricity, labour, salaries, consumables, overheads) |

            Extra production columns: `Workflow_stage`, sample fields, `Production_supervisor`.
            """
        )
