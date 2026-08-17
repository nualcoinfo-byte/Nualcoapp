"""
Database layer for Nualco Aluminum Alloy Manufacturing Tracker.

Runs on Neon Postgres when DATABASE_URL is available (from the environment or
.env.local), otherwise falls back to the local SQLite file. All SQL goes
through a single SQLAlchemy engine; queries are written in the portable
subset both dialects support:

- placeholders use `?` and are translated to `%s` for Postgres
- upserts use `ON CONFLICT` (supported by both Postgres and SQLite 3.24+)
- generated ids are read with `RETURNING` (Postgres and SQLite 3.35+)
- selected columns carry quoted aliases so result keys keep their exact
  case on Postgres, which folds unquoted identifiers to lowercase
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from sqlalchemy import Connection, CursorResult, MetaData, Table, create_engine, select

DB_PATH = Path(__file__).resolve().parent / "nualco.db"
ENV_FILE = Path(__file__).resolve().parent / ".env.local"


def _database_url() -> str | None:
    def _clean(value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).strip().strip('"').strip("'")
        return text or None

    url = _clean(os.environ.get("DATABASE_URL"))
    if url:
        return url

    # Streamlit Cloud / local .streamlit/secrets.toml
    try:
        import streamlit as st  # type: ignore

        secrets = st.secrets
        for key in ("DATABASE_URL", "database_url"):
            if key in secrets:
                url = _clean(secrets[key])
                if url:
                    return url
        # Nested forms some dashboards use: [postgres] url = "..."
        for section in ("postgres", "neon", "db"):
            if section in secrets:
                block = secrets[section]
                for key in ("DATABASE_URL", "database_url", "url", "uri"):
                    try:
                        url = _clean(block[key])
                    except Exception:
                        url = None
                    if url:
                        return url
    except Exception:
        pass

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return _clean(line.partition("=")[2])
    return None


_URL = _database_url()
if _URL:
    ENGINE = create_engine(_URL, pool_pre_ping=True)
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


def _q(sql: str) -> str:
    """Translate `?` placeholders to the driver's paramstyle."""
    return sql.replace("?", "%s") if IS_POSTGRES else sql


def _exec(conn: Connection, sql: str, params: Any = None) -> CursorResult:
    return conn.exec_driver_sql(_q(sql), params)


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Open a connection wrapped in a transaction (commit/rollback on exit)."""
    with ENGINE.begin() as conn:
        yield conn


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
]

CORE_ELEMENTS = ["Si", "Fe", "Cu", "Mn", "Mg", "Al"]

WORKFLOW_STAGES = [
    "Raw Material",
    "Melting/Furnace",
    "Casting",
    "Quality Inspection",
    "Finished Goods",
]

BATCH_QA_STATUS = ["Pending QA", "In-Progress", "Approved", "Rejected"]
FG_STATUS_UNDER_TESTING = "Under_Testing"
FG_STATUS_AVAILABLE = "Available"
FG_STATUS_ASSIGNED = "Assigned"
FG_STATUSES = [FG_STATUS_UNDER_TESTING, FG_STATUS_AVAILABLE, FG_STATUS_ASSIGNED]
INVENTORY_STATUS = ["Awaiting Assay", "Ready For Melt", "Not Ready for Melt"]
ACTIVE_STATUS = ["Active", "Inactive"]
SAMPLE_OK_STATUS = ["OK", "NOT OK"]
ELEMENT_LEVEL = ["Low", "Medium", "High"]
SHIFTS = ["A", "B"]
MELT_NOS = [1, 2, 3, 4, 5, 6, 7, 9]
HEAT_NOS = list(range(1, 13))
YIELD_TARGET_PCT = 70.0

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
    Alloy_family TEXT,
    Availability_class TEXT,
    Recovery {float},
    Photo {blob},
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Cost_per_kg {float} DEFAULT 0,
    Fe TEXT CHECK(Fe IS NULL OR Fe IN ('Low', 'Medium', 'High')),
    Cu TEXT CHECK(Cu IS NULL OR Cu IN ('Low', 'Medium', 'High')),
    Mg TEXT CHECK(Mg IS NULL OR Mg IN ('Low', 'Medium', 'High')),
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Raw_Material_Name, Effective_date)
);
CREATE TABLE IF NOT EXISTS Raw_Material_Inventory (
    Lot_id {autopk},
    Raw_Material_Name TEXT NOT NULL,
    Vendor_code INTEGER REFERENCES Vendor_Master(Vendor_code),
    Supplier_Invoice TEXT,
    Supplier_invoice_date TEXT,
    Received_date TEXT,
    Received_weight {float},
    Remaining_Weight {float},
    Storage_bay TEXT,
    Raw_Material_Status TEXT DEFAULT 'Awaiting Assay',
    Photo {blob},
    Cost_per_kg {float},
    Invoice_Document {blob},
    Invoice_Document_name TEXT,
    Invoice_Document_type TEXT
);
CREATE TABLE IF NOT EXISTS Raw_Material_Spec (
    Raw_Material_Name TEXT NOT NULL,
    Lot_id INTEGER NOT NULL REFERENCES Raw_Material_Inventory(Lot_id),
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Percentage {float},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Raw_Material_Name, Lot_id, Element_symbol)
);
CREATE TABLE IF NOT EXISTS Alloy_Master (
    Alloy_id {autopk},
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Alloy_name TEXT NOT NULL,
    Alloy_Family TEXT,
    Created_by TEXT,
    Created_at TEXT DEFAULT {now},
    Colour_code TEXT,
    Bis_Designation TEXT,
    Sludge_factor {float},
    Revision_datetime TEXT,
    Remarks TEXT,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
    Other_elements_Each {float},
    Other_elements_Total {float},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT
);
CREATE TABLE IF NOT EXISTS Alloy_Master_spec (
    Alloy_id INTEGER NOT NULL REFERENCES Alloy_Master(Alloy_id),
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Min_percent {float},
    Max_percent {float},
    Last_updated_by TEXT,
    Last_updated_datetime TEXT,
    PRIMARY KEY (Alloy_id, Element_symbol)
);
CREATE TABLE IF NOT EXISTS Furnace_Master (
    Furnace TEXT PRIMARY KEY,
    Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
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
    Melt_No INTEGER,
    Heat_no TEXT,
    Melting_team TEXT,
    Output_Weight {float} DEFAULT 0,
    Output_pieces {float} DEFAULT 0,
    Notes TEXT,
    Photo1 {blob}, Photo2 {blob}, Photo3 {blob},
    Production_status TEXT DEFAULT 'Pending QA',
    Workflow_stage TEXT DEFAULT 'Raw Material',
    Degassing_time TEXT,
    Sampled_pcs {float},
    Defect_pcs {float},
    Top_Sample TEXT CHECK(Top_Sample IS NULL OR Top_Sample IN ('OK', 'NOT OK')),
    Middle_Sample TEXT CHECK(Middle_Sample IS NULL OR Middle_Sample IN ('OK', 'NOT OK')),
    Bottom_Sample TEXT CHECK(Bottom_Sample IS NULL OR Bottom_Sample IN ('OK', 'NOT OK')),
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
    Output_pieces {float},
    Finished_Goods_Status TEXT NOT NULL DEFAULT 'Under_Testing'
        CHECK(Finished_Goods_Status IN ('Under_Testing', 'Available', 'Assigned'))
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
    Photo1 {blob}, Photo2 {blob},
    PRIMARY KEY (Batch_ID, Raw_Material_Name, Lot_id, Charge_time)
);
CREATE TABLE IF NOT EXISTS Batch_Chemical_Composition (
    Batch_ID TEXT NOT NULL REFERENCES Production_batch(Batch_ID),
    Element_symbol TEXT NOT NULL REFERENCES Element_Master(Element_Symbol),
    Percentage {float},
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
    Customer_PO_No TEXT PRIMARY KEY,
    Cust_code TEXT REFERENCES Customer_Master(Cust_code),
    Customer_name TEXT REFERENCES Customer_Master(Customer_name),
    Alloy_Id INTEGER REFERENCES Alloy_Master(Alloy_id),
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
    PO_Document_type TEXT
);
"""

_DIALECT_TYPES = {
    True: {  # Postgres
        "float": "DOUBLE PRECISION",
        "blob": "BYTEA",
        "autopk": "INTEGER PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY",
        "now": "(CURRENT_TIMESTAMP::text)",
    },
    False: {  # SQLite
        "float": "REAL",
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
        _ensure_columns(
            conn,
            "Purchase_Order",
            [
                ("PO_Document", "BYTEA" if IS_POSTGRES else "BLOB"),
                ("PO_Document_name", "TEXT"),
                ("PO_Document_type", "TEXT"),
            ],
        )
        _ensure_columns(
            conn,
            "Raw_Material_Inventory",
            [
                ("Invoice_Document", "BYTEA" if IS_POSTGRES else "BLOB"),
                ("Invoice_Document_name", "TEXT"),
                ("Invoice_Document_type", "TEXT"),
            ],
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
        ):
            _ensure_columns(conn, table, _audit_cols)
        if not IS_POSTGRES:
            _ensure_columns(
                conn,
                "Raw_Material_Master",
                [
                    ("Cost_per_kg", "REAL DEFAULT 0"),
                    ("ISRI_CODE", "TEXT REFERENCES ISRI_CODE_TABLE(ISRI_CODE)"),
                    ("Fe", "TEXT"),
                    ("Cu", "TEXT"),
                    ("Mg", "TEXT"),
                ],
            )
            _ensure_columns(
                conn,
                "Raw_Material_Inventory",
                [
                    ("Supplier_invoice_date", "TEXT"),
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
                    ("Sludge_factor", "REAL"),
                    ("Revision_datetime", "TEXT"),
                    ("Remarks", "TEXT"),
                    ("Status", "TEXT DEFAULT 'Active'"),
                    ("Other_elements_Each", "REAL"),
                    ("Other_elements_Total", "REAL"),
                ],
            )
            _ensure_columns(
                conn,
                "Build_of_Material",
                [("Cust_code", "TEXT REFERENCES Customer_Master(Cust_code)")],
            )

        _ensure_finished_goods_release_trigger(conn)


def _ensure_finished_goods_release_trigger(conn: Connection) -> None:
    """When a batch is Approved, release its Under_Testing finished-goods bundles."""
    if not IS_POSTGRES:
        return
    _exec(
        conn,
        """
        CREATE OR REPLACE FUNCTION release_finished_goods_on_batch_approved()
        RETURNS TRIGGER AS $fn$
        BEGIN
            IF NEW.production_status = 'Approved'
               AND (OLD.production_status IS DISTINCT FROM 'Approved') THEN
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


# ---------- Generic helpers ----------

def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with ENGINE.connect() as conn:
        return [dict(r) for r in _exec(conn, sql, tuple(params)).mappings()]


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    with ENGINE.connect() as conn:
        row = _exec(conn, sql, tuple(params)).mappings().first()
        return dict(row) if row is not None else None


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
    table = Table(table_name, MetaData(), autoload_with=ENGINE)
    stmt = select(table)
    if order_by is not None:
        stmt = stmt.order_by(table.c[order_by])
    with ENGINE.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings()]


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
        "pk": ["raw_material_name", "lot_id", "element_symbol"],
        "order_by": "lot_id",
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
        "pk": ["customer_po_no"],
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
        "key": "element_master",
        "label": "Elements",
        "pk": ["serial_no"],
        "order_by": "serial_no",
        "identity": [],
        "allow_add": False,
    },
]


def _resolve_table_name(table_name: str) -> str:
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(ENGINE)
    names = insp.get_table_names()
    if table_name in names:
        return table_name
    lower = table_name.lower()
    if lower in names:
        return lower
    raise ValueError(f"Table not found: {table_name}")


def editable_columns(table_name: str) -> list[str]:
    """Column names for the editor, excluding binary photo fields."""
    from sqlalchemy import inspect as sa_inspect

    resolved = _resolve_table_name(table_name)
    cols: list[str] = []
    for c in sa_inspect(ENGINE).get_columns(resolved):
        type_name = type(c["type"]).__name__.upper()
        if "BLOB" in type_name or "BYTEA" in type_name or "LARGEBINARY" in type_name:
            continue
        cols.append(c["name"])
    return cols


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
            key = _row_key(row, pk_cols)
            is_new = key not in original_map or any(v is None for v in key)

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

            values = [row.get(c) for c in write_cols]
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


def list_elements() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT Serial_no AS "Serial_no", Element_Name AS "Element_Name",
               Element_Symbol AS "Element_Symbol"
        FROM Element_Master ORDER BY Serial_no
        """
    )


def list_element_symbols(subset: Optional[Iterable[str]] = None) -> list[str]:
    """Element symbols in Element_Master.Serial_no order.

    If ``subset`` is given, return only those symbols (still in serial order).
    """
    symbols = [e["Element_Symbol"] for e in list_elements()]
    if subset is None:
        return symbols
    wanted = {str(s) for s in subset}
    return [s for s in symbols if s in wanted]


def list_furnaces(active_only: bool = True) -> list[str]:
    sql = 'SELECT Furnace AS "Furnace" FROM Furnace_Master'
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY CAST(Furnace AS INTEGER), Furnace"
    return [r["Furnace"] for r in fetch_all(sql)]


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


def list_alloys() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT a.Alloy_id AS "Alloy_id", a.Alloy_name AS "Alloy_name",
               a.Cust_code AS "Cust_code", c.Customer_name AS "Customer_name",
               a.Alloy_Family AS "Alloy_Family", a.Colour_code AS "Colour_code",
               a.Bis_Designation AS "Bis_Designation",
               a.Sludge_factor AS "Sludge_factor",
               a.Revision_datetime AS "Revision_datetime",
               a.Remarks AS "Remarks", a.Status AS "Status",
               a.Other_elements_Each AS "Other_elements_Each",
               a.Other_elements_Total AS "Other_elements_Total"
        FROM Alloy_Master a
        LEFT JOIN Customer_Master c ON c.Cust_code = a.Cust_code
        ORDER BY a.Alloy_name
        """
    )


def list_inventory_lots(
    material: Optional[str] = None, ready_only: bool = False
) -> list[dict[str, Any]]:
    sql = """
        SELECT i.Lot_id AS "Lot_id", i.Raw_Material_Name AS "Raw_Material_Name",
               i.Vendor_code AS "Vendor_code", v.Vendor_name AS "Vendor_name",
               i.Remaining_Weight AS "Remaining_Weight",
               i.Raw_Material_Status AS "Raw_Material_Status",
               i.Received_date AS "Received_date"
        FROM Raw_Material_Inventory i
        LEFT JOIN Vendor_Master v ON v.Vendor_code = i.Vendor_code
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
    alloy_family: str,
    availability_class: str,
    recovery: Optional[float],
    status: str,
    cost_per_kg: float,
    photo: Optional[bytes] = None,
    isri_code: Optional[str] = None,
    fe: Optional[str] = None,
    cu: Optional[str] = None,
    mg: Optional[str] = None,
) -> None:
    by_val, dt_val = audit_stamp()
    execute(
        """
        INSERT INTO Raw_Material_Master
            (Raw_Material_Name, Effective_date, Vendor_code, ISRI_CODE, Alloy_family,
             Availability_class, Recovery, Photo, Status, Cost_per_kg, Fe, Cu, Mg,
             Last_updated_by, Last_updated_datetime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Raw_Material_Name, Effective_date) DO UPDATE SET
            Vendor_code=excluded.Vendor_code, ISRI_CODE=excluded.ISRI_CODE,
            Alloy_family=excluded.Alloy_family,
            Availability_class=excluded.Availability_class, Recovery=excluded.Recovery,
            Photo=excluded.Photo, Status=excluded.Status, Cost_per_kg=excluded.Cost_per_kg,
            Fe=excluded.Fe, Cu=excluded.Cu, Mg=excluded.Mg,
            Last_updated_by=excluded.Last_updated_by,
            Last_updated_datetime=excluded.Last_updated_datetime
        """,
        (
            name,
            effective_date,
            vendor_code,
            isri_code,
            alloy_family,
            availability_class,
            recovery,
            photo,
            status,
            cost_per_kg,
            fe,
            cu,
            mg,
            by_val,
            dt_val,
        ),
    )


def add_inventory_lot(
    material: str,
    vendor_code: Optional[int],
    invoice: str,
    received_date: str,
    weight: float,
    storage_bay: str,
    status: str,
    photo: Optional[bytes] = None,
    supplier_invoice_date: Optional[str] = None,
    cost_per_kg: Optional[float] = None,
    invoice_document: Optional[bytes] = None,
    invoice_document_name: Optional[str] = None,
    invoice_document_type: Optional[str] = None,
) -> int:
    if invoice_document and invoice_document_name:
        _validate_invoice_document_name(invoice_document_name)
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Raw_Material_Inventory
                (Raw_Material_Name, Vendor_code, Supplier_Invoice, Supplier_invoice_date,
                 Received_date, Received_weight, Remaining_Weight, Storage_bay,
                 Raw_Material_Status, Photo, Cost_per_kg,
                 Invoice_Document, Invoice_Document_name, Invoice_Document_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Lot_id
            """,
            (
                material,
                vendor_code,
                invoice,
                supplier_invoice_date,
                received_date,
                weight,
                weight,
                storage_bay,
                status,
                photo,
                cost_per_kg,
                invoice_document,
                invoice_document_name,
                invoice_document_type,
            ),
        )
        return int(result.scalar_one())


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
    existing = fetch_one(
        'SELECT Lot_id AS "Lot_id" FROM Raw_Material_Inventory WHERE Lot_id = ?',
        (lot_id,),
    )
    if not existing:
        raise ValueError(f"Lot {lot_id} not found.")
    execute(
        """
        UPDATE Raw_Material_Inventory
        SET Invoice_Document = ?, Invoice_Document_name = ?, Invoice_Document_type = ?
        WHERE Lot_id = ?
        """,
        (file_bytes, filename.strip(), content_type or "", lot_id),
    )


def get_inventory_invoice_document(lot_id: int) -> Optional[dict[str, Any]]:
    return fetch_one(
        """
        SELECT Lot_id AS "Lot_id",
               Invoice_Document AS "Invoice_Document",
               Invoice_Document_name AS "Invoice_Document_name",
               Invoice_Document_type AS "Invoice_Document_type"
        FROM Raw_Material_Inventory
        WHERE Lot_id = ?
        """,
        (lot_id,),
    )


def set_lot_chemistry(material: str, lot_id: int, composition: dict[str, float]) -> None:
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        _exec(
            conn,
            "DELETE FROM Raw_Material_Spec WHERE Raw_Material_Name = ? AND Lot_id = ?",
            (material, lot_id),
        )
        rows = [
            (material, lot_id, sym, pct, by_val, dt_val)
            for sym, pct in composition.items()
            if pct is not None
        ]
        if rows:
            _exec(
                conn,
                """
                INSERT INTO Raw_Material_Spec
                    (Raw_Material_Name, Lot_id, Element_symbol, Percentage,
                     Last_updated_by, Last_updated_datetime)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def get_lot_chemistry(lot_id: int) -> dict[str, float]:
    rows = fetch_all(
        """
        SELECT s.Element_symbol AS "Element_symbol", s.Percentage AS "Percentage"
        FROM Raw_Material_Spec s
        LEFT JOIN Element_Master _el ON _el.Element_Symbol = s.Element_symbol
        WHERE s.Lot_id = ?
        ORDER BY COALESCE(_el.Serial_no, 9999), s.Element_symbol
        """,
        (lot_id,),
    )
    return {r["Element_symbol"]: r["Percentage"] for r in rows}


# ---------- Alloys ----------

def add_alloy(
    cust_code: Optional[str],
    alloy_name: str,
    family: str,
    created_by: str,
    specs: dict[str, tuple[Optional[float], Optional[float]]],
    colour_code: Optional[str] = None,
    bis_designation: Optional[str] = None,
    sludge_factor: Optional[float] = None,
    revision_datetime: Optional[str] = None,
    remarks: Optional[str] = None,
    status: str = "Active",
    other_elements_each: Optional[float] = None,
    other_elements_total: Optional[float] = None,
) -> int:
    by_val, dt_val = audit_stamp()
    with get_connection() as conn:
        result = _exec(
            conn,
            """
            INSERT INTO Alloy_Master
                (Cust_code, Alloy_name, Alloy_Family, Created_by, Created_at,
                 Colour_code, Bis_Designation, Sludge_factor,
                 Revision_datetime, Remarks, Status,
                 Other_elements_Each, Other_elements_Total,
                 Last_updated_by, Last_updated_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING Alloy_id
            """,
            (
                cust_code,
                alloy_name,
                family,
                created_by,
                datetime.now().isoformat(timespec="seconds"),
                colour_code,
                bis_designation,
                sludge_factor,
                revision_datetime,
                remarks,
                status,
                other_elements_each,
                other_elements_total,
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
                (alloy_id, sym, mn, mx, by_val, dt_val),
            )
        return alloy_id


# ---------- Furnace ----------

def upsert_furnace(name: str, status: str) -> None:
    execute(
        """
        INSERT INTO Furnace_Master (Furnace, Status) VALUES (?, ?)
        ON CONFLICT(Furnace) DO UPDATE SET Status = excluded.Status
        """,
        (name, status),
    )


def upsert_melter(name: str, status: str) -> None:
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


# ---------- Production batches ----------

def make_batch_id(furnace: str, heat_no: str | int) -> str:
    """Unique Batch ID = Furnace + Heat number (e.g. F3-H07)."""
    return f"F{furnace}-H{int(heat_no):02d}"


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

    for label, value in (
        ("Top_Sample", top_sample),
        ("Middle_Sample", middle_sample),
        ("Bottom_Sample", bottom_sample),
    ):
        if value and value not in SAMPLE_OK_STATUS:
            raise ValueError(f"{label} must be one of {SAMPLE_OK_STATUS}.")

    if production_supervisor:
        supervisors = list_production_supervisors(active_only=False)
        if production_supervisor not in supervisors:
            raise ValueError(
                f"Production supervisor '{production_supervisor}' is not in Production_supervisor."
            )

    total_weight = sum(float(i["Weight"]) for i in inputs)

    with get_connection() as conn:
        _exec(
            conn,
            """
            INSERT INTO Production_batch
                (Batch_ID, Alloy_id, Production_Date, Shift, Furnace, Melt_No,
                 Heat_no, Melting_team, Output_Weight, Notes, Production_status, Workflow_stage,
                 Degassing_time, Sampled_pcs, Defect_pcs,
                 Top_Sample, Middle_Sample, Bottom_Sample,
                 Top_Sample_Remarks, Middle_Sample_Remarks, Bottom_Sample_Remarks,
                 Top_Sample_datetime, Middle_Sample_datetime, Bottom_Sample_datetime,
                 Production_supervisor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending QA', 'Raw Material',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                alloy_id,
                production_date,
                shift,
                furnace,
                melt_no,
                str(heat_no),
                melting_team,
                total_weight,
                notes,
                degassing_time,
                sampled_pcs,
                defect_pcs,
                top_sample,
                middle_sample,
                bottom_sample,
                top_sample_remarks,
                middle_sample_remarks,
                bottom_sample_remarks,
                top_sample_datetime,
                middle_sample_datetime,
                bottom_sample_datetime,
                production_supervisor,
            ),
        )

        for item in inputs:
            # Deduct inventory
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
                     Charge_time, Notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

        for sym, pct in composition.items():
            if pct is None:
                continue
            _exec(
                conn,
                """
                INSERT INTO Batch_Chemical_Composition (Batch_ID, Element_symbol, Percentage)
                VALUES (?, ?, ?)
                """,
                (batch_id, sym, pct),
            )

    return batch_id


def update_batch_workflow(
    batch_id: str,
    workflow_stage: str,
    qa_status: Optional[str] = None,
) -> None:
    fields = ["Workflow_stage = ?"]
    params: list[Any] = [workflow_stage]
    if qa_status:
        fields.append("Production_status = ?")
        params.append(qa_status)
    params.append(batch_id)
    execute(
        f"UPDATE Production_batch SET {', '.join(fields)} WHERE Batch_ID = ?",
        params,
    )
    # App-level hook (also covered by Postgres trigger): release locked FG stock.
    if qa_status == "Approved":
        release_finished_goods_for_batch(batch_id)


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
    output_pieces: Optional[float] = None,
) -> int:
    """Create a finished-goods bundle locked as Under_Testing until the batch is Approved."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found.")

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
                output_pieces,
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
        SELECT Bundle_id AS "Bundle_id", Batch_ID AS "Batch_ID",
               Output_Weight AS "Output_Weight", Output_pieces AS "Output_pieces",
               Finished_Goods_Status AS "Finished_Goods_Status"
        FROM Finished_Goods_Inventory
        WHERE 1=1
    """
    params: list[Any] = []
    if batch_id:
        sql += " AND Batch_ID = ?"
        params.append(batch_id)
    if assignable_only:
        sql += " AND Finished_Goods_Status = ?"
        params.append(FG_STATUS_AVAILABLE)
    elif status:
        sql += " AND Finished_Goods_Status = ?"
        params.append(status)
    sql += " ORDER BY Bundle_id DESC"
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
            "Approve the production batch to release Under_Testing stock."
        )
    execute(
        """
        UPDATE Finished_Goods_Inventory
        SET Finished_Goods_Status = ? WHERE Bundle_id = ?
        """,
        (FG_STATUS_ASSIGNED, bundle_id),
    )


_BATCH_COLUMNS = """
    Batch_ID AS "Batch_ID", Alloy_id AS "Alloy_id",
    Production_Date AS "Production_Date", Shift AS "Shift",
    Furnace AS "Furnace", Melt_No AS "Melt_No", Heat_no AS "Heat_no",
    Melting_team AS "Melting_team", Output_Weight AS "Output_Weight",
    Output_pieces AS "Output_pieces",
    Notes AS "Notes", Production_status AS "Production_status",
    Workflow_stage AS "Workflow_stage",
    Degassing_time AS "Degassing_time",
    Sampled_pcs AS "Sampled_pcs", Defect_pcs AS "Defect_pcs",
    Top_Sample AS "Top_Sample", Middle_Sample AS "Middle_Sample",
    Bottom_Sample AS "Bottom_Sample",
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
               b.Furnace AS "Furnace", b.Heat_no AS "Heat_no", b.Melt_No AS "Melt_No",
               b.Shift AS "Shift", b.Output_Weight AS "Output_Weight",
               b.Output_pieces AS "Output_pieces",
               b.Production_status AS "Production_status",
               b.Workflow_stage AS "Workflow_stage", a.Alloy_name AS "Alloy_name",
               b.Production_supervisor AS "Production_supervisor",
               b.Top_Sample AS "Top_Sample", b.Middle_Sample AS "Middle_Sample",
               b.Bottom_Sample AS "Bottom_Sample"
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
        SELECT Customer_PO_No AS "Customer_PO_No",
               Cust_code AS "Cust_code",
               Customer_name AS "Customer_name",
               Alloy_Id AS "Alloy_Id",
               Order_Date AS "Order_Date",
               Delivery_Date AS "Delivery_Date",
               Order_Qty AS "Order_Qty",
               Rate AS "Rate",
               Billing_Address AS "Billing_Address",
               Billing_City AS "Billing_City",
               Billing_state AS "Billing_state",
               Billing_Pincode AS "Billing_Pincode",
               Billing_country AS "Billing_country",
               Shipping_address AS "Shipping_address",
               Shipping_City AS "Shipping_City",
               Shipping_state AS "Shipping_state",
               Shipping_Pincode AS "Shipping_Pincode",
               Shipping_country AS "Shipping_country",
               PO_Document_name AS "PO_Document_name",
               PO_Document_type AS "PO_Document_type",
               CASE WHEN PO_Document IS NULL THEN 0 ELSE 1 END AS "Has_Document"
        FROM Purchase_Order
        ORDER BY Order_Date DESC, Customer_PO_No
        """
    )


def upsert_purchase_order(data: dict[str, Any]) -> None:
    """Insert or update a purchase order keyed on Customer_PO_No."""
    po_no = (data.get("Customer_PO_No") or "").strip()
    if not po_no:
        raise ValueError("Customer PO No is required.")
    if not data.get("Cust_code"):
        raise ValueError("Customer is required.")
    if not data.get("Customer_name"):
        raise ValueError("Customer name is required.")

    execute(
        """
        INSERT INTO Purchase_Order
            (Customer_PO_No, Cust_code, Customer_name, Alloy_Id,
             Order_Date, Delivery_Date, Order_Qty, Rate,
             Billing_Address, Billing_City, Billing_state, Billing_Pincode, Billing_country,
             Shipping_address, Shipping_City, Shipping_state, Shipping_Pincode, Shipping_country)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Customer_PO_No) DO UPDATE SET
            Cust_code=excluded.Cust_code,
            Customer_name=excluded.Customer_name,
            Alloy_Id=excluded.Alloy_Id,
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
            Shipping_country=excluded.Shipping_country
        """,
        (
            po_no,
            data["Cust_code"],
            data["Customer_name"],
            data.get("Alloy_Id"),
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
        ),
    )


def save_po_document(
    customer_po_no: str,
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
) -> None:
    """Attach a PDF/Word/Excel purchase-order document to an existing PO."""
    if not customer_po_no:
        raise ValueError("Customer PO No is required.")
    if not file_bytes:
        raise ValueError("Document file is empty.")
    name = (filename or "").strip()
    lower = name.lower()
    if not any(lower.endswith(ext) for ext in PO_DOCUMENT_EXTENSIONS):
        raise ValueError(
            "PO document must be PDF, Word (.doc/.docx), or Excel (.xls/.xlsx)."
        )
    existing = fetch_one(
        'SELECT Customer_PO_No AS "Customer_PO_No" FROM Purchase_Order WHERE Customer_PO_No = ?',
        (customer_po_no,),
    )
    if not existing:
        raise ValueError(f"Purchase order '{customer_po_no}' not found.")
    execute(
        """
        UPDATE Purchase_Order
        SET PO_Document = ?, PO_Document_name = ?, PO_Document_type = ?
        WHERE Customer_PO_No = ?
        """,
        (file_bytes, name, content_type or "", customer_po_no),
    )


def get_po_document(customer_po_no: str) -> Optional[dict[str, Any]]:
    return fetch_one(
        """
        SELECT Customer_PO_No AS "Customer_PO_No",
               PO_Document AS "PO_Document",
               PO_Document_name AS "PO_Document_name",
               PO_Document_type AS "PO_Document_type"
        FROM Purchase_Order
        WHERE Customer_PO_No = ?
        """,
        (customer_po_no,),
    )


def clear_po_document(customer_po_no: str) -> None:
    execute(
        """
        UPDATE Purchase_Order
        SET PO_Document = NULL, PO_Document_name = NULL, PO_Document_type = NULL
        WHERE Customer_PO_No = ?
        """,
        (customer_po_no,),
    )


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
