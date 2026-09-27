"""Compress photos already stored in the database (one-time clean-up).

New photos are compressed when they are taken (pages_common.compress_photo). This shrinks the ones saved before that
the same way: upright, longest side 1600 px, JPEG quality 80. Phone photos of 3-7 MB become ~150-300 KB.

    python scripts/compress_existing_photos.py                          # development: report only, change nothing
    python scripts/compress_existing_photos.py --apply                  # development: compress
    python scripts/compress_existing_photos.py --target production      # production: report only
    python scripts/compress_existing_photos.py --target production --apply --yes-production
    python scripts/compress_existing_photos.py --restore backups/photo_originals/<run folder>   # put originals back

What it touches
  * Photo columns only: batch_input, batch_output, raw_material_purchase (vehicle / weighment slip),
    raw_material_inventory, raw_material_master and the legacy production_batch photo1-3.
  * Never documents whose file type is stored beside them (invoices, PO documents, deviation letters,
    furnace-oil documents): converting those to JPEG would make the stored type wrong.
  * A photo is rewritten only if the compressed copy is at least 20% smaller, so already-compressed photos are
    skipped and running it twice changes nothing. Non-images (e.g. a PDF saved as a photo) are left alone.

Safety
  * Report only unless --apply. Production additionally needs --yes-production.
  * Development requires APP_ENV=development|staging in .env.local; production uses PRODUCTION_DATABASE_URL from
    .env.production.local.
  * Before a row is rewritten its original photo is saved under backups/photo_originals/<run>/ (with a
    manifest.csv), and --restore writes those originals back. Each row is its own transaction, so a run stopped
    half-way leaves every row either original or compressed, and can simply be run again.
  * Postgres keeps the freed space for reuse; it is handed back to the disk only by VACUUM FULL (--vacuum-full,
    which briefly locks each table: run it when nobody is using the app).

Never prints credentials.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from refresh_staging_db import describe, die, engine, read_env  # noqa: E402

# table -> (primary-key columns, photo columns). Documents with a stored file type are deliberately absent.
PHOTO_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "batch_input": (
        ("batch_id", "raw_material_name", "lot_id", "charge_time"),
        ("weighment_scale_photo", "input_photo"),
    ),
    "batch_output": (("output_id",), ("weighment_scale_photo", "output_photo")),
    "raw_material_purchase": (("purchase_id",), ("vehicle_photo", "weighment_slip_photo")),
    "raw_material_inventory": (("lot_id",), ("photo",)),
    "raw_material_master": (("raw_material_name", "effective_date"), ("photo",)),
    "production_batch": (("batch_id",), ("photo1", "photo2", "photo3")),
}
MIN_SAVING = 0.20  # rewrite only when the compressed copy is at least this much smaller
BACKUP_ROOT = REPO / "backups" / "photo_originals"


def target_url(target: str) -> str:
    if target == "production":
        url = read_env(REPO / ".env.production.local").get("PRODUCTION_DATABASE_URL", "")
        if not url:
            die("PRODUCTION_DATABASE_URL is not set in .env.production.local.")
        return url
    env = read_env(REPO / ".env.local")
    if env.get("APP_ENV", "").lower() not in ("development", "staging"):
        die("APP_ENV in .env.local must be 'development' or 'staging'.")
    url = env.get("DATABASE_URL", "")
    if not url:
        die("DATABASE_URL is not set in .env.local.")
    return url


def existing_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = %(t)s",
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def mb(n: float) -> str:
    return f"{n / 1e6:,.1f} MB"


def compress_run(url: str, *, apply: bool, backup_dir: Path | None) -> None:
    from pages_common import compress_photo  # same settings as new uploads

    eng = engine(url)
    manifest = None
    if apply:
        backup_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = open(backup_dir / "manifest.csv", "w", newline="", encoding="utf-8")
        manifest = csv.writer(manifest_file)
        manifest.writerow(["table", "column", "pk_json", "file", "bytes_before", "bytes_after"])

    grand_before = grand_after = 0
    grand_rewritten = 0
    try:
        for table, (pk_cols, photo_cols) in PHOTO_COLUMNS.items():
            with eng.connect() as conn:
                present = existing_columns(conn, table)
            if not present:
                continue
            cols = [c for c in photo_cols if c in present]
            for col in cols:
                pk_list = ", ".join(pk_cols)
                with eng.connect() as conn:
                    keys = conn.exec_driver_sql(
                        f"select {pk_list}, octet_length({col}) from {table} "
                        f"where {col} is not null order by octet_length({col}) desc"
                    ).fetchall()
                if not keys:
                    continue
                before = after = rewritten = skipped = 0
                started = time.time()
                where = " and ".join(f"{c} = %(k{i})s" for i, c in enumerate(pk_cols))
                for row in keys:
                    params = {f"k{i}": row[i] for i in range(len(pk_cols))}
                    with eng.connect() as conn:
                        raw = conn.exec_driver_sql(
                            f"select {col} from {table} where {where}", params
                        ).scalar()
                    raw = bytes(raw) if raw is not None else b""
                    small = compress_photo(raw) if raw else raw
                    before += len(raw)
                    if raw and len(small) <= len(raw) * (1 - MIN_SAVING):
                        after += len(small)
                        rewritten += 1
                        if apply:
                            pk_values = [str(row[i]) for i in range(len(pk_cols))]
                            safe = "_".join("".join(ch if ch.isalnum() or ch in "-." else "-" for ch in v) for v in pk_values)
                            ext = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if raw[:3] == b"\xff\xd8\xff" else ".bin"
                            rel = Path(table) / col / f"{safe}{ext}"
                            (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                            (backup_dir / rel).write_bytes(raw)
                            with eng.begin() as conn:
                                conn.exec_driver_sql(
                                    f"update {table} set {col} = %(photo)s where {where}",
                                    {**params, "photo": small},
                                )
                            manifest.writerow([table, col, json.dumps(pk_values), str(rel), len(raw), len(small)])
                            manifest_file.flush()
                    else:
                        after += len(raw)
                        skipped += 1
                grand_before += before
                grand_after += after
                grand_rewritten += rewritten
                verb = "compressed" if apply else "would compress"
                print(
                    f"  {table}.{col}: {len(keys)} photo(s), {mb(before)} -> {mb(after)}; "
                    f"{verb} {rewritten}, left {skipped} as they are ({time.time() - started:.0f}s)"
                )
    finally:
        if manifest is not None:
            manifest_file.close()

    saved = grand_before - grand_after
    pct = (saved / grand_before * 100) if grand_before else 0
    print(
        f"Total: {mb(grand_before)} -> {mb(grand_after)} ({mb(saved)}, {pct:.0f}% smaller), "
        f"{grand_rewritten} photo(s) {'compressed' if apply else 'to compress'}."
    )
    if apply and grand_rewritten:
        print(f"Originals saved in {backup_dir}")
    elif not apply:
        print("Nothing was changed. Add --apply to compress.")


def restore_run(url: str, backup_dir: Path) -> None:
    manifest = backup_dir / "manifest.csv"
    if not manifest.exists():
        die(f"No manifest.csv in {backup_dir}.")
    eng = engine(url)
    restored = 0
    with open(manifest, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            table, col = rec["table"], rec["column"]
            if table not in PHOTO_COLUMNS or col not in PHOTO_COLUMNS[table][1]:
                die(f"Unexpected column {table}.{col} in the manifest.")
            pk_cols = PHOTO_COLUMNS[table][0]
            pk_values = json.loads(rec["pk_json"])
            params = {f"k{i}": v for i, v in enumerate(pk_values)}
            params["photo"] = (backup_dir / rec["file"]).read_bytes()
            where = " and ".join(f"{c}::text = %(k{i})s" for i, c in enumerate(pk_cols))
            with eng.begin() as conn:
                conn.exec_driver_sql(f"update {table} set {col} = %(photo)s where {where}", params)
            restored += 1
    print(f"Restored {restored} original photo(s) from {backup_dir}.")


def vacuum_full(url: str) -> None:
    eng = engine(url).execution_options(isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for table in PHOTO_COLUMNS:
            if not existing_columns(conn, table):
                continue
            started = time.time()
            conn.exec_driver_sql(f"vacuum full {table}")
            size = conn.exec_driver_sql(f"select pg_total_relation_size('{table}')").scalar()
            print(f"  vacuum full {table}: {mb(size)} ({time.time() - started:.1f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--target", choices=["development", "production"], default="development")
    ap.add_argument("--apply", action="store_true", help="compress (default: report only)")
    ap.add_argument("--yes-production", action="store_true", help="required with --target production --apply")
    ap.add_argument("--restore", metavar="BACKUP_DIR", help="write the originals in BACKUP_DIR back")
    ap.add_argument("--vacuum-full", action="store_true", help="hand freed space back to the disk (locks tables briefly)")
    args = ap.parse_args()

    writes = args.apply or args.restore or args.vacuum_full
    if args.target == "production" and writes and not args.yes_production:
        die("Changing production needs --yes-production as well.")
    url = target_url(args.target)
    print(f"Target: {args.target} ({describe(url)})")

    if args.restore:
        restore_run(url, Path(args.restore))
        return
    backup_dir = None
    if args.apply:
        backup_dir = BACKUP_ROOT / f"{args.target}_{time.strftime('%Y%m%d_%H%M%S')}"
    compress_run(url, apply=args.apply, backup_dir=backup_dir)
    if args.vacuum_full:
        vacuum_full(url)


if __name__ == "__main__":
    main()
