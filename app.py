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
    parse_any_date,
    show_dataframe,
    _render_dashboard_refresh_bar,
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
    "Packing List": st.Page(
        "app_pages/packing_list.py",
        title="Packing List",
        url_path="packing-list",
    ),
    "Test Certificate": st.Page(
        "app_pages/test_certificate.py",
        title="Test Certificate",
        url_path="test-certificate",
    ),
    "Production Batch & Chemistry": st.Page(
        "app_pages/production_batch.py",
        title="Production Batch & Chemistry",
        url_path="production-batch-chemistry",
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


