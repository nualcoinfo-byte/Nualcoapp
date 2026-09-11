"""One-off import of tank dip-chart data (depth -> litres) from source Excel files.

Populates Service_Oil_Tank_Measurement (Inch, Litres) and
Ten_KL_Tank_Measurement (Centimeter, Litres) from the workbooks supplied by
the user. Safe to re-run: rows are upserted on the depth primary key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database as db

SERVICE_TANK_FILE = Path(r"C:\Users\DELL PC\Downloads\Service Oil Tank.xlsx")
TEN_KL_TANK_FILE = Path(r"C:\Users\DELL PC\Downloads\10 Kilo litres Tank.xlsx")


def _read_rows(path: Path) -> list[tuple[float, float]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for depth, litres in ws.iter_rows(min_row=2, values_only=True):
        if depth is None or litres is None:
            continue
        rows.append((float(depth), float(litres)))
    return rows


def main() -> None:
    db.init_db()

    service_rows = _read_rows(SERVICE_TANK_FILE)
    ten_kl_rows = _read_rows(TEN_KL_TANK_FILE)

    with db.get_connection() as conn:
        for inch, litres in service_rows:
            db._exec(
                conn,
                """
                INSERT INTO Service_Oil_Tank_Measurement (Inch, Litres)
                VALUES (?, ?)
                ON CONFLICT (Inch) DO UPDATE SET Litres = excluded.Litres
                """,
                (inch, litres),
            )
        for cm, litres in ten_kl_rows:
            db._exec(
                conn,
                """
                INSERT INTO Ten_KL_Tank_Measurement (Centimeter, Litres)
                VALUES (?, ?)
                ON CONFLICT (Centimeter) DO UPDATE SET Litres = excluded.Litres
                """,
                (cm, litres),
            )

    print(f"Service_Oil_Tank_Measurement: {len(service_rows)} rows loaded")
    print(f"Ten_KL_Tank_Measurement: {len(ten_kl_rows)} rows loaded")


if __name__ == "__main__":
    main()
