"""
Database layer for Nualco Aluminum Alloy Manufacturing Tracker.

Runs on Neon Postgres when DATABASE_URL is available (from the environment or
.env.local), otherwise falls back to the local SQLite file. Native Postgres
on port 5432 is preferred; if that path is blocked, queries go over Neon's
HTTPS SQL endpoint instead. All SQL is written in the portable subset both
dialects support:

- placeholders use `?` and are translated to `%s` for Postgres
- upserts use `ON CONFLICT` (supported by both Postgres and SQLite 3.24+)
- generated ids are read with `RETURNING` (Postgres and SQLite 3.35+)
- selected columns carry quoted aliases so result keys keep their exact
  case on Postgres, which folds unquoted identifiers to lowercase
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Optional, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import Connection, CursorResult, MetaData, Table, create_engine, select
from sqlalchemy.exc import OperationalError

DB_PATH = Path(__file__).resolve().parent / "nualco.db"
ENV_FILE = Path(__file__).resolve().parent / ".env.local"


def _force_sqlite() -> bool:
    return os.environ.get("NUALCO_FORCE_SQLITE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _prepare_postgres_url(url: str) -> str:
    """Strip options that hang Windows libpq, and disable GSS encryption."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("channel_binding", None)
    query["sslmode"] = query.get("sslmode") or "require"
    query["gssencmode"] = "disable"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


def _database_url() -> str | None:
    if _force_sqlite():
        return None

    def _clean(value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).strip().strip('"').strip("'")
        return text or None

    unpooled: list[str] = []
    pooled: list[str] = []

    def _consider(value: str | None) -> None:
        url = _clean(value)
        if not url:
            return
        if "-pooler" in url:
            pooled.append(url)
        else:
            unpooled.append(url)

    _consider(os.environ.get("DATABASE_URL_UNPOOLED"))
    _consider(os.environ.get("DATABASE_URL"))

    # Streamlit Cloud / local .streamlit/secrets.toml
    # Only touch Streamlit if it is already loaded; importing it outside
    # `streamlit run` can hang the interpreter.
    if "streamlit" in sys.modules:
        try:
            import streamlit as st  # type: ignore

            secrets = st.secrets
            for key in (
                "DATABASE_URL_UNPOOLED",
                "database_url_unpooled",
                "DATABASE_URL",
                "database_url",
            ):
                if key in secrets:
                    _consider(secrets[key])
            for section in ("postgres", "neon", "db"):
                if section not in secrets:
                    continue
                block = secrets[section]
                for key in (
                    "DATABASE_URL_UNPOOLED",
                    "database_url_unpooled",
                    "DATABASE_URL",
                    "database_url",
                    "url",
                    "uri",
                ):
                    try:
                        _consider(block[key])
                    except Exception:
                        pass
        except Exception:
            pass

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() in {
                "DATABASE_URL_UNPOOLED",
                "DATABASE_URL",
            }:
                _consider(raw)

    # Streamlit is a long-running process: prefer the direct Neon endpoint.
    # The pooler URL often fails after idle with "server closed unexpectedly".
    chosen = unpooled[0] if unpooled else (pooled[0] if pooled else None)
    return _prepare_postgres_url(chosen) if chosen else None


def _postgres_ssl_ready(url: str, timeout: float = 4.0) -> bool:
    """True only if Neon answers the Postgres SSLRequest. TCP-open is not enough."""
    host = urlparse(url).hostname
    if not host:
        return False
    sock = None
    try:
        sock = socket.create_connection((host, 5432), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout)
        sock.sendall(struct.pack("!ii", 8, 80877103))
        return sock.recv(1) == b"S"
    except OSError:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


_URL = _database_url()
_USE_NEON_HTTP = False
if _URL and _postgres_ssl_ready(_URL):
    # Neon compute can scale to zero. Recycle before the typical 5-minute
    # suspend, ping before checkout, and disable GSS (Windows libpq can hang
    # for minutes on gssencmode=prefer).
    ENGINE = create_engine(
        _URL,
        pool_pre_ping=True,
        pool_recycle=280,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": 8,
            "sslmode": "require",
            "gssencmode": "disable",
        },
    )
    DB_LABEL = "Neon Postgres"
elif _URL:
    # Port 5432 often times out on this Windows network (TCP open, SSL never
    # completes). Neon SQL-over-HTTPS on 443 still works.
    from neon_http import HttpEngine

    _USE_NEON_HTTP = True
    ENGINE = HttpEngine(_URL)
    DB_LABEL = "Neon Postgres"
else:
    # check_same_thread=False because Streamlit reruns scripts on worker
    # threads, so connections cross thread boundaries.
    ENGINE = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    DB_LABEL = DB_PATH.name

IS_POSTGRES = ENGINE.dialect.name == "postgresql"

_T = TypeVar("_T")
_CONNECT_RETRIES = 1
_CONNECT_BACKOFF_S = 1.5


def switch_to_sqlite() -> None:
    """Drop the Neon engine and keep using a local SQLite file."""
    global ENGINE, DB_LABEL, IS_POSTGRES, _URL, _USE_NEON_HTTP
    try:
        ENGINE.dispose()
    except Exception:
        pass
    _URL = None
    _USE_NEON_HTTP = False
    ENGINE = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
    )
    DB_LABEL = DB_PATH.name
    IS_POSTGRES = False


def _is_transient_db_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "server closed the connection",
            "server terminated abnormally",
            "ssl connection has been closed",
            "ssl syscall",
            "connection reset",
            "could not connect",
            "timeout expired",
            "the connection is closed",
            "consuming input failed",
        )
    )


def _retry_on_disconnect(op: Callable[[], _T]) -> _T:
    """Retry a connect/query after Neon scale-to-zero or a dropped pooler socket."""
    delay = _CONNECT_BACKOFF_S
    last: OperationalError | None = None
    for attempt in range(_CONNECT_RETRIES):
        try:
            return op()
        except OperationalError as exc:
            last = exc
            if not IS_POSTGRES or not _is_transient_db_error(exc):
                raise
            if attempt == _CONNECT_RETRIES - 1:
                raise
            try:
                ENGINE.dispose()
            except Exception:
                pass
            time.sleep(delay)
            delay = min(delay * 2, 8)
    assert last is not None
    raise last


def _q(sql: str) -> str:
    """Translate `?` placeholders to the driver's paramstyle."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def _exec(conn: Connection, sql: str, params: Any = None) -> CursorResult:
    q = _q(sql)
    if params is None:
        return conn.exec_driver_sql(q)
    return conn.exec_driver_sql(q, params)


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Open a connection wrapped in a transaction (commit/rollback on exit)."""
    delay = _CONNECT_BACKOFF_S
    for attempt in range(_CONNECT_RETRIES):
        cm = None
        try:
            cm = ENGINE.begin()
            conn = cm.__enter__()
        except OperationalError as exc:
            if cm is not None:
                try:
                    cm.__exit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass
            if (
                not IS_POSTGRES
                or not _is_transient_db_error(exc)
                or attempt == _CONNECT_RETRIES - 1
            ):
                raise
            try:
                ENGINE.dispose()
            except Exception:
                pass
            time.sleep(delay)
            delay = min(delay * 2, 8)
            continue
        try:
            yield conn
        except BaseException:
            cm.__exit__(*sys.exc_info())
            raise
        else:
            cm.__exit__(None, None, None)
        return


ELEMENTS: list[tuple[int, str, str]] = [
    (1, "Silicon", "Si"),
    (2, "Iron", "Fe"),
    (3, "Copper", "Cu"),
    (4, "Manganese", "Mn"),
    (5, "Magnesium", "Mg"),
    (6, "Chromium", "Cr"),
    (7, "Nickel", "Ni"),
    (8, "Zinc", "Zn"),
    (9, "Lead", "Pb"),
    (10, "Tin", "Sn"),
    (11, "Titanium", "Ti"),
    (12, "Strontium", "Sr"),
    (13, "Calcium", "Ca"),
    (14, "Phosporous", "P"),
    (15, "Berilium", "Be"),
    (16, "Cadmium", "Cd"),
    (17, "Sodium", "Na"),
    (18, "Silver", "Ag"),
    (19, "Arsenic", "As"),
    (20, "Boron", "B"),
    (21, "Barium", "Ba"),
    (22, "Bismuth", "Bi"),
    (23, "Cerium", "Ce"),
    (24, "Cobalt", "Co"),
    (25, "Gallium", "Ga"),
    (26, "Mercury", "Hg"),
    (27, "Indium", "In"),
    (28, "Lanthanum", "La"),
    (29, "Lithium", "Li"),
    (30, "Molybdenum", "Mo"),
    (31, "Antimony", "Sb"),
    (32, "Scandium", "Sc"),
    (33, "Vanadium", "V"),
    (34, "Zirconium", "Zr"),
    (35, "Potassium", "K"),
    (36, "Aluminium", "Al"),
    (37, "Other Elements Each", "OE"),
    (38, "Other Elements Total", "OT"),
    (39, "Sludge Factor", "SF"),
]

CORE_ELEMENTS = ["Si", "Fe", "Cu", "Mn", "Mg", "Al"]

WORKFLOW_STAGES = [
    "Raw Material",
    "Melting/Furnace",
    "Casting",
    "Quality Inspection",
    "Finished Goods",
]

BATCH_STATUS_IN_PROGRESS = "In-Progress"
BATCH_STATUS_COMPLETED = "Completed"
BATCH_PRODUCTION_STATUS = [BATCH_STATUS_IN_PROGRESS, BATCH_STATUS_COMPLETED]
BATCH_QA_STATUS = BATCH_PRODUCTION_STATUS
K_MOLD_MAX = 0.5
FG_STATUS_UNDER_TESTING = "Under_Testing"
FG_STATUS_AVAILABLE = "Available"
FG_STATUS_ASSIGNED = "Assigned"
FG_STATUS_DISPATCHED = "Dispatched"
FG_STATUS_REJECTED = "Rejected"
FG_STATUSES = [
    FG_STATUS_UNDER_TESTING,
    FG_STATUS_AVAILABLE,
    FG_STATUS_ASSIGNED,
    FG_STATUS_DISPATCHED,
    FG_STATUS_REJECTED,
]
# Assigned is leftover from the old assign step; packing list can still dispatch it.
FG_DISPATCHABLE_STATUSES = {FG_STATUS_AVAILABLE, FG_STATUS_ASSIGNED}
INVENTORY_STATUS = ["Awaiting Assay", "Ready For Melt", "Not Ready for Melt"]
ACTIVE_STATUS = ["Active", "Inactive"]
CRUCIBLE_STATUS = ["Available", "Damaged"]
PURCHASE_ORDER_STATUS = ["Open", "Closed", "Cancelled"]
SAMPLE_OK_STATUS = ["OK", "NOT OK"]
PACKING_STATUS_IN_PROGRESS = "In-Progress"
PACKING_STATUS_VERIFIED = "Verified"
PACKING_LIST_STATUS = [PACKING_STATUS_IN_PROGRESS, PACKING_STATUS_VERIFIED]
# Non-spec metal taken off a heat: samples, furnace heel, and off-chemistry ingot.
# These IDs live in Alloy_Master but have no Alloy_Master_spec rows.
SIDESTREAM_ALLOY_IDS = (78, 79, 80)
SIDESTREAM_RM_EFFECTIVE_DATE = "2026-08-01"
SIDESTREAM_RM_RECOVERY = 99.0
SIDESTREAM_RM_NAMES = {
    78: "Broken Ingot",
    79: "Furnace Empty",
    80: "Not Ok Ingot",
}
RAW_MATERIAL_AVAILABILITY = ["Standard", "Spot", "Contract", "Internal"]
SHIFTS = ["A", "B"]
MELT_NOS = [1, 2, 3, 4, 5, 6, 7, 9]
HEAT_NOS = list(range(1, 13))
YIELD_TARGET_PCT = 70.0
# Typical finished-alloy ingot piece weight. Used to flag a likely
# wrong piece count when supervisors enter batch output.
ALLOY_PIECE_KG_MIN = 5.6
ALLOY_PIECE_KG_MAX = 6.1

ELECTRICITY_LINE_1 = "EB Line 1"
ELECTRICITY_LINE_2 = "EB Line 2"
ELECTRICITY_LINES = (ELECTRICITY_LINE_1, ELECTRICITY_LINE_2)

# Tables that track who/when last changed a row.
AUDIT_TABLES = {
    "alloy_master",
    "alloy_master_spec",
    "customer_master",
    "element_master",
    "melter_master",
    "state_city_master",
    "trolley_master",
    "vendor_master",
    "raw_material_master",
    "raw_material_spec",
    "raw_material_purchase",
    "packing_list",
}
_ACTING_USER: str = "system"


def set_acting_user(name: str | None) -> None:
    """Set the user stamp used for Last_updated_by (from the Streamlit sidebar)."""
    global _ACTING_USER
    text = (name or "").strip()
    _ACTING_USER = text or "system"


def get_acting_user() -> str:
    return _ACTING_USER or "system"


def audit_stamp() -> tuple[str, str]:
    """Return (Last_updated_by, Last_updated_datetime) for the current actor."""
    return get_acting_user(), datetime.now().isoformat(timespec="seconds")


# ---------- Schema ----------

_SCHEMA_SHARED = """
CREATE TABLE IF NOT EXISTS Customer_Master (
    Cust_code TEXT PRIMARY KEY,
    Customer_name TEXT NOT NULL UNIQUE,
    GST TEXT, PAN TEXT, Address TEXT, City TEXT,
    State TEXT, Pincode TEXT, Country TEXT,
    Contact1_name TEXT, Phone1 TEXT, Contact_name2 TEXT, Phone2 TEXT,
    Email TEXT, Website TEXT,
    Bank_account TEXT, IFSC_code TEXT, Bank_name TEXT, Branch_category TEXT,
    Created_date TEXT DEFAULT {now},
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Vendor_Master (
    Vendor_code {autopk},
    Vendor_name TEXT NOT NULL UNIQUE,
    GST TEXT, PAN TEXT, Address TEXT, City TEXT,
    State TEXT, Pincode TEXT, Country TEXT,
    Contact1 TEXT, Phone1 TEXT, Contact2 TEXT, Phone2 TEXT,
    Email TEXT, Website TEXT,
    Credit_period INTEGER,
    Bank_account TEXT, Branch TEXT, IFSC_code TEXT, Bank_name TEXT,
    Creation_date TEXT DEFAULT {now},
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Element_Master (
    Serial_no INTEGER PRIMARY KEY,
    Element_Name TEXT NOT NULL,
    Element_Symbol TEXT NOT NULL UNIQUE,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS ISRI_CODE_TABLE (
    ISRI_CODE TEXT PRIMARY KEY,
    Description TEXT,
    Comments TEXT
);
CREATE TABLE IF NOT EXISTS Raw_Material_Master (
    Raw_Material_Name TEXT NOT NULL,
    Effective_date TEXT NOT NULL,
    Vendor_code INTEGER REFERENCES Vendor_Master(Vendor_code),
    ISRI_CODE TEXT REFERENCES ISRI_CODE_TABLE(ISRI_CODE),
    Availability_class TEXT,
    Recovery {float},
    Photo {blob},
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Cost_per_kg {float} DEFAULT 0,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Raw_Material_Name, Effective_date)
);
CREATE TABLE IF NOT EXISTS Raw_Material_Purchase (
    Purchase_id {autopk},
    Vendor_code INTEGER REFERENCES Vendor_Master(Vendor_code),
    Supplier_Invoice TEXT,
    Supplier_invoice_date TEXT,
    Received_date TEXT,
    Invoice_Document {blob},
    Invoice_Document_name TEXT,
    Invoice_Document_type TEXT,
    Vehicle_photo {blob},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Raw_Material_Inventory (
    Lot_id {autopk},
    Purchase_id INTEGER REFERENCES Raw_Material_Purchase(Purchase_id),
    Raw_Material_Name TEXT NOT NULL,
    Received_weight {float},
    Remaining_Weight {float},
    Storage_bay TEXT,
    Raw_Material_Status TEXT DEFAULT 'Awaiting Assay',
    Photo {blob},
    Cost_per_kg {float},
    Source_Batch_ID TEXT REFERENCES Production_batch(Batch_ID),
    Source_Alloy_id INTEGER REFERENCES Alloy_Master(Alloy_id)
);
CREATE TABLE IF NOT EXISTS Raw_Material_Spec (
    Raw_Material_Name TEXT NOT NULL,
    Effective_date TEXT NOT NULL,
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Percentage {pct4},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Raw_Material_Name, Effective_date, Element_symbol),
    FOREIGN KEY (Raw_Material_Name, Effective_date)
        REFERENCES Raw_Material_Master (Raw_Material_Name, Effective_date)
);
CREATE TABLE IF NOT EXISTS Alloy_Master (
    Alloy_id {autopk},
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Alloy_name TEXT NOT NULL,
    Alloy_Family TEXT,
    Alloy_group TEXT,
    Created_by TEXT,
    Created_at TEXT DEFAULT {now},
    Colour_code TEXT,
    Bis_Designation TEXT,
    Revision_datetime TEXT,
    Remarks TEXT,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Alloy_Master_spec (
    Alloy_id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Min_percent {pct4},
    Max_percent {pct4},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Alloy_id, Element_symbol)
);
CREATE TABLE IF NOT EXISTS Furnace_Master (
    Furnace TEXT PRIMARY KEY,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
);
CREATE TABLE IF NOT EXISTS Crucible_Master (
    Crucible_no TEXT PRIMARY KEY,
    furnace TEXT REFERENCES Furnace_Master(Furnace),
    Crucible_status TEXT CHECK(Crucible_status IN ('Available', 'Damaged')) DEFAULT 'Available',
    Vendor_name TEXT REFERENCES Vendor_Master(Vendor_name)
);
CREATE TABLE IF NOT EXISTS Melter_Master (
    Melter_Name TEXT PRIMARY KEY,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Trolley_Master (
    Trolley_name TEXT PRIMARY KEY,
    Colour TEXT,
    Weight {float},
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS State_City_Master (
    State TEXT NOT NULL,
    City TEXT NOT NULL,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (State, City)
);
CREATE TABLE IF NOT EXISTS Month_code (
    Month TEXT PRIMARY KEY,
    Code TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS Access_matrix (
    ID TEXT PRIMARY KEY,
    Name TEXT NOT NULL,
    Access TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS Production_supervisor (
    Production_supervisor TEXT PRIMARY KEY,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
);
CREATE TABLE IF NOT EXISTS Production_batch (
    Batch_ID TEXT PRIMARY KEY,
    Alloy_id INTEGER REFERENCES Alloy_Master(Alloy_id),
    Production_Date TEXT,
    Shift TEXT CHECK(Shift IN ('A', 'B')),
    Furnace TEXT REFERENCES Furnace_Master(Furnace),
    Crucible_no TEXT REFERENCES Crucible_Master(Crucible_no),
    Melt_No INTEGER,
    Heat_no TEXT,
    Melting_team TEXT,
    Notes TEXT,
    Photo1 {blob}, Photo2 {blob}, Photo3 {blob},
    Production_status TEXT NOT NULL DEFAULT 'In-Progress'
        CHECK(Production_status IN ('In-Progress', 'Completed')),
    Workflow_stage TEXT DEFAULT 'Raw Material',
    Degassing_time TEXT,
    Sampled_pcs {float},
    Defect_pcs {float},
    Top_Sample TEXT CHECK(Top_Sample IS NULL OR Top_Sample IN ('OK', 'NOT OK')),
    Middle_Sample TEXT CHECK(Middle_Sample IS NULL OR Middle_Sample IN ('OK', 'NOT OK')),
    Bottom_Sample TEXT CHECK(Bottom_Sample IS NULL OR Bottom_Sample IN ('OK', 'NOT OK')),
    Vacum_Sample TEXT CHECK(Vacum_Sample IS NULL OR Vacum_Sample IN ('OK', 'NOT OK')),
    Top_Sample_Remarks TEXT,
    Middle_Sample_Remarks TEXT,
    Bottom_Sample_Remarks TEXT,
    Top_Sample_datetime TEXT,
    Middle_Sample_datetime TEXT,
    Bottom_Sample_datetime TEXT,
    Production_supervisor TEXT REFERENCES Production_supervisor(Production_supervisor)
);
CREATE TABLE IF NOT EXISTS Finished_Goods_Inventory (
    Bundle_id {autopk},
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    Output_Weight {float},
    Output_pieces INTEGER,
    Finished_Goods_Status TEXT NOT NULL DEFAULT 'Under_Testing'
        CHECK(Finished_Goods_Status IN (
            'Under_Testing', 'Available', 'Assigned', 'Dispatched', 'Rejected'
        ))
);
CREATE TABLE IF NOT EXISTS Packing_list (
    Packing_list_id {autopk},
    Invoice_date TEXT,
    Invoice_number TEXT NOT NULL,
    Customer_PO_No TEXT NOT NULL,
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Customer_name TEXT,
    Alloy_id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
    Colour_code TEXT,
    Vehicle_no TEXT,
    Packing_list_status TEXT NOT NULL DEFAULT 'In-Progress'
        CHECK (Packing_list_status IN ('In-Progress', 'Verified')),
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Packing_list_batch (
    Packing_list_id INTEGER NOT NULL REFERENCES Packing_list(Packing_list_id),
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    PRIMARY KEY (Packing_list_id, Batch_ID)
);
CREATE TABLE IF NOT EXISTS batch_input (
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    Raw_Material_Name TEXT NOT NULL,
    Lot_id INTEGER NOT NULL REFERENCES Raw_Material_Inventory(Lot_id),
    Weight {float} NOT NULL,
    Weighment_scale_weight {float},
    Trolley_weight {float},
    Trolley_name TEXT REFERENCES Trolley_Master(Trolley_name),
    Charge_time TEXT DEFAULT {now},
    Notes TEXT,
    Weighment_scale_photo {blob}, Input_photo {blob},
    PRIMARY KEY (Batch_ID, Raw_Material_Name, Lot_id, Charge_time)
);
CREATE TABLE IF NOT EXISTS batch_output (
    Output_id {autopk},
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    Alloy_id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
    Weight {float} NOT NULL,
    Weighment_scale_weight {float},
    Stand_weight {float},
    Pieces INTEGER,
    Notes TEXT,
    Output_time TEXT DEFAULT {now},
    Weighment_scale_photo {blob},
    Output_photo {blob},
    cost_of_production_per_kg {pct4},
    cost_of_production_overall_per_kg {pct4},
    conversion_rate_applied {pct4},
    conversion_expense_month DATE
);
CREATE TABLE IF NOT EXISTS Batch_Chemical_Composition (
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Percentage {pct4},
    PRIMARY KEY (Batch_ID, Element_symbol)
);
CREATE TABLE IF NOT EXISTS Build_of_Material (
    BOMID {float} NOT NULL,
    Effective_date TEXT NOT NULL,
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Alloy_Name TEXT,
    Raw_Material_Name TEXT,
    Quantity {float},
    Sequence_Order {float},
    notes TEXT,
    PRIMARY KEY (BOMID, Effective_date)
);
CREATE TABLE IF NOT EXISTS Purchase_Order (
    Customer_PO_No TEXT NOT NULL,
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Customer_name TEXT REFERENCES Customer_Master(Customer_name),
    Alloy_Id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
    Order_Date TEXT,
    Delivery_Date TEXT,
    Order_Qty {float},
    Rate {float},
    Billing_Address TEXT,
    Billing_City TEXT,
    Billing_state TEXT,
    Billing_Pincode TEXT,
    Billing_country TEXT,
    Shipping_address TEXT,
    Shipping_City TEXT,
    Shipping_state TEXT,
    Shipping_Pincode TEXT,
    Shipping_country TEXT,
    PO_Document {blob},
    PO_Document_name TEXT,
    PO_Document_type TEXT,
    Purchase_Order_Status TEXT DEFAULT 'Open'
        CHECK(Purchase_Order_Status IN ('Open', 'Closed', 'Cancelled')),
    PRIMARY KEY (Customer_PO_No, Alloy_Id)
);
CREATE TABLE IF NOT EXISTS Furnace_Oil_Purchase (
    Purchase_id {autopk},
    Vendor_code INTEGER REFERENCES Vendor_Master(Vendor_code),
    Supplier_Invoice TEXT,
    Supplier_invoice_date TEXT,
    Received_date TEXT NOT NULL,
    Quantity {float} NOT NULL,
    Weight_in_kgs {float},
    Rate_per_litre {float},
    Storage_tank TEXT,
    Purchase_type TEXT DEFAULT 'Purchase'
        CHECK(Purchase_type IN ('Purchase', 'Opening')),
    Invoice_Document {blob},
    Invoice_Document_name TEXT,
    Invoice_Document_type TEXT,
    Weighment_slip {blob},
    Weighment_slip_name TEXT,
    Weighment_slip_type TEXT,
    Notes TEXT,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Furnace_Oil_Consumption (
    Consumption_date TEXT PRIMARY KEY,
    Furnace TEXT REFERENCES Furnace_Master(Furnace),
    Shift TEXT CHECK(Shift IS NULL OR Shift IN ('A', 'B')),
    Quantity {float} NOT NULL,
    Batch_ID TEXT,
    Notes TEXT,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Furnace_Oil_Inventory (
    Inventory_date TEXT PRIMARY KEY,
    Opening_qty {float} NOT NULL DEFAULT 0,
    Purchase_qty {float} NOT NULL DEFAULT 0,
    Consumption_qty {float} NOT NULL DEFAULT 0,
    Closing_qty {float} NOT NULL DEFAULT 0,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Electricity_Consumption (
    Consumption_date TEXT NOT NULL,
    Line TEXT NOT NULL,
    Opening_reading {float} NOT NULL,
    Closing_reading {float} NOT NULL,
    Units_consumed {float} NOT NULL,
    Notes TEXT,
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Consumption_date, Line)
);
CREATE TABLE IF NOT EXISTS Cost_of_conversion (
    id {autopk},
    expense_month DATE NOT NULL UNIQUE,
    oil_rate_per_kg {pct4} NOT NULL,
    electricity_rate_per_kg {pct4} NOT NULL,
    labour_rate_per_kg {pct4} NOT NULL,
    salaries_rate_per_kg {pct4} NOT NULL,
    consumables_rate_per_kg {pct4} NOT NULL,
    overheads_rate_per_kg {pct4} NOT NULL,
    total_conversion_rate_per_kg {pct4} NOT NULL
);
CREATE TABLE IF NOT EXISTS Alloy_Data_Checker (
    Customer_Name TEXT,
    alloy_family TEXT,
    Alloy_Name TEXT,
    Bis_Designation TEXT,
    Element_Name TEXT,
    Colour_Code TEXT,
    min_percent {float},
    max_percent {float}
);
"""

_DIALECT_TYPES = {
    True: {  # Postgres
        "float": "DOUBLE PRECISION",
        "pct4": "NUMERIC(10, 4)",
        "blob": "BYTEA",
        "autopk": "INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY",
        "now": "(CURRENT_TIMESTAMP::text)",
    },
    False: {  # SQLite
        "float": "REAL",
        "pct4": "NUMERIC(10, 4)",
        "blob": "BLOB",
        "autopk": "INTEGER PRIMARY KEY AUTOINCREMENT",
        "now": "CURRENT_TIMESTAMP",
    },
}


def init_db() -> None:
    """Create all tables and seed Element_Master / default furnaces."""
    schema = _SCHEMA_SHARED
    for key, value in _DIALECT_TYPES[IS_POSTGRES].items():
        schema = schema.replace("{" + key + "}", value)

    with get_connection() as conn:
        for stmt in schema.split(";"):
            if stmt.strip():
                _exec(conn, stmt)

        _exec(
            conn,
            """
            INSERT INTO Element_Master (Serial_no, Element_Name, Element_Symbol)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            ELEMENTS,
        )

        for n in range(1, 5):
            _exec(
                conn,
                "INSERT INTO Furnace_Master (Furnace, Status) VALUES (?, 'Active') "
                "ON CONFLICT DO NOTHING",
                (str(n),),
            )

        # Add columns missing from older databases (SQLite and Postgres).
        _ensure_columns(
            conn,
            "Production_batch",
            [
                ("Workflow_stage", "TEXT DEFAULT 'Raw Material'"),
                ("Degassing_time", "TEXT"),
                ("Sampled_pcs", "REAL"),
                ("Defect_pcs", "REAL"),
                ("Top_Sample", "TEXT"),
                ("Middle_Sample", "TEXT"),
                ("Bottom_Sample", "TEXT"),
                ("Top_Sample_Remarks", "TEXT"),
                ("Middle_Sample_Remarks", "TEXT"),
                ("Bottom_Sample_Remarks", "TEXT"),
                ("Top_Sample_datetime", "TEXT"),
                ("Middle_Sample_datetime", "TEXT"),
                ("Bottom_Sample_datetime", "TEXT"),
                ("Production_supervisor", "TEXT"),
                ("Vacum_Sample", "TEXT"),
                ("Crucible_no", "TEXT REFERENCES Crucible_Master(Crucible_no)"),
            ],
        )
        _ensure_columns(
            conn,
            "batch_input",
            [
                ("Weighment_scale_weight", "REAL"),
                ("Trolley_weight", "REAL"),
                ("Trolley_name", "TEXT"),
            ],
        )
        _rename_column(conn, "batch_input", "Photo1", "Weighment_scale_photo")
        _rename_column(conn, "batch_input", "Photo2", "Input_photo")
        _ensure_columns(
            conn,
            "batch_input",
            [
                (
                    "Weighment_scale_photo",
                    "BYTEA" if IS_POSTGRES else "BLOB",
                ),
                (
                    "Input_photo",
                    "BYTEA" if IS_POSTGRES else "BLOB",
                ),
            ],
        )
        _ensure_columns(
            conn,
            "Purchase_Order",
            [
                ("PO_Document", "BYTEA" if IS_POSTGRES else "BLOB"),
                ("PO_Document_name", "TEXT"),
                ("PO_Document_type", "TEXT"),
                ("Purchase_Order_Status", "TEXT DEFAULT 'Open'"),
            ],
        )
        _ensure_purchase_order_status(conn)
        _ensure_columns(
            conn,
            "Raw_Material_Inventory",
            [
                ("Cost_per_kg", "DOUBLE PRECISION" if IS_POSTGRES else "REAL"),
            ],
        )
        _drop_columns(
            conn,
            "Raw_Material_Master",
            ["Alloy_family", "Fe", "Cu", "Mg"],
        )
        _audit_cols = [
            ("Last_updated_by", "TEXT"),
            ("Last_updated_datetime", "TEXT"),
        ]
        for table in (
            "Alloy_Master",
            "Alloy_Master_spec",
            "Customer_Master",
            "Element_Master",
            "Melter_Master",
            "State_City_Master",
            "Trolley_Master",
            "Vendor_Master",
            "Raw_Material_Master",
            "Raw_Material_Spec",
            "Raw_Material_Purchase",
        ):
            _ensure_columns(conn, table, _audit_cols)
        if not IS_POSTGRES:
            _ensure_columns(
                conn,
                "Raw_Material_Master",
                [
                    ("Cost_per_kg", "REAL DEFAULT 0"),
                    ("ISRI_CODE", "TEXT REFERENCES ISRI_CODE_TABLE(ISRI_CODE)"),
                ],
            )
            _ensure_columns(
                conn,
                "Raw_Material_Inventory",
                [
                    ("Cost_per_kg", "REAL"),
                ],
            )
            _ensure_columns(
                conn,
                "Alloy_Master",
                [
                    ("Cust_code", "TEXT REFERENCES Customer_Master(Cust_code)"),
                    ("Colour_code", "TEXT"),
                    ("Bis_Designation", "TEXT"),
                    ("Revision_datetime", "TEXT"),
                    ("Remarks", "TEXT"),
                    ("Status", "TEXT DEFAULT 'Active'"),
                    ("Alloy_group", "TEXT"),
                ],
            )
            _drop_columns(
                conn,
                "Alloy_Master",
                [
                    "Sludge_factor",
                    "Other_elements_Each",
                    "Other_elements_Total",
                ],
            )
            _ensure_columns(
                conn,
                "Build_of_Material",
                [("Cust_code", "TEXT REFERENCES Customer_Master(Cust_code)")],
            )

        _ensure_purchase_order_status(conn)
        _ensure_purchase_order_composite_pk(conn)
        _ensure_finished_goods_status(conn)
        _ensure_production_batch_status(conn)
        _ensure_finished_goods_release_trigger(conn)
        _ensure_vacum_sample_check(conn)
        _drop_columns(
            conn,
            "Production_batch",
            ["Output_Weight", "Output_pieces"],
        )
        _ensure_columns(
            conn,
            "batch_output",
            [
                ("Weighment_scale_weight", "REAL"),
                ("Stand_weight", "REAL"),
                (
                    "Weighment_scale_photo",
                    "BYTEA" if IS_POSTGRES else "BLOB",
                ),
                (
                    "Output_photo",
                    "BYTEA" if IS_POSTGRES else "BLOB",
                ),
                ("cost_of_production_per_kg", "NUMERIC(10, 4)"),
                ("cost_of_production_overall_per_kg", "NUMERIC(10, 4)"),
                ("conversion_rate_applied", "NUMERIC(10, 4)"),
                ("conversion_expense_month", "DATE"),
            ],
        )
        _backfill_batch_output_production_costs(conn)
        _ensure_crucible_status(conn)
        _ensure_one_available_crucible(conn)
        _ensure_columns(
            conn,
            "Furnace_Oil_Purchase",
            [
                ("Weight_in_kgs", "DOUBLE PRECISION" if IS_POSTGRES else "REAL"),
                (
                    "Weighment_slip",
                    "BYTEA" if IS_POSTGRES else "BLOB",
                ),
                ("Weighment_slip_name", "TEXT"),
                ("Weighment_slip_type", "TEXT"),
            ],
        )
        _ensure_furnace_oil_consumption_daily(conn)
        _ensure_electricity_consumption_lines(conn)
        _ensure_raw_material_spec_as_master_child(conn)
        _ensure_raw_material_purchase_header(conn)
        _ensure_sidestream_remelt_inventory(conn)
        _ensure_packing_list(conn)
        _ensure_columns(
            conn,
            "Alloy_Master",
            [("Alloy_group", "TEXT")],
        )
        _ensure_case_insensitive_text_pks(conn)
        _ensure_percentage_4dp(conn)
        _ensure_integer_piece_columns(conn)


def _spec_column_names(conn: Connection) -> set[str]:
    if IS_POSTGRES:
        rows = list(
            _exec(
                conn,
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'raw_material_spec'
                """,
            ).mappings()
        )
        return {str(row["column_name"]).lower() for row in rows}
    rows = list(_exec(conn, "PRAGMA table_info(Raw_Material_Spec)").mappings())
    return {str(row["name"]).lower() for row in rows}


def _ensure_raw_material_spec_as_master_child(conn: Connection) -> None:
    """Make Raw_Material_Spec a child of Raw_Material_Master (name + effective_date)."""
    cols = _spec_column_names(conn)
    if not cols:
        return
    if IS_POSTGRES:
        if "lot_id" in cols:
            _exec(
                conn,
                "ALTER TABLE raw_material_spec ADD COLUMN IF NOT EXISTS effective_date TEXT",
            )
            _exec(
                conn,
                """
                UPDATE raw_material_spec s
                SET raw_material_name = m.raw_material_name,
                    effective_date = m.effective_date
                FROM (
                    SELECT DISTINCT ON (LOWER(raw_material_name))
                           raw_material_name, effective_date
                    FROM raw_material_master
                    ORDER BY LOWER(raw_material_name), effective_date DESC
                ) m
                WHERE LOWER(s.raw_material_name) = LOWER(m.raw_material_name)
                """,
            )
            _exec(
                conn,
                """
                DELETE FROM raw_material_spec
                WHERE effective_date IS NULL OR TRIM(effective_date) = ''
                """,
            )
            _exec(
                conn,
                """
                DELETE FROM raw_material_spec a
                USING raw_material_spec b
                WHERE LOWER(a.raw_material_name) = LOWER(b.raw_material_name)
                  AND a.effective_date = b.effective_date
                  AND LOWER(a.element_symbol) = LOWER(b.element_symbol)
                  AND a.ctid <> b.ctid
                  AND (
                        (a.lot_id IS NOT NULL AND b.lot_id IS NULL)
                     OR (
                          (a.lot_id IS NULL) = (b.lot_id IS NULL)
                          AND a.ctid < b.ctid
                        )
                  )
                """,
            )
            _exec(
                conn,
                "ALTER TABLE raw_material_spec DROP CONSTRAINT IF EXISTS raw_material_spec_lot_id_fkey",
            )
            _exec(conn, "DROP INDEX IF EXISTS raw_material_spec_uniq_ci")
            _exec(conn, "ALTER TABLE raw_material_spec DROP COLUMN IF EXISTS lot_id")
        _exec(
            conn,
            "ALTER TABLE raw_material_spec DROP CONSTRAINT IF EXISTS raw_material_spec_pkey",
        )
        _exec(
            conn,
            "ALTER TABLE raw_material_spec ALTER COLUMN effective_date SET NOT NULL",
        )
        exists = _exec(
            conn,
            """
            SELECT 1 AS ok
            FROM pg_constraint
            WHERE conname = 'raw_material_spec_pkey'
            """,
        ).first()
        if not exists:
            _exec(
                conn,
                """
                ALTER TABLE raw_material_spec
                ADD CONSTRAINT raw_material_spec_pkey
                PRIMARY KEY (raw_material_name, effective_date, element_symbol)
                """,
            )
        fk = _exec(
            conn,
            """
            SELECT 1 AS ok
            FROM pg_constraint
            WHERE conname = 'raw_material_spec_master_fkey'
            """,
        ).first()
        if not fk:
            _exec(
                conn,
                """
                ALTER TABLE raw_material_spec
                ADD CONSTRAINT raw_material_spec_master_fkey
                FOREIGN KEY (raw_material_name, effective_date)
                REFERENCES raw_material_master (raw_material_name, effective_date)
                """,
            )
        return

    if "lot_id" in cols or "effective_date" not in cols:
        _exec(
            conn,
            """
            CREATE TABLE Raw_Material_Spec__new (
                Raw_Material_Name TEXT NOT NULL,
                Effective_date TEXT NOT NULL,
                Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
                Percentage NUMERIC(10, 4),
                Last_updated_by TEXT,
                Last_updated_datetime TEXT,
                PRIMARY KEY (Raw_Material_Name, Effective_date, Element_symbol),
                FOREIGN KEY (Raw_Material_Name, Effective_date)
                    REFERENCES Raw_Material_Master (Raw_Material_Name, Effective_date)
            )
            """,
        )
        has_lot = "lot_id" in cols
        lot_pref = "CASE WHEN s.Lot_id IS NULL THEN 0 ELSE 1 END" if has_lot else "0"
        _exec(
            conn,
            f"""
            INSERT INTO Raw_Material_Spec__new
                (Raw_Material_Name, Effective_date, Element_symbol, Percentage,
                 Last_updated_by, Last_updated_datetime)
            SELECT Raw_Material_Name, Effective_date, Element_symbol, Percentage,
                   Last_updated_by, Last_updated_datetime
            FROM (
                SELECT m.Raw_Material_Name AS Raw_Material_Name,
                       m.Effective_date AS Effective_date,
                       s.Element_symbol AS Element_symbol,
                       s.Percentage AS Percentage,
                       s.Last_updated_by AS Last_updated_by,
                       s.Last_updated_datetime AS Last_updated_datetime,
                       ROW_NUMBER() OVER (
                           PARTITION BY LOWER(m.Raw_Material_Name),
                                        m.Effective_date,
                                        LOWER(s.Element_symbol)
                           ORDER BY {lot_pref}
                       ) AS rn
                FROM Raw_Material_Spec s
                JOIN Raw_Material_Master m
                  ON LOWER(m.Raw_Material_Name) = LOWER(s.Raw_Material_Name)
                WHERE m.Effective_date = (
                    SELECT MAX(m2.Effective_date)
                    FROM Raw_Material_Master m2
                    WHERE LOWER(m2.Raw_Material_Name) = LOWER(s.Raw_Material_Name)
                )
            ) ranked
            WHERE rn = 1
            """,
        )
        _exec(conn, "DROP TABLE Raw_Material_Spec")
        _exec(conn, "ALTER TABLE Raw_Material_Spec__new RENAME TO Raw_Material_Spec")


def _table_column_names(conn: Connection, table: str) -> set[str]:
    """Lowercase column names for a table, or empty if it does not exist."""
    if IS_POSTGRES:
        rows = list(
            _exec(
                conn,
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ?
                """,
                (table.lower(),),
            ).mappings()
        )
        return {str(row["column_name"]).lower() for row in rows}
    rows = list(_exec(conn, f"PRAGMA table_info({table})").mappings())
    return {str(row["name"]).lower() for row in rows}


_INVENTORY_INVOICE_COLUMNS = (
    "Vendor_code",
    "Supplier_Invoice",
    "Supplier_invoice_date",
    "Received_date",
    "Vehicle_photo",
    "Invoice_Document",
    "Invoice_Document_name",
    "Invoice_Document_type",
)


def _ensure_raw_material_purchase_header(conn: Connection) -> None:
    """Move invoice documents off inventory lots onto Raw_Material_Purchase."""
    inv_cols = _table_column_names(conn, "Raw_Material_Inventory")
    if not inv_cols:
        return
    if "purchase_id" not in inv_cols:
        fk_type = (
            "INTEGER REFERENCES Raw_Material_Purchase(Purchase_id)"
            if not IS_POSTGRES
            else "INTEGER"
        )
        _exec(conn, f"ALTER TABLE Raw_Material_Inventory ADD COLUMN Purchase_id {fk_type}")
        inv_cols = _table_column_names(conn, "Raw_Material_Inventory")

    has_legacy = any(col.lower() in inv_cols for col in _INVENTORY_INVOICE_COLUMNS)
    if has_legacy:
        select_cols = [
            'Lot_id AS "Lot_id"',
            'Raw_Material_Name AS "Raw_Material_Name"',
        ]
        for col, alias in (
            ("vendor_code", "Vendor_code"),
            ("supplier_invoice", "Supplier_Invoice"),
            ("supplier_invoice_date", "Supplier_invoice_date"),
            ("received_date", "Received_date"),
            ("invoice_document", "Invoice_Document"),
            ("invoice_document_name", "Invoice_Document_name"),
            ("invoice_document_type", "Invoice_Document_type"),
            ("vehicle_photo", "Vehicle_photo"),
        ):
            if col.lower() in inv_cols:
                select_cols.append(f'{alias} AS "{alias}"')
        lots = list(
            _exec(
                conn,
                f"""
                SELECT {", ".join(select_cols)}
                FROM Raw_Material_Inventory
                WHERE Purchase_id IS NULL
                ORDER BY Lot_id
                """,
            ).mappings()
        )
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for lot in lots:
            invoice = str(lot.get("Supplier_Invoice") or "").strip()
            vendor = lot.get("Vendor_code")
            if invoice:
                key: tuple[Any, ...] = ("invoice", vendor, invoice.lower())
            else:
                key = ("lot", lot["Lot_id"])
            groups.setdefault(key, []).append(dict(lot))

        def _first(rows: list[dict[str, Any]], field: str) -> Any:
            for row in rows:
                value = row.get(field)
                if value not in (None, ""):
                    return value
            return None

        by_val, dt_val = audit_stamp()
        for grouped in groups.values():
            result = _exec(
                conn,
                """
                INSERT INTO Raw_Material_Purchase
                    (Vendor_code, Supplier_Invoice, Supplier_invoice_date,
                     Received_date, Invoice_Document, Invoice_Document_name,
                     Invoice_Document_type, Vehicle_photo,
                     Last_updated_by, Last_updated_datetime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING Purchase_id
                """,
                (
                    _first(grouped, "Vendor_code"),
                    _first(grouped, "Supplier_Invoice"),
                    _first(grouped, "Supplier_invoice_date"),
                    _first(grouped, "Received_date"),
                    _first(grouped, "Invoice_Document"),
                    _first(grouped, "Invoice_Document_name"),
                    _first(grouped, "Invoice_Document_type"),
                    _first(grouped, "Vehicle_photo"),
                    by_val,
                    dt_val,
                ),
            )
            purchase_id = int(result.scalar_one())
            lot_ids = [int(row["Lot_id"]) for row in grouped]
            placeholders = ", ".join("?" for _ in lot_ids)
            _exec(
                conn,
                f"""
                UPDATE Raw_Material_Inventory
                SET Purchase_id = ?
                WHERE Lot_id IN ({placeholders})
                """,
                tuple([purchase_id, *lot_ids]),
            )

        _drop_columns(conn, "Raw_Material_Inventory", list(_INVENTORY_INVOICE_COLUMNS))

    if IS_POSTGRES:
        def _try_ddl(sql: str) -> None:
            if _USE_NEON_HTTP:
                try:
                    _exec(conn, sql)
                except Exception:
                    pass
                return
            _exec(conn, "SAVEPOINT rm_purchase_ddl")
            try:
                _exec(conn, sql)
                _exec(conn, "RELEASE SAVEPOINT rm_purchase_ddl")
            except Exception:
                _exec(conn, "ROLLBACK TO SAVEPOINT rm_purchase_ddl")

        _try_ddl(
            "ALTER TABLE raw_material_inventory ALTER COLUMN purchase_id DROP NOT NULL"
        )
        has_fk = None
        try:
            has_fk = _exec(
                conn,
                """
                SELECT 1
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                WHERE c.conrelid = 'raw_material_inventory'::regclass
                  AND c.contype = 'f'
                  AND a.attname = 'purchase_id'
                """,
            ).first()
        except Exception:
            has_fk = True
        if not has_fk:
            _try_ddl(
                """
                ALTER TABLE raw_material_inventory
                ADD CONSTRAINT raw_material_inventory_purchase_fkey
                FOREIGN KEY (purchase_id)
                REFERENCES raw_material_purchase (purchase_id)
                """
            )


def _sidestream_rm_name_on_conn(conn: Connection, alloy_id: int) -> str:
    row = (
        _exec(
            conn,
            """
            SELECT Alloy_name AS "Alloy_name"
            FROM Alloy_Master
            WHERE Alloy_id = ?
            """,
            (int(alloy_id),),
        )
        .mappings()
        .first()
    )
    wanted = str((row or {}).get("Alloy_name") or "").strip()
    if not wanted:
        wanted = SIDESTREAM_RM_NAMES.get(int(alloy_id), f"Alloy {alloy_id}")
    stored = (
        _exec(
            conn,
            """
            SELECT Raw_Material_Name AS "Raw_Material_Name"
            FROM Raw_Material_Master
            WHERE LOWER(Raw_Material_Name) = LOWER(?)
            ORDER BY Effective_date DESC
            LIMIT 1
            """,
            (wanted,),
        )
        .mappings()
        .first()
    )
    if stored and stored.get("Raw_Material_Name"):
        return str(stored["Raw_Material_Name"])
    return wanted


def _ensure_sidestream_remelt_inventory(conn: Connection) -> None:
    """Allow remelt returns on inventory: nullable purchase, source heat, master grades."""
    _ensure_columns(
        conn,
        "Raw_Material_Inventory",
        [
            ("Source_Batch_ID", "TEXT REFERENCES Production_batch(Batch_ID)"),
            ("Source_Alloy_id", "INTEGER REFERENCES Alloy_Master(Alloy_id)"),
        ],
    )
    if IS_POSTGRES:
        try:
            _exec(
                conn,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_rm_inventory_source_batch_alloy
                ON Raw_Material_Inventory (Source_Batch_ID, Source_Alloy_id)
                """,
            )
        except Exception:
            pass
    else:
        _exec(
            conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rm_inventory_source_batch_alloy
            ON Raw_Material_Inventory (Source_Batch_ID, Source_Alloy_id)
            """,
        )
    by_val, dt_val = audit_stamp()
    for alloy_id in SIDESTREAM_ALLOY_IDS:
        name = _sidestream_rm_name_on_conn(conn, alloy_id)
        _exec(
            conn,
            """
            INSERT INTO Raw_Material_Master
                (Raw_Material_Name, Effective_date, Vendor_code, ISRI_CODE,
                 Availability_class, Recovery, Photo, Status, Cost_per_kg,
                 Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, NULL, NULL, 'Internal', ?, NULL, 'Active', 0, ?, ?)
            ON CONFLICT(Raw_Material_Name, Effective_date) DO UPDATE SET
                Availability_class = excluded.Availability_class,
                Recovery = excluded.Recovery,
                Status = excluded.Status,
                Last_updated_by = excluded.Last_updated_by,
                Last_updated_datetime = excluded.Last_updated_datetime
            """,
            (
                name,
                SIDESTREAM_RM_EFFECTIVE_DATE,
                SIDESTREAM_RM_RECOVERY,
                by_val,
                dt_val,
            ),
        )
    batches = list(
        _exec(
            conn,
            """
            SELECT DISTINCT Batch_ID AS "Batch_ID"
            FROM batch_output
            WHERE Alloy_id IN (?, ?, ?)
            """,
            SIDESTREAM_ALLOY_IDS,
        ).mappings()
    )
    for row in batches:
        _sync_sidestream_inventory(conn, row["Batch_ID"])


PERCENT_SCALE = 4
PERCENT_4DP_TABLES = frozenset(
    {"raw_material_spec", "batch_chemical_composition", "alloy_master_spec"}
)
PERCENT_4DP_COLUMNS = frozenset({"percentage", "min_percent", "max_percent"})


def round_percent_4(value: Any) -> Optional[float]:
    """Store chemistry percentages with at most 4 decimal places."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), PERCENT_SCALE)
    except (TypeError, ValueError):
        return None


def _furnace_oil_consumption_pk_columns(conn: Connection) -> list[str]:
    if IS_POSTGRES:
        rows = list(
            _exec(
                conn,
                """
                SELECT a.attname AS name
                FROM pg_index i
                JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                WHERE i.indrelid = 'furnace_oil_consumption'::regclass
                  AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """,
            ).mappings()
        )
        return [str(row["name"]) for row in rows]
    rows = list(_exec(conn, "PRAGMA table_info(Furnace_Oil_Consumption)").mappings())
    return [str(row["name"]) for row in rows if row.get("pk")]


def _ensure_furnace_oil_consumption_daily(conn: Connection) -> None:
    """Key daily consumption on Consumption_date only."""
    try:
        pk_cols = _furnace_oil_consumption_pk_columns(conn)
    except Exception:
        return
    if not pk_cols:
        return
    if pk_cols == ["consumption_date"] or pk_cols == ["Consumption_date"]:
        if IS_POSTGRES:
            _exec(
                conn,
                "ALTER TABLE furnace_oil_consumption ALTER COLUMN furnace DROP NOT NULL",
            )
            _exec(
                conn,
                "ALTER TABLE furnace_oil_consumption ALTER COLUMN shift DROP NOT NULL",
            )
        return
    if IS_POSTGRES:
        _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS furnace_oil_consumption__daily (
                consumption_date TEXT PRIMARY KEY,
                furnace TEXT,
                shift TEXT,
                quantity DOUBLE PRECISION NOT NULL,
                batch_id TEXT,
                notes TEXT,
                last_updated_by TEXT,
                last_updated_datetime TEXT
            )
            """,
        )
        _exec(conn, "DELETE FROM furnace_oil_consumption__daily")
        _exec(
            conn,
            """
            INSERT INTO furnace_oil_consumption__daily
                (consumption_date, quantity, notes, last_updated_by, last_updated_datetime)
            SELECT consumption_date,
                   SUM(quantity),
                   MAX(notes),
                   MAX(last_updated_by),
                   MAX(last_updated_datetime)
            FROM furnace_oil_consumption
            GROUP BY consumption_date
            """,
        )
        _exec(conn, "DROP TABLE furnace_oil_consumption")
        _exec(
            conn,
            "ALTER TABLE furnace_oil_consumption__daily RENAME TO furnace_oil_consumption",
        )
        return
    _exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS Furnace_Oil_Consumption__daily (
            Consumption_date TEXT PRIMARY KEY,
            Furnace TEXT,
            Shift TEXT,
            Quantity REAL NOT NULL,
            Batch_ID TEXT,
            Notes TEXT,
            Last_updated_by TEXT,
            Last_updated_datetime TEXT
        )
        """,
    )
    _exec(conn, "DELETE FROM Furnace_Oil_Consumption__daily")
    _exec(
        conn,
        """
        INSERT INTO Furnace_Oil_Consumption__daily
            (Consumption_date, Quantity, Notes, Last_updated_by, Last_updated_datetime)
        SELECT Consumption_date, SUM(Quantity), MAX(Notes),
               MAX(Last_updated_by), MAX(Last_updated_datetime)
        FROM Furnace_Oil_Consumption
        GROUP BY Consumption_date
        """,
    )
    _exec(conn, "DROP TABLE Furnace_Oil_Consumption")
    _exec(
        conn,
        "ALTER TABLE Furnace_Oil_Consumption__daily RENAME TO Furnace_Oil_Consumption",
    )


def _electricity_consumption_pk_columns(conn: Connection) -> list[str]:
    if IS_POSTGRES:
        rows = list(
            _exec(
                conn,
                """
                SELECT a.attname AS name
                FROM pg_index i
                JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                WHERE i.indrelid = 'electricity_consumption'::regclass
                  AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """,
            ).mappings()
        )
        return [str(row["name"]) for row in rows]
    rows = list(_exec(conn, "PRAGMA table_info(Electricity_Consumption)").mappings())
    return [str(row["name"]) for row in rows if row.get("pk")]


def _ensure_electricity_consumption_lines(conn: Connection) -> None:
    """Key daily readings on Consumption_date + Line (EB Line 1 / EB Line 2)."""
    try:
        pk_cols = _electricity_consumption_pk_columns(conn)
    except Exception:
        return
    if not pk_cols:
        return
    normalized = [str(col).lower() for col in pk_cols]
    if normalized == ["consumption_date", "line"]:
        _exec(conn, "DROP INDEX IF EXISTS electricity_consumption_text_pk_ci")
        return
    line_default = ELECTRICITY_LINE_1
    if IS_POSTGRES:
        _exec(conn, "DROP INDEX IF EXISTS electricity_consumption_text_pk_ci")
        _exec(
            conn,
            """
            CREATE TABLE IF NOT EXISTS electricity_consumption__lines (
                consumption_date TEXT NOT NULL,
                line TEXT NOT NULL,
                opening_reading DOUBLE PRECISION NOT NULL,
                closing_reading DOUBLE PRECISION NOT NULL,
                units_consumed DOUBLE PRECISION NOT NULL,
                notes TEXT,
                last_updated_by TEXT,
                last_updated_datetime TEXT,
                PRIMARY KEY (consumption_date, line)
            )
            """,
        )
        _exec(conn, "DELETE FROM electricity_consumption__lines")
        _exec(
            conn,
            f"""
            INSERT INTO electricity_consumption__lines
                (consumption_date, line, opening_reading, closing_reading,
                 units_consumed, notes, last_updated_by, last_updated_datetime)
            SELECT consumption_date, '{line_default}', opening_reading, closing_reading,
                   units_consumed, notes, last_updated_by, last_updated_datetime
            FROM electricity_consumption
            """,
        )
        _exec(conn, "DROP TABLE electricity_consumption")
        _exec(
            conn,
            "ALTER TABLE electricity_consumption__lines RENAME TO electricity_consumption",
        )
        return
    _exec(conn, "DROP INDEX IF EXISTS electricity_consumption_text_pk_ci")
    _exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS Electricity_Consumption__lines (
            Consumption_date TEXT NOT NULL,
            Line TEXT NOT NULL,
            Opening_reading REAL NOT NULL,
            Closing_reading REAL NOT NULL,
            Units_consumed REAL NOT NULL,
            Notes TEXT,
            Last_updated_by TEXT,
            Last_updated_datetime TEXT,
            PRIMARY KEY (Consumption_date, Line)
        )
        """,
    )
    _exec(conn, "DELETE FROM Electricity_Consumption__lines")
    _exec(
        conn,
        f"""
        INSERT INTO Electricity_Consumption__lines
            (Consumption_date, Line, Opening_reading, Closing_reading,
             Units_consumed, Notes, Last_updated_by, Last_updated_datetime)
        SELECT Consumption_date, '{line_default}', Opening_reading, Closing_reading,
               Units_consumed, Notes, Last_updated_by, Last_updated_datetime
        FROM Electricity_Consumption
        """,
    )
    _exec(conn, "DROP TABLE Electricity_Consumption")
    _exec(
        conn,
        "ALTER TABLE Electricity_Consumption__lines RENAME TO Electricity_Consumption",
    )


def _ensure_percentage_4dp(conn: Connection) -> None:
    """Store chemistry % columns as NUMERIC(10,4)."""
    if not IS_POSTGRES:
        return
    for table, columns in (
        ("raw_material_spec", ("percentage",)),
        ("batch_chemical_composition", ("percentage",)),
        ("alloy_master_spec", ("min_percent", "max_percent")),
    ):
        for column in columns:
            _exec(
                conn,
                f"""
                ALTER TABLE {table}
                ALTER COLUMN {column} TYPE NUMERIC(10, 4)
                USING ROUND({column}::numeric, {PERCENT_SCALE})
                """,
            )


INTEGER_PIECE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("batch_output", "pieces"),
    ("finished_goods_inventory", "output_pieces"),
)


def _as_whole_pieces(value: Any) -> Optional[int]:
    """Accept a positive whole piece count; reject fractional values."""
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    rounded = int(round(num))
    if abs(num - rounded) > 1e-6:
        raise ValueError("Pieces must be a whole number with no decimal places.")
    return rounded


def _ensure_integer_piece_columns(conn: Connection) -> None:
    """Store piece counts as INTEGER (no fractional pieces)."""
    if not IS_POSTGRES:
        return
    for table, column in INTEGER_PIECE_COLUMNS:
        row = _exec(
            conn,
            """
            SELECT data_type AS data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ? AND column_name = ?
            """,
            (table, column),
        ).mappings().first()
        if not row or str(row["data_type"]).lower() == "integer":
            continue
        _exec(
            conn,
            f"""
            ALTER TABLE {table}
            ALTER COLUMN {column} TYPE INTEGER
            USING CASE
                WHEN {column} IS NULL THEN NULL
                ELSE ROUND({column}::numeric)::integer
            END
            """,
        )


def _ensure_purchase_order_status(conn: Connection) -> None:
    """Backfill and enforce Purchase_Order_Status ∈ Open / Closed / Cancelled."""
    _exec(
        conn,
        """
        UPDATE Purchase_Order
        SET Purchase_Order_Status = 'Open'
        WHERE Purchase_Order_Status IS NULL
           OR TRIM(Purchase_Order_Status) = ''
        """,
    )
    if IS_POSTGRES:
        exists = _exec(
            conn,
            """
            SELECT 1 AS ok
            FROM pg_constraint
            WHERE conname = 'purchase_order_status_check'
            """,
        ).first()
        if not exists:
            _exec(
                conn,
                """
                ALTER TABLE Purchase_Order
                ADD CONSTRAINT purchase_order_status_check
                CHECK (Purchase_Order_Status IN ('Open', 'Closed', 'Cancelled'))
                """,
            )
    else:
        # SQLite: table-level CHECK is only on CREATE; column already has DEFAULT.
        pass


def _ensure_crucible_status(conn: Connection) -> None:
    """Enforce Crucible_status ∈ Available / Damaged and map older values."""
    _exec(
        conn,
        """
        UPDATE Crucible_Master
        SET Crucible_status = CASE
            WHEN Crucible_status IN ('Available', 'Damaged') THEN Crucible_status
            WHEN Crucible_status IN ('Inactive', 'Damaged') THEN 'Damaged'
            ELSE 'Available'
        END
        """,
    )
    if not IS_POSTGRES:
        return
    rows = list(
        _exec(
            conn,
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'crucible_master'::regclass
              AND contype = 'c'
              AND position(
                    'crucible_status' IN lower(pg_get_constraintdef(oid))
                  ) > 0
            """,
        ).mappings()
    )
    if any(
        "Available" in (r["def"] or "") and "Damaged" in (r["def"] or "")
        for r in rows
    ):
        return
    for row in rows:
        _exec(
            conn,
            f'ALTER TABLE Crucible_Master DROP CONSTRAINT "{row["conname"]}"',
        )
    _exec(
        conn,
        """
        ALTER TABLE Crucible_Master
        ADD CONSTRAINT crucible_master_crucible_status_check
        CHECK (Crucible_status IN ('Available', 'Damaged'))
        """,
    )


def _ensure_one_available_crucible(conn: Connection) -> None:
    """A furnace may have only one Available crucible at a time."""
    _exec(
        conn,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS crucible_master_one_available_per_furnace
        ON Crucible_Master (furnace)
        WHERE Crucible_status = 'Available'
        """,
    )


def _ensure_packing_list(conn: Connection) -> None:
    """Create packing-list tables used to dispatch finished goods."""
    if IS_POSTGRES:
        id_sql = "INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY"
    else:
        id_sql = "INTEGER PRIMARY KEY AUTOINCREMENT"
    _exec(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS Packing_list (
            Packing_list_id {id_sql},
            Invoice_date TEXT,
            Invoice_number TEXT NOT NULL,
            Customer_PO_No TEXT NOT NULL,
            Cust_code TEXT REFERENCES Customer_Master(Cust_code),
            Customer_name TEXT,
            Alloy_id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
            Colour_code TEXT,
            Vehicle_no TEXT,
            Packing_list_status TEXT NOT NULL DEFAULT 'In-Progress'
                CHECK (Packing_list_status IN ('In-Progress', 'Verified')),
            Last_updated_by TEXT,
            Last_updated_datetime TEXT
        )
        """,
    )
    _exec(
        conn,
        """
        CREATE TABLE IF NOT EXISTS Packing_list_batch (
            Packing_list_id INTEGER NOT NULL REFERENCES Packing_list(Packing_list_id),
            Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
            PRIMARY KEY (Packing_list_id, Batch_ID)
        )
        """,
    )
    _ensure_columns(
        conn,
        "Packing_list",
        [
            ("Invoice_date", "TEXT"),
            ("Invoice_number", "TEXT"),
            ("Customer_PO_No", "TEXT"),
            ("Cust_code", "TEXT"),
            ("Customer_name", "TEXT"),
            ("Alloy_id", "INTEGER"),
            ("Colour_code", "TEXT"),
            ("Vehicle_no", "TEXT"),
            ("Packing_list_status", "TEXT"),
            ("Last_updated_by", "TEXT"),
            ("Last_updated_datetime", "TEXT"),
        ],
    )


# TEXT columns that participate in a PRIMARY KEY. True = fold case with LOWER().
_TEXT_PK_CI: list[tuple[str, list[tuple[str, bool]]]] = [
    ("Customer_Master", [("Cust_code", True)]),
    ("ISRI_CODE_TABLE", [("ISRI_CODE", True)]),
    ("Raw_Material_Master", [("Raw_Material_Name", True), ("Effective_date", True)]),
    (
        "Raw_Material_Spec",
        [
            ("Raw_Material_Name", True),
            ("Effective_date", True),
            ("Element_symbol", True),
        ],
    ),
    ("Furnace_Master", [("Furnace", True)]),
    ("Crucible_Master", [("Crucible_no", True)]),
    ("Melter_Master", [("Melter_Name", True)]),
    ("Trolley_Master", [("Trolley_name", True)]),
    ("State_City_Master", [("State", True), ("City", True)]),
    ("Month_code", [("Month", True)]),
    ("Access_matrix", [("ID", True)]),
    ("Production_supervisor", [("Production_supervisor", True)]),
    ("Production_batch", [("Batch_ID", True)]),
    (
        "batch_input",
        [
            ("Batch_ID", True),
            ("Raw_Material_Name", True),
            ("Lot_id", False),
            ("Charge_time", True),
        ],
    ),
    ("Batch_Chemical_Composition", [("Batch_ID", True), ("Element_symbol", True)]),
    ("Build_of_Material", [("BOMID", False), ("Effective_date", True)]),
    ("Purchase_Order", [("Customer_PO_No", True), ("Alloy_Id", False)]),
    ("Alloy_Master_spec", [("Alloy_id", False), ("Element_symbol", True)]),
    ("Furnace_Oil_Consumption", [("Consumption_date", True)]),
    ("Furnace_Oil_Inventory", [("Inventory_date", True)]),
    ("Electricity_Consumption", [("Consumption_date", True), ("Line", True)]),
    (
        "Packing_list_batch",
        [("Packing_list_id", False), ("Batch_ID", True)],
    ),
]


def _text_pk_column_names(table: str) -> set[str]:
    key = table.lower()
    for name, parts in _TEXT_PK_CI:
        if name.lower() == key:
            return {col.lower() for col, fold in parts if fold}
    return set()


def _ci_group_sql(parts: list[tuple[str, bool]]) -> str:
    return ", ".join(f"LOWER({col})" if fold else col for col, fold in parts)


def _ensure_case_insensitive_text_pks(conn: Connection) -> None:
    """TEXT primary keys treat letter case as the same value (C-01 == c-01)."""
    id_col = "ctid" if IS_POSTGRES else "rowid"
    for table, parts in _TEXT_PK_CI:
        group_sql = _ci_group_sql(parts)
        index_name = f"{table.lower()}_text_pk_ci"
        try:
            nested = conn.begin_nested()
            try:
                _exec(
                    conn,
                    f"""
                    DELETE FROM {table}
                    WHERE {id_col} NOT IN (
                        SELECT MIN({id_col}) FROM {table} GROUP BY {group_sql}
                    )
                    """,
                )
                nested.commit()
            except Exception:
                nested.rollback()
        except Exception:
            pass
        try:
            nested = conn.begin_nested()
            try:
                _exec(
                    conn,
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS {index_name}
                    ON {table} ({group_sql})
                    """,
                )
                nested.commit()
            except Exception:
                nested.rollback()
        except Exception:
            pass


def existing_text_key(table: str, column: str, value: Any) -> str:
    """Strip a TEXT key and reuse the stored spelling if a case-insensitive match exists."""
    text = "" if value is None else str(value).strip()
    if not text:
        return text
    row = fetch_one(
        f'SELECT {column} AS "k" FROM {table} WHERE LOWER({column}) = LOWER(?)',
        (text,),
    )
    stored = (row or {}).get("k")
    return str(stored) if stored not in (None, "") else text


def _ensure_vacum_sample_check(conn: Connection) -> None:
    """Enforce Vacum_Sample ∈ OK / NOT OK (NULL allowed)."""
    if not IS_POSTGRES:
        return
    exists = _exec(
        conn,
        """
        SELECT 1 AS ok
        FROM pg_constraint
        WHERE conname = 'production_batch_vacum_sample_check'
        """,
    ).first()
    if exists:
        return
    _exec(
        conn,
        """
        ALTER TABLE Production_batch
        ADD CONSTRAINT production_batch_vacum_sample_check
        CHECK (Vacum_Sample IS NULL OR Vacum_Sample IN ('OK', 'NOT OK'))
        """,
    )


def _purchase_order_pk_columns(conn: Connection) -> list[str]:
    if IS_POSTGRES:
        rows = list(
            _exec(
                conn,
                """
                SELECT a.attname AS name
                FROM pg_index i
                CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS x(attnum, ordinality)
                JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = x.attnum
                WHERE i.indrelid = 'purchase_order'::regclass
                  AND i.indisprimary
                ORDER BY x.ordinality
                """,
            ).mappings()
        )
        return [str(r["name"]) for r in rows]
    rows = list(_exec(conn, "PRAGMA table_info(Purchase_Order)").mappings())
    pk_rows = [r for r in rows if int(r["pk"] or 0) > 0]
    pk_rows.sort(key=lambda r: int(r["pk"]))
    return [str(r["name"]) for r in pk_rows]


def _ensure_purchase_order_composite_pk(conn: Connection) -> None:
    """Identify each PO line by (Customer_PO_No, Alloy_Id) so one PO can cover many alloys."""
    normalized = [c.lower() for c in _purchase_order_pk_columns(conn)]
    if normalized == ["customer_po_no", "alloy_id"]:
        return

    null_alloy = (
        _exec(
            conn,
            "SELECT COUNT(*) AS n FROM Purchase_Order WHERE Alloy_Id IS NULL",
        )
        .mappings()
        .first()
    )
    if int((null_alloy or {}).get("n") or 0) > 0:
        raise ValueError(
            "Cannot make Alloy_Id part of the Purchase_Order key: "
            "some purchase orders have no alloy. Assign an alloy to every PO first."
        )

    if IS_POSTGRES:
        pk_name_row = (
            _exec(
                conn,
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'purchase_order'::regclass AND contype = 'p'
                """,
            )
            .mappings()
            .first()
        )
        if pk_name_row:
            _exec(
                conn,
                f"ALTER TABLE Purchase_Order DROP CONSTRAINT {pk_name_row['conname']}",
            )
        _exec(conn, "ALTER TABLE Purchase_Order ALTER COLUMN Alloy_Id SET NOT NULL")
        _exec(
            conn,
            "ALTER TABLE Purchase_Order ADD PRIMARY KEY (Customer_PO_No, Alloy_Id)",
        )
        return

    _exec(
        conn,
        """
        CREATE TABLE Purchase_Order__pk (
            Customer_PO_No TEXT NOT NULL,
            Cust_code TEXT REFERENCES Customer_Master(Cust_code),
            Customer_name TEXT REFERENCES Customer_Master(Customer_name),
            Alloy_Id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
            Order_Date TEXT,
            Delivery_Date TEXT,
            Order_Qty REAL,
            Rate REAL,
            Billing_Address TEXT,
            Billing_City TEXT,
            Billing_state TEXT,
            Billing_Pincode TEXT,
            Billing_country TEXT,
            Shipping_address TEXT,
            Shipping_City TEXT,
            Shipping_state TEXT,
            Shipping_Pincode TEXT,
            Shipping_country TEXT,
            PO_Document BLOB,
            PO_Document_name TEXT,
            PO_Document_type TEXT,
            Purchase_Order_Status TEXT DEFAULT 'Open'
                CHECK(Purchase_Order_Status IN ('Open', 'Closed', 'Cancelled')),
            PRIMARY KEY (Customer_PO_No, Alloy_Id)
        )
        """,
    )
    _exec(
        conn,
        """
        INSERT INTO Purchase_Order__pk
        SELECT Customer_PO_No, Cust_code, Customer_name, Alloy_Id,
               Order_Date, Delivery_Date, Order_Qty, Rate,
               Billing_Address, Billing_City, Billing_state, Billing_Pincode,
               Billing_country, Shipping_address, Shipping_City, Shipping_state,
               Shipping_Pincode, Shipping_country, PO_Document, PO_Document_name,
               PO_Document_type, Purchase_Order_Status
        FROM Purchase_Order
        """,
    )
    _exec(conn, "DROP TABLE Purchase_Order")
    _exec(conn, "ALTER TABLE Purchase_Order__pk RENAME TO Purchase_Order")


def _ensure_finished_goods_status(conn: Connection) -> None:
    """Allow Finished_Goods_Status: Under_Testing, Available, Assigned, Dispatched, Rejected."""
    if not IS_POSTGRES:
        return
    rows = list(
        _exec(
            conn,
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = 'finished_goods_inventory'::regclass
              AND contype = 'c'
              AND position(
                    'finished_goods_status' IN lower(pg_get_constraintdef(oid))
                  ) > 0
            """,
        ).mappings()
    )
    if any(
        "Dispatched" in (r["def"] or "") and "Rejected" in (r["def"] or "")
        for r in rows
    ):
        return
    for row in rows:
        _exec(
            conn,
            f'ALTER TABLE Finished_Goods_Inventory DROP CONSTRAINT "{row["conname"]}"',
        )
    _exec(
        conn,
        """
        ALTER TABLE Finished_Goods_Inventory
        ADD CONSTRAINT finished_goods_status_check
        CHECK (Finished_Goods_Status IN (
            'Under_Testing', 'Available', 'Assigned', 'Dispatched', 'Rejected'
        ))
        """,
    )


def _ensure_production_batch_status(conn: Connection) -> None:
    """Restrict Production_status to In-Progress and Completed."""
    _exec(
        conn,
        """
        UPDATE Production_batch
        SET Production_status = 'Completed'
        WHERE Production_status IN ('Approved', 'Completed')
        """,
    )
    _exec(
        conn,
        """
        UPDATE Production_batch
        SET Production_status = 'In-Progress'
        WHERE Production_status IS NULL
           OR Production_status NOT IN ('In-Progress', 'Completed')
        """,
    )
    if not IS_POSTGRES:
        return
    _exec(
        conn,
        """
        ALTER TABLE Production_batch
        ALTER COLUMN Production_status SET DEFAULT 'In-Progress'
        """,
    )
    _exec(
        conn,
        "ALTER TABLE Production_batch ALTER COLUMN Production_status SET NOT NULL",
    )
    rows = list(
        _exec(
            conn,
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'production_batch'::regclass
              AND contype = 'c'
              AND position(
                    'production_status' IN lower(pg_get_constraintdef(oid))
                  ) > 0
            """,
        ).mappings()
    )
    for row in rows:
        _exec(
            conn,
            f'ALTER TABLE Production_batch DROP CONSTRAINT "{row["conname"]}"',
        )
    _exec(
        conn,
        """
        ALTER TABLE Production_batch
        ADD CONSTRAINT production_batch_production_status_check
        CHECK (Production_status IN ('In-Progress', 'Completed'))
        """,
    )


def _ensure_finished_goods_release_trigger(conn: Connection) -> None:
    """When a batch is Completed, release its Under_Testing finished-goods bundles."""
    if not IS_POSTGRES:
        return
    _exec(
        conn,
        """
        CREATE OR REPLACE FUNCTION release_finished_goods_on_batch_approved()
        RETURNS TRIGGER AS $fn$
        BEGIN
            IF NEW.production_status = 'Completed'
               AND (OLD.production_status IS DISTINCT FROM 'Completed') THEN
                UPDATE finished_goods_inventory
                SET finished_goods_status = 'Available'
                WHERE batch_id = NEW.batch_id
                  AND finished_goods_status = 'Under_Testing';
            END IF;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql
        """,
    )
    _exec(conn, "DROP TRIGGER IF EXISTS trg_release_fg_on_approved ON production_batch")
    _exec(
        conn,
        """
        CREATE TRIGGER trg_release_fg_on_approved
        AFTER UPDATE OF production_status ON production_batch
        FOR EACH ROW
        EXECUTE PROCEDURE release_finished_goods_on_batch_approved()
        """,
    )


def _ensure_columns(conn: Connection, table: str, columns: list[tuple[str, str]]) -> None:
    if IS_POSTGRES:
        physical = table.lower()
        rows = _exec(
            conn,
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (physical,),
        ).mappings()
        existing = {row["name"] for row in rows}
        for name, typedef in columns:
            if name.lower() not in existing:
                _exec(conn, f"ALTER TABLE {table} ADD COLUMN {name} {typedef}")
    else:
        existing = {
            row["name"] for row in _exec(conn, f"PRAGMA table_info({table})").mappings()
        }
        for name, typedef in columns:
            if name not in existing:
                _exec(conn, f"ALTER TABLE {table} ADD COLUMN {name} {typedef}")


def _drop_columns(conn: Connection, table: str, columns: list[str]) -> None:
    """Drop leftover columns from older schemas when they still exist."""
    if IS_POSTGRES:
        physical = table.lower()
        rows = _exec(
            conn,
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (physical,),
        ).mappings()
        existing = {row["name"] for row in rows}
        for name in columns:
            if name.lower() in existing:
                _exec(conn, f'ALTER TABLE {table} DROP COLUMN IF EXISTS "{name.lower()}"')
    else:
        existing = {
            row["name"] for row in _exec(conn, f"PRAGMA table_info({table})").mappings()
        }
        for name in columns:
            if name in existing:
                _exec(conn, f"ALTER TABLE {table} DROP COLUMN {name}")


def _rename_column(
    conn: Connection, table: str, old_name: str, new_name: str
) -> None:
    """Rename a column when the old name exists and the new name does not."""
    if IS_POSTGRES:
        physical = table.lower()
        rows = _exec(
            conn,
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (physical,),
        ).mappings()
        existing = {row["name"] for row in rows}
        if old_name.lower() in existing and new_name.lower() not in existing:
            _exec(
                conn,
                f'ALTER TABLE {table} RENAME COLUMN "{old_name.lower()}" TO "{new_name.lower()}"',
            )
    else:
        existing = {
            row["name"] for row in _exec(conn, f"PRAGMA table_info({table})").mappings()
        }
        if old_name in existing and new_name not in existing:
            _exec(conn, f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}")


# ---------- Generic helpers ----------

def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    def _run() -> list[dict[str, Any]]:
        with ENGINE.connect() as conn:
            return [dict(r) for r in _exec(conn, sql, tuple(params)).mappings()]

    return _retry_on_disconnect(_run)


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    def _run() -> Optional[dict[str, Any]]:
        with ENGINE.connect() as conn:
            row = _exec(conn, sql, tuple(params)).mappings().first()
            return dict(row) if row is not None else None

    return _retry_on_disconnect(_run)


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with get_connection() as conn:
        return _exec(conn, sql, tuple(params)).rowcount


def execute_many(sql: str, seq: list[tuple[Any, ...]]) -> None:
    with get_connection() as conn:
        _exec(conn, sql, seq)


def get_all_records(table_name: str, order_by: str | None = None) -> list[dict[str, Any]]:
    """Return all records from the given table as a list of dicts.

    Uses SQLAlchemy table reflection, so it works for any table in the
    database without needing model classes. Optionally sorts by a column.
    """
    if IS_POSTGRES:
        # Postgres stores unquoted identifiers in lowercase
        table_name = table_name.lower()
        order_by = order_by.lower() if order_by else None
    def _run() -> list[dict[str, Any]]:
        if _USE_NEON_HTTP:
            sql = f"SELECT * FROM {table_name}"
            if order_by is not None:
                sql += f" ORDER BY {order_by}"
            with ENGINE.connect() as conn:
                return [dict(r) for r in _exec(conn, sql).mappings()]
        table = Table(table_name, MetaData(), autoload_with=ENGINE)
        stmt = select(table)
        if order_by is not None:
            stmt = stmt.order_by(table.c[order_by])
        with ENGINE.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings()]

    return _retry_on_disconnect(_run)


# ---------- Data browser / editor ----------

# Tables users can inspect and correct from the Data Browser page.
# `pk` / `order_by` / `identity` use the physical (Postgres-lowercase) names.
EDITABLE_TABLES: list[dict[str, Any]] = [
    {
        "key": "customer_master",
        "label": "Customers",
        "pk": ["cust_code"],
        "order_by": "cust_code",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "vendor_master",
        "label": "Vendors",
        "pk": ["vendor_code"],
        "order_by": "vendor_code",
        "identity": ["vendor_code"],
        "allow_add": False,
    },
    {
        "key": "alloy_master",
        "label": "Alloys",
        "pk": ["alloy_id"],
        "order_by": "alloy_id",
        "identity": ["alloy_id"],
        "allow_add": False,
    },
    {
        "key": "alloy_master_spec",
        "label": "Alloy specs (min/max %)",
        "pk": ["alloy_id", "element_symbol"],
        "order_by": "alloy_id",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "raw_material_master",
        "label": "Raw material master",
        "pk": ["raw_material_name", "effective_date"],
        "order_by": "raw_material_name",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "raw_material_purchase",
        "label": "Raw material purchases",
        "pk": ["purchase_id"],
        "order_by": "purchase_id",
        "identity": ["purchase_id"],
        "allow_add": False,
    },
    {
        "key": "raw_material_inventory",
        "label": "Raw material inventory",
        "pk": ["lot_id"],
        "order_by": "lot_id",
        "identity": ["lot_id"],
        "allow_add": False,
    },
    {
        "key": "raw_material_spec",
        "label": "Raw material chemistry",
        "pk": ["raw_material_name", "effective_date", "element_symbol"],
        "order_by": "raw_material_name",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "isri_code_table",
        "label": "ISRI codes",
        "pk": ["isri_code"],
        "order_by": "isri_code",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "purchase_order",
        "label": "Purchase orders",
        "pk": ["customer_po_no", "alloy_id"],
        "order_by": "customer_po_no",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "furnace_master",
        "label": "Furnaces",
        "pk": ["furnace"],
        "order_by": "furnace",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "crucible_master",
        "label": "Crucibles",
        "pk": ["crucible_no"],
        "order_by": "crucible_no",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "melter_master",
        "label": "Melters",
        "pk": ["melter_name"],
        "order_by": "melter_name",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "trolley_master",
        "label": "Trolleys",
        "pk": ["trolley_name"],
        "order_by": "trolley_name",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "state_city_master",
        "label": "States & cities",
        "pk": ["state", "city"],
        "order_by": "state",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "month_code",
        "label": "Month codes",
        "pk": ["month"],
        "order_by": "month",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "access_matrix",
        "label": "Access matrix",
        "pk": ["id"],
        "order_by": "id",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "finished_goods_inventory",
        "label": "Finished goods inventory",
        "pk": ["bundle_id"],
        "order_by": "bundle_id",
        "identity": ["bundle_id"],
        "allow_add": True,
    },
    {
        "key": "production_supervisor",
        "label": "Production supervisors",
        "pk": ["production_supervisor"],
        "order_by": "production_supervisor",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "batch_output",
        "label": "Batch outputs",
        "pk": ["output_id"],
        "order_by": "output_id",
        "identity": ["output_id"],
        "allow_add": False,
    },
    {
        "key": "element_master",
        "label": "Elements",
        "pk": ["serial_no"],
        "order_by": "serial_no",
        "identity": [],
        "allow_add": False,
    },
    {
        "key": "furnace_oil_purchase",
        "label": "Furnace oil purchases",
        "pk": ["purchase_id"],
        "order_by": "purchase_id",
        "identity": ["purchase_id"],
        "allow_add": False,
    },
    {
        "key": "furnace_oil_consumption",
        "label": "Furnace oil consumption",
        "pk": ["consumption_date"],
        "order_by": "consumption_date",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "furnace_oil_inventory",
        "label": "Furnace oil inventory",
        "pk": ["inventory_date"],
        "order_by": "inventory_date",
        "identity": [],
        "allow_add": False,
    },
    {
        "key": "electricity_consumption",
        "label": "Electricity consumption",
        "pk": ["consumption_date", "line"],
        "order_by": "consumption_date",
        "identity": [],
        "allow_add": True,
    },
    {
        "key": "cost_of_conversion",
        "label": "Cost of conversion",
        "pk": ["id"],
        "order_by": "expense_month",
        "identity": ["id"],
        "allow_add": False,
    },
]


def _resolve_table_name(table_name: str) -> str:
    from sqlalchemy import inspect as sa_inspect

    def _run() -> str:
        insp = sa_inspect(ENGINE)
        names = insp.get_table_names()
        if table_name in names:
            return table_name
        lower = table_name.lower()
        if lower in names:
            return lower
        raise ValueError(f"Table not found: {table_name}")

    return _retry_on_disconnect(_run)


def editable_columns(table_name: str) -> list[str]:
    """Column names for the editor, excluding binary photo fields."""
    from sqlalchemy import inspect as sa_inspect

    resolved = _resolve_table_name(table_name)

    def _run() -> list[str]:
        cols: list[str] = []
        for c in sa_inspect(ENGINE).get_columns(resolved):
            type_name = type(c["type"]).__name__.upper()
            if "BLOB" in type_name or "BYTEA" in type_name or "LARGEBINARY" in type_name:
                continue
            cols.append(c["name"])
        return cols

    return _retry_on_disconnect(_run)


def load_editable_table(table_name: str, order_by: str | None = None) -> list[dict[str, Any]]:
    resolved = _resolve_table_name(table_name)
    cols = editable_columns(resolved)
    if not cols:
        return []
    select_list = ", ".join(f't.{c} AS "{c}"' for c in cols)
    # Always sort chemistry / spec tables by Element_Master.Serial_no
    has_element = any(c.lower() == "element_symbol" for c in cols)
    if has_element:
        sym_col = next(c for c in cols if c.lower() == "element_symbol")
        return fetch_all(
            f"""
            SELECT {select_list}
            FROM {resolved} t
            LEFT JOIN Element_Master _el ON _el.Element_Symbol = t.{sym_col}
            ORDER BY COALESCE(_el.Serial_no, 9999), t.{sym_col}
            """
        )
    order = order_by if order_by in cols else cols[0]
    return fetch_all(f"SELECT {select_list} FROM {resolved} t ORDER BY t.{order}")


def _row_key(row: dict[str, Any], pk: list[str]) -> tuple[Any, ...]:
    return tuple(row.get(c) for c in pk)


def save_table_edits(
    table_name: str,
    pk_cols: list[str],
    original_rows: list[dict[str, Any]],
    edited_rows: list[dict[str, Any]],
    identity_cols: Optional[list[str]] = None,
) -> dict[str, int]:
    """Upsert edited/new rows. Returns counts of inserted and updated rows."""
    identity_cols = identity_cols or []
    resolved = _resolve_table_name(table_name)
    cols = editable_columns(resolved)
    original_map = {_row_key(r, pk_cols): r for r in original_rows}
    stamp_by = next((c for c in cols if c.lower() == "last_updated_by"), None)
    stamp_dt = next((c for c in cols if c.lower() == "last_updated_datetime"), None)
    by_val, dt_val = audit_stamp() if (stamp_by or stamp_dt) else (None, None)

    inserted = updated = 0
    with get_connection() as conn:
        for row in edited_rows:
            # Skip completely empty new rows
            if all(row.get(c) in (None, "") for c in cols):
                continue
            if stamp_by:
                row[stamp_by] = by_val
            if stamp_dt:
                row[stamp_dt] = dt_val
            text_pk = _text_pk_column_names(resolved)
            for col in pk_cols:
                if col.lower() in text_pk and row.get(col) not in (None, ""):
                    row[col] = existing_text_key(resolved, col, row[col])
            key = _row_key(row, pk_cols)
            is_new = key not in original_map or any(v is None for v in key)

            if resolved.lower() == "batch_output":
                lower_row = {str(k).lower(): v for k, v in row.items()}
                bid = lower_row.get("batch_id")
                require_completed_batch_for_output(str(bid) if bid not in (None, "") else "")

            write_cols = [
                c for c in cols
                if c in row and not (is_new and c in identity_cols and row.get(c) in (None, ""))
            ]
            if stamp_by and stamp_by not in write_cols:
                write_cols.append(stamp_by)
            if stamp_dt and stamp_dt not in write_cols:
                write_cols.append(stamp_dt)
            if not write_cols:
                continue

            # For new identity rows, omit the identity column so the DB generates it
            if is_new and identity_cols:
                write_cols = [c for c in write_cols if c not in identity_cols or row.get(c) not in (None, "")]

            values = [
                round_percent_4(row.get(c))
                if (
                    resolved.lower() in PERCENT_4DP_TABLES
                    and c.lower() in PERCENT_4DP_COLUMNS
                )
                else row.get(c)
                for c in write_cols
            ]
            placeholders = ", ".join("?" for _ in write_cols)
            col_sql = ", ".join(write_cols)

            if is_new and identity_cols and all(row.get(c) in (None, "") for c in identity_cols):
                _exec(
                    conn,
                    f"INSERT INTO {resolved} ({col_sql}) VALUES ({placeholders})",
                    tuple(values),
                )
                inserted += 1
                continue

            non_pk = [c for c in write_cols if c not in pk_cols]
            if not non_pk and key in original_map:
                continue
            update_sql = ", ".join(f"{c}=excluded.{c}" for c in non_pk) if non_pk else f"{pk_cols[0]}=excluded.{pk_cols[0]}"
            pk_sql = ", ".join(pk_cols)
            _exec(
                conn,
                f"""
                INSERT INTO {resolved} ({col_sql}) VALUES ({placeholders})
                ON CONFLICT ({pk_sql}) DO UPDATE SET {update_sql}
                """,
                tuple(values),
            )
            if key in original_map:
                if any(original_map[key].get(c) != row.get(c) for c in write_cols):
                    updated += 1
            else:
                inserted += 1

    if resolved.lower() in {
        "furnace_oil_purchase",
        "furnace_oil_consumption",
        "furnace_oil_inventory",
    }:
        rebuild_furnace_oil_inventory()
    return {"inserted": inserted, "updated": updated}


# ---------- Lookups ----------

def list_customers(active_only: bool = True) -> list[str]:
    sql = 'SELECT Customer_name AS "Customer_name" FROM Customer_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Customer_name"
    return [r["Customer_name"] for r in fetch_all(sql)]


def list_customer_codes(active_only: bool = True) -> list[dict[str, Any]]:
    """Customers with their codes, for code-based FK lookups."""
    sql = 'SELECT Cust_code AS "Cust_code", Customer_name AS "Customer_name" FROM Customer_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Cust_code"
    return fetch_all(sql)


def get_customer(cust_code: str) -> Optional[dict[str, Any]]:
    return fetch_one(
        """
        SELECT Cust_code AS "Cust_code", Customer_name AS "Customer_name",
               Address AS "Address", City AS "City", State AS "State",
               Pincode AS "Pincode", Country AS "Country"
        FROM Customer_Master
        WHERE Cust_code = ?
        """,
        (cust_code,),
    )


def list_vendors(active_only: bool = True) -> list[dict[str, Any]]:
    """Vendors with their auto-generated codes, for code-based FK lookups."""
    sql = 'SELECT Vendor_code AS "Vendor_code", Vendor_name AS "Vendor_name" FROM Vendor_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Vendor_name"
    return fetch_all(sql)


def list_suppliers(active_only: bool = True) -> list[str]:
    """Vendor names only; kept for display lookups."""
    return [v["Vendor_name"] for v in list_vendors(active_only)]


# Chemistry entry UIs show only the first N elements by Serial_no.
ENTRY_CHEM_ELEMENT_LIMIT = 15
BATCH_CHEM_EXTRA_SYMBOLS = ("OE", "OT", "SF")
RAW_MATERIAL_SPEC_HIDDEN_SYMBOLS = frozenset(BATCH_CHEM_EXTRA_SYMBOLS)


def list_elements(limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Return Element_Master rows ordered by Serial_no.

    If ``limit`` is set, only the first ``limit`` rows (by Serial_no) are returned.
    """
    sql = """
        SELECT Serial_no AS "Serial_no", Element_Name AS "Element_Name",
               Element_Symbol AS "Element_Symbol"
        FROM Element_Master
        ORDER BY Serial_no
    """
    if limit is not None:
        sql += " LIMIT ?"
        return fetch_all(sql, (int(limit),))
    return fetch_all(sql)


def list_entry_elements() -> list[dict[str, Any]]:
    """Elements shown on chemistry / spec entry forms (Serial_no order, first 15)."""
    return list_elements(limit=ENTRY_CHEM_ELEMENT_LIMIT)


def list_batch_chem_elements() -> list[dict[str, Any]]:
    """First 15 Element_Master rows plus OE, OT, and SF (ladle chemistry and alloy specs)."""
    entry = list_entry_elements()
    seen = {e["Element_Symbol"] for e in entry}
    extra = [
        e
        for e in list_elements()
        if e["Element_Symbol"] in BATCH_CHEM_EXTRA_SYMBOLS
        and e["Element_Symbol"] not in seen
    ]
    extra.sort(key=lambda e: int(e.get("Serial_no") or 0))
    return entry + extra


def omit_raw_material_spec_hidden(composition: dict[str, Any]) -> dict[str, Any]:
    """Drop OE / OT / SF from a Raw_Material_Spec map."""
    return {
        key: value
        for key, value in composition.items()
        if str(key) not in RAW_MATERIAL_SPEC_HIDDEN_SYMBOLS
    }


def list_raw_material_spec_elements(*, entry_only: bool = False) -> list[dict[str, Any]]:
    """Elements for Raw_Material_Spec entry and display (never OE, OT, or SF)."""
    source = list_entry_elements() if entry_only else list_elements()
    return [
        el
        for el in source
        if el["Element_Symbol"] not in RAW_MATERIAL_SPEC_HIDDEN_SYMBOLS
    ]


def list_extra_elements() -> list[dict[str, Any]]:
    """Element_Master rows after the default entry list (Serial_no > first 15)."""
    entry_syms = {e["Element_Symbol"] for e in list_entry_elements()}
    return [e for e in list_elements() if e["Element_Symbol"] not in entry_syms]


OTHER_SPEC_SERIAL_MIN = 16
OTHER_SPEC_SERIAL_MAX = 36


def list_other_spec_elements() -> list[dict[str, Any]]:
    """Element_Master rows with Serial_no from 16 through 36 (alloy spec extras)."""
    sql = """
        SELECT Serial_no AS "Serial_no", Element_Name AS "Element_Name",
               Element_Symbol AS "Element_Symbol"
        FROM Element_Master
        WHERE Serial_no BETWEEN ? AND ?
        ORDER BY Serial_no
    """
    return fetch_all(sql, (OTHER_SPEC_SERIAL_MIN, OTHER_SPEC_SERIAL_MAX))


def list_element_symbols(
    subset: Optional[Iterable[str]] = None,
    *,
    limit: Optional[int] = None,
) -> list[str]:
    """Element symbols in Element_Master.Serial_no order.

    If ``subset`` is given, return only those symbols (still in serial order).
    If ``limit`` is given, only the first ``limit`` elements by Serial_no are
    considered (applied before subset filtering).
    """
    symbols = [e["Element_Symbol"] for e in list_elements(limit=limit)]
    if subset is None:
        return symbols
    wanted = {str(s) for s in subset}
    return [s for s in symbols if s in wanted]


def list_entry_element_symbols() -> list[str]:
    """Symbols for chemistry / spec entry forms (first 15 by Serial_no)."""
    return list_element_symbols(limit=ENTRY_CHEM_ELEMENT_LIMIT)


def list_furnaces(active_only: bool = True) -> list[str]:
    sql = 'SELECT Furnace AS "Furnace" FROM Furnace_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY CAST(Furnace AS INTEGER), Furnace"
    return [r["Furnace"] for r in fetch_all(sql)]


def list_crucibles(
    furnace: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT Crucible_no AS "Crucible_no", furnace AS "furnace",
               Crucible_status AS "Crucible_status",
               Vendor_name AS "Vendor_name"
        FROM Crucible_Master
    """
    params: list[Any] = []
    clauses: list[str] = []
    if furnace:
        clauses.append("LOWER(furnace) = LOWER(?)")
        params.append(furnace)
    if status:
        clauses.append("Crucible_status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY furnace, Crucible_no"
    return fetch_all(sql, params)


def get_available_crucible(furnace: str) -> Optional[dict[str, Any]]:
    """Return the Available crucible for a furnace, if any."""
    if not furnace:
        return None
    rows = list_crucibles(furnace=furnace, status="Available")
    return rows[0] if rows else None


def require_available_crucible(furnace: str) -> str:
    """Return the Available Crucible_no for a furnace, or raise."""
    current = get_available_crucible(furnace)
    if not current:
        raise ValueError(
            "No crucible available for the respective furnace."
        )
    return str(current["Crucible_no"])


def _require_single_available(furnace: Optional[str], crucible_no: str) -> None:
    if not furnace:
        return
    current = get_available_crucible(furnace)
    if current and str(current["Crucible_no"]).casefold() != str(crucible_no).casefold():
        raise ValueError(
            f"Furnace {furnace} already has Available crucible "
            f"{current['Crucible_no']}. Mark that one Damaged before "
            "making another Available."
        )


def list_melters(active_only: bool = True) -> list[str]:
    sql = 'SELECT Melter_Name AS "Melter_Name" FROM Melter_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Melter_Name"
    return [r["Melter_Name"] for r in fetch_all(sql)]


def list_production_supervisors(active_only: bool = True) -> list[str]:
    sql = (
        'SELECT Production_supervisor AS "Production_supervisor" '
        "FROM Production_supervisor"
    )
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Production_supervisor"
    return [r["Production_supervisor"] for r in fetch_all(sql)]


def list_trolleys(active_only: bool = True) -> list[dict[str, Any]]:
    sql = """
        SELECT Trolley_name AS "Trolley_name", Colour AS "Colour",
               Weight AS "Weight", Status AS "Status"
        FROM Trolley_Master
    """
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Trolley_name"
    return fetch_all(sql)


def list_states(active_only: bool = True) -> list[str]:
    sql = 'SELECT DISTINCT State AS "State" FROM State_City_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY State"
    return [r["State"] for r in fetch_all(sql)]


def list_cities(state: Optional[str] = None, active_only: bool = True) -> list[str]:
    sql = 'SELECT City AS "City" FROM State_City_Master WHERE 1=1'
    params: list[Any] = []
    if active_only:
        sql += " AND Status = 'Active'"
    if state:
        sql += " AND State = ?"
        params.append(state)
    sql += " ORDER BY City"
    return [r["City"] for r in fetch_all(sql, params)]


def list_isri_codes() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT ISRI_CODE AS "ISRI_CODE", Description AS "Description"
        FROM ISRI_CODE_TABLE ORDER BY ISRI_CODE
        """
    )


def list_raw_materials(active_only: bool = True) -> list[str]:
    sql = 'SELECT DISTINCT Raw_Material_Name AS "Raw_Material_Name" FROM Raw_Material_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Raw_Material_Name"
    return [r["Raw_Material_Name"] for r in fetch_all(sql)]


def find_raw_material_name(name: str) -> Optional[str]:
    """Return the stored spelling of a raw_material_name, ignoring letter case."""
    text = "" if name is None else str(name).strip()
    if not text:
        return None
    row = fetch_one(
        """
        SELECT Raw_Material_Name AS "Raw_Material_Name"
        FROM Raw_Material_Master
        WHERE LOWER(Raw_Material_Name) = LOWER(?)
        """,
        (text,),
    )
    stored = (row or {}).get("Raw_Material_Name")
    return str(stored) if stored not in (None, "") else None


def get_raw_material_master(name: str) -> Optional[dict[str, Any]]:
    """Latest Raw_Material_Master row for a name (case-insensitive)."""
    stored = find_raw_material_name(name)
    if not stored:
        return None
    rows = fetch_all(
        """
        SELECT m.Raw_Material_Name AS "Raw_Material_Name",
               m.Effective_date AS "Effective_date",
               m.Vendor_code AS "Vendor_code",
               v.Vendor_name AS "Vendor_name",
               m.ISRI_CODE AS "ISRI_CODE",
               m.Availability_class AS "Availability_class",
               m.Recovery AS "Recovery",
               m.Cost_per_kg AS "Cost_per_kg",
               m.Status AS "Status",
               m.Last_updated_by AS "Last_updated_by",
               m.Last_updated_datetime AS "Last_updated_datetime"
        FROM Raw_Material_Master m
        LEFT JOIN Vendor_Master v ON v.Vendor_code = m.Vendor_code
        WHERE m.Raw_Material_Name = ?
        ORDER BY m.Effective_date DESC
        """,
        (stored,),
    )
    return rows[0] if rows else None


def _as_effective_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()[:10]


def _raw_material_spec_parent(
    name: str, effective_date: Optional[str] = None
) -> Optional[tuple[str, str]]:
    stored = find_raw_material_name(name)
    if not stored:
        return None
    wanted = _as_effective_date(effective_date)
    if wanted:
        row = fetch_one(
            """
            SELECT Raw_Material_Name AS "Raw_Material_Name",
                   Effective_date AS "Effective_date"
            FROM Raw_Material_Master
            WHERE LOWER(Raw_Material_Name) = LOWER(?) AND Effective_date = ?
            """,
            (stored, wanted),
        )
        if row:
            return str(row["Raw_Material_Name"]), _as_effective_date(row["Effective_date"])
    latest = get_raw_material_master(stored)
    if not latest:
        return None
    return stored, _as_effective_date(latest.get("Effective_date"))


def get_raw_material_master_spec(
    name: str, effective_date: Optional[str] = None
) -> dict[str, float]:
    """Chemistry for one Raw_Material_Master version (name + effective date)."""
    parent = _raw_material_spec_parent(name, effective_date)
    if not parent:
        return {}
    stored, spec_date = parent
    rows = fetch_all(
        """
        SELECT s.Element_symbol AS "Element_symbol", s.Percentage AS "Percentage"
        FROM Raw_Material_Spec s
        LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
        WHERE LOWER(s.Raw_Material_Name) = LOWER(?) AND s.Effective_date = ?
        ORDER BY COALESCE(_el.Serial_no, 9999), s.Element_symbol
        """,
        (stored, spec_date),
    )
    return omit_raw_material_spec_hidden(
        {
            str(r["Element_symbol"]): float(r["Percentage"])
            for r in rows
            if r.get("Percentage") is not None
        }
    )


def set_raw_material_master_spec(
    name: str,
    composition: dict[str, float],
    effective_date: Optional[str] = None,
) -> None:
    """Replace chemistry for one Raw_Material_Master version."""
    parent = _raw_material_spec_parent(name, effective_date)
    if not parent:
        raise ValueError("Raw material name and effective date are required.")
    stored, spec_date = parent
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        _exec(
            conn,
            """
            DELETE FROM Raw_Material_Spec
            WHERE LOWER(Raw_Material_Name) = LOWER(?)
              AND Effective_date = ?
              AND LOWER(Element_symbol) NOT IN ('oe', 'ot', 'sf')
            """,
            (stored, spec_date),
        )
        for sym, pct in omit_raw_material_spec_hidden(composition).items():
            rounded = round_percent_4(pct)
            if rounded is None:
                continue
            symbol = existing_text_key("Element_Master", "Element_Symbol", sym)
            _exec(
                conn,
                """
                INSERT INTO Raw_Material_Spec
                    (Raw_Material_Name, Effective_date, Element_symbol, Percentage,
                     Last_updated_by, Last_updated_datetime)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (stored, spec_date, symbol, rounded, by_val, dt_val),
            )


def list_raw_material_master() -> list[dict[str, Any]]:
    """All raw-material grades, excluding the Photo blob."""
    return fetch_all(
        """
        SELECT m.Raw_Material_Name AS "Raw_Material_Name",
               m.Effective_date AS "Effective_date",
               m.Vendor_code AS "Vendor_code",
               v.Vendor_name AS "Vendor_name",
               m.ISRI_CODE AS "ISRI_CODE",
               m.Availability_class AS "Availability_class",
               m.Recovery AS "Recovery",
               m.Cost_per_kg AS "Cost_per_kg",
               m.Status AS "Status",
               m.Last_updated_by AS "Last_updated_by",
               m.Last_updated_datetime AS "Last_updated_datetime"
        FROM Raw_Material_Master m
        LEFT JOIN Vendor_Master v ON v.Vendor_code = m.Vendor_code
        ORDER BY m.Raw_Material_Name, m.Effective_date DESC
        """
    )


def list_lots_for_material(material: str) -> list[dict[str, Any]]:
    """Prior inventory lots for a material, newest first (for chemistry copy)."""
    return fetch_all(
        """
        SELECT i.Lot_id AS "Lot_id",
               p.Received_date AS "Received_date",
               i.Received_weight AS "Received_weight",
               p.Supplier_Invoice AS "Supplier_Invoice",
               (
                   SELECT COUNT(*) FROM Raw_Material_Spec s
                   WHERE LOWER(s.Raw_Material_Name) = LOWER(i.Raw_Material_Name)
               ) AS "Chem_count"
        FROM Raw_Material_Inventory i
        LEFT JOIN Raw_Material_Purchase p ON p.Purchase_id = i.Purchase_id
        WHERE i.Raw_Material_Name = ?
        ORDER BY i.Lot_id DESC
        """,
        (material,),
    )


def list_alloys(*, include_sidestream: bool = True) -> list[dict[str, Any]]:
    sql = """
        SELECT a.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               a.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name",
               a.Alloy_Family AS "Alloy_Family", a.Alloy_group AS "Alloy_group",
               a.Colour_code AS "Colour_code",
               a.Bis_Designation AS "Bis_Designation",
               a.Revision_datetime AS "Revision_datetime",
               a.Remarks AS "Remarks", a.Status AS "Status"
        FROM Alloy_Master a
        LEFT JOIN Customer_Master c ON c.Cust_code = a.Cust_code
    """
    params: tuple[Any, ...] = ()
    if not include_sidestream:
        placeholders = ", ".join("?" for _ in SIDESTREAM_ALLOY_IDS)
        sql += f" WHERE a.Alloy_id NOT IN ({placeholders})"
        params = SIDESTREAM_ALLOY_IDS
    sql += " ORDER BY a.Alloy_name"
    return fetch_all(sql, params)


def get_alloy(alloy_id: object) -> Optional[dict[str, Any]]:
    try:
        aid = int(alloy_id)
    except (TypeError, ValueError):
        return None
    return fetch_one(
        """
        SELECT a.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group", a.Colour_code AS "Colour_code",
               a.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name"
        FROM Alloy_Master a
        LEFT JOIN Customer_Master c ON c.Cust_code = a.Cust_code
        WHERE a.Alloy_id = ?
        """,
        (aid,),
    )


def get_alloy_specs(alloy_id: int) -> dict[str, dict[str, Any]]:
    """Min/max % from Alloy_Master_spec keyed by Element_symbol."""
    rows = fetch_all(
        """
        SELECT s.Element_symbol AS "Element_symbol",
               s.Min_percent AS "Min_percent",
               s.Max_percent AS "Max_percent"
        FROM Alloy_Master_spec s
        LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
        WHERE s.Alloy_id = ?
        ORDER BY COALESCE(_el.Serial_no, 9999), s.Element_symbol
        """,
        (int(alloy_id),),
    )
    return {str(r["Element_symbol"]): r for r in rows}


def list_inventory_lots(
    material: Optional[str] = None, ready_only: bool = False
) -> list[dict[str, Any]]:
    sql = """
        SELECT i.Lot_id AS "Lot_id", i.Raw_Material_Name AS "Raw_Material_Name",
               i.Purchase_id AS "Purchase_id",
               p.Vendor_code AS "Vendor_code", v.Vendor_name AS "Vendor_name",
               i.Remaining_Weight AS "Remaining_Weight",
               i.Received_weight AS "Received_weight",
               i.Cost_per_kg AS "Cost_per_kg",
               i.Raw_Material_Status AS "Raw_Material_Status",
               i.Source_Batch_ID AS "Source_Batch_ID",
               i.Source_Alloy_id AS "Source_Alloy_id",
               b.Alloy_id AS "Origin_Alloy_id",
               oa.Alloy_name AS "Origin_Alloy_name",
               COALESCE(p.Received_date, b.Production_Date) AS "Received_date"
        FROM Raw_Material_Inventory i
        LEFT JOIN Raw_Material_Purchase p ON p.Purchase_id = i.Purchase_id
        LEFT JOIN Vendor_Master v ON v.Vendor_code = p.Vendor_code
        LEFT JOIN Production_batch b ON b.Batch_ID = i.Source_Batch_ID
        LEFT JOIN Alloy_Master oa ON oa.Alloy_id = b.Alloy_id
        WHERE i.Remaining_Weight > 0
    """
    params: list[Any] = []
    if material:
        sql += " AND i.Raw_Material_Name = ?"
        params.append(material)
    if ready_only:
        sql += " AND i.Raw_Material_Status = 'Ready For Melt'"
    sql += " ORDER BY i.Lot_id DESC"
    return fetch_all(sql, params)


# ---------- Customers / Suppliers ----------

def upsert_customer(data: dict[str, Any]) -> None:
    """Insert or update a customer keyed on Cust_code."""
    data = dict(data)
    data["Cust_code"] = existing_text_key(
        "Customer_Master", "Cust_code", data.get("Cust_code")
    )
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Customer_Master
            (Cust_code, Customer_name, GST, PAN, Address, City, State, Pincode, Country,
             Contact1_name, Phone1, Contact_name2, Phone2, Email, Website,
             Bank_account, IFSC_code, Bank_name, Branch_category, Status,
             Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Cust_code) DO UPDATE SET
            Customer_name=excluded.Customer_name, GST=excluded.GST, PAN=excluded.PAN,
            Address=excluded.Address, City=excluded.City, State=excluded.State,
            Pincode=excluded.Pincode, Country=excluded.Country,
            Contact1_name=excluded.Contact1_name, Phone1=excluded.Phone1,
            Contact_name2=excluded.Contact_name2, Phone2=excluded.Phone2,
            Email=excluded.Email, Website=excluded.Website,
            Bank_account=excluded.Bank_account, IFSC_code=excluded.IFSC_code,
            Bank_name=excluded.Bank_name, Branch_category=excluded.Branch_category,
            Status=excluded.Status,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (
            data["Cust_code"],
            data["Customer_name"],
            data.get("GST"),
            data.get("PAN"),
            data.get("Address"),
            data.get("City"),
            data.get("State"),
            data.get("Pincode"),
            data.get("Country"),
            data.get("Contact1_name"),
            data.get("Phone1"),
            data.get("Contact_name2"),
            data.get("Phone2"),
            data.get("Email"),
            data.get("Website"),
            data.get("Bank_account"),
            data.get("IFSC_code"),
            data.get("Bank_name"),
            data.get("Branch_category"),
            data.get("Status", "Active"),
            by_val,
            dt_val,
        ),
    )


def upsert_supplier(data: dict[str, Any]) -> None:
    """Insert or update a vendor by name; Vendor_code and Creation_date are
    auto-generated on insert (Creation_date is preserved on update)."""
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Vendor_Master
            (Vendor_name, GST, PAN, Address, City, State, Pincode, Country,
             Contact1, Phone1, Contact2, Phone2, Email, Website,
             Credit_period, Bank_account, Branch, IFSC_code, Bank_name, Status,
             Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Vendor_name) DO UPDATE SET
            GST=excluded.GST, PAN=excluded.PAN,
            Address=excluded.Address, City=excluded.City, State=excluded.State,
            Pincode=excluded.Pincode, Country=excluded.Country,
            Contact1=excluded.Contact1, Phone1=excluded.Phone1,
            Contact2=excluded.Contact2, Phone2=excluded.Phone2,
            Email=excluded.Email, Website=excluded.Website,
            Credit_period=excluded.Credit_period,
            Bank_account=excluded.Bank_account, Branch=excluded.Branch,
            IFSC_code=excluded.IFSC_code, Bank_name=excluded.Bank_name,
            Status=excluded.Status,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (
            data["Vendor_name"],
            data.get("GST"),
            data.get("PAN"),
            data.get("Address"),
            data.get("City"),
            data.get("State"),
            data.get("Pincode"),
            data.get("Country"),
            data.get("Contact1"),
            data.get("Phone1"),
            data.get("Contact2"),
            data.get("Phone2"),
            data.get("Email"),
            data.get("Website"),
            data.get("Credit_period"),
            data.get("Bank_account"),
            data.get("Branch"),
            data.get("IFSC_code"),
            data.get("Bank_name"),
            data.get("Status", "Active"),
            by_val,
            dt_val,
        ),
    )


# ---------- Raw materials ----------

def add_raw_material_master(
    name: str,
    effective_date: str,
    vendor_code: Optional[int],
    availability_class: str,
    recovery: Optional[float],
    status: str,
    cost_per_kg: float,
    photo: Optional[bytes] = None,
    isri_code: Optional[str] = None,
    create_new: bool = False,
) -> str:
    """Insert or update a raw-material grade. Returns the stored name spelling."""
    text = "" if name is None else str(name).strip()
    if not text:
        raise ValueError("Raw material name is required.")
    stored = find_raw_material_name(text)
    if create_new:
        if stored:
            raise ValueError(
                f'A raw material named "{stored}" already exists '
                "(names are not case-sensitive)."
            )
        name = text
    else:
        if not stored:
            raise ValueError(
                f'Raw material "{text}" was not found. Choose an existing name to modify.'
            )
        name = stored
    effective_date = (effective_date or "").strip()
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Raw_Material_Master
            (Raw_Material_Name, Effective_date, Vendor_code, ISRI_CODE,
             Availability_class, Recovery, Photo, Status, Cost_per_kg,
             Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Raw_Material_Name, Effective_date) DO UPDATE SET
            Vendor_code=excluded.Vendor_code, ISRI_CODE=excluded.ISRI_CODE,
            Availability_class=excluded.Availability_class, Recovery=excluded.Recovery,
            Photo=COALESCE(excluded.Photo, Raw_Material_Master.Photo),
            Status=excluded.Status, Cost_per_kg=excluded.Cost_per_kg,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (
            name,
            effective_date,
            vendor_code,
            isri_code,
            availability_class,
            recovery,
            photo,
            status,
            cost_per_kg,
            by_val,
            dt_val,
        ),
    )
    return name


def add_raw_material_purchase(
    vendor_code: Optional[int],
    invoice: str,
    received_date: str,
    supplier_invoice_date: Optional[str] = None,
    invoice_document: Optional[bytes] = None,
    invoice_document_name: Optional[str] = None,
    invoice_document_type: Optional[str] = None,
    vehicle_photo: Optional[bytes] = None,
) -> int:
    """Create a vendor-invoice header that inventory lots can attach to."""
    if invoice_document and invoice_document_name:
        _validate_invoice_document_name(invoice_document_name)
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Raw_Material_Purchase
                (Vendor_code, Supplier_Invoice, Supplier_invoice_date, Received_date,
                 Invoice_Document, Invoice_Document_name, Invoice_Document_type,
                 Vehicle_photo, Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Purchase_id
            """,
            (
                vendor_code,
                invoice,
                supplier_invoice_date,
                received_date,
                invoice_document,
                invoice_document_name,
                invoice_document_type,
                vehicle_photo,
                by_val,
                dt_val,
            ),
        )
        return int(result.scalar_one())


def add_inventory_lot(
    material: str,
    purchase_id: int,
    weight: float,
    storage_bay: str,
    status: str,
    photo: Optional[bytes] = None,
    cost_per_kg: Optional[float] = None,
) -> int:
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Raw_Material_Inventory
                (Purchase_id, Raw_Material_Name, Received_weight, Remaining_Weight,
                 Storage_bay, Raw_Material_Status, Photo, Cost_per_kg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Lot_id
            """,
            (
                int(purchase_id),
                material,
                weight,
                weight,
                storage_bay,
                status,
                photo,
                cost_per_kg,
            ),
        )
        return int(result.scalar_one())


def save_raw_material_invoice(
    vendor_code: Optional[int],
    invoice: str,
    received_date: str,
    lines: list[dict[str, Any]],
    storage_bay: str,
    status: str,
    supplier_invoice_date: Optional[str] = None,
    invoice_document: Optional[bytes] = None,
    invoice_document_name: Optional[str] = None,
    invoice_document_type: Optional[str] = None,
    vehicle_photo: Optional[bytes] = None,
) -> tuple[int, list[int]]:
    """Save one vendor invoice and its lots in a single transaction."""
    if not lines:
        raise ValueError("Add at least one raw material line.")
    if invoice_document and invoice_document_name:
        _validate_invoice_document_name(invoice_document_name)
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Raw_Material_Purchase
                (Vendor_code, Supplier_Invoice, Supplier_invoice_date, Received_date,
                 Invoice_Document, Invoice_Document_name, Invoice_Document_type,
                 Vehicle_photo, Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Purchase_id
            """,
            (
                vendor_code,
                invoice,
                supplier_invoice_date,
                received_date,
                invoice_document,
                invoice_document_name,
                invoice_document_type,
                vehicle_photo,
                by_val,
                dt_val,
            ),
        )
        purchase_id = int(result.scalar_one())
        lot_ids: list[int] = []
        for line in lines:
            weight = float(line["weight"])
            lot = _exec(
                conn,
                """
                INSERT INTO Raw_Material_Inventory
                    (Purchase_id, Raw_Material_Name, Received_weight, Remaining_Weight,
                     Storage_bay, Raw_Material_Status, Photo, Cost_per_kg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING Lot_id
                """,
                (
                    purchase_id,
                    line["material"],
                    weight,
                    weight,
                    storage_bay,
                    status,
                    line.get("photo"),
                    line.get("cost"),
                ),
            )
            lot_ids.append(int(lot.scalar_one()))
        return purchase_id, lot_ids


INVOICE_DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".jpg",
    ".jpeg",
    ".png",
)


def _validate_invoice_document_name(filename: str) -> None:
    lower = (filename or "").strip().lower()
    if not any(lower.endswith(ext) for ext in INVOICE_DOCUMENT_EXTENSIONS):
        raise ValueError(
            "Invoice document must be Photo (JPEG/PNG), PDF, Word (.doc/.docx), "
            "or Excel (.xls/.xlsx)."
        )


def save_inventory_invoice_document(
    lot_id: int,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> None:
    if not file_bytes:
        raise ValueError("Invoice file is empty.")
    _validate_invoice_document_name(filename)
    row = fetch_one(
        """
        SELECT Purchase_id AS "Purchase_id"
        FROM Raw_Material_Inventory
        WHERE Lot_id = ?
        """,
        (lot_id,),
    )
    if not row or not row.get("Purchase_id"):
        raise ValueError(f"Lot {lot_id} not found.")
    by_val, dt_val = audit_stamp()
    execute(
        """
        UPDATE Raw_Material_Purchase
        SET Invoice_Document = ?, Invoice_Document_name = ?, Invoice_Document_type = ?,
            Last_updated_by = ?, Last_updated_datetime = ?
        WHERE Purchase_id = ?
        """,
        (file_bytes, filename.strip(), content_type or "", by_val, dt_val, row["Purchase_id"]),
    )


def get_inventory_vehicle_photo(lot_id: int) -> Optional[bytes]:
    row = fetch_one(
        """
        SELECT p.Vehicle_photo AS "Vehicle_photo"
        FROM Raw_Material_Inventory i
        JOIN Raw_Material_Purchase p ON p.Purchase_id = i.Purchase_id
        WHERE i.Lot_id = ?
        """,
        (lot_id,),
    )
    data = (row or {}).get("Vehicle_photo")
    return data if data else None


def get_inventory_invoice_document(lot_id: int) -> Optional[dict[str, Any]]:
    return fetch_one(
        """
        SELECT i.Lot_id AS "Lot_id",
               i.Purchase_id AS "Purchase_id",
               p.Invoice_Document AS "Invoice_Document",
               p.Invoice_Document_name AS "Invoice_Document_name",
               p.Invoice_Document_type AS "Invoice_Document_type"
        FROM Raw_Material_Inventory i
        JOIN Raw_Material_Purchase p ON p.Purchase_id = i.Purchase_id
        WHERE i.Lot_id = ?
        """,
        (lot_id,),
    )


def set_lot_chemistry(material: str, lot_id: int, composition: dict[str, float]) -> None:
    """Write chemistry onto the parent raw-material master version for this lot."""
    set_raw_material_master_spec(material, composition)


def get_lot_chemistry(lot_id: int) -> dict[str, float]:
    """Grade specification for the material on this inventory lot."""
    row = fetch_one(
        """
        SELECT Raw_Material_Name AS "Raw_Material_Name"
        FROM Raw_Material_Inventory
        WHERE Lot_id = ?
        """,
        (lot_id,),
    )
    name = (row or {}).get("Raw_Material_Name")
    if not name:
        return {}
    return get_raw_material_master_spec(str(name))


# ---------- Alloys ----------

def add_alloy(
    cust_code: Optional[str],
    alloy_name: str,
    family: str,
    created_by: str,
    specs: dict[str, tuple[Optional[float], Optional[float]]],
    colour_code: Optional[str] = None,
    bis_designation: Optional[str] = None,
    revision_datetime: Optional[str] = None,
    remarks: Optional[str] = None,
    status: str = "Active",
    alloy_group: Optional[str] = None,
) -> int:
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Alloy_Master
                (Cust_code, Alloy_name, Alloy_Family, Alloy_group, Created_by, Created_at,
                 Colour_code, Bis_Designation,
                 Revision_datetime, Remarks, Status,
                 Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Alloy_id
            """,
            (
                cust_code,
                alloy_name,
                family,
                alloy_group,
                created_by,
                datetime.now().isoformat(timespec="seconds"),
                colour_code,
                bis_designation,
                revision_datetime,
                remarks,
                status,
                by_val,
                dt_val,
            ),
        )
        alloy_id = int(result.scalar_one())
        for sym, (mn, mx) in specs.items():
            if mn is None and mx is None:
                continue
            _exec(
                conn,
                """
                INSERT INTO Alloy_Master_spec
                    (Alloy_id, Element_symbol, Min_percent, Max_percent,
                     Last_updated_by, Last_updated_datetime)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (alloy_id, sym, round_percent_4(mn), round_percent_4(mx), by_val, dt_val),
            )
        return alloy_id


# ---------- Furnace ----------

def upsert_furnace(name: str, status: str) -> None:
    name = existing_text_key("Furnace_Master", "Furnace", name)
    execute(
        """
        INSERT INTO Furnace_Master (Furnace, Status) VALUES (?, ?)
        ON CONFLICT(Furnace) DO UPDATE SET Status = excluded.Status
        """,
        (name, status),
    )


def upsert_crucible(
    crucible_no: str,
    furnace: Optional[str],
    crucible_status: str,
    vendor_name: Optional[str],
) -> None:
    crucible_no = existing_text_key("Crucible_Master", "Crucible_no", crucible_no)
    if furnace:
        furnace = existing_text_key("Furnace_Master", "Furnace", furnace)
    if vendor_name:
        vendor_name = existing_text_key("Vendor_Master", "Vendor_name", vendor_name)
    if crucible_status not in CRUCIBLE_STATUS:
        raise ValueError(f"Crucible_status must be one of {CRUCIBLE_STATUS}.")
    if crucible_status == "Available":
        _require_single_available(furnace, crucible_no)
    execute(
        """
        INSERT INTO Crucible_Master
            (Crucible_no, furnace, Crucible_status, Vendor_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(Crucible_no) DO UPDATE SET
            furnace = excluded.furnace,
            Crucible_status = excluded.Crucible_status,
            Vendor_name = excluded.Vendor_name
        """,
        (crucible_no, furnace, crucible_status, vendor_name),
    )


def update_crucible_status(crucible_no: str, status: str) -> None:
    if status not in CRUCIBLE_STATUS:
        raise ValueError(f"Crucible_status must be one of {CRUCIBLE_STATUS}.")
    crucible_no = existing_text_key("Crucible_Master", "Crucible_no", crucible_no)
    if status == "Available":
        row = fetch_one(
            """
            SELECT furnace AS "furnace"
            FROM Crucible_Master
            WHERE LOWER(Crucible_no) = LOWER(?)
            """,
            (crucible_no,),
        )
        furnace = (row or {}).get("furnace")
        _require_single_available(furnace, crucible_no)
    execute(
        """
        UPDATE Crucible_Master
        SET Crucible_status = ?
        WHERE LOWER(Crucible_no) = LOWER(?)
        """,
        (status, crucible_no),
    )


def delete_crucible(crucible_no: str) -> None:
    execute(
        "DELETE FROM Crucible_Master WHERE LOWER(Crucible_no) = LOWER(?)",
        (crucible_no,),
    )


def upsert_melter(name: str, status: str) -> None:
    name = existing_text_key("Melter_Master", "Melter_Name", name)
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Melter_Master
            (Melter_Name, Status, Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(Melter_Name) DO UPDATE SET
            Status = excluded.Status,
            Last_updated_by = excluded.Last_updated_by,
            Last_updated_datetime = excluded.Last_updated_datetime
        """,
        (name, status, by_val, dt_val),
    )


def upsert_trolley(
    name: str,
    colour: Optional[str],
    weight: Optional[float],
    status: str,
) -> None:
    name = existing_text_key("Trolley_Master", "Trolley_name", name)
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Trolley_Master
            (Trolley_name, Colour, Weight, Status, Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(Trolley_name) DO UPDATE SET
            Colour=excluded.Colour, Weight=excluded.Weight, Status=excluded.Status,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (name, colour, weight, status, by_val, dt_val),
    )


def list_access_users() -> list[str]:
    """Names from Access_matrix for the sidebar acting-user picker."""
    try:
        rows = fetch_all(
            'SELECT Name AS "Name" FROM Access_matrix ORDER BY Name'
        )
        return [r["Name"] for r in rows if r.get("Name")]
    except Exception:
        return []


def is_admin_user(name: str | None = None) -> bool:
    """True when the acting user is Admin (Name or Access_matrix.Access)."""
    user = (name or get_acting_user() or "").strip()
    if not user:
        return False
    if user.lower() == "admin":
        return True
    try:
        row = fetch_one(
            """
            SELECT Name AS "Name", Access AS "Access"
            FROM Access_matrix
            WHERE LOWER(Name) = LOWER(?)
            """,
            (user,),
        )
    except Exception:
        return False
    if not row:
        return False
    access = str(row.get("Access") or "").strip().lower()
    return access in {"admin", "administrator"}


def k_mold_value(sampled_pcs: object, defect_pcs: object) -> float | None:
    """Defect pcs / sampled pcs. None when sampled pcs is missing or zero."""
    try:
        sampled = float(sampled_pcs)
        defect = float(defect_pcs)
    except (TypeError, ValueError):
        return None
    if sampled <= 0:
        return None
    return defect / sampled


def _is_missing_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def production_batch_completion_gaps(
    *,
    degassing_time: object = None,
    sampled_pcs: object = None,
    defect_pcs: object = None,
    top_sample: object = None,
    middle_sample: object = None,
    bottom_sample: object = None,
    vacum_sample: object = None,
    top_sample_datetime: object = None,
    middle_sample_datetime: object = None,
    bottom_sample_datetime: object = None,
    chemistry_count: int = 0,
    charge_line_count: int = 0,
) -> list[str]:
    """Return labels of fields that must be filled before Completed."""
    gaps: list[str] = []
    if _is_missing_value(degassing_time):
        gaps.append("Degassing time")
    try:
        sampled = float(sampled_pcs) if sampled_pcs not in (None, "") else None
    except (TypeError, ValueError):
        sampled = None
    if sampled is None or sampled <= 0:
        gaps.append("Sampled pcs")
        sampled = None
    if defect_pcs in (None, ""):
        gaps.append("Defect pcs")
        k_mold = None
    else:
        try:
            defect = float(defect_pcs)
        except (TypeError, ValueError):
            gaps.append("Defect pcs")
            k_mold = None
        else:
            k_mold = (defect / sampled) if sampled else None
    if k_mold is None:
        if sampled:
            gaps.append(f"K Mold Value (must be {K_MOLD_MAX:g} or below)")
    elif k_mold > K_MOLD_MAX:
        gaps.append(
            f"K Mold Value must be {K_MOLD_MAX:g} or below (currently {k_mold:.3f})"
        )
    if _is_missing_value(top_sample) or str(top_sample) not in SAMPLE_OK_STATUS:
        gaps.append("Top sample")
    if _is_missing_value(middle_sample) or str(middle_sample) not in SAMPLE_OK_STATUS:
        gaps.append("Middle sample")
    if _is_missing_value(bottom_sample) or str(bottom_sample) not in SAMPLE_OK_STATUS:
        gaps.append("Bottom sample")
    if _is_missing_value(top_sample_datetime):
        gaps.append("Top sample datetime")
    if _is_missing_value(middle_sample_datetime):
        gaps.append("Middle sample datetime")
    if _is_missing_value(bottom_sample_datetime):
        gaps.append("Bottom sample datetime")
    if _is_missing_value(vacum_sample) or str(vacum_sample) not in SAMPLE_OK_STATUS:
        gaps.append("Vacum sample")
    if int(chemistry_count or 0) < 1:
        gaps.append("Batch chemistry")
    if int(charge_line_count or 0) < 1:
        gaps.append("At least one charge line")
    return gaps


def production_batch_completion_gaps_for_id(batch_id: str) -> list[str]:
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    chem = get_batch_chemistry(batch_id)
    inputs = get_batch_inputs(batch_id)
    chem_n = 0
    for row in chem:
        try:
            if float(row.get("Percentage") or 0) > 0:
                chem_n += 1
        except (TypeError, ValueError):
            continue
    charge_n = 0
    for row in inputs:
        try:
            if float(row.get("Weight") or 0) > 0:
                charge_n += 1
        except (TypeError, ValueError):
            continue
    return production_batch_completion_gaps(
        degassing_time=batch.get("Degassing_time"),
        sampled_pcs=batch.get("Sampled_pcs"),
        defect_pcs=batch.get("Defect_pcs"),
        top_sample=batch.get("Top_Sample"),
        middle_sample=batch.get("Middle_Sample"),
        bottom_sample=batch.get("Bottom_Sample"),
        vacum_sample=batch.get("Vacum_Sample"),
        top_sample_datetime=batch.get("Top_Sample_datetime"),
        middle_sample_datetime=batch.get("Middle_Sample_datetime"),
        bottom_sample_datetime=batch.get("Bottom_Sample_datetime"),
        chemistry_count=chem_n,
        charge_line_count=charge_n,
    )


def require_completed_batch_for_output(batch_id: str) -> None:
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    if batch.get("Production_status") != BATCH_STATUS_COMPLETED:
        raise ValueError(
            f"Batch output can be entered only after {batch_id} is marked "
            "Completed on Production Batch & Chemistry."
        )


# ---------- Production batches ----------

def make_batch_id(furnace: str, heat_no: str | int) -> str:
    """Unique Batch ID = Furnace + Heat number (e.g. F3-H07)."""
    return f"F{furnace}-H{int(heat_no):02d}"


def _insert_charge_lines(conn: Connection, batch_id: str, inputs: list[dict[str, Any]]) -> None:
    for item in inputs:
        lot = _exec(
            conn,
            'SELECT Remaining_Weight AS "Remaining_Weight" '
            "FROM Raw_Material_Inventory WHERE Lot_id = ?",
            (item["Lot_id"],),
        ).mappings().first()
        if not lot:
            raise ValueError(f"Lot {item['Lot_id']} not found.")
        remaining = float(lot["Remaining_Weight"] or 0)
        w = float(item["Weight"])
        if w > remaining + 1e-9:
            raise ValueError(
                f"Insufficient stock on Lot {item['Lot_id']}: need {w}, have {remaining}."
            )
        _exec(
            conn,
            """
            UPDATE Raw_Material_Inventory
            SET Remaining_Weight = Remaining_Weight - ?
            WHERE Lot_id = ?
            """,
            (w, item["Lot_id"]),
        )
        _exec(
            conn,
            """
            INSERT INTO batch_input
                (Batch_ID, Raw_Material_Name, Lot_id, Weight,
                 Weighment_scale_weight, Trolley_weight, Trolley_name,
                 Charge_time, Notes, Weighment_scale_photo, Input_photo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                item["Raw_Material_Name"],
                item["Lot_id"],
                w,
                item.get("Weighment_scale_weight"),
                item.get("Trolley_weight"),
                item.get("Trolley_name"),
                item.get("Charge_time") or datetime.now().isoformat(timespec="seconds"),
                item.get("Notes", ""),
                item.get("Weighment_scale_photo"),
                item.get("Input_photo"),
            ),
        )


def _replace_batch_chemistry(
    conn: Connection, batch_id: str, composition: dict[str, float]
) -> None:
    _exec(
        conn,
        "DELETE FROM Batch_Chemical_Composition WHERE Batch_ID = ?",
        (batch_id,),
    )
    for sym, pct in composition.items():
        rounded = round_percent_4(pct)
        if rounded is None:
            continue
        _exec(
            conn,
            """
            INSERT INTO Batch_Chemical_Composition (Batch_ID, Element_symbol, Percentage)
            VALUES (?, ?, ?)
            """,
            (batch_id, sym, rounded),
        )


def _validate_batch_samples_and_supervisor(
    top_sample: Optional[str],
    middle_sample: Optional[str],
    bottom_sample: Optional[str],
    vacum_sample: Optional[str],
    production_supervisor: Optional[str],
) -> None:
    for label, value in (
        ("Top_Sample", top_sample),
        ("Middle_Sample", middle_sample),
        ("Bottom_Sample", bottom_sample),
        ("Vacum_Sample", vacum_sample),
    ):
        if value and value not in SAMPLE_OK_STATUS:
            raise ValueError(f"{label} must be one of {SAMPLE_OK_STATUS}.")
    if production_supervisor:
        supervisors = list_production_supervisors(active_only=False)
        if production_supervisor not in supervisors:
            raise ValueError(
                f"Production supervisor '{production_supervisor}' is not in Production_supervisor."
            )


def create_batch(
    furnace: str,
    heat_no: str | int,
    alloy_id: Optional[int],
    production_date: str,
    shift: str,
    melt_no: int,
    melting_team: str,
    notes: str,
    inputs: list[dict[str, Any]],
    composition: dict[str, float],
    degassing_time: Optional[str] = None,
    sampled_pcs: Optional[float] = None,
    defect_pcs: Optional[float] = None,
    top_sample: Optional[str] = None,
    middle_sample: Optional[str] = None,
    bottom_sample: Optional[str] = None,
    vacum_sample: Optional[str] = None,
    top_sample_remarks: Optional[str] = None,
    middle_sample_remarks: Optional[str] = None,
    bottom_sample_remarks: Optional[str] = None,
    top_sample_datetime: Optional[str] = None,
    middle_sample_datetime: Optional[str] = None,
    bottom_sample_datetime: Optional[str] = None,
    production_supervisor: Optional[str] = None,
) -> str:
    batch_id = make_batch_id(furnace, heat_no)
    existing = fetch_one(
        'SELECT Batch_ID AS "Batch_ID" FROM Production_batch WHERE Batch_ID = ?',
        (batch_id,),
    )
    if existing:
        raise ValueError(f"Batch ID {batch_id} already exists. Choose another Heat No.")

    _validate_batch_samples_and_supervisor(
        top_sample,
        middle_sample,
        bottom_sample,
        vacum_sample,
        production_supervisor,
    )

    crucible_no = require_available_crucible(furnace)

    with get_connection() as conn:
        _exec(
            conn,
            """
            INSERT INTO Production_batch
                (Batch_ID, Alloy_id, Production_Date, Shift, Furnace, Crucible_no, Melt_No,
                 Heat_no, Melting_team, Notes, Production_status, Workflow_stage,
                 Degassing_time, Sampled_pcs, Defect_pcs,
                 Top_Sample, Middle_Sample, Bottom_Sample, Vacum_Sample,
                 Top_Sample_Remarks, Middle_Sample_Remarks, Bottom_Sample_Remarks,
                 Top_Sample_datetime, Middle_Sample_datetime, Bottom_Sample_datetime,
                 Production_supervisor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'In-Progress', 'Raw Material',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                alloy_id,
                production_date,
                shift,
                furnace,
                crucible_no,
                melt_no,
                str(heat_no),
                melting_team,
                notes,
                degassing_time,
                sampled_pcs,
                defect_pcs,
                top_sample,
                middle_sample,
                bottom_sample,
                vacum_sample,
                top_sample_remarks,
                middle_sample_remarks,
                bottom_sample_remarks,
                top_sample_datetime,
                middle_sample_datetime,
                bottom_sample_datetime,
                production_supervisor,
            ),
        )
        _insert_charge_lines(conn, batch_id, inputs)
        _replace_batch_chemistry(conn, batch_id, composition)

    return batch_id


def update_production_batch_input(
    batch_id: str,
    *,
    alloy_id: Optional[int],
    production_date: str,
    shift: str,
    melt_no: int,
    melting_team: str,
    notes: str,
    extra_inputs: list[dict[str, Any]],
    composition: dict[str, float],
    degassing_time: Optional[str] = None,
    sampled_pcs: Optional[float] = None,
    defect_pcs: Optional[float] = None,
    top_sample: Optional[str] = None,
    middle_sample: Optional[str] = None,
    bottom_sample: Optional[str] = None,
    vacum_sample: Optional[str] = None,
    top_sample_remarks: Optional[str] = None,
    middle_sample_remarks: Optional[str] = None,
    bottom_sample_remarks: Optional[str] = None,
    top_sample_datetime: Optional[str] = None,
    middle_sample_datetime: Optional[str] = None,
    bottom_sample_datetime: Optional[str] = None,
    production_supervisor: Optional[str] = None,
    allow_completed: bool = False,
) -> None:
    """Update header, samples, chemistry, and append extra charge lines."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    status = batch.get("Production_status")
    if status == BATCH_STATUS_COMPLETED and not allow_completed:
        raise ValueError(
            f"Batch {batch_id} is Completed and cannot be edited. "
            "An Admin can unlock it on Production Batch & Chemistry to correct history."
        )
    if status == BATCH_STATUS_COMPLETED and allow_completed and not is_admin_user():
        raise ValueError("Only Admin can correct a Completed batch.")

    _validate_batch_samples_and_supervisor(
        top_sample,
        middle_sample,
        bottom_sample,
        vacum_sample,
        production_supervisor,
    )

    with get_connection() as conn:
        _exec(
            conn,
            """
            UPDATE Production_batch SET
                Alloy_id = ?, Production_Date = ?, Shift = ?, Melt_No = ?,
                Melting_team = ?, Notes = ?,
                Degassing_time = ?, Sampled_pcs = ?, Defect_pcs = ?,
                Top_Sample = ?, Middle_Sample = ?, Bottom_Sample = ?, Vacum_Sample = ?,
                Top_Sample_Remarks = ?, Middle_Sample_Remarks = ?, Bottom_Sample_Remarks = ?,
                Top_Sample_datetime = ?, Middle_Sample_datetime = ?, Bottom_Sample_datetime = ?,
                Production_supervisor = ?
            WHERE Batch_ID = ?
            """,
            (
                alloy_id,
                production_date,
                shift,
                melt_no,
                melting_team,
                notes,
                degassing_time,
                sampled_pcs,
                defect_pcs,
                top_sample,
                middle_sample,
                bottom_sample,
                vacum_sample,
                top_sample_remarks,
                middle_sample_remarks,
                bottom_sample_remarks,
                top_sample_datetime,
                middle_sample_datetime,
                bottom_sample_datetime,
                production_supervisor,
                batch_id,
            ),
        )
        if extra_inputs:
            _insert_charge_lines(conn, batch_id, extra_inputs)
        _replace_batch_chemistry(conn, batch_id, composition)


def complete_production_batch(batch_id: str) -> None:
    """Set production_status to Completed after required fields are present."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    if batch.get("Production_status") == BATCH_STATUS_COMPLETED:
        return
    gaps = production_batch_completion_gaps_for_id(batch_id)
    if gaps:
        raise ValueError(
            "Cannot mark Completed until these are entered: " + "; ".join(gaps)
        )
    execute(
        "UPDATE Production_batch SET Production_status = ? WHERE Batch_ID = ?",
        (BATCH_STATUS_COMPLETED, batch_id),
    )
    release_finished_goods_for_batch(batch_id)


def update_batch_workflow(
    batch_id: str,
    workflow_stage: str,
    qa_status: Optional[str] = None,
) -> None:
    """Update workflow stage only. Production_status is set on the chemistry page."""
    del qa_status
    execute(
        "UPDATE Production_batch SET Workflow_stage = ? WHERE Batch_ID = ?",
        (workflow_stage, batch_id),
    )


def release_finished_goods_for_batch(batch_id: str) -> int:
    """Flip Under_Testing bundles for a batch to Available. Returns rows updated."""
    return execute(
        """
        UPDATE Finished_Goods_Inventory
        SET Finished_Goods_Status = ?
        WHERE Batch_ID = ? AND Finished_Goods_Status = ?
        """,
        (FG_STATUS_AVAILABLE, batch_id, FG_STATUS_UNDER_TESTING),
    )


def add_finished_goods_bundle(
    batch_id: str,
    output_weight: Optional[float] = None,
    output_pieces: Optional[int] = None,
) -> int:
    """Create a finished-goods bundle locked as Under_Testing until the batch is Completed."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    pieces = _as_whole_pieces(output_pieces)

    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Finished_Goods_Inventory
                (Batch_ID, Output_Weight, Output_pieces, Finished_Goods_Status)
            VALUES (?, ?, ?, ?)
            RETURNING Bundle_id
            """,
            (
                batch_id,
                output_weight,
                pieces,
                FG_STATUS_UNDER_TESTING,
            ),
        )
        row = result.first()
        return int(row[0])


def list_finished_goods(
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    assignable_only: bool = False,
) -> list[dict[str, Any]]:
    """List finished-goods bundles. assignable_only=True returns Available only."""
    sql = """
        SELECT fg.Bundle_id AS "Bundle_id", fg.Batch_ID AS "Batch_ID",
               fg.Output_Weight AS "Output_Weight", fg.Output_pieces AS "Output_pieces",
               fg.Finished_Goods_Status AS "Finished_Goods_Status",
               a.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group"
        FROM Finished_Goods_Inventory fg
        LEFT JOIN Production_batch b ON b.Batch_ID = fg.Batch_ID
        LEFT JOIN Alloy_Master a ON a.Alloy_id = b.Alloy_id
        WHERE 1=1
    """
    params: list[Any] = []
    if batch_id:
        sql += " AND fg.Batch_ID = ?"
        params.append(batch_id)
    if assignable_only:
        sql += " AND fg.Finished_Goods_Status = ?"
        params.append(FG_STATUS_AVAILABLE)
    elif status:
        sql += " AND fg.Finished_Goods_Status = ?"
        params.append(status)
    sql += " ORDER BY fg.Bundle_id DESC"
    return fetch_all(sql, params)


def assign_finished_goods_bundle(bundle_id: int) -> None:
    """Assign a bundle. Under_Testing (and non-Available) stock is locked."""
    row = fetch_one(
        """
        SELECT Bundle_id AS "Bundle_id",
               Finished_Goods_Status AS "Finished_Goods_Status"
        FROM Finished_Goods_Inventory WHERE Bundle_id = ?
        """,
        (bundle_id,),
    )
    if not row:
        raise ValueError(f"Bundle {bundle_id} not found.")
    if row["Finished_Goods_Status"] != FG_STATUS_AVAILABLE:
        raise ValueError(
            f"Bundle {bundle_id} cannot be assigned — status is "
            f"'{row['Finished_Goods_Status']}' (must be Available). "
            "Mark the production batch Completed to release Under_Testing stock."
        )
    execute(
        """
        UPDATE Finished_Goods_Inventory
        SET Finished_Goods_Status = ? WHERE Bundle_id = ?
        """,
        (FG_STATUS_ASSIGNED, bundle_id),
    )


def _norm_match_text(value: object) -> str:
    return str(value or "").strip().casefold()


def _product_output_totals_on_conn(
    conn: Connection, batch_id: str, product_alloy_id: object
) -> tuple[float, Optional[int]]:
    try:
        aid = int(product_alloy_id)
    except (TypeError, ValueError):
        return 0.0, None
    row = (
        _exec(
            conn,
            """
            SELECT COALESCE(SUM(Weight), 0) AS "Weight",
                   COALESCE(SUM(Pieces), 0) AS "Pieces"
            FROM batch_output
            WHERE Batch_ID = ? AND Alloy_id = ?
            """,
            (batch_id, aid),
        )
        .mappings()
        .first()
    )
    if not row:
        return 0.0, None
    weight = float(row["Weight"] or 0)
    try:
        pieces = int(row["Pieces"] or 0)
    except (TypeError, ValueError):
        pieces = 0
    return weight, (pieces if pieces > 0 else None)


def sync_finished_goods_from_output(batch_id: str) -> Optional[int]:
    """Create or update the product-alloy FG bundle from batch_output."""
    with get_connection() as conn:
        return _sync_finished_goods_from_output(conn, batch_id)


def _sync_finished_goods_from_output(conn: Connection, batch_id: str) -> Optional[int]:
    batch = (
        _exec(
            conn,
            """
            SELECT Batch_ID AS "Batch_ID", Alloy_id AS "Alloy_id",
                   Production_status AS "Production_status"
            FROM Production_batch WHERE Batch_ID = ?
            """,
            (batch_id,),
        )
        .mappings()
        .first()
    )
    if not batch or batch.get("Alloy_id") in (None, ""):
        return None
    weight, pieces = _product_output_totals_on_conn(conn, batch_id, batch["Alloy_id"])
    if weight <= 0:
        return None
    status = (
        FG_STATUS_AVAILABLE
        if batch.get("Production_status") == BATCH_STATUS_COMPLETED
        else FG_STATUS_UNDER_TESTING
    )
    existing = list(
        _exec(
            conn,
            """
            SELECT Bundle_id AS "Bundle_id",
                   Finished_Goods_Status AS "Finished_Goods_Status"
            FROM Finished_Goods_Inventory
            WHERE Batch_ID = ?
            ORDER BY Bundle_id DESC
            """,
            (batch_id,),
        ).mappings()
    )
    locked = {
        FG_STATUS_DISPATCHED,
        FG_STATUS_REJECTED,
    }
    if any((row["Finished_Goods_Status"] or "") in locked for row in existing):
        return int(existing[0]["Bundle_id"]) if existing else None
    editable = [
        row
        for row in existing
        if (row["Finished_Goods_Status"] or "")
        in {FG_STATUS_AVAILABLE, FG_STATUS_UNDER_TESTING, FG_STATUS_ASSIGNED}
    ]
    if editable:
        bundle_id = int(editable[0]["Bundle_id"])
        _exec(
            conn,
            """
            UPDATE Finished_Goods_Inventory
            SET Output_Weight = ?, Output_pieces = ?, Finished_Goods_Status = ?
            WHERE Bundle_id = ?
            """,
            (weight, pieces, status, bundle_id),
        )
        return bundle_id
    result = _exec(
        conn,
        """
        INSERT INTO Finished_Goods_Inventory
            (Batch_ID, Output_Weight, Output_pieces, Finished_Goods_Status)
        VALUES (?, ?, ?, ?)
        RETURNING Bundle_id
        """,
        (batch_id, weight, pieces, status),
    )
    row = result.first()
    return int(row[0]) if row else None


def backfill_finished_goods_from_output() -> int:
    """Ensure every completed heat with product output has an FG bundle."""
    batches = fetch_all(
        """
        SELECT Batch_ID AS "Batch_ID"
        FROM Production_batch
        WHERE Production_status = ?
        """,
        (BATCH_STATUS_COMPLETED,),
    )
    updated = 0
    with get_connection() as conn:
        for row in batches:
            if _sync_finished_goods_from_output(conn, row["Batch_ID"]):
                updated += 1
    return updated


def _ensure_packing_list_ready() -> None:
    with get_connection() as conn:
        _ensure_packing_list(conn)


def list_packing_po_numbers() -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT p.Customer_PO_No AS "Customer_PO_No"
        FROM Purchase_Order p
        WHERE COALESCE(p.Purchase_Order_Status, 'Open') <> 'Cancelled'
        ORDER BY p.Customer_PO_No
        """
    )
    return [str(r["Customer_PO_No"]) for r in rows if r.get("Customer_PO_No")]


def list_packing_po_customers(po_no: str) -> list[dict[str, Any]]:
    """Customers on a PO, names taken from Customer_Master when possible."""
    return fetch_all(
        """
        SELECT DISTINCT
            COALESCE(c.Cust_code, p.Cust_code) AS "Cust_code",
            COALESCE(c.Customer_name, p.Customer_name) AS "Customer_name"
        FROM Purchase_Order p
        LEFT JOIN Customer_Master c ON LOWER(c.Cust_code) = LOWER(p.Cust_code)
        WHERE p.Customer_PO_No = ?
        ORDER BY COALESCE(c.Customer_name, p.Customer_name)
        """,
        (po_no,),
    )


def list_packing_po_alloys(
    po_no: str, cust_code: Optional[str] = None
) -> list[dict[str, Any]]:
    sql = """
        SELECT DISTINCT p.Alloy_Id AS "Alloy_id",
               a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group",
               a.Colour_code AS "Colour_code"
        FROM Purchase_Order p
        LEFT JOIN Alloy_Master a ON a.Alloy_id = p.Alloy_Id
        WHERE p.Customer_PO_No = ?
    """
    params: list[Any] = [po_no]
    if cust_code:
        sql += " AND LOWER(p.Cust_code) = LOWER(?)"
        params.append(cust_code)
    sql += " ORDER BY a.Alloy_name"
    return fetch_all(sql, params)


def _alloy_matches_target(
    batch_alloy: dict[str, Any],
    target: dict[str, Any],
    *,
    match_name: bool,
    match_group: bool,
) -> bool:
    if batch_alloy.get("Alloy_id") not in (None, "") and target.get("Alloy_id") not in (
        None,
        "",
    ):
        try:
            if int(batch_alloy["Alloy_id"]) == int(target["Alloy_id"]):
                return True
        except (TypeError, ValueError):
            pass
    batch_name = _norm_match_text(batch_alloy.get("Alloy_name"))
    batch_group = _norm_match_text(batch_alloy.get("Alloy_group"))
    target_name = _norm_match_text(target.get("Alloy_name"))
    target_group = _norm_match_text(target.get("Alloy_group"))
    if match_name and target_name:
        if batch_name == target_name:
            return True
        if batch_group and batch_group == target_name:
            return True
    if match_group and target_group:
        if batch_group == target_group:
            return True
        if batch_name and batch_name == target_group:
            return True
    return False


def list_dispatchable_batches(
    alloy_id: int,
    *,
    match_name: bool = True,
    match_group: bool = True,
    include_batch_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Batches whose alloy_name or alloy_group matches the selected PO alloy."""
    _ensure_packing_list_ready()
    target = get_alloy(alloy_id)
    if not target:
        raise ValueError(f"Alloy {alloy_id} was not found.")
    include = {str(b) for b in (include_batch_ids or []) if b}
    verified = fetch_all(
        """
        SELECT lb.Batch_ID AS "Batch_ID"
        FROM Packing_list_batch lb
        JOIN Packing_list p ON p.Packing_list_id = lb.Packing_list_id
        WHERE p.Packing_list_status = ?
        """,
        (PACKING_STATUS_VERIFIED,),
    )
    taken = {str(r["Batch_ID"]) for r in verified} - include
    rows = fetch_all(
        """
        SELECT b.Batch_ID AS "Batch_ID",
               a.Alloy_id AS "Alloy_id",
               a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group",
               fg.Bundle_id AS "Bundle_id",
               fg.Output_Weight AS "Output_Weight",
               fg.Output_pieces AS "Output_pieces",
               fg.Finished_Goods_Status AS "Finished_Goods_Status"
        FROM Production_batch b
        JOIN Alloy_Master a ON a.Alloy_id = b.Alloy_id
        JOIN Finished_Goods_Inventory fg ON fg.Batch_ID = b.Batch_ID
        WHERE b.Production_status = ?
        ORDER BY b.Batch_ID, fg.Bundle_id DESC
        """,
        (BATCH_STATUS_COMPLETED,),
    )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        bid = str(row["Batch_ID"])
        if bid in seen:
            continue
        seen.add(bid)
        if bid in taken:
            continue
        status = row.get("Finished_Goods_Status") or ""
        if bid not in include and status not in FG_DISPATCHABLE_STATUSES:
            continue
        if not _alloy_matches_target(
            row, target, match_name=match_name, match_group=match_group
        ):
            continue
        out.append(row)
    return out


def list_packing_lists() -> list[dict[str, Any]]:
    _ensure_packing_list_ready()
    return fetch_all(
        """
        SELECT p.Packing_list_id AS "Packing_list_id",
               p.Invoice_date AS "Invoice_date",
               p.Invoice_number AS "Invoice_number",
               p.Customer_PO_No AS "Customer_PO_No",
               p.Cust_code AS "Cust_code",
               p.Customer_name AS "Customer_name",
               p.Alloy_id AS "Alloy_id",
               a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group",
               p.Colour_code AS "Colour_code",
               p.Vehicle_no AS "Vehicle_no",
               p.Packing_list_status AS "Packing_list_status",
               (
                   SELECT COUNT(*) FROM Packing_list_batch lb
                   WHERE lb.Packing_list_id = p.Packing_list_id
               ) AS "Batch_count"
        FROM Packing_list p
        LEFT JOIN Alloy_Master a ON a.Alloy_id = p.Alloy_id
        ORDER BY p.Packing_list_id DESC
        """
    )


def get_packing_list(packing_list_id: int) -> Optional[dict[str, Any]]:
    _ensure_packing_list_ready()
    header = fetch_one(
        """
        SELECT p.Packing_list_id AS "Packing_list_id",
               p.Invoice_date AS "Invoice_date",
               p.Invoice_number AS "Invoice_number",
               p.Customer_PO_No AS "Customer_PO_No",
               p.Cust_code AS "Cust_code",
               p.Customer_name AS "Customer_name",
               p.Alloy_id AS "Alloy_id",
               a.Alloy_name AS "Alloy_name",
               a.Alloy_group AS "Alloy_group",
               p.Colour_code AS "Colour_code",
               p.Vehicle_no AS "Vehicle_no",
               p.Packing_list_status AS "Packing_list_status"
        FROM Packing_list p
        LEFT JOIN Alloy_Master a ON a.Alloy_id = p.Alloy_id
        WHERE p.Packing_list_id = ?
        """,
        (packing_list_id,),
    )
    if not header:
        return None
    header["batches"] = fetch_all(
        """
        SELECT Batch_ID AS "Batch_ID"
        FROM Packing_list_batch
        WHERE Packing_list_id = ?
        ORDER BY Batch_ID
        """,
        (packing_list_id,),
    )
    return header


def _dispatch_batches_on_conn(
    conn: Connection, batch_ids: list[str], *, dispatch: bool
) -> None:
    if not batch_ids:
        return
    placeholders = ", ".join("?" for _ in batch_ids)
    if dispatch:
        _exec(
            conn,
            f"""
            UPDATE Finished_Goods_Inventory
            SET Finished_Goods_Status = ?
            WHERE Batch_ID IN ({placeholders})
              AND Finished_Goods_Status IN (?, ?)
            """,
            (
                FG_STATUS_DISPATCHED,
                *batch_ids,
                FG_STATUS_AVAILABLE,
                FG_STATUS_ASSIGNED,
            ),
        )
        return
    other_verified = {
        str(r["Batch_ID"])
        for r in _exec(
            conn,
            """
            SELECT lb.Batch_ID AS "Batch_ID"
            FROM Packing_list_batch lb
            JOIN Packing_list p ON p.Packing_list_id = lb.Packing_list_id
            WHERE p.Packing_list_status = ?
            """,
            (PACKING_STATUS_VERIFIED,),
        ).mappings()
    }
    restore = [b for b in batch_ids if b not in other_verified]
    if not restore:
        return
    rest_ph = ", ".join("?" for _ in restore)
    _exec(
        conn,
        f"""
        UPDATE Finished_Goods_Inventory
        SET Finished_Goods_Status = ?
        WHERE Batch_ID IN ({rest_ph})
          AND Finished_Goods_Status = ?
        """,
        (FG_STATUS_AVAILABLE, *restore, FG_STATUS_DISPATCHED),
    )


def save_packing_list(
    *,
    packing_list_id: Optional[int] = None,
    invoice_date: Optional[str],
    invoice_number: str,
    customer_po_no: str,
    cust_code: Optional[str],
    customer_name: Optional[str],
    alloy_id: int,
    colour_code: Optional[str],
    vehicle_no: Optional[str],
    packing_list_status: str,
    batch_ids: list[str],
) -> int:
    """Create or update a packing list. Verified lists dispatch FG stock."""
    _ensure_packing_list_ready()
    status = (packing_list_status or "").strip()
    if status not in PACKING_LIST_STATUS:
        raise ValueError(
            f"Packing_list_status must be one of {', '.join(PACKING_LIST_STATUS)}."
        )
    invoice = (invoice_number or "").strip()
    if not invoice:
        raise ValueError("Invoice number is required.")
    po_no = (customer_po_no or "").strip()
    if not po_no:
        raise ValueError("P.O. Number is required.")
    aid = int(alloy_id)
    alloys = list_packing_po_alloys(po_no, cust_code)
    if not any(int(r["Alloy_id"]) == aid for r in alloys if r.get("Alloy_id") not in (None, "")):
        raise ValueError(
            f"Alloy {aid} is not on purchase order {po_no}."
        )
    unique_batches = []
    seen: set[str] = set()
    for bid in batch_ids:
        text = str(bid or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique_batches.append(text)
    if status == PACKING_STATUS_VERIFIED and not unique_batches:
        raise ValueError("Select at least one batch_id before marking Verified.")

    previous = get_packing_list(packing_list_id) if packing_list_id else None
    if packing_list_id and not previous:
        raise ValueError(f"Packing list {packing_list_id} was not found.")
    previous_batches = [
        str(r["Batch_ID"]) for r in (previous.get("batches") or [])
    ] if previous else []
    previous_status = (previous or {}).get("Packing_list_status")
    if status == PACKING_STATUS_VERIFIED:
        for bid in unique_batches:
            if bid in previous_batches and previous_status == PACKING_STATUS_VERIFIED:
                continue
            rows = list_finished_goods(batch_id=bid)
            if not any(
                (r.get("Finished_Goods_Status") or "") in FG_DISPATCHABLE_STATUSES
                for r in rows
            ):
                raise ValueError(
                    f"No Available finished goods for {bid}. "
                    "Save product output on Batch Output first."
                )
    by_val, dt_val = audit_stamp()

    with get_connection() as conn:
        if packing_list_id:
            result = _exec(
                conn,
                """
                UPDATE Packing_list SET
                    Invoice_date = ?, Invoice_number = ?, Customer_PO_No = ?,
                    Cust_code = ?, Customer_name = ?, Alloy_id = ?,
                    Colour_code = ?, Vehicle_no = ?, Packing_list_status = ?,
                    Last_updated_by = ?, Last_updated_datetime = ?
                WHERE Packing_list_id = ?
                """,
                (
                    invoice_date,
                    invoice,
                    po_no,
                    cust_code,
                    customer_name,
                    aid,
                    colour_code,
                    (vehicle_no or "").strip() or None,
                    status,
                    by_val,
                    dt_val,
                    packing_list_id,
                ),
            )
            pid = int(packing_list_id)
            _exec(
                conn,
                "DELETE FROM Packing_list_batch WHERE Packing_list_id = ?",
                (pid,),
            )
        else:
            result = _exec(
                conn,
                """
                INSERT INTO Packing_list (
                    Invoice_date, Invoice_number, Customer_PO_No, Cust_code,
                    Customer_name, Alloy_id, Colour_code, Vehicle_no,
                    Packing_list_status, Last_updated_by, Last_updated_datetime
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING Packing_list_id
                """,
                (
                    invoice_date,
                    invoice,
                    po_no,
                    cust_code,
                    customer_name,
                    aid,
                    colour_code,
                    (vehicle_no or "").strip() or None,
                    status,
                    by_val,
                    dt_val,
                ),
            )
            row = result.first()
            pid = int(row[0])
        for bid in unique_batches:
            _exec(
                conn,
                """
                INSERT INTO Packing_list_batch (Packing_list_id, Batch_ID)
                VALUES (?, ?)
                """,
                (pid, bid),
            )
        if previous_status == PACKING_STATUS_VERIFIED:
            dropped = (
                previous_batches
                if status != PACKING_STATUS_VERIFIED
                else [b for b in previous_batches if b not in unique_batches]
            )
            _dispatch_batches_on_conn(conn, dropped, dispatch=False)
        if status == PACKING_STATUS_VERIFIED:
            _dispatch_batches_on_conn(conn, unique_batches, dispatch=True)
    return pid


_BATCH_COLUMNS = """
    Batch_ID AS "Batch_ID", Alloy_id AS "Alloy_id",
    Production_Date AS "Production_Date", Shift AS "Shift",
    Furnace AS "Furnace", Crucible_no AS "Crucible_no",
    Melt_No AS "Melt_No", Heat_no AS "Heat_no",
    Melting_team AS "Melting_team",
    COALESCE(
        (SELECT SUM(Weight) FROM batch_input i WHERE i.Batch_ID = Production_batch.Batch_ID),
        0
    ) AS "Input_Weight",
    COALESCE(
        (SELECT SUM(Weight) FROM batch_output o WHERE o.Batch_ID = Production_batch.Batch_ID),
        0
    ) AS "Output_Weight",
    COALESCE(
        (SELECT SUM(Pieces) FROM batch_output o WHERE o.Batch_ID = Production_batch.Batch_ID),
        0
    ) AS "Output_pieces",
    Notes AS "Notes", Production_status AS "Production_status",
    Workflow_stage AS "Workflow_stage",
    Degassing_time AS "Degassing_time",
    Sampled_pcs AS "Sampled_pcs", Defect_pcs AS "Defect_pcs",
    Top_Sample AS "Top_Sample", Middle_Sample AS "Middle_Sample",
    Bottom_Sample AS "Bottom_Sample", Vacum_Sample AS "Vacum_Sample",
    Top_Sample_Remarks AS "Top_Sample_Remarks",
    Middle_Sample_Remarks AS "Middle_Sample_Remarks",
    Bottom_Sample_Remarks AS "Bottom_Sample_Remarks",
    Top_Sample_datetime AS "Top_Sample_datetime",
    Middle_Sample_datetime AS "Middle_Sample_datetime",
    Bottom_Sample_datetime AS "Bottom_Sample_datetime",
    Production_supervisor AS "Production_supervisor"
"""


def get_batch(batch_id: str) -> Optional[dict[str, Any]]:
    return fetch_one(
        f"SELECT {_BATCH_COLUMNS} FROM Production_batch WHERE Batch_ID = ?",
        (batch_id,),
    )


def get_batch_inputs(batch_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT Raw_Material_Name AS "Raw_Material_Name", Lot_id AS "Lot_id",
               Weight AS "Weight",
               Weighment_scale_weight AS "Weighment_scale_weight",
               Trolley_weight AS "Trolley_weight",
               Trolley_name AS "Trolley_name",
               Charge_time AS "Charge_time", Notes AS "Notes"
        FROM batch_input WHERE Batch_ID = ?
        ORDER BY Charge_time
        """,
        (batch_id,),
    )


def alloy_piece_avg_kg(net_weight: object, pieces: object) -> float | None:
    """Net output weight divided by piece count, rounded to 0.01 kg."""
    try:
        weight = float(net_weight)
        count = int(round(float(pieces)))
    except (TypeError, ValueError):
        return None
    if weight <= 0 or count <= 0:
        return None
    return round(weight / count, 2)


def alloy_piece_avg_out_of_range(
    avg_kg: object, alloy_id: object = None
) -> bool:
    """True when a product-alloy piece average is outside 5.6–6.1 kg."""
    if avg_kg is None:
        return False
    try:
        avg = float(avg_kg)
    except (TypeError, ValueError):
        return False
    if alloy_id is not None and is_sidestream_alloy(alloy_id):
        return False
    return avg < ALLOY_PIECE_KG_MIN or avg > ALLOY_PIECE_KG_MAX


def is_sidestream_alloy(alloy_id: object) -> bool:
    try:
        return int(alloy_id) in SIDESTREAM_ALLOY_IDS
    except (TypeError, ValueError):
        return False


def allowed_batch_output_alloy_ids(batch_alloy_id: Optional[int]) -> set[int]:
    allowed = set(SIDESTREAM_ALLOY_IDS)
    if batch_alloy_id:
        allowed.add(int(batch_alloy_id))
    return allowed


def list_batch_output_alloys(batch_alloy_id: Optional[int]) -> list[dict[str, Any]]:
    """Product alloy (if any) plus Broken Ingot / Furnace Empty / Not Ok Ingot."""
    ids: list[int] = []
    if batch_alloy_id:
        ids.append(int(batch_alloy_id))
    for sid in SIDESTREAM_ALLOY_IDS:
        if sid not in ids:
            ids.append(sid)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = fetch_all(
        f"""
        SELECT Alloy_id AS "Alloy_id", Alloy_name AS "Alloy_name"
        FROM Alloy_Master
        WHERE Alloy_id IN ({placeholders})
        """,
        tuple(ids),
    )
    by_id = {int(r["Alloy_id"]): r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def _as_sql_photo(value: Any) -> bytes | None:
    if value is None or value == "":
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    elif isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes) and value:
        return value
    return None


_BATCH_OUTPUT_COST_SELECT = """
               o.cost_of_production_per_kg AS "cost_of_production_per_kg",
               o.cost_of_production_overall_per_kg AS "cost_of_production_overall_per_kg",
               o.conversion_rate_applied AS "conversion_rate_applied",
               o.conversion_expense_month AS "conversion_expense_month"
"""


def _as_cost_4(value: Any) -> float:
    rounded = round_percent_4(value)
    return 0.0 if rounded is None else rounded


def _iso_date_or_none(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else None


def _as_month_start(value: Any) -> Optional[date]:
    """First day of the calendar month for a stored date, or None if missing."""
    text = _as_effective_date(value)
    if len(text) < 7:
        return None
    try:
        return date(int(text[:4]), int(text[5:7]), 1)
    except ValueError:
        return None


def conversion_cutoff_for_batch(batch_id: str) -> date:
    """Production-month start for conversion lookup (this month if the date is missing)."""
    row = fetch_one(
        'SELECT Production_Date AS "Production_Date" FROM Production_batch WHERE Batch_ID = ?',
        (batch_id,),
    )
    month = _as_month_start(row["Production_Date"] if row else None)
    return month or date.today().replace(day=1)


def _conversion_cutoff_on_conn(conn: Connection, batch_id: str) -> date:
    row = (
        _exec(
            conn,
            'SELECT Production_Date AS "Production_Date" FROM Production_batch WHERE Batch_ID = ?',
            (batch_id,),
        )
        .mappings()
        .first()
    )
    month = _as_month_start(row["Production_Date"] if row else None)
    return month or date.today().replace(day=1)


def get_latest_cost_of_conversion(
    as_of: date | str | None = None,
) -> Optional[dict[str, Any]]:
    """Conversion row for as_of's month, else the latest earlier month.

    Production-month rates are preferred. If that month is not on file yet
    (typical in the first week), the previous available month is used.
    """
    if as_of is None:
        as_of_iso = date.today().replace(day=1).isoformat()
    else:
        month = _as_month_start(as_of)
        as_of_iso = (month or date.today().replace(day=1)).isoformat()
    return fetch_one(
        """
        SELECT id AS "id",
               expense_month AS "expense_month",
               oil_rate_per_kg AS "oil_rate_per_kg",
               electricity_rate_per_kg AS "electricity_rate_per_kg",
               labour_rate_per_kg AS "labour_rate_per_kg",
               salaries_rate_per_kg AS "salaries_rate_per_kg",
               consumables_rate_per_kg AS "consumables_rate_per_kg",
               overheads_rate_per_kg AS "overheads_rate_per_kg",
               total_conversion_rate_per_kg AS "total_conversion_rate_per_kg"
        FROM Cost_of_conversion
        WHERE expense_month <= ?
        ORDER BY expense_month DESC, id DESC
        LIMIT 1
        """,
        (as_of_iso,),
    )


def _latest_conversion_on_conn(
    conn: Connection, as_of: date
) -> tuple[float, Optional[str]]:
    cutoff = _as_month_start(as_of) or date.today().replace(day=1)
    row = (
        _exec(
            conn,
            """
            SELECT total_conversion_rate_per_kg AS "total_conversion_rate_per_kg",
                   expense_month AS "expense_month"
            FROM Cost_of_conversion
            WHERE expense_month <= ?
            ORDER BY expense_month DESC, id DESC
            LIMIT 1
            """,
            (cutoff.isoformat(),),
        )
        .mappings()
        .first()
    )
    if not row:
        return 0.0, None
    return _as_cost_4(row["total_conversion_rate_per_kg"]), _iso_date_or_none(
        row["expense_month"]
    )


def _batch_input_material_cost_on_conn(conn: Connection, batch_id: str) -> float:
    row = (
        _exec(
            conn,
            """
            SELECT COALESCE(SUM(i.Weight * COALESCE(inv.Cost_per_kg, 0)), 0)
                   AS "input_cost"
            FROM batch_input i
            LEFT JOIN Raw_Material_Inventory inv ON inv.Lot_id = i.Lot_id
            WHERE i.Batch_ID = ?
            """,
            (batch_id,),
        )
        .mappings()
        .first()
    )
    return _as_cost_4(row["input_cost"] if row else 0)


def _batch_output_weight_on_conn(conn: Connection, batch_id: str) -> float:
    row = (
        _exec(
            conn,
            """
            SELECT COALESCE(SUM(Weight), 0) AS "output_kg"
            FROM batch_output
            WHERE Batch_ID = ?
            """,
            (batch_id,),
        )
        .mappings()
        .first()
    )
    return float(row["output_kg"] or 0) if row else 0.0


def compute_batch_production_cost(
    batch_id: str, *, as_of: date | None = None
) -> dict[str, Any]:
    """Material + conversion unit costs using the production month, else the previous month."""
    as_of = as_of or conversion_cutoff_for_batch(batch_id)
    input_row = fetch_one(
        """
        SELECT COALESCE(SUM(i.Weight * COALESCE(inv.Cost_per_kg, 0)), 0)
               AS "input_cost"
        FROM batch_input i
        LEFT JOIN Raw_Material_Inventory inv ON inv.Lot_id = i.Lot_id
        WHERE i.Batch_ID = ?
        """,
        (batch_id,),
    )
    output_row = fetch_one(
        """
        SELECT COALESCE(SUM(Weight), 0) AS "output_kg"
        FROM batch_output
        WHERE Batch_ID = ?
        """,
        (batch_id,),
    )
    input_cost = _as_cost_4(input_row["input_cost"] if input_row else 0)
    output_kg = float(output_row["output_kg"] or 0) if output_row else 0.0
    conversion = get_latest_cost_of_conversion(as_of)
    conversion_rate = _as_cost_4(
        conversion["total_conversion_rate_per_kg"] if conversion else 0
    )
    conversion_month = _iso_date_or_none(
        conversion["expense_month"] if conversion else None
    )
    material_per_kg = (
        round(input_cost / output_kg, PERCENT_SCALE) if output_kg > 0 else None
    )
    overall_per_kg = (
        round(material_per_kg + conversion_rate, PERCENT_SCALE)
        if material_per_kg is not None
        else None
    )
    return {
        "input_cost_total": input_cost,
        "output_weight_total": output_kg,
        "cost_of_production_per_kg": material_per_kg,
        "conversion_rate_applied": conversion_rate if conversion else 0.0,
        "conversion_expense_month": conversion_month,
        "cost_of_production_overall_per_kg": overall_per_kg,
    }


def _apply_batch_output_costs(
    conn: Connection, batch_id: str, *, as_of: date | None = None
) -> None:
    """Write the same batch-level unit costs onto every output line."""
    cutoff = as_of or _conversion_cutoff_on_conn(conn, batch_id)
    output_kg = _batch_output_weight_on_conn(conn, batch_id)
    if output_kg <= 0:
        return
    input_cost = _batch_input_material_cost_on_conn(conn, batch_id)
    material_per_kg = round(input_cost / output_kg, PERCENT_SCALE)
    conversion_rate, conversion_month = _latest_conversion_on_conn(conn, cutoff)
    overall_per_kg = round(material_per_kg + conversion_rate, PERCENT_SCALE)
    _exec(
        conn,
        """
        UPDATE batch_output
        SET cost_of_production_per_kg = ?,
            cost_of_production_overall_per_kg = ?,
            conversion_rate_applied = ?,
            conversion_expense_month = ?
        WHERE Batch_ID = ?
        """,
        (
            material_per_kg,
            overall_per_kg,
            conversion_rate,
            conversion_month,
            batch_id,
        ),
    )


def _backfill_batch_output_production_costs(conn: Connection) -> None:
    rows = list(
        _exec(
            conn,
            """
            SELECT DISTINCT Batch_ID AS "Batch_ID"
            FROM batch_output
            WHERE cost_of_production_per_kg IS NULL
            """,
        ).mappings()
    )
    for row in rows:
        _apply_batch_output_costs(conn, row["Batch_ID"])


def refresh_outputs_missing_conversion_rate() -> int:
    """Recompute conversion on saved outputs (production month, else previous month)."""
    rows = fetch_all(
        """
        SELECT DISTINCT Batch_ID AS "Batch_ID"
        FROM batch_output
        """
    )
    if not rows:
        return 0
    with get_connection() as conn:
        for row in rows:
            _apply_batch_output_costs(conn, row["Batch_ID"])
    return len(rows)


def get_batch_outputs(
    batch_id: str, *, include_photos: bool = False
) -> list[dict[str, Any]]:
    photo_sql = ""
    if include_photos:
        photo_sql = """
               , o.Weighment_scale_photo AS "Weighment_scale_photo",
               o.Output_photo AS "Output_photo"
        """
    return fetch_all(
        f"""
        SELECT o.Output_id AS "Output_id", o.Batch_ID AS "Batch_ID",
               o.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               o.Weight AS "Weight",
               o.Weighment_scale_weight AS "Weighment_scale_weight",
               o.Stand_weight AS "Stand_weight",
               o.Pieces AS "Pieces",
               o.Notes AS "Notes", o.Output_time AS "Output_time",
               {_BATCH_OUTPUT_COST_SELECT},
               (o.Weighment_scale_photo IS NOT NULL) AS "Has_scale_photo",
               (o.Output_photo IS NOT NULL) AS "Has_output_photo"
               {photo_sql}
        FROM batch_output o
        LEFT JOIN Alloy_Master a ON a.Alloy_id = o.Alloy_id
        WHERE o.Batch_ID = ?
        ORDER BY o.Output_id
        """,
        (batch_id,),
    )


def list_all_batch_outputs(limit: int = 200) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT o.Output_id AS "Output_id", o.Batch_ID AS "Batch_ID",
               b.Furnace AS "Furnace", b.Heat_no AS "Heat_no",
               o.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               o.Weight AS "Weight",
               o.Weighment_scale_weight AS "Weighment_scale_weight",
               o.Stand_weight AS "Stand_weight",
               o.Pieces AS "Pieces",
               o.Notes AS "Notes", o.Output_time AS "Output_time",
               o.cost_of_production_per_kg AS "cost_of_production_per_kg",
               o.cost_of_production_overall_per_kg AS "cost_of_production_overall_per_kg",
               o.conversion_rate_applied AS "conversion_rate_applied",
               o.conversion_expense_month AS "conversion_expense_month",
               (o.Weighment_scale_photo IS NOT NULL) AS "Has_scale_photo",
               (o.Output_photo IS NOT NULL) AS "Has_output_photo"
        FROM batch_output o
        LEFT JOIN Alloy_Master a ON a.Alloy_id = o.Alloy_id
        LEFT JOIN Production_batch b ON b.Batch_ID = o.Batch_ID
        ORDER BY o.Output_id DESC
        LIMIT ?
        """,
        (limit,),
    )


def save_batch_outputs(batch_id: str, lines: list[dict[str, Any]]) -> None:
    """Replace all output rows for a batch. Weight > 0 lines are kept."""
    require_completed_batch_for_output(batch_id)
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")
    allowed = allowed_batch_output_alloy_ids(batch.get("Alloy_id"))
    cleaned: list[dict[str, Any]] = []
    for line in lines:
        try:
            alloy_id = int(line["Alloy_id"])
        except (TypeError, ValueError, KeyError):
            continue
        scale = float(line.get("Weighment_scale_weight") or 0)
        stand = float(line.get("Stand_weight") or 0)
        if scale > 0:
            weight = max(scale - stand, 0.0)
        else:
            weight = float(line.get("Weight") or 0)
        if weight <= 0:
            continue
        if alloy_id not in allowed:
            raise ValueError(
                f"Alloy {alloy_id} is not an allowed output for batch {batch_id}. "
                "Use the batch alloy or Broken Ingot / Furnace Empty / Not Ok Ingot."
            )
        pieces_raw = line.get("Pieces")
        pieces = _as_whole_pieces(pieces_raw)
        cleaned.append(
            {
                "Alloy_id": alloy_id,
                "Weight": weight,
                "Weighment_scale_weight": scale if scale > 0 else None,
                "Stand_weight": stand if stand > 0 else (0.0 if scale > 0 else None),
                "Pieces": pieces,
                "Notes": (str(line.get("Notes") or "")).strip() or None,
                "Weighment_scale_photo": _as_sql_photo(
                    line.get("Weighment_scale_photo")
                ),
                "Output_photo": _as_sql_photo(line.get("Output_photo")),
            }
        )

    stamp = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        _exec(conn, "DELETE FROM batch_output WHERE Batch_ID = ?", (batch_id,))
        for row in cleaned:
            _exec(
                conn,
                """
                INSERT INTO batch_output
                    (Batch_ID, Alloy_id, Weight, Weighment_scale_weight, Stand_weight,
                     Pieces, Notes, Output_time, Weighment_scale_photo, Output_photo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    row["Alloy_id"],
                    row["Weight"],
                    row["Weighment_scale_weight"],
                    row["Stand_weight"],
                    row["Pieces"],
                    row["Notes"],
                    stamp,
                    row["Weighment_scale_photo"],
                    row["Output_photo"],
                ),
            )
        if cleaned:
            _apply_batch_output_costs(conn, batch_id)
        _sync_sidestream_inventory(conn, batch_id)
        _sync_finished_goods_from_output(conn, batch_id)


def _sqlite_internal_remelt_purchase_id(conn: Connection) -> int:
    row = (
        _exec(
            conn,
            """
            SELECT Purchase_id AS "Purchase_id"
            FROM Raw_Material_Purchase
            WHERE Supplier_Invoice = 'INTERNAL-REMELT'
            """,
        )
        .mappings()
        .first()
    )
    if row:
        return int(row["Purchase_id"])
    by_val, dt_val = audit_stamp()
    result = _exec(
        conn,
        """
        INSERT INTO Raw_Material_Purchase
            (Vendor_code, Supplier_Invoice, Supplier_invoice_date, Received_date,
             Last_updated_by, Last_updated_datetime)
        VALUES (NULL, 'INTERNAL-REMELT', NULL, ?, ?, ?)
        RETURNING Purchase_id
        """,
        (date.today().isoformat(), by_val, dt_val),
    )
    return int(result.scalar_one())


def _sync_sidestream_inventory(conn: Connection, batch_id: str) -> None:
    """Turn Broken Ingot / Furnace Empty / Not Ok Ingot outputs into remelt lots."""
    placeholders = ", ".join("?" for _ in SIDESTREAM_ALLOY_IDS)
    totals = list(
        _exec(
            conn,
            f"""
            SELECT o.Alloy_id AS "Alloy_id",
                   COALESCE(SUM(o.Weight), 0) AS "Weight",
                   MAX(o.cost_of_production_overall_per_kg)
                       AS "cost_of_production_overall_per_kg"
            FROM batch_output o
            WHERE o.Batch_ID = ?
              AND o.Alloy_id IN ({placeholders})
            GROUP BY o.Alloy_id
            """,
            (batch_id, *SIDESTREAM_ALLOY_IDS),
        ).mappings()
    )
    wanted = {
        int(row["Alloy_id"]): (
            float(row["Weight"] or 0),
            _as_cost_4(row["cost_of_production_overall_per_kg"]),
        )
        for row in totals
        if float(row["Weight"] or 0) > 0
    }
    existing = list(
        _exec(
            conn,
            """
            SELECT Lot_id AS "Lot_id",
                   Source_Alloy_id AS "Source_Alloy_id",
                   Received_weight AS "Received_weight",
                   Remaining_Weight AS "Remaining_Weight"
            FROM Raw_Material_Inventory
            WHERE Source_Batch_ID = ?
            """,
            (batch_id,),
        ).mappings()
    )
    by_alloy = {int(row["Source_Alloy_id"]): dict(row) for row in existing if row.get("Source_Alloy_id") is not None}

    purchase_id = None if IS_POSTGRES else _sqlite_internal_remelt_purchase_id(conn)

    for alloy_id, (weight, cost_per_kg) in wanted.items():
        rm_name = _sidestream_rm_name_on_conn(conn, alloy_id)
        current = by_alloy.get(alloy_id)
        if current is None:
            _exec(
                conn,
                """
                INSERT INTO Raw_Material_Inventory
                    (Purchase_id, Raw_Material_Name, Received_weight, Remaining_Weight,
                     Storage_bay, Raw_Material_Status, Cost_per_kg,
                     Source_Batch_ID, Source_Alloy_id)
                VALUES (?, ?, ?, ?, 'Remelt', 'Ready For Melt', ?, ?, ?)
                """,
                (
                    purchase_id,
                    rm_name,
                    weight,
                    weight,
                    cost_per_kg,
                    batch_id,
                    alloy_id,
                ),
            )
            continue
        old_received = float(current["Received_weight"] or 0)
        remaining = float(current["Remaining_Weight"] or 0)
        consumed = max(old_received - remaining, 0.0)
        if weight + 1e-9 < consumed:
            raise ValueError(
                f"Cannot reduce {rm_name} on {batch_id} to {weight:.2f} kg: "
                f"{consumed:.2f} kg from this lot has already been charged into another heat."
            )
        _exec(
            conn,
            """
            UPDATE Raw_Material_Inventory
            SET Raw_Material_Name = ?,
                Received_weight = ?,
                Remaining_Weight = ?,
                Cost_per_kg = ?,
                Raw_Material_Status = 'Ready For Melt',
                Storage_bay = COALESCE(Storage_bay, 'Remelt')
            WHERE Lot_id = ?
            """,
            (rm_name, weight, weight - consumed, cost_per_kg, current["Lot_id"]),
        )

    for alloy_id, current in by_alloy.items():
        if alloy_id in wanted:
            continue
        old_received = float(current["Received_weight"] or 0)
        remaining = float(current["Remaining_Weight"] or 0)
        consumed = max(old_received - remaining, 0.0)
        if consumed > 1e-9:
            raise ValueError(
                f"Cannot remove remelt lot {current['Lot_id']} for batch {batch_id}: "
                f"{consumed:.2f} kg has already been charged into another heat."
            )
        _exec(
            conn,
            "DELETE FROM Raw_Material_Inventory WHERE Lot_id = ?",
            (current["Lot_id"],),
        )


def get_batch_chemistry(batch_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT s.Element_symbol AS "Element_symbol", s.Percentage AS "Percentage"
        FROM Batch_Chemical_Composition s
        LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
        WHERE s.Batch_ID = ?
        ORDER BY COALESCE(_el.Serial_no, 9999), s.Element_symbol
        """,
        (batch_id,),
    )


def list_batches() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT b.Batch_ID AS "Batch_ID", b.Production_Date AS "Production_Date",
               b.Furnace AS "Furnace", b.Crucible_no AS "Crucible_no",
               b.Heat_no AS "Heat_no", b.Melt_No AS "Melt_No",
               b.Shift AS "Shift", b.Alloy_id AS "Alloy_id",
               COALESCE(
                   (SELECT SUM(i.Weight) FROM batch_input i WHERE i.Batch_ID = b.Batch_ID),
                   0
               ) AS "Input_Weight",
               COALESCE(
                   (SELECT SUM(o.Weight) FROM batch_output o WHERE o.Batch_ID = b.Batch_ID),
                   0
               ) AS "Output_Weight",
               COALESCE(
                   (SELECT SUM(o.Pieces) FROM batch_output o WHERE o.Batch_ID = b.Batch_ID),
                   0
               ) AS "Output_pieces",
               b.Production_status AS "Production_status",
               b.Workflow_stage AS "Workflow_stage", a.Alloy_name AS "Alloy_name",
               b.Production_supervisor AS "Production_supervisor",
               b.Top_Sample AS "Top_Sample", b.Middle_Sample AS "Middle_Sample",
               b.Bottom_Sample AS "Bottom_Sample", b.Vacum_Sample AS "Vacum_Sample"
        FROM Production_batch b
        LEFT JOIN Alloy_Master a ON a.Alloy_id = b.Alloy_id
        ORDER BY b.Production_Date DESC, b.Batch_ID DESC
        """
    )


def calc_yield(input_weight: float, output_weight: float) -> dict[str, float]:
    recovery = (output_weight / input_weight * 100.0) if input_weight > 0 else 0.0
    return {
        "input_weight": input_weight,
        "output_weight": output_weight,
        "recovery_pct": recovery,
        "loss_kg": input_weight - output_weight,
    }


# ---------- Purchase orders ----------

PO_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")


def list_purchase_orders() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT p.Customer_PO_No AS "Customer_PO_No",
               p.Cust_code AS "Cust_code",
               p.Customer_name AS "Customer_name",
               p.Alloy_Id AS "Alloy_Id",
               a.Alloy_name AS "Alloy_name",
               p.Order_Date AS "Order_Date",
               p.Delivery_Date AS "Delivery_Date",
               p.Order_Qty AS "Order_Qty",
               p.Rate AS "Rate",
               p.Billing_Address AS "Billing_Address",
               p.Billing_City AS "Billing_City",
               p.Billing_state AS "Billing_state",
               p.Billing_Pincode AS "Billing_Pincode",
               p.Billing_country AS "Billing_country",
               p.Shipping_address AS "Shipping_address",
               p.Shipping_City AS "Shipping_City",
               p.Shipping_state AS "Shipping_state",
               p.Shipping_Pincode AS "Shipping_Pincode",
               p.Shipping_country AS "Shipping_country",
               p.PO_Document_name AS "PO_Document_name",
               p.PO_Document_type AS "PO_Document_type",
               COALESCE(p.Purchase_Order_Status, 'Open') AS "Purchase_Order_Status",
               CASE WHEN p.PO_Document IS NULL THEN 0 ELSE 1 END AS "Has_Document"
        FROM Purchase_Order p
        LEFT JOIN Alloy_Master a ON a.Alloy_id = p.Alloy_Id
        ORDER BY p.Order_Date DESC, p.Customer_PO_No, p.Alloy_Id
        """
    )


def _require_alloy_id(alloy_id: Any) -> int:
    if alloy_id is None or alloy_id == "":
        raise ValueError(
            "Alloy is required. Each purchase-order line is identified by "
            "Customer PO No and Alloy."
        )
    try:
        return int(alloy_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Alloy is required.") from exc


def update_purchase_order_status(
    customer_po_no: str, status: str, alloy_id: int
) -> None:
    status = (status or "").strip()
    if status not in PURCHASE_ORDER_STATUS:
        raise ValueError(
            f"Purchase_Order_Status must be one of {', '.join(PURCHASE_ORDER_STATUS)}."
        )
    po_no = (customer_po_no or "").strip()
    if not po_no:
        raise ValueError("Customer PO No is required.")
    alloy = _require_alloy_id(alloy_id)
    n = execute(
        """
        UPDATE Purchase_Order
        SET Purchase_Order_Status = ?
        WHERE Customer_PO_No = ? AND Alloy_Id = ?
        """,
        (status, po_no, alloy),
    )
    if n == 0:
        raise ValueError(f"Purchase order '{po_no}' for alloy {alloy} not found.")


_PO_UPSERT_SQL = """
        INSERT INTO Purchase_Order
            (Customer_PO_No, Cust_code, Customer_name, Alloy_Id,
             Order_Date, Delivery_Date, Order_Qty, Rate,
             Billing_Address, Billing_City, Billing_state, Billing_Pincode, Billing_country,
             Shipping_address, Shipping_City, Shipping_state, Shipping_Pincode, Shipping_country,
             Purchase_Order_Status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Customer_PO_No, Alloy_Id) DO UPDATE SET
            Cust_code=excluded.Cust_code,
            Customer_name=excluded.Customer_name,
            Order_Date=excluded.Order_Date,
            Delivery_Date=excluded.Delivery_Date,
            Order_Qty=excluded.Order_Qty,
            Rate=excluded.Rate,
            Billing_Address=excluded.Billing_Address,
            Billing_City=excluded.Billing_City,
            Billing_state=excluded.Billing_state,
            Billing_Pincode=excluded.Billing_Pincode,
            Billing_country=excluded.Billing_country,
            Shipping_address=excluded.Shipping_address,
            Shipping_City=excluded.Shipping_City,
            Shipping_state=excluded.Shipping_state,
            Shipping_Pincode=excluded.Shipping_Pincode,
            Shipping_country=excluded.Shipping_country,
            Purchase_Order_Status=excluded.Purchase_Order_Status
        """


def _purchase_order_status(data: dict[str, Any]) -> str:
    status = (data.get("Purchase_Order_Status") or "Open").strip()
    if status not in PURCHASE_ORDER_STATUS:
        raise ValueError(
            f"Purchase_Order_Status must be one of {', '.join(PURCHASE_ORDER_STATUS)}."
        )
    return status


def _purchase_order_header(data: dict[str, Any]) -> tuple[str, str]:
    po_no = existing_text_key(
        "Purchase_Order", "Customer_PO_No", data.get("Customer_PO_No")
    )
    if not po_no:
        raise ValueError("Customer PO No is required.")
    if not data.get("Cust_code"):
        raise ValueError("Customer is required.")
    if not data.get("Customer_name"):
        raise ValueError("Customer name is required.")
    return po_no, _purchase_order_status(data)


def _upsert_purchase_order_row(
    conn: Connection, data: dict[str, Any], po_no: str, alloy_id: int, status: str
) -> None:
    _exec(
        conn,
        _PO_UPSERT_SQL,
        (
            po_no,
            data["Cust_code"],
            data["Customer_name"],
            alloy_id,
            data.get("Order_Date"),
            data.get("Delivery_Date"),
            data.get("Order_Qty"),
            data.get("Rate"),
            data.get("Billing_Address"),
            data.get("Billing_City"),
            data.get("Billing_state"),
            data.get("Billing_Pincode"),
            data.get("Billing_country"),
            data.get("Shipping_address"),
            data.get("Shipping_City"),
            data.get("Shipping_state"),
            data.get("Shipping_Pincode"),
            data.get("Shipping_country"),
            status,
        ),
    )


def upsert_purchase_order(data: dict[str, Any]) -> None:
    """Insert or update a purchase-order line keyed on (Customer_PO_No, Alloy_Id)."""
    po_no, status = _purchase_order_header(data)
    alloy_id = _require_alloy_id(data.get("Alloy_Id"))
    with get_connection() as conn:
        _upsert_purchase_order_row(conn, data, po_no, alloy_id, status)


def upsert_purchase_order_lines(
    header: dict[str, Any], lines: list[dict[str, Any]]
) -> list[int]:
    """Save several alloy lines for one customer PO. Returns the alloy ids written."""
    po_no, status = _purchase_order_header(header)
    if not lines:
        raise ValueError("Add at least one alloy line with qty greater than zero.")

    prepared: list[tuple[int, dict[str, Any]]] = []
    seen: set[int] = set()
    for line in lines:
        alloy_id = _require_alloy_id(line.get("Alloy_Id"))
        if alloy_id in seen:
            raise ValueError(
                f"Alloy {alloy_id} is listed more than once on this purchase order."
            )
        try:
            qty = float(line.get("Order_Qty") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Order qty must be a number.") from exc
        if qty <= 0:
            raise ValueError("Each alloy line needs an order qty greater than zero.")
        seen.add(alloy_id)
        prepared.append((alloy_id, line))

    with get_connection() as conn:
        for alloy_id, line in prepared:
            row = {**header, **line}
            _upsert_purchase_order_row(conn, row, po_no, alloy_id, status)
    return [alloy_id for alloy_id, _ in prepared]


def save_po_document(
    customer_po_no: str,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    alloy_id: Optional[int] = None,
) -> None:
    """Attach a PDF/Word/Excel purchase-order document to an existing PO line."""
    if not customer_po_no:
        raise ValueError("Customer PO No is required.")
    alloy = _require_alloy_id(alloy_id)
    if not file_bytes:
        raise ValueError("Document file is empty.")
    name = (filename or "").strip()
    lower = name.lower()
    if not any(lower.endswith(ext) for ext in PO_DOCUMENT_EXTENSIONS):
        raise ValueError(
            "PO document must be PDF, Word (.doc/.docx), or Excel (.xls/.xlsx)."
        )
    existing = fetch_one(
        """
        SELECT Customer_PO_No AS "Customer_PO_No"
        FROM Purchase_Order
        WHERE Customer_PO_No = ? AND Alloy_Id = ?
        """,
        (customer_po_no, alloy),
    )
    if not existing:
        raise ValueError(f"Purchase order '{customer_po_no}' for alloy {alloy} not found.")
    execute(
        """
        UPDATE Purchase_Order
        SET PO_Document = ?, PO_Document_name = ?, PO_Document_type = ?
        WHERE Customer_PO_No = ? AND Alloy_Id = ?
        """,
        (file_bytes, name, content_type or "", customer_po_no, alloy),
    )


def get_po_document(customer_po_no: str, alloy_id: int) -> Optional[dict[str, Any]]:
    alloy = _require_alloy_id(alloy_id)
    return fetch_one(
        """
        SELECT Customer_PO_No AS "Customer_PO_No",
               Alloy_Id AS "Alloy_Id",
               PO_Document AS "PO_Document",
               PO_Document_name AS "PO_Document_name",
               PO_Document_type AS "PO_Document_type"
        FROM Purchase_Order
        WHERE Customer_PO_No = ? AND Alloy_Id = ?
        """,
        (customer_po_no, alloy),
    )


def clear_po_document(customer_po_no: str, alloy_id: int) -> None:
    alloy = _require_alloy_id(alloy_id)
    execute(
        """
        UPDATE Purchase_Order
        SET PO_Document = NULL, PO_Document_name = NULL, PO_Document_type = NULL
        WHERE Customer_PO_No = ? AND Alloy_Id = ?
        """,
        (customer_po_no, alloy),
    )


# ---------- Furnace oil ----------

FURNACE_OIL_PURCHASE_TYPES = ("Purchase", "Opening")


def _oil_qty(value: Any) -> float:
    try:
        qty = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return qty if qty > 0 else 0.0


def rebuild_furnace_oil_inventory() -> None:
    """Rebuild the daily inventory ledger from purchases and consumption."""
    by_val, dt_val = audit_stamp()
    purchases = fetch_all(
        """
        SELECT Received_date AS "Received_date", Quantity AS "Quantity",
               Purchase_type AS "Purchase_type"
        FROM Furnace_Oil_Purchase
        """
    )
    consumed = fetch_all(
        """
        SELECT Consumption_date AS "Consumption_date", Quantity AS "Quantity"
        FROM Furnace_Oil_Consumption
        """
    )
    days: dict[str, dict[str, float]] = {}

    def _day(key: str) -> dict[str, float]:
        return days.setdefault(key, {"opening": 0.0, "purchase": 0.0, "consumption": 0.0})

    for row in purchases:
        key = _as_effective_date(row.get("Received_date"))
        if not key:
            continue
        qty = _oil_qty(row.get("Quantity"))
        if (row.get("Purchase_type") or "Purchase") == "Opening":
            _day(key)["opening"] += qty
        else:
            _day(key)["purchase"] += qty
    for row in consumed:
        key = _as_effective_date(row.get("Consumption_date"))
        if not key:
            continue
        _day(key)["consumption"] += _oil_qty(row.get("Quantity"))

    ledger: list[tuple[str, float, float, float, float]] = []
    carried = 0.0
    for key in sorted(days):
        rec = days[key]
        opening = rec["opening"] if rec["opening"] > 0 else carried
        closing = opening + rec["purchase"] - rec["consumption"]
        ledger.append((key, opening, rec["purchase"], rec["consumption"], closing))
        carried = closing

    with get_connection() as conn:
        _exec(conn, "DELETE FROM Furnace_Oil_Inventory")
        for key, opening, purchase, consumption, closing in ledger:
            _exec(
                conn,
                """
                INSERT INTO Furnace_Oil_Inventory
                    (Inventory_date, Opening_qty, Purchase_qty, Consumption_qty,
                     Closing_qty, Last_updated_by, Last_updated_datetime)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key, opening, purchase, consumption, closing, by_val, dt_val),
            )


def get_furnace_oil_stock() -> float:
    row = fetch_one(
        """
        SELECT Closing_qty AS "Closing_qty"
        FROM Furnace_Oil_Inventory
        ORDER BY Inventory_date DESC
        LIMIT 1
        """
    )
    if not row:
        return 0.0
    try:
        return float(row.get("Closing_qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def list_furnace_oil_purchases(limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT p.Purchase_id AS "Purchase_id",
               p.Purchase_type AS "Purchase_type",
               v.Vendor_name AS "Vendor_name",
               p.Vendor_code AS "Vendor_code",
               p.Supplier_Invoice AS "Supplier_Invoice",
               p.Supplier_invoice_date AS "Supplier_invoice_date",
               p.Received_date AS "Received_date",
               p.Quantity AS "Quantity",
               p.Weight_in_kgs AS "Weight_in_kgs",
               p.Rate_per_litre AS "Rate_per_litre",
               p.Storage_tank AS "Storage_tank",
               p.Invoice_Document_name AS "Invoice_Document_name",
               p.Weighment_slip_name AS "Weighment_slip_name",
               p.Notes AS "Notes"
        FROM Furnace_Oil_Purchase p
        LEFT JOIN Vendor_Master v ON v.Vendor_code = p.Vendor_code
        ORDER BY p.Received_date DESC, p.Purchase_id DESC
        LIMIT ?
        """,
        (limit,),
    )


def list_furnace_oil_consumption(limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT Consumption_date AS "Consumption_date",
               Quantity AS "Quantity",
               Notes AS "Notes"
        FROM Furnace_Oil_Consumption
        ORDER BY Consumption_date DESC
        LIMIT ?
        """,
        (limit,),
    )


def list_furnace_oil_inventory(limit: int = 90) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT Inventory_date AS "Inventory_date",
               Opening_qty AS "Opening_qty",
               Purchase_qty AS "Purchase_qty",
               Consumption_qty AS "Consumption_qty",
               Closing_qty AS "Closing_qty"
        FROM Furnace_Oil_Inventory
        ORDER BY Inventory_date DESC
        LIMIT ?
        """,
        (limit,),
    )


def add_furnace_oil_purchase(
    vendor_code: Optional[int],
    invoice: str,
    invoice_date: str,
    received_date: str,
    quantity: float,
    rate_per_litre: Optional[float] = None,
    weight_in_kgs: Optional[float] = None,
    storage_tank: Optional[str] = None,
    notes: Optional[str] = None,
    invoice_document: Optional[bytes] = None,
    invoice_document_name: Optional[str] = None,
    invoice_document_type: Optional[str] = None,
    weighment_slip: Optional[bytes] = None,
    weighment_slip_name: Optional[str] = None,
    weighment_slip_type: Optional[str] = None,
    purchase_type: str = "Purchase",
) -> int:
    qty = _oil_qty(quantity)
    if qty <= 0:
        raise ValueError("Quantity (litres) must be greater than zero.")
    kind = (purchase_type or "Purchase").strip()
    if kind not in FURNACE_OIL_PURCHASE_TYPES:
        raise ValueError("Purchase type must be Purchase or Opening.")
    if kind == "Purchase" and not (invoice or "").strip():
        raise ValueError("Vendor invoice is required.")
    if invoice_document and invoice_document_name:
        _validate_invoice_document_name(invoice_document_name)
    if weighment_slip and weighment_slip_name:
        _validate_invoice_document_name(weighment_slip_name)
    weight = _oil_qty(weight_in_kgs) or None
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Furnace_Oil_Purchase
                (Vendor_code, Supplier_Invoice, Supplier_invoice_date, Received_date,
                 Quantity, Weight_in_kgs, Rate_per_litre, Storage_tank, Purchase_type,
                 Invoice_Document, Invoice_Document_name, Invoice_Document_type,
                 Weighment_slip, Weighment_slip_name, Weighment_slip_type,
                 Notes, Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Purchase_id
            """,
            (
                vendor_code,
                (invoice or "").strip() or ("OPENING" if kind == "Opening" else ""),
                invoice_date,
                received_date,
                qty,
                weight,
                rate_per_litre,
                (storage_tank or "").strip() or None,
                kind,
                invoice_document,
                invoice_document_name,
                invoice_document_type,
                weighment_slip,
                weighment_slip_name,
                weighment_slip_type,
                (notes or "").strip() or None,
                by_val,
                dt_val,
            ),
        )
        purchase_id = int(result.scalar_one())
    rebuild_furnace_oil_inventory()
    return purchase_id


def get_furnace_oil_consumption_row(consumption_date: str) -> Optional[dict[str, Any]]:
    return fetch_one(
        """
        SELECT Consumption_date AS "Consumption_date", Quantity AS "Quantity"
        FROM Furnace_Oil_Consumption
        WHERE Consumption_date = ?
        """,
        (consumption_date,),
    )


def add_furnace_oil_consumption(
    consumption_date: str,
    quantity: float,
    notes: Optional[str] = None,
) -> None:
    qty = _oil_qty(quantity)
    if qty <= 0:
        raise ValueError("Consumption (litres) must be greater than zero.")
    day = _as_effective_date(consumption_date)
    if not day:
        raise ValueError("Consumption date is required.")
    existing = get_furnace_oil_consumption_row(day)
    available = get_furnace_oil_stock() + _oil_qty(existing.get("Quantity") if existing else 0)
    if qty > available + 1e-9:
        raise ValueError(
            f"Consumption {qty:g} L exceeds available stock {available:g} L."
        )
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Furnace_Oil_Consumption
            (Consumption_date, Quantity, Notes, Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(Consumption_date) DO UPDATE SET
            Quantity=excluded.Quantity,
            Notes=excluded.Notes,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (day, qty, (notes or "").strip() or None, by_val, dt_val),
    )
    rebuild_furnace_oil_inventory()


def furnace_oil_month_totals(year: int, month: int) -> dict[str, float]:
    prefix = f"{year:04d}-{month:02d}"
    purchased = fetch_one(
        """
        SELECT COALESCE(SUM(Quantity), 0) AS "qty"
        FROM Furnace_Oil_Purchase
        WHERE Purchase_type = 'Purchase' AND Received_date LIKE ?
        """,
        (f"{prefix}%",),
    )
    used = fetch_one(
        """
        SELECT COALESCE(SUM(Quantity), 0) AS "qty"
        FROM Furnace_Oil_Consumption
        WHERE Consumption_date LIKE ?
        """,
        (f"{prefix}%",),
    )
    return {
        "purchased": float((purchased or {}).get("qty") or 0),
        "consumed": float((used or {}).get("qty") or 0),
    }


# ---------- Electricity ----------

def normalize_electricity_line(line: Optional[str]) -> str:
    text = ("" if line is None else str(line)).strip()
    folded = " ".join(text.lower().split())
    aliases = {
        "eb line 1": ELECTRICITY_LINE_1,
        "eb lin1": ELECTRICITY_LINE_1,
        "eb line1": ELECTRICITY_LINE_1,
        "line 1": ELECTRICITY_LINE_1,
        "line1": ELECTRICITY_LINE_1,
        "1": ELECTRICITY_LINE_1,
        "eb line 2": ELECTRICITY_LINE_2,
        "eb line2": ELECTRICITY_LINE_2,
        "line 2": ELECTRICITY_LINE_2,
        "line2": ELECTRICITY_LINE_2,
        "2": ELECTRICITY_LINE_2,
    }
    if folded in aliases:
        return aliases[folded]
    raise ValueError("Select EB Line 1 or EB Line 2.")


def get_electricity_consumption(
    consumption_date: str, line: str
) -> Optional[dict[str, Any]]:
    day = _as_effective_date(consumption_date)
    if not day:
        return None
    line_name = normalize_electricity_line(line)
    return fetch_one(
        """
        SELECT Consumption_date AS "Consumption_date",
               Line AS "Line",
               Opening_reading AS "Opening_reading",
               Closing_reading AS "Closing_reading",
               Units_consumed AS "Units_consumed",
               Notes AS "Notes"
        FROM Electricity_Consumption
        WHERE Consumption_date = ? AND Line = ?
        """,
        (day, line_name),
    )


def get_previous_electricity_closing(
    consumption_date: str, line: str
) -> Optional[float]:
    """Latest closing reading for this line before this date."""
    day = _as_effective_date(consumption_date)
    if not day:
        return None
    line_name = normalize_electricity_line(line)
    row = fetch_one(
        """
        SELECT Closing_reading AS "Closing_reading"
        FROM Electricity_Consumption
        WHERE Consumption_date < ? AND Line = ?
        ORDER BY Consumption_date DESC
        LIMIT 1
        """,
        (day, line_name),
    )
    if not row:
        return None
    try:
        return float(row.get("Closing_reading"))
    except (TypeError, ValueError):
        return None


def list_electricity_consumption(
    limit: int = 60, line: Optional[str] = None
) -> list[dict[str, Any]]:
    sql = """
        SELECT Consumption_date AS "Consumption_date",
               Line AS "Line",
               Opening_reading AS "Opening_reading",
               Closing_reading AS "Closing_reading",
               Units_consumed AS "Units_consumed",
               Notes AS "Notes"
        FROM Electricity_Consumption
    """
    params: list[Any] = []
    if line:
        sql += " WHERE Line = ?"
        params.append(normalize_electricity_line(line))
    sql += " ORDER BY Consumption_date DESC, Line LIMIT ?"
    params.append(limit)
    return fetch_all(sql, tuple(params))


def electricity_month_totals(
    year: int, month: int, line: Optional[str] = None
) -> dict[str, Any]:
    prefix = f"{year:04d}-{month:02d}"
    by_line = {name: 0.0 for name in ELECTRICITY_LINES}
    if line:
        line_name = normalize_electricity_line(line)
        row = fetch_one(
            """
            SELECT COALESCE(SUM(Units_consumed), 0) AS "qty"
            FROM Electricity_Consumption
            WHERE Consumption_date LIKE ? AND Line = ?
            """,
            (f"{prefix}%", line_name),
        )
        qty = float((row or {}).get("qty") or 0)
        by_line[line_name] = qty
        return {"consumed": qty, "by_line": by_line}
    rows = fetch_all(
        """
        SELECT Line AS "Line", COALESCE(SUM(Units_consumed), 0) AS "qty"
        FROM Electricity_Consumption
        WHERE Consumption_date LIKE ?
        GROUP BY Line
        """,
        (f"{prefix}%",),
    )
    total = 0.0
    for row in rows:
        qty = float((row or {}).get("qty") or 0)
        total += qty
        try:
            name = normalize_electricity_line(row.get("Line"))
        except ValueError:
            continue
        by_line[name] = qty
    return {"consumed": total, "by_line": by_line}


def add_electricity_consumption(
    consumption_date: str,
    opening_reading: float,
    closing_reading: float,
    notes: Optional[str] = None,
    line: str = ELECTRICITY_LINE_1,
) -> float:
    day = _as_effective_date(consumption_date)
    if not day:
        raise ValueError("Consumption date is required.")
    line_name = normalize_electricity_line(line)
    try:
        opening = round(float(opening_reading), 1)
        closing = round(float(closing_reading), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("Opening and closing power readings are required.") from exc
    if opening < 0 or closing < 0:
        raise ValueError("Power readings cannot be negative.")
    units = round(closing - opening, 1)
    if units < 0:
        raise ValueError(
            "Closing reading must be greater than or equal to the opening reading."
        )
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Electricity_Consumption
            (Consumption_date, Line, Opening_reading, Closing_reading, Units_consumed,
             Notes, Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Consumption_date, Line) DO UPDATE SET
            Opening_reading=excluded.Opening_reading,
            Closing_reading=excluded.Closing_reading,
            Units_consumed=excluded.Units_consumed,
            Notes=excluded.Notes,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (
            day,
            line_name,
            opening,
            closing,
            units,
            (notes or "").strip() or None,
            by_val,
            dt_val,
        ),
    )
    return units


CONVERSION_RATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("oil_rate_per_kg", "Oil rate per kg"),
    ("electricity_rate_per_kg", "Electricity rate per kg"),
    ("labour_rate_per_kg", "Labour rate per kg"),
    ("salaries_rate_per_kg", "Salaries rate per kg"),
    ("consumables_rate_per_kg", "Consumables rate per kg"),
    ("overheads_rate_per_kg", "Overheads rate per kg"),
)


def _month_start_iso(value: Any) -> str:
    """Store expense_month as the first day of that month (YYYY-MM-01)."""
    text = _as_effective_date(value)
    if len(text) < 7:
        raise ValueError("Expense month is required.")
    year = int(text[:4])
    month = int(text[5:7])
    return date(year, month, 1).isoformat()


def _as_rate_4(value: Any, label: str) -> float:
    rounded = round_percent_4(value)
    if rounded is None:
        raise ValueError(f"{label} is required.")
    if rounded < 0:
        raise ValueError(f"{label} cannot be negative.")
    return rounded


def list_cost_of_conversion(limit: int = 120) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT id AS "id",
               expense_month AS "expense_month",
               oil_rate_per_kg AS "oil_rate_per_kg",
               electricity_rate_per_kg AS "electricity_rate_per_kg",
               labour_rate_per_kg AS "labour_rate_per_kg",
               salaries_rate_per_kg AS "salaries_rate_per_kg",
               consumables_rate_per_kg AS "consumables_rate_per_kg",
               overheads_rate_per_kg AS "overheads_rate_per_kg",
               total_conversion_rate_per_kg AS "total_conversion_rate_per_kg"
        FROM Cost_of_conversion
        ORDER BY expense_month DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )


def get_cost_of_conversion(expense_month: str | date) -> Optional[dict[str, Any]]:
    month = _month_start_iso(expense_month)
    return fetch_one(
        """
        SELECT id AS "id",
               expense_month AS "expense_month",
               oil_rate_per_kg AS "oil_rate_per_kg",
               electricity_rate_per_kg AS "electricity_rate_per_kg",
               labour_rate_per_kg AS "labour_rate_per_kg",
               salaries_rate_per_kg AS "salaries_rate_per_kg",
               consumables_rate_per_kg AS "consumables_rate_per_kg",
               overheads_rate_per_kg AS "overheads_rate_per_kg",
               total_conversion_rate_per_kg AS "total_conversion_rate_per_kg"
        FROM Cost_of_conversion
        WHERE expense_month = ?
        """,
        (month,),
    )


def save_cost_of_conversion(
    expense_month: str | date,
    oil_rate_per_kg: float,
    electricity_rate_per_kg: float,
    labour_rate_per_kg: float,
    salaries_rate_per_kg: float,
    consumables_rate_per_kg: float,
    overheads_rate_per_kg: float,
) -> float:
    """Upsert one month's conversion rates. Total is stored as the sum of the six rates."""
    month = _month_start_iso(expense_month)
    rates = {
        "oil_rate_per_kg": _as_rate_4(oil_rate_per_kg, "Oil rate per kg"),
        "electricity_rate_per_kg": _as_rate_4(
            electricity_rate_per_kg, "Electricity rate per kg"
        ),
        "labour_rate_per_kg": _as_rate_4(labour_rate_per_kg, "Labour rate per kg"),
        "salaries_rate_per_kg": _as_rate_4(salaries_rate_per_kg, "Salaries rate per kg"),
        "consumables_rate_per_kg": _as_rate_4(
            consumables_rate_per_kg, "Consumables rate per kg"
        ),
        "overheads_rate_per_kg": _as_rate_4(overheads_rate_per_kg, "Overheads rate per kg"),
    }
    total = round(sum(rates.values()), PERCENT_SCALE)
    execute(
        """
        INSERT INTO Cost_of_conversion
            (expense_month, oil_rate_per_kg, electricity_rate_per_kg,
             labour_rate_per_kg, salaries_rate_per_kg, consumables_rate_per_kg,
             overheads_rate_per_kg, total_conversion_rate_per_kg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (expense_month) DO UPDATE SET
            oil_rate_per_kg = excluded.oil_rate_per_kg,
            electricity_rate_per_kg = excluded.electricity_rate_per_kg,
            labour_rate_per_kg = excluded.labour_rate_per_kg,
            salaries_rate_per_kg = excluded.salaries_rate_per_kg,
            consumables_rate_per_kg = excluded.consumables_rate_per_kg,
            overheads_rate_per_kg = excluded.overheads_rate_per_kg,
            total_conversion_rate_per_kg = excluded.total_conversion_rate_per_kg
        """,
        (
            month,
            rates["oil_rate_per_kg"],
            rates["electricity_rate_per_kg"],
            rates["labour_rate_per_kg"],
            rates["salaries_rate_per_kg"],
            rates["consumables_rate_per_kg"],
            rates["overheads_rate_per_kg"],
            total,
        ),
    )
    refresh_outputs_missing_conversion_rate()
    return total


# ---------- BOM ----------

def add_bom_line(
    bom_id: float,
    effective_date: str,
    cust_code: Optional[str],
    alloy_name: Optional[str],
    raw_material: Optional[str],
    quantity: float,
    sequence: float,
    notes: str,
) -> None:
    execute(
        """
        INSERT INTO Build_of_Material
            (BOMID, Effective_date, Cust_code, Alloy_Name,
             Raw_Material_Name, Quantity, Sequence_Order, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(BOMID, Effective_date) DO UPDATE SET
            Cust_code=excluded.Cust_code, Alloy_Name=excluded.Alloy_Name,
            Raw_Material_Name=excluded.Raw_Material_Name, Quantity=excluded.Quantity,
            Sequence_Order=excluded.Sequence_Order, notes=excluded.notes
        """,
        (bom_id, effective_date, cust_code, alloy_name, raw_material, quantity, sequence, notes),
    )


def today_str() -> str:
    return date.today().isoformat()
