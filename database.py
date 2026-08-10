"""
SQLite database layer for Nualco Aluminum Alloy Manufacturing Tracker.
Creates all master and transactional tables if they do not exist.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

DB_PATH = Path(__file__).resolve().parent / "nualco.db"

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

BATCH_QA_STATUS = ["Pending QA", "Approved", "Rejected"]
INVENTORY_STATUS = ["Awaiting Assay", "Ready For Melt", "Not Ready for Melt"]
ACTIVE_STATUS = ["Active", "Inactive"]
SHIFTS = ["A", "B"]
MELT_NOS = [1, 2, 3, 4, 5, 6, 7, 9]
HEAT_NOS = list(range(1, 13))
YIELD_TARGET_PCT = 70.0


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and seed Element_Master / default furnaces."""
    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Customer_Master (
                Cust_code TEXT PRIMARY KEY,
                Custome_Name TEXT NOT NULL UNIQUE,
                GST TEXT,
                PAN TEXT,
                Address TEXT,
                City TEXT,
                State TEXT,
                Pincode TEXT,
                Country TEXT,
                Contact1_name TEXT,
                Phone1 TEXT,
                Contact_name2 TEXT,
                phone2 TEXT,
                email TEXT,
                website TEXT,
                Bank_account TEXT,
                IFSC_CODE TEXT,
                Bank_name TEXT,
                Branch_Category TEXT,
                created_date TEXT DEFAULT CURRENT_TIMESTAMP,
                Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Supplier_Master (
                Supplier TEXT PRIMARY KEY,
                GST TEXT,
                PAN TEXT,
                Vendor_code TEXT,
                Address TEXT,
                City TEXT,
                State TEXT,
                Pincode TEXT,
                Country TEXT,
                Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Element_Master (
                Serial_no INTEGER PRIMARY KEY,
                Element_Name TEXT NOT NULL,
                Element_Symbol TEXT NOT NULL UNIQUE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Raw_Material_Master (
                Raw_Material_Name TEXT NOT NULL,
                Effective_date TEXT NOT NULL,
                Supplier TEXT,
                Alloy_family TEXT,
                Availability_class TEXT,
                Recovery REAL,
                Photo BLOB,
                Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active',
                Cost_per_kg REAL DEFAULT 0,
                PRIMARY KEY (Raw_Material_Name, Effective_date),
                FOREIGN KEY (Supplier) REFERENCES Supplier_Master(Supplier)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Raw_Material_Inventory (
                Lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                Raw_Material_Name TEXT NOT NULL,
                Supplier TEXT,
                Supplier_Invoice TEXT,
                Received_date TEXT,
                Received_weight REAL,
                Remaining_Weight REAL,
                Storage_bay TEXT,
                Status TEXT DEFAULT 'Awaiting Assay',
                Photo BLOB,
                FOREIGN KEY (Supplier) REFERENCES Supplier_Master(Supplier)
            )
            """
        )

        # Specs keyed by material + lot + element.
        # Note: Raw_Material_Name is not alone a candidate key on Raw_Material_Master
        # (composite PK with Effective_date), so name FKs are enforced in app logic.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Raw_Material_Spec (
                Raw_Material_Name TEXT NOT NULL,
                Lot_id INTEGER NOT NULL,
                Element_symbol TEXT NOT NULL,
                Percentage REAL,
                PRIMARY KEY (Raw_Material_Name, Lot_id, Element_symbol),
                FOREIGN KEY (Lot_id) REFERENCES Raw_Material_Inventory(Lot_id),
                FOREIGN KEY (Element_symbol) REFERENCES Element_Master(Element_Symbol)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Alloy_Master (
                Alloy_id INTEGER PRIMARY KEY AUTOINCREMENT,
                Customer_name TEXT,
                Alloy_name TEXT NOT NULL,
                Alloy_Family TEXT,
                Created_by TEXT,
                Created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (Customer_name) REFERENCES Customer_Master(Custome_Name)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Alloy_Master_spec (
                Alloy_id INTEGER NOT NULL,
                Element_symbol TEXT NOT NULL,
                Min_percent REAL,
                Max_percent REAL,
                PRIMARY KEY (Alloy_id, Element_symbol),
                FOREIGN KEY (Alloy_id) REFERENCES Alloy_Master(Alloy_id),
                FOREIGN KEY (Element_symbol) REFERENCES Element_Master(Element_Symbol)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Furnace_Master (
                Furnace TEXT PRIMARY KEY,
                Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Production_batch (
                Batch_ID TEXT PRIMARY KEY,
                Alloy_id INTEGER,
                Production_Date TEXT,
                Shift TEXT CHECK(Shift IN ('A', 'B')),
                Furnace TEXT,
                Melt_No INTEGER,
                Heat_no TEXT,
                Melting_team TEXT,
                Weight REAL DEFAULT 0,
                pieces REAL DEFAULT 0,
                Notes TEXT,
                Photo1 BLOB,
                Photo2 BLOB,
                Photo3 BLOB,
                Status TEXT DEFAULT 'Pending QA',
                Workflow_stage TEXT DEFAULT 'Raw Material',
                Output_Weight REAL,
                FOREIGN KEY (Alloy_id) REFERENCES Alloy_Master(Alloy_id),
                FOREIGN KEY (Furnace) REFERENCES Furnace_Master(Furnace)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_input (
                Batch_ID TEXT NOT NULL,
                Raw_Material_Name TEXT NOT NULL,
                Lot_id INTEGER NOT NULL,
                Weight REAL NOT NULL,
                Charge_time TEXT DEFAULT CURRENT_TIMESTAMP,
                Notes TEXT,
                Photo1 BLOB,
                Photo2 BLOB,
                PRIMARY KEY (Batch_ID, Raw_Material_Name, Lot_id, Charge_time),
                FOREIGN KEY (Batch_ID) REFERENCES Production_batch(Batch_ID),
                FOREIGN KEY (Lot_id) REFERENCES Raw_Material_Inventory(Lot_id)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Batch_Chemical_Composition (
                Batch_ID TEXT NOT NULL,
                Element_symbol TEXT NOT NULL,
                Percentage REAL,
                PRIMARY KEY (Batch_ID, Element_symbol),
                FOREIGN KEY (Batch_ID) REFERENCES Production_batch(Batch_ID),
                FOREIGN KEY (Element_symbol) REFERENCES Element_Master(Element_Symbol)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS Build_of_Material (
                BOMID REAL NOT NULL,
                Effective_date TEXT NOT NULL,
                Customer_Name TEXT,
                Alloy_Name TEXT,
                Raw_Material_Name TEXT,
                Quantity REAL,
                Sequence_Order REAL,
                notes TEXT,
                PRIMARY KEY (BOMID, Effective_date),
                FOREIGN KEY (Customer_Name) REFERENCES Customer_Master(Custome_Name)
            )
            """
        )

        # Seed elements
        cur.executemany(
            """
            INSERT OR IGNORE INTO Element_Master (Serial_no, Element_Name, Element_Symbol)
            VALUES (?, ?, ?)
            """,
            ELEMENTS,
        )

        # Default furnaces
        for n in range(1, 5):
            cur.execute(
                "INSERT OR IGNORE INTO Furnace_Master (Furnace, Status) VALUES (?, 'Active')",
                (str(n),),
            )

        # Migrate older DBs: Customer_Master PK + columns
        _migrate_customer_master(cur)

        # Migrate older DBs: add columns if missing
        _ensure_columns(
            cur,
            "Raw_Material_Master",
            [("Cost_per_kg", "REAL DEFAULT 0")],
        )
        _ensure_columns(
            cur,
            "Production_batch",
            [
                ("Workflow_stage", "TEXT DEFAULT 'Raw Material'"),
                ("Output_Weight", "REAL"),            ],
        )


def _ensure_columns(cur: sqlite3.Cursor, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, typedef in columns:
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typedef}")


def _customer_master_pk(cur: sqlite3.Cursor) -> Optional[str]:
    for row in cur.execute("PRAGMA table_info(Customer_Master)").fetchall():
        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        if row[5] == 1:
            return row[1]
    return None


def _migrate_customer_master(cur: sqlite3.Cursor) -> None:
    """Ensure Cust_code is PK and contact/bank columns exist (rebuild when needed)."""
    info = cur.execute("PRAGMA table_info(Customer_Master)").fetchall()
    if not info:
        return

    existing = {row[1] for row in info}
    pk = _customer_master_pk(cur)
    needed = {
        "Cust_code",
        "Custome_Name",
        "Contact1_name",
        "Phone1",
        "Contact_name2",
        "phone2",
        "email",
        "website",
        "Bank_account",
        "IFSC_CODE",
        "Bank_name",
        "Branch_Category",
        "created_date",
    }
    if pk == "Cust_code" and needed.issubset(existing):
        return

    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute(
        """
        CREATE TABLE Customer_Master__new (
            Cust_code TEXT PRIMARY KEY,
            Custome_Name TEXT NOT NULL UNIQUE,
            GST TEXT,
            PAN TEXT,
            Address TEXT,
            City TEXT,
            State TEXT,
            Pincode TEXT,
            Country TEXT,
            Contact1_name TEXT,
            Phone1 TEXT,
            Contact_name2 TEXT,
            phone2 TEXT,
            email TEXT,
            website TEXT,
            Bank_account TEXT,
            IFSC_CODE TEXT,
            Bank_name TEXT,
            Branch_Category TEXT,
            created_date TEXT DEFAULT CURRENT_TIMESTAMP,
            Status TEXT CHECK(Status IN ('Active', 'Inactive')) DEFAULT 'Active'
        )
        """
    )

    cols = [
        "Cust_code",
        "Custome_Name",
        "GST",
        "PAN",
        "Address",
        "City",
        "State",
        "Pincode",
        "Country",
        "Contact1_name",
        "Phone1",
        "Contact_name2",
        "phone2",
        "email",
        "website",
        "Bank_account",
        "IFSC_CODE",
        "Bank_name",
        "Branch_Category",
        "created_date",
        "Status",
    ]
    select_exprs = []
    for col in cols:
        if col == "Cust_code" and "Cust_code" in existing:
            select_exprs.append(
                "CASE WHEN Cust_code IS NULL OR TRIM(Cust_code) = '' "
                "THEN 'CUST-' || Custome_Name ELSE Cust_code END AS Cust_code"
            )
        elif col == "Cust_code":
            select_exprs.append("'CUST-' || Custome_Name AS Cust_code")
        elif col == "created_date" and "created_date" not in existing:
            select_exprs.append("CURRENT_TIMESTAMP AS created_date")
        elif col in existing:
            select_exprs.append(col)
        else:
            select_exprs.append(f"NULL AS {col}")

    cur.execute(
        f"""
        INSERT INTO Customer_Master__new ({", ".join(cols)})
        SELECT {", ".join(select_exprs)}
        FROM Customer_Master
        """
    )
    cur.execute("DROP TABLE Customer_Master")
    cur.execute("ALTER TABLE Customer_Master__new RENAME TO Customer_Master")
    cur.execute("PRAGMA foreign_keys = ON")


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with get_connection() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid or 0


def execute_many(sql: str, seq: list[tuple[Any, ...]]) -> None:
    with get_connection() as conn:
        conn.executemany(sql, seq)


# ---------- Lookups ----------

def list_customers(active_only: bool = True) -> list[str]:
    sql = "SELECT Custome_Name FROM Customer_Master"
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Custome_Name"
    return [r["Custome_Name"] for r in fetch_all(sql)]


def list_suppliers(active_only: bool = True) -> list[str]:
    sql = "SELECT Supplier FROM Supplier_Master"
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Supplier"
    return [r["Supplier"] for r in fetch_all(sql)]


def list_elements() -> list[sqlite3.Row]:
    return fetch_all(
        "SELECT Serial_no, Element_Name, Element_Symbol FROM Element_Master ORDER BY Serial_no"
    )


def list_furnaces(active_only: bool = True) -> list[str]:
    sql = "SELECT Furnace FROM Furnace_Master"
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY CAST(Furnace AS INTEGER), Furnace"
    return [r["Furnace"] for r in fetch_all(sql)]


def list_raw_materials(active_only: bool = True) -> list[str]:
    sql = """
        SELECT DISTINCT Raw_Material_Name FROM Raw_Material_Master
    """
    if active_only:
        sql += " WHERE Status = 'Active'"
    sql += " ORDER BY Raw_Material_Name"
    return [r["Raw_Material_Name"] for r in fetch_all(sql)]


def list_alloys() -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT Alloy_id, Alloy_name, Customer_name, Alloy_Family
        FROM Alloy_Master
        ORDER BY Alloy_name
        """
    )


def list_inventory_lots(
    material: Optional[str] = None, ready_only: bool = False
) -> list[sqlite3.Row]:
    sql = """
        SELECT Lot_id, Raw_Material_Name, Supplier, Remaining_Weight, Status, Received_date
        FROM Raw_Material_Inventory
        WHERE Remaining_Weight > 0
    """
    params: list[Any] = []
    if material:
        sql += " AND Raw_Material_Name = ?"
        params.append(material)
    if ready_only:
        sql += " AND Status = 'Ready For Melt'"
    sql += " ORDER BY Lot_id DESC"
    return fetch_all(sql, params)


# ---------- Customers / Suppliers ----------

def upsert_customer(data: dict[str, Any]) -> None:
    created = data.get("created_date") or datetime.now().isoformat(timespec="seconds")
    execute(
        """
        INSERT INTO Customer_Master
            (Cust_code, Custome_Name, GST, PAN, Address, City, State, Pincode, Country,
             Contact1_name, Phone1, Contact_name2, phone2, email, website,
             Bank_account, IFSC_CODE, Bank_name, Branch_Category, created_date, Status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Cust_code) DO UPDATE SET
            Custome_Name=excluded.Custome_Name,
            GST=excluded.GST, PAN=excluded.PAN,
            Address=excluded.Address, City=excluded.City, State=excluded.State,
            Pincode=excluded.Pincode, Country=excluded.Country,
            Contact1_name=excluded.Contact1_name, Phone1=excluded.Phone1,
            Contact_name2=excluded.Contact_name2, phone2=excluded.phone2,
            email=excluded.email, website=excluded.website,
            Bank_account=excluded.Bank_account, IFSC_CODE=excluded.IFSC_CODE,
            Bank_name=excluded.Bank_name, Branch_Category=excluded.Branch_Category,
            Status=excluded.Status
        """,
        (
            data["Cust_code"],
            data["Custome_Name"],
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
            data.get("phone2"),
            data.get("email"),
            data.get("website"),
            data.get("Bank_account"),
            data.get("IFSC_CODE"),
            data.get("Bank_name"),
            data.get("Branch_Category"),
            created,
            data.get("Status", "Active"),
        ),
    )


def upsert_supplier(data: dict[str, Any]) -> None:
    execute(
        """
        INSERT INTO Supplier_Master
            (Supplier, GST, PAN, Vendor_code, Address, City, State, Pincode, Country, Status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Supplier) DO UPDATE SET
            GST=excluded.GST, PAN=excluded.PAN, Vendor_code=excluded.Vendor_code,
            Address=excluded.Address, City=excluded.City, State=excluded.State,
            Pincode=excluded.Pincode, Country=excluded.Country, Status=excluded.Status
        """,
        (
            data["Supplier"],
            data.get("GST"),
            data.get("PAN"),
            data.get("Vendor_code"),
            data.get("Address"),
            data.get("City"),
            data.get("State"),
            data.get("Pincode"),
            data.get("Country"),
            data.get("Status", "Active"),
        ),
    )


# ---------- Raw materials ----------

def add_raw_material_master(
    name: str,
    effective_date: str,
    supplier: Optional[str],
    alloy_family: str,
    availability_class: str,
    recovery: Optional[float],
    status: str,
    cost_per_kg: float,
    photo: Optional[bytes] = None,
) -> None:
    execute(
        """
        INSERT OR REPLACE INTO Raw_Material_Master
            (Raw_Material_Name, Effective_date, Supplier, Alloy_family,
             Availability_class, Recovery, Photo, Status, Cost_per_kg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            effective_date,
            supplier,
            alloy_family,
            availability_class,
            recovery,
            photo,
            status,
            cost_per_kg,
        ),
    )


def add_inventory_lot(
    material: str,
    supplier: Optional[str],
    invoice: str,
    received_date: str,
    weight: float,
    storage_bay: str,
    status: str,
    photo: Optional[bytes] = None,
) -> int:
    return execute(
        """
        INSERT INTO Raw_Material_Inventory
            (Raw_Material_Name, Supplier, Supplier_Invoice, Received_date,
             Received_weight, Remaining_Weight, Storage_bay, Status, Photo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (material, supplier, invoice, received_date, weight, weight, storage_bay, status, photo),
    )


def set_lot_chemistry(material: str, lot_id: int, composition: dict[str, float]) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM Raw_Material_Spec WHERE Raw_Material_Name = ? AND Lot_id = ?",
            (material, lot_id),
        )
        conn.executemany(
            """
            INSERT INTO Raw_Material_Spec
                (Raw_Material_Name, Lot_id, Element_symbol, Percentage)
            VALUES (?, ?, ?, ?)
            """,
            [(material, lot_id, sym, pct) for sym, pct in composition.items() if pct is not None],
        )


def get_lot_chemistry(lot_id: int) -> dict[str, float]:
    rows = fetch_all(
        "SELECT Element_symbol, Percentage FROM Raw_Material_Spec WHERE Lot_id = ?",
        (lot_id,),
    )
    return {r["Element_symbol"]: r["Percentage"] for r in rows}


# ---------- Alloys ----------

def add_alloy(
    customer: Optional[str],
    alloy_name: str,
    family: str,
    created_by: str,
    specs: dict[str, tuple[Optional[float], Optional[float]]],
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO Alloy_Master
                (Customer_name, Alloy_name, Alloy_Family, Created_by, Created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (customer, alloy_name, family, created_by, datetime.now().isoformat(timespec="seconds")),
        )
        alloy_id = cur.lastrowid
        for sym, (mn, mx) in specs.items():
            if mn is None and mx is None:
                continue
            conn.execute(
                """
                INSERT INTO Alloy_Master_spec (Alloy_id, Element_symbol, Min_percent, Max_percent)
                VALUES (?, ?, ?, ?)
                """,
                (alloy_id, sym, mn, mx),
            )
        return int(alloy_id)


# ---------- Furnace ----------

def upsert_furnace(name: str, status: str) -> None:
    execute(
        """
        INSERT INTO Furnace_Master (Furnace, Status) VALUES (?, ?)
        ON CONFLICT(Furnace) DO UPDATE SET Status = excluded.Status
        """,
        (name, status),
    )


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
) -> str:
    batch_id = make_batch_id(furnace, heat_no)
    existing = fetch_one("SELECT Batch_ID FROM Production_batch WHERE Batch_ID = ?", (batch_id,))
    if existing:
        raise ValueError(f"Batch ID {batch_id} already exists. Choose another Heat No.")

    total_weight = sum(float(i["Weight"]) for i in inputs)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO Production_batch
                (Batch_ID, Alloy_id, Production_Date, Shift, Furnace, Melt_No,
                 Heat_no, Melting_team, Weight, Notes, Status, Workflow_stage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending QA', 'Raw Material')
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
            ),
        )

        for item in inputs:
            # Deduct inventory
            lot = conn.execute(
                "SELECT Remaining_Weight FROM Raw_Material_Inventory WHERE Lot_id = ?",
                (item["Lot_id"],),
            ).fetchone()
            if not lot:
                raise ValueError(f"Lot {item['Lot_id']} not found.")
            remaining = float(lot["Remaining_Weight"] or 0)
            w = float(item["Weight"])
            if w > remaining + 1e-9:
                raise ValueError(
                    f"Insufficient stock on Lot {item['Lot_id']}: need {w}, have {remaining}."
                )
            conn.execute(
                """
                UPDATE Raw_Material_Inventory
                SET Remaining_Weight = Remaining_Weight - ?
                WHERE Lot_id = ?
                """,
                (w, item["Lot_id"]),
            )
            conn.execute(
                """
                INSERT INTO batch_input
                    (Batch_ID, Raw_Material_Name, Lot_id, Weight, Charge_time, Notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    item["Raw_Material_Name"],
                    item["Lot_id"],
                    w,
                    item.get("Charge_time") or datetime.now().isoformat(timespec="seconds"),
                    item.get("Notes", ""),
                ),
            )

        for sym, pct in composition.items():
            if pct is None:
                continue
            conn.execute(
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
    output_weight: Optional[float] = None,
) -> None:
    fields = ["Workflow_stage = ?"]
    params: list[Any] = [workflow_stage]
    if qa_status:
        fields.append("Status = ?")
        params.append(qa_status)
    if output_weight is not None:
        fields.append("Output_Weight = ?")
        params.append(output_weight)
    params.append(batch_id)
    execute(
        f"UPDATE Production_batch SET {', '.join(fields)} WHERE Batch_ID = ?",
        params,
    )


def get_batch(batch_id: str) -> Optional[sqlite3.Row]:
    return fetch_one("SELECT * FROM Production_batch WHERE Batch_ID = ?", (batch_id,))


def get_batch_inputs(batch_id: str) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT Raw_Material_Name, Lot_id, Weight, Charge_time, Notes
        FROM batch_input WHERE Batch_ID = ?
        ORDER BY Charge_time
        """,
        (batch_id,),
    )


def get_batch_chemistry(batch_id: str) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT Element_symbol, Percentage
        FROM Batch_Chemical_Composition WHERE Batch_ID = ?
        ORDER BY Element_symbol
        """,
        (batch_id,),
    )


def list_batches() -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT b.Batch_ID, b.Production_Date, b.Furnace, b.Heat_no, b.Melt_No,
               b.Shift, b.Weight, b.Output_Weight, b.Status, b.Workflow_stage,
               a.Alloy_name
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


# ---------- BOM ----------

def add_bom_line(
    bom_id: float,
    effective_date: str,
    customer: Optional[str],
    alloy_name: Optional[str],
    raw_material: Optional[str],
    quantity: float,
    sequence: float,
    notes: str,
) -> None:
    execute(
        """
        INSERT OR REPLACE INTO Build_of_Material
            (BOMID, Effective_date, Customer_Name, Alloy_Name,
             Raw_Material_Name, Quantity, Sequence_Order, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (bom_id, effective_date, customer, alloy_name, raw_material, quantity, sequence, notes),
    )


def today_str() -> str:
    return date.today().isoformat()
