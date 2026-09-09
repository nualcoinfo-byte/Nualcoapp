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
# Sidebar marker so a stale Railway replica is obvious on the login page.
APP_BUILD = "2026-09-03-dashboard-produce"

st.set_page_config(
    page_title="Nualco Alloy Tracker",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject Streamlit Cloud secrets into the environment BEFORE importing the
# database module, which binds SQLAlchemy's engine at import time.
_secret_url = ""
try:
    if "DATABASE_URL" in st.secrets:
        _secret_url = str(st.secrets["DATABASE_URL"]).strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = _secret_url
        # A leftover Neon DATABASE_URL_UNPOOLED must not override Supabase.
        if "neon.tech" not in _secret_url.lower() and "DATABASE_URL_UNPOOLED" in os.environ:
            os.environ.pop("DATABASE_URL_UNPOOLED", None)
except Exception:
    pass

# A previous SQLite fallback must not lock the session when a Postgres URL
# is configured. Streamlit Cloud keeps session_state across reruns.
if _secret_url or os.environ.get("DATABASE_URL"):
    os.environ.pop("NUALCO_FORCE_SQLITE", None)
    st.session_state.pop("use_sqlite", None)
    st.session_state.pop("_offline_sqlite", None)
elif st.session_state.get("use_sqlite"):
    os.environ["NUALCO_FORCE_SQLITE"] = "1"

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

from pages_common import (  # noqa: E402
    df_from_rows,
    _show_db_connection_error,
    photo_bytes,
    _as_photo_bytes,
    _pandas_styler,
    empty_percent_input,
    dialog_all_element_percentages,
    _is_blank_date,
    parse_any_date,
    format_ui_date,
    to_storage_date,
    show_dataframe,
    ui_date_input,
    _parse_master_date,
    merge_percent_composition,
    _render_dashboard_refresh_bar,
    UI_DATE_WIDGET_FORMAT,
    CHEM_PERCENT_STEP,
    CHEM_PERCENT_FORMAT,
)

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


def _on_streamlit_cloud() -> bool:
    return (
        os.path.isdir("/mount/src")
        or os.path.isdir("/home/appuser")
        or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
        or bool(os.environ.get("RAILWAY_PROJECT_ID"))
    )


@st.cache_resource
def _init_postgres() -> bool:
    """Handshake once per process. Failures are cleared by the caller."""
    db._ensure_packing_list_ready()
    db._ensure_company_ready()
    db.start_dashboard_refresh_scheduler()
    return True


def bootstrap() -> str:
    """Open the database. Full init_db() is skipped on Postgres (it can hang)."""
    if db.IS_POSTGRES:
        try:
            db.adopt_supabase_pooler()
            _init_postgres()
            st.session_state.pop("_neon_init_error", None)
            return "postgres"
        except Exception as exc:
            st.session_state["_neon_init_error"] = str(exc)
            try:
                _init_postgres.clear()
            except Exception:
                pass
            text = str(exc).lower()
            if "tenant" in text or "enotfound" in text or "could not connect" in text:
                try:
                    if db.adopt_supabase_pooler():
                        _init_postgres()
                        st.session_state.pop("_neon_init_error", None)
                        return "postgres"
                except Exception as exc2:
                    st.session_state["_neon_init_error"] = str(exc2)
                    try:
                        _init_postgres.clear()
                    except Exception:
                        pass
            if _on_streamlit_cloud():
                return "postgres-error"
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


def draft_banner(label: str, has_draft: bool, on_discard) -> None:
    """Show a dismissable 'unsaved draft' banner when has_draft is True.

    The draft itself already survives page navigation via session_state;
    this just makes that state visible instead of leaving it silent, and
    gives an explicit way to discard it.
    """
    if not has_draft:
        return
    b1, b2 = st.columns([5, 1])
    with b1:
        st.warning(f"You have unsaved {label}. It's kept until you save or discard it.")
    with b2:
        if st.button("Discard", key=f"discard_{label.replace(' ', '_')}"):
            on_discard()
            st.rerun()




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
            "_ret_save",
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
    ("Overview", ["Dashboard", "Production Data Analysis"]),
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
            "Daily Batch Summary",
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
ADMIN_PAGE_ROLES = "Roles & permissions"
ADMIN_PAGE_PASSWORDS = "Employee passwords"
ADMIN_NAV_PAGES = [
    ADMIN_PAGE_CANCEL_ISSUED,
    ADMIN_PAGE_ROLES,
    ADMIN_PAGE_PASSWORDS,
]
_NAV_SECTION_KEY = {
    "Overview": "overview",
    "Purchasing & inventory": "purchasing",
    "Production": "production",
    "Utilities & conversion": "utilities",
    "Masters": "masters",
    "Tools": "tools",
    ADMIN_NAV_SECTION: "admin",
}
_BASE_NAV_PAGES = [page for _section, pages in NAV_SECTIONS for page in pages]
_ALL_NAV_PAGES = _BASE_NAV_PAGES + ADMIN_NAV_PAGES
_ALL_SECTION_NAMES = [section for section, _pages in NAV_SECTIONS] + [ADMIN_NAV_SECTION]


def _nav_sections(*, include_admin: bool) -> list[tuple[str, list[str]]]:
    sections = list(NAV_SECTIONS)
    if include_admin:
        sections.append((ADMIN_NAV_SECTION, list(ADMIN_NAV_PAGES)))
    return sections


def _nav_sections_for_role(role_id: object, role_name: object) -> list[tuple[str, list[str]]]:
    allowed = set(db.nav_section_keys_for_role(role_id, role_name))
    if db.role_is_admin(role_name, role_id):
        allowed = set(db.ALL_NAV_SECTION_KEYS)
    sections: list[tuple[str, list[str]]] = []
    for section, pages in NAV_SECTIONS:
        if _NAV_SECTION_KEY.get(section) in allowed:
            sections.append((section, list(pages)))
    if "admin" in allowed:
        sections.append((ADMIN_NAV_SECTION, list(ADMIN_NAV_PAGES)))
    return sections


def _on_nav_section(section: str) -> None:
    chosen = st.session_state.get(f"nav_radio_{section}")
    if not chosen:
        return
    st.session_state.nav_page = chosen
    for other in _ALL_SECTION_NAMES:
        if other != section:
            st.session_state[f"nav_radio_{other}"] = None


def _apply_logged_in_actor(emp: dict) -> None:
    db.set_session_actor(
        name=db.employee_display_name(emp),
        employee_id=str(emp.get("employee_id") or ""),
        role_name=str(emp.get("role_name") or ""),
        role_id=emp.get("role_id"),
    )


def _render_login() -> None:
    st.sidebar.caption("Sign in with your employee ID.")
    st.title("Sign in")
    st.caption("Use the employee ID from the employees table. Admin sets passwords.")
    first_setup = not db.any_employee_has_password()
    if first_setup:
        st.info(
            "No passwords are set yet. Create the first Admin password here, "
            "then use **Employee passwords** to set passwords for everyone else."
        )
        candidates = db.list_admin_employees() or db.list_employees(include_inactive=False)
        if not candidates:
            st.error("There are no employees in the database. Load the employees table first.")
            return
        labels = {
            f"{db.employee_display_name(row)}  ·  {row.get('employee_id')}  ·  "
            f"{row.get('role_name') or '—'}": str(row.get("employee_id"))
            for row in candidates
        }
        with st.form("first_admin_password"):
            pick = st.selectbox("Admin employee", list(labels.keys()))
            password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Set password and sign in", type="primary")
        if submitted:
            if password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                emp = db.bootstrap_first_admin_password(labels[pick], password)
            except Exception as exc:
                st.error(str(exc))
                return
            st.session_state.auth_employee = emp
            st.session_state.nav_page = "Dashboard"
            st.rerun()
        return

    with st.form("employee_login"):
        login_id = st.text_input("Employee ID", placeholder="001")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        emp = db.authenticate_employee(login_id, password)
        if not emp:
            st.error("Invalid employee ID or password.")
            return
        st.session_state.auth_employee = emp
        st.session_state.nav_page = "Dashboard"
        st.rerun()


st.sidebar.divider()
auth_employee = st.session_state.get("auth_employee")
if not auth_employee:
    db.clear_session_actor()
    if _db_mode == "postgres-error":
        st.sidebar.error(
            "Could not reach Supabase yet. Click **Retry**, or reboot the app."
        )
        if st.sidebar.button("Retry database connection"):
            st.session_state.pop("_neon_init_error", None)
            st.cache_resource.clear()
            st.rerun()
    try:
        _render_login()
    except Exception as exc:
        st.error(f"Could not load the login page: {exc}")
    st.sidebar.markdown(
        f"**DB:** `{db.DB_LABEL}`  \n`build {APP_BUILD}`"
    )
    st.stop()

_apply_logged_in_actor(auth_employee)
is_admin = db.is_admin_user()
nav_sections = _nav_sections_for_role(
    auth_employee.get("role_id"),
    auth_employee.get("role_name"),
)
allowed_pages = [page for _section, pages in nav_sections for page in pages]

st.sidebar.markdown(
    f"**{html.escape(db.employee_display_name(auth_employee))}**  \n"
    f"`{html.escape(str(auth_employee.get('employee_id') or ''))}` · "
    f"{html.escape(str(auth_employee.get('role_name') or '—'))}"
)
if st.sidebar.button("Log out"):
    st.session_state.pop("auth_employee", None)
    db.clear_session_actor()
    st.session_state.nav_page = "Dashboard"
    st.rerun()

if st.session_state.get("nav_page") not in allowed_pages:
    st.session_state.nav_page = allowed_pages[0] if allowed_pages else "Dashboard"

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
if PAGE not in allowed_pages:
    st.error("You do not have access to this page.")
    st.stop()

st.sidebar.markdown(
    f"**Yield target:** {db.YIELD_TARGET_PCT:.0f}%  \n"
    f"**DB:** `{db.DB_LABEL}`  \n"
    f"`build {APP_BUILD}`"
)
if _db_mode == "postgres-error":
    st.sidebar.error(
        "Could not reach Supabase yet. Click **Retry**, or reboot the app "
        "from Manage app. Keep `DATABASE_URL` as your Supabase URI "
        "(password `@` encoded as `%40`)."
    )
    neon_err = st.session_state.get("_neon_init_error")
    if neon_err:
        st.sidebar.caption(f"Database init error: {neon_err}")
    if st.sidebar.button("Retry database connection"):
        st.session_state.pop("_neon_init_error", None)
        st.cache_resource.clear()
        st.rerun()
elif st.session_state.get("use_sqlite") or os.environ.get("NUALCO_FORCE_SQLITE"):
    st.sidebar.warning(
        "Offline SQLite mode. Rows you save stay on this PC and are not "
        "written to the shared database."
    )
    neon_err = st.session_state.get("_neon_init_error")
    if neon_err:
        st.sidebar.caption(f"Database init error: {neon_err}")
    if st.sidebar.button("Reconnect to database"):
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


def _style_spec_check_table(data: pd.DataFrame):
    """Highlight rows whose Status starts with Out of spec."""

    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Status") or "")
        if status.startswith("Out of spec"):
            return ["background-color: #fdecea; color: #c62828; font-weight: 700"] * len(
                row
            )
        return [""] * len(row)

    styler = _pandas_styler(data)
    if styler is None:
        return data
    return styler.apply(_row_style, axis=1)


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


def _po_rate(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _allocate_po_to_produce(rows: list[dict], alloy_plan: dict[int, dict]) -> None:
    """Assign remaining melt kg to each open PO line.

    Finished goods are shared per alloy, so cover earlier-due lines first.
    ``rows`` must already be sorted by priority / delivery date.
    """
    fg_left = {
        aid: float(item.get("FG_Available_Qty") or 0) for aid, item in alloy_plan.items()
    }
    for row in rows:
        try:
            alloy_id = int(row.get("Alloy_Id"))
        except (TypeError, ValueError):
            alloy_id = None
        uncovered = max(
            0.0, _kg(row.get("Balance_Qty")) - _kg(row.get("In_packing_Qty"))
        )
        if alloy_id is None:
            row["To_Produce_Qty"] = uncovered
            continue
        take = min(uncovered, fg_left.get(alloy_id, 0.0))
        fg_left[alloy_id] = fg_left.get(alloy_id, 0.0) - take
        row["To_Produce_Qty"] = max(0.0, uncovered - take)






@st.cache_data(ttl=60, show_spinner=False)
def _dashboard_overview_data(year: int, month: int) -> dict:
    """Dashboard-only cache for the small summary-row lookups.

    list_po_supply_status() is already materialized-view backed and fast;
    these are plain table scans that don't need to re-run on every rerun
    of this page (furnace filter changes, the refresh bar's own button,
    etc.), so a short TTL is enough to cut repeat DB round-trips. Scoped to
    the Dashboard only -- other pages that call these same db.* functions
    (e.g. Furnace Oil Purchase showing the stock right after a save) still
    read live, since they need to reflect a write immediately.
    """
    return {
        "batches": db.list_batches(),
        "materials": db.list_raw_materials(),
        "lots": db.list_inventory_lots(),
        "alloys": db.list_alloys(),
        "oil_stock": db.get_furnace_oil_stock(),
        "elec_month": db.electricity_month_totals(year, month),
    }


@st.cache_data(ttl=60, show_spinner=False)
def _production_batch_reference_data() -> dict:
    """Cached bundle of rarely-changing reference lists Production Batch &
    Chemistry reads.

    Streamlit reruns the whole script on every keystroke/click, so without
    caching, each of these plain table scans (and its own RLS-session round
    trip) re-runs on every single interaction on this page — the app's
    heaviest, most-interacted-with page. Furnaces/melters/supervisors/alloys/
    raw materials/trolleys/chemistry elements only change via their own
    Master pages, so a short TTL is enough to keep this fresh in practice.

    raw_material_master_by_name folds in every charge line's per-material
    Recovery lookup (previously one db.get_raw_material_master() call per
    line, every rerun) into this same cached bundle — newest Effective_date
    row wins per name, same rule used everywhere else in this codebase.
    """
    master_by_name: dict[str, dict] = {}
    for row in db.list_raw_material_master():
        master_by_name.setdefault(str(row["Raw_Material_Name"]).lower(), row)
    return {
        "furnaces": db.list_furnaces(),
        "melters": db.list_melters(),
        "supervisors": db.list_production_supervisors(),
        "alloys": db.list_alloys(include_sidestream=False),
        "raw_materials": db.list_raw_materials(),
        "trolleys": db.list_trolleys(active_only=True),
        "chem_elements": db.list_batch_chem_elements(),
        "raw_material_master_by_name": master_by_name,
    }


# ── Phase 1 multipage dispatch ────────────────────────────────────────────────
# st.navigation/st.Page run only the 7 pages already migrated to app_pages/;
# everything else still runs via the legacy `elif PAGE ==` chain below,
# unchanged. position="hidden" keeps Streamlit's own nav widget invisible so
# the existing NAV_SECTIONS sidebar (unchanged, above) is still what the user
# sees and clicks -- st.navigation here is pure internal plumbing to legally
# execute the migrated files, not a second navigation UI.
def _legacy_stub() -> None:
    """No-op placeholder Page. The un-migrated pages still run via the
    `elif PAGE ==` chain below; later phases move more of them into
    MIGRATED_PAGES/app_pages/ one at a time."""
    return None


MIGRATED_PAGES: dict[str, st.Page] = {
    "Company": st.Page("app_pages/company.py", title="Company", url_path="company"),
    "Customers": st.Page(
        "app_pages/customers.py", title="Customers", url_path="customers"
    ),
    "Vendors": st.Page("app_pages/vendors.py", title="Vendors", url_path="vendors"),
    "Furnaces": st.Page(
        "app_pages/furnaces.py", title="Furnaces", url_path="furnaces"
    ),
    "Crucibles": st.Page(
        "app_pages/crucibles.py", title="Crucibles", url_path="crucibles"
    ),
    "Melters": st.Page("app_pages/melters.py", title="Melters", url_path="melters"),
    "Trolleys": st.Page(
        "app_pages/trolleys.py", title="Trolleys", url_path="trolleys"
    ),
    "Raw Material Logging": st.Page(
        "app_pages/raw_material_logging.py",
        title="Raw Material Logging",
        url_path="raw-material-logging",
    ),
    "Raw Material Inventory": st.Page(
        "app_pages/raw_material_inventory.py",
        title="Raw Material Inventory",
        url_path="raw-material-inventory",
    ),
    "Furnace Oil Purchase": st.Page(
        "app_pages/furnace_oil_purchase.py",
        title="Furnace Oil Purchase",
        url_path="furnace-oil-purchase",
    ),
    "Production Batches": st.Page(
        "app_pages/production_batches.py",
        title="Production Batches",
        url_path="production-batches",
    ),
    "Furnace Oil Consumption": st.Page(
        "app_pages/furnace_oil_consumption.py",
        title="Furnace Oil Consumption",
        url_path="furnace-oil-consumption",
    ),
    "Electricity Consumption": st.Page(
        "app_pages/electricity_consumption.py",
        title="Electricity Consumption",
        url_path="electricity-consumption",
    ),
    "Cost of Conversion": st.Page(
        "app_pages/cost_of_conversion.py",
        title="Cost of Conversion",
        url_path="cost-of-conversion",
    ),
    "Finished Goods Inventory": st.Page(
        "app_pages/finished_goods_inventory.py",
        title="Finished Goods Inventory",
        url_path="finished-goods-inventory",
    ),
    "Purchase Orders": st.Page(
        "app_pages/purchase_orders.py",
        title="Purchase Orders",
        url_path="purchase-orders",
    ),
    "All Purchase Orders": st.Page(
        "app_pages/all_purchase_orders.py",
        title="All Purchase Orders",
        url_path="all-purchase-orders",
    ),
    "Bill of Materials": st.Page(
        "app_pages/bill_of_materials.py",
        title="Bill of Materials",
        url_path="bill-of-materials",
    ),
    "Material Recovery & Yield": st.Page(
        "app_pages/material_recovery_yield.py",
        title="Material Recovery & Yield",
        url_path="material-recovery-yield",
    ),
    "Production Data Analysis": st.Page(
        "app_pages/production_data_analysis.py",
        title="Production Data Analysis",
        url_path="production-data-analysis",
    ),
    "Raw Material Master": st.Page(
        "app_pages/raw_material_master.py",
        title="Raw Material Master",
        url_path="raw-material-master",
    ),
    "Alloys": st.Page("app_pages/alloys.py", title="Alloys", url_path="alloys"),
    "Data Browser": st.Page(
        "app_pages/data_browser.py",
        title="Data Browser",
        url_path="data-browser",
    ),
    "Masters Overview": st.Page(
        "app_pages/masters_overview.py",
        title="Masters Overview",
        url_path="masters-overview",
    ),
    ADMIN_PAGE_CANCEL_ISSUED: st.Page(
        "app_pages/admin_cancel_issued.py",
        title="Cancel issued certificate",
        url_path="admin-cancel-issued",
    ),
    ADMIN_PAGE_ROLES: st.Page(
        "app_pages/admin_roles.py",
        title="Roles & permissions",
        url_path="admin-roles",
    ),
    ADMIN_PAGE_PASSWORDS: st.Page(
        "app_pages/admin_passwords.py",
        title="Employee passwords",
        url_path="admin-passwords",
    ),
    "Batch Output": st.Page(
        "app_pages/batch_output.py",
        title="Batch Output",
        url_path="batch-output",
    ),
    "Daily Batch Summary": st.Page(
        "app_pages/daily_batch_summary.py",
        title="Daily Batch Summary",
        url_path="daily-batch-summary",
    ),
}
_LEGACY_PAGE = st.Page(_legacy_stub, title="Legacy", url_path="legacy", default=True)

_pg = st.navigation(list(MIGRATED_PAGES.values()) + [_LEGACY_PAGE], position="hidden")
_target = MIGRATED_PAGES.get(PAGE, _LEGACY_PAGE)
if _pg.url_path != _target.url_path:
    st.switch_page(_target)  # NoReturn -- stops execution here, triggers a rerun
_pg.run()  # runs the matched migrated file, or no-ops for the legacy stub


# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ═══════════════════════════════════════════════════════════════════════════════
if PAGE == "Dashboard":
    st.title("Production Dashboard")
    st.caption(
        "Open purchase-order lines versus quantity already dispatched, "
        "balance still to supply, rate, and finished-goods stock. "
        "Produce lines with a **To produce** quantity first. "
        "Finished goods are shared across every open PO for that alloy, "
        "allocated to earlier delivery dates first. "
        "Dispatch is verified packing-list weight."
    )
    _render_dashboard_refresh_bar(key_prefix="dash")
    today = date.today()
    try:
        supply_rows = db.list_po_supply_status()
        overview = _dashboard_overview_data(today.year, today.month)
        batches = overview["batches"]
        materials = overview["materials"]
        lots = overview["lots"]
        alloys = overview["alloys"]
        oil_stock = overview["oil_stock"]
        elec_month = overview["elec_month"]
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
    _allocate_po_to_produce(po_view, alloy_plan)

    total_order = sum(_kg(r.get("Order_Qty")) for r in open_rows)
    total_dispatched = sum(_kg(r.get("Dispatched_Qty")) for r in open_rows)
    total_balance = sum(_kg(r.get("Balance_Qty")) for r in open_rows)
    total_fg = sum(item["FG_Available_Qty"] for item in alloy_plan.values())
    total_to_produce = sum(_kg(r.get("To_Produce_Qty")) for r in po_view)
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
        "One row per open customer PO and alloy. "
        "**To produce** is the remaining melt after in-packing and available "
        "finished goods (shared FG is applied to earlier delivery dates first). "
        "**Overdue** / **Due today** are by delivery date. "
        "**Produce** means this alloy still has a melt shortfall."
    )
    if not po_view:
        st.info("No open purchase orders. Create one under **Purchase Orders**.")
    else:
        po_df = pd.DataFrame(
            [
                {
                    "Priority": r.get("Priority"),
                    "Customer": r.get("Customer_name") or r.get("Cust_code") or "—",
                    "PO No": r.get("Customer_PO_No"),
                    "Alloy": r.get("Alloy_name") or r.get("Alloy_Id"),
                    "Delivery date": r.get("Delivery_Date"),
                    "Rate": _po_rate(r.get("Rate")),
                    "Order qty (kg)": _kg(r.get("Order_Qty")),
                    "Dispatched (kg)": _kg(r.get("Dispatched_Qty")),
                    "In packing (kg)": _kg(r.get("In_packing_Qty")),
                    "Balance (kg)": _kg(r.get("Balance_Qty")),
                    "FG available (kg)": _kg(r.get("FG_Available_Qty")),
                    "Under testing (kg)": _kg(r.get("FG_Under_Testing_Qty")),
                    "To produce (kg)": _kg(r.get("To_Produce_Qty")),
                }
                for r in po_view
            ]
        )
        kg_cols = [
            "Order qty (kg)",
            "Dispatched (kg)",
            "In packing (kg)",
            "Balance (kg)",
            "FG available (kg)",
            "Under testing (kg)",
            "To produce (kg)",
        ]
        show_dataframe(
            po_df,
            column_config={
                "Priority": st.column_config.TextColumn("Priority"),
                "Rate": st.column_config.NumberColumn(format="%.2f"),
                **{
                    col: st.column_config.NumberColumn(format="%.1f")
                    for col in kg_cols
                },
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

    _pb_ref = _production_batch_reference_data()
    furnaces = _pb_ref["furnaces"]
    melters = _pb_ref["melters"]
    supervisors = _pb_ref["supervisors"]
    alloys = _pb_ref["alloys"]
    alloy_labels = {
        f"{a['Alloy_id']} — {a['Alloy_name']}"
        + (f" ({a['Customer_name']})" if a["Customer_name"] else ""): a["Alloy_id"]
        for a in alloys
    }
    materials = _pb_ref["raw_materials"]

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

        trolleys = _pb_ref["trolleys"]
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

        _charge_line_fields = (
            "mat", "lot", "trolley", "trolley_w", "_prev_trolley_label",
            "scale_w", "wsp_open", "wsp_cam", "wsp_file", "wsp_bytes",
            "wt", "ln", "inp_open", "inp_cam", "inp_file", "inp_bytes",
        )

        def _charge_draft_has_input() -> bool:
            for idx in range(len(furnace_charge_lines)):
                if st.session_state.get(_pk(f"mat_{idx}")):
                    return True
                if st.session_state.get(_pk(f"lot_{idx}")):
                    return True
                if st.session_state.get(_pk(f"trolley_{idx}")):
                    return True
                if float(st.session_state.get(_pk(f"scale_w_{idx}")) or 0) > 0:
                    return True
                if (st.session_state.get(_pk(f"ln_{idx}")) or "").strip():
                    return True
            return False

        def _discard_charge_draft() -> None:
            for idx in range(len(furnace_charge_lines)):
                for field in _charge_line_fields:
                    st.session_state.pop(_pk(f"{field}_{idx}"), None)
            drafts[furnace] = [
                {"material": "", "lot_id": None, "weight": 0.0, "notes": ""}
            ]

        if not locked:
            draft_banner(
                "charge line input", _charge_draft_has_input(), _discard_charge_draft
            )

        if not trolleys:
            st.error("Define at least one active trolley under **Trolleys**.")

        @st.fragment
        def _render_charge_and_estimate() -> None:
            """Charge-line entry + live cost estimate, isolated from the rest
            of the page.

            Streamlit reruns the whole script on every widget interaction by
            default; on this page that meant every keystroke in a charge-line
            field re-ran the per-line lot/master lookups, the multi-query cost
            estimate, header fields, chemistry entry, everything. @st.fragment
            scopes reruns triggered by a widget inside this function to just
            this function, so editing charge lines stays fast without touching
            the rest of the form. It still participates fully in any full-page
            rerun (e.g. switching furnace, saving the batch), so outer values
            it closes over (furnace, existing_batch, preview_id, alloy_id,
            saved_charges, locked, the cached reference-data lists) are only
            ever stale between fragment-scoped reruns, and none of those change
            as a result of editing a charge line itself.
            """
            # Batch the lot lookup for every line's already-selected material
            # into one query instead of one query per line (N+1). Streamlit
            # already updated session_state for the widget that triggered
            # this rerun before the script runs, so this sees this rerun's
            # selections, not last rerun's.
            _selected_materials = [
                st.session_state.get(_pk(f"mat_{idx}"))
                for idx in range(len(furnace_charge_lines))
            ]
            _selected_materials = [m for m in _selected_materials if m]
            lots_by_material: dict[str, list[dict]] = {}
            if _selected_materials:
                for lot in db.list_inventory_lots(materials=_selected_materials):
                    lots_by_material.setdefault(
                        str(lot["Raw_Material_Name"]).lower(), []
                    ).append(lot)

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
                lots = lots_by_material.get(mat.lower(), []) if mat else []
                lot_opts = {}
                lot_cost_by_label = {}
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
                    lot_cost_by_label[label] = lot.get("Cost_per_kg")
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

                if mat:
                    master_row = _pb_ref["raw_material_master_by_name"].get(
                        mat.lower(), {}
                    )
                    recovery_val = master_row.get("Recovery")
                    lot_cost_val = lot_cost_by_label.get(lot_label) if lot_label else None
                    st.caption(
                        "Recovery: "
                        + (f"{float(recovery_val):.1f}%" if recovery_val is not None else "—")
                        + "  |  Cost/kg: "
                        + (f"₹{float(lot_cost_val):,.2f}" if lot_cost_val is not None else "—")
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

            estimate_lines = list(saved_charges) + list(display_charges)
            estimate = (
                db.estimate_batch_input_cost(
                    estimate_lines,
                    batch_id=preview_id if existing_batch else None,
                )
                if estimate_lines
                else None
            )
            if estimate and estimate["lines"]:
                st.markdown("##### Estimated cost")
                est_per_kg = estimate["estimated_cost_per_kg"]
                material_per_kg = estimate["estimated_material_per_kg"]
                e1, e2, e3, e4 = st.columns(4)
                e1.metric("Charge cost", f"{estimate['input_cost_total']:,.2f}")
                e2.metric(
                    "Estimated output (kg)", f"{estimate['estimated_output_kg']:,.2f}"
                )
                e3.metric(
                    "Material ₹/kg",
                    f"{material_per_kg:,.2f}" if material_per_kg is not None else "—",
                )
                e4.metric(
                    "Estimated ₹/kg",
                    f"{est_per_kg:,.2f}" if est_per_kg is not None else "—",
                )

                open_po = db.latest_open_po_rate(alloy_id) if alloy_id else None
                po_rate = (
                    float(open_po["Rate"])
                    if open_po and open_po.get("Rate") is not None
                    else None
                )
                if po_rate is not None:
                    cost_target = po_rate / (1 + db.MIN_PROFIT_MARGIN_PCT / 100.0)
                    st.markdown(
                        '<p style="font-size:0.8rem;color:rgba(49,51,63,0.6);'
                        'margin-bottom:0.2rem">Cost Target (₹/kg)</p>',
                        unsafe_allow_html=True,
                    )
                    color = (
                        "inherit"
                        if est_per_kg is None
                        else ("#2e7d32" if est_per_kg <= cost_target else "#c62828")
                    )
                    st.markdown(
                        f'<p style="font-size:1.5rem;font-weight:600;'
                        f'color:{color};margin:0">{cost_target:,.2f}</p>',
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        f"Cost Target = Open PO rate ÷ (1 + {db.MIN_PROFIT_MARGIN_PCT:.0f}%) "
                        f"— the ₹/kg needed to hit a minimum "
                        f"{db.MIN_PROFIT_MARGIN_PCT:.0f}% profit margin on cost, based "
                        f"on the latest Open PO for this alloy "
                        f"({open_po.get('Customer_PO_No') or '—'}, "
                        f"{format_ui_date(open_po.get('Order_Date')) or '—'}). Green "
                        "when Estimated ₹/kg is at or under target, red when over."
                    )
                elif alloy_id:
                    st.caption(
                        "No Open purchase order for this alloy — no cost target to "
                        "compare the estimated cost against."
                    )

                conv_month = estimate["conversion_expense_month"]
                st.caption(
                    "Charge cost ÷ estimated output, plus conversion "
                    f"**{estimate['conversion_rate_applied']:,.2f} ₹/kg**"
                    + (f" ({format_ui_date(conv_month)})" if conv_month else "")
                    + ". Estimated output is each line's weight × the recovery on the "
                    "newest **Raw Material Master** row for that material, less the "
                    "expected output of any material returned as **Scrap**."
                )
                if estimate["scrap_returned_kg"] > 0:
                    st.caption(
                        f"Scrap returns on this heat: **{estimate['scrap_returned_kg']:,.2f} kg** "
                        f"charged, cutting estimated output by "
                        f"**{estimate['scrap_output_reduction_kg']:,.2f} kg**. Charge cost is "
                        "unaffected — the heat still carries that cost."
                    )
                if estimate["missing_recovery"]:
                    st.warning(
                        "No recovery on Raw Material Master for: "
                        + ", ".join(estimate["missing_recovery"])
                        + " — these contribute no estimated output."
                    )
                if estimate["missing_cost"]:
                    st.warning(
                        "No cost per kg on the lot for: "
                        + ", ".join(estimate["missing_cost"])
                        + " — these contribute no cost."
                    )
                with st.expander("Estimate breakdown by charge line"):
                    show_dataframe(
                        df_from_rows(
                            [
                                {
                                    "Raw material": d["Raw_Material_Name"],
                                    "Lot": d["Lot_id"],
                                    "Weight (kg)": d["Weight"],
                                    "Cost/kg": d["Cost_per_kg"],
                                    "Cost from": d["Cost_source"],
                                    "Line cost": d["Line_cost"],
                                    "Recovery %": d["Recovery_pct"],
                                    "Master effective": d["Effective_date"],
                                    "Est. output (kg)": d["Estimated_output_kg"],
                                }
                                for d in estimate["lines"]
                            ]
                        )
                    )
                    cost_caption = "Cost/kg is the charged lot's cost."
                    if estimate["scrap_returned_kg"] > 0:
                        cost_caption += (
                            " Est. output (kg) per line does **not** reflect Scrap "
                            "returns — the "
                            f"**{estimate['scrap_output_reduction_kg']:,.2f} kg** "
                            "scrap deduction is only applied to the total above."
                        )
                    st.caption(cost_caption)

        _render_charge_and_estimate()

        # The fragment above writes its charge-line drafts to session_state
        # under this key on every run (fragment-scoped or full); a full
        # rerun always re-executes the fragment before reaching here, so
        # this is always current for what follows.
        pending_charges_key = _pk("pending_charges")
        pending_charges = st.session_state.get(pending_charges_key) or []

        if existing_batch:
            try:
                returnable = db.list_batch_input_returnable(preview_id)
            except Exception as exc:
                returnable = []
                st.warning(f"Could not read returnable charge material: {exc}")
            open_lines = [r for r in returnable if r["Returnable_weight"] > 0]

            if open_lines and not locked:
                st.markdown("##### Return charge material")
                st.caption(
                    "Send part of a saved charge line back to inventory, or write it "
                    "off as scrap. **Back to inventory** reduces this heat's input "
                    "weight and puts the metal back on the lot. **Scrap** leaves the "
                    "input weight alone, so this heat still carries its cost."
                )
                return_labels = {
                    (
                        f"{r['Raw_Material_Name']} — Lot {r['Lot_id']} — "
                        f"{r['Returnable_weight']:,.2f} kg on charge"
                    ): r
                    for r in open_lines
                }
                rc1, rc2 = st.columns([2.6, 1.4])
                with rc1:
                    return_pick = st.selectbox(
                        "Charged material to return",
                        options=list(return_labels.keys()),
                        key=_pk("ret_line"),
                        help="Only material saved as a charge line on this heat.",
                    )
                chosen_return = return_labels[return_pick]
                max_return = float(chosen_return["Returnable_weight"])
                with rc2:
                    return_weight = empty_percent_input(
                        "Return weight (kg) *",
                        key=_pk("ret_weight"),
                        max_value=None,
                        step=0.1,
                        help=f"Up to {max_return:,.2f} kg is still on this charge line.",
                    )
                rc3, rc4 = st.columns([1.6, 2.4])
                with rc3:
                    return_kind = st.radio(
                        "Return to",
                        options=db.BATCH_INPUT_RETURN_TYPES,
                        format_func=lambda v: (
                            "Back to inventory" if v == "Inventory" else "Scrap"
                        ),
                        horizontal=True,
                        key=_pk("ret_kind"),
                    )
                with rc4:
                    return_notes = st.text_input("Return notes", key=_pk("ret_notes"))

                if _button_clicked(
                    st.button(
                        "Record return", type="primary", key=_pk("ret_save")
                    ),
                    _pk("ret_save"),
                ):
                    try:
                        db.save_batch_input_return(
                            preview_id,
                            chosen_return["Raw_Material_Name"],
                            int(chosen_return["Lot_id"]),
                            float(return_weight or 0),
                            return_kind,
                            return_notes,
                            allow_completed=bool(is_completed) and not locked,
                        )
                        where = (
                            "back to inventory"
                            if return_kind == "Inventory"
                            else "as scrap"
                        )
                        st.success(
                            f"Returned **{float(return_weight or 0):,.2f} kg** of "
                            f"**{chosen_return['Raw_Material_Name']}** "
                            f"(lot {chosen_return['Lot_id']}) {where}."
                        )
                        st.session_state.pop(_pk("ret_weight"), None)
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            elif open_lines and locked:
                st.caption(
                    "Charge material cannot be returned on a Completed heat."
                )

            try:
                past_returns = db.list_batch_input_returns(preview_id)
            except Exception:
                past_returns = []
            if past_returns:
                returned_kg = sum(
                    float(r["Weight"] or 0)
                    for r in past_returns
                    if r["Return_type"] == "Inventory"
                )
                scrapped_kg = sum(
                    float(r["Weight"] or 0)
                    for r in past_returns
                    if r["Return_type"] == "Scrap"
                )
                with st.expander(
                    f"Returned to inventory {returned_kg:,.2f} kg · "
                    f"scrapped {scrapped_kg:,.2f} kg"
                ):
                    show_dataframe(df_from_rows(past_returns))
                    st.caption(
                        "Inventory returns are already deducted from the input "
                        "weight above. Scrap is not."
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
            elif not pending_charges:
                st.caption("Add at least one charge line with net weight > 0.")
            if create_clicked:
                try:
                    if available_crucible is None:
                        raise ValueError(
                            "No crucible available for the respective furnace."
                        )
                    inputs_to_save = list(pending_charges)
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
        entry_elements = _pb_ref["chem_elements"]
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
            charge_line_count=len(saved_charges) + len(pending_charges),
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
                extra_inputs=pending_charges,
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
                "Mark the heat **Completed**, save product-alloy output on "
                "**Batch Output**, then click **Mark Output as Completed**."
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
