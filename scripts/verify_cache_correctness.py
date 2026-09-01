"""Prove the read cache never serves data that a write has already changed."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import event  # noqa: E402

import database as db  # noqa: E402

COUNT = {"n": 0}


def main() -> None:
    @event.listens_for(db.ENGINE, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        COUNT["n"] += 1

    db._ensure_packing_list_ready()
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' -- ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    print("read cache serves repeats without touching the database")
    db.bump_data_version()
    first = db.list_alloys()
    n0 = COUNT["n"]
    second = db.list_alloys()
    check("second call issued no statements", COUNT["n"] == n0)
    check("same rows returned", first == second)

    print("\na caller mutating the result cannot corrupt the cache")
    rows = db.list_alloys()
    if rows:
        rows[0]["Alloy_name"] = "MUTATED BY CALLER"
        rows[0]["injected_key"] = True
    fresh = db.list_alloys()
    check(
        "cache unaffected by caller mutation",
        bool(fresh) and fresh[0].get("Alloy_name") != "MUTATED BY CALLER",
        str(fresh[0].get("Alloy_name")) if fresh else "no alloys",
    )
    check("no injected keys leaked", not any("injected_key" in r for r in fresh))

    print("\nnested rows are copied too")
    pls = db.list_packing_lists_for_certificate()
    if pls:
        pl_id = int(pls[0]["Packing_list_id"])
        cert = db.get_packing_list_certificate(pl_id)
        if cert and cert.get("lines"):
            cert["lines"][0]["Display_heat_no"] = "MUTATED"
            again = db.get_packing_list_certificate(pl_id)
            check(
                "nested line list not corrupted",
                again["lines"][0].get("Display_heat_no") != "MUTATED",
            )
        else:
            check("nested line list not corrupted", True, "no certificate lines")

    print("\na write makes the change visible immediately")
    original = db.get_visual_inspection(pl_id) if pls else None
    if original:
        warm = db.get_visual_inspection(pl_id)
        n1 = COUNT["n"]
        db.get_visual_inspection(pl_id)
        check("cached before the write", COUNT["n"] == n1)

        flipped = [dict(r) for r in original]
        flipped[0]["Include_in_print"] = 0 if flipped[0]["Include_in_print"] else 1
        db.save_visual_inspection(pl_id, flipped)

        after = db.get_visual_inspection(pl_id)
        check(
            "read after write reflects the new value",
            after[0]["Include_in_print"] == flipped[0]["Include_in_print"],
            f"expected {flipped[0]['Include_in_print']}, got {after[0]['Include_in_print']}",
        )
        db.save_visual_inspection(pl_id, original)
        restored = db.get_visual_inspection(pl_id)
        check(
            "restore is visible too",
            restored[0]["Include_in_print"] == original[0]["Include_in_print"],
        )
        _ = warm

    print("\nTTL expiry")
    db.bump_data_version()

    @db.cached_read(ttl_s=1.0)
    def _short() -> float:
        return time.monotonic()

    a = _short()
    b = _short()
    check("within TTL the value is reused", a == b)
    time.sleep(1.2)
    c = _short()
    check("after TTL a fresh value is fetched", c != a)

    print("\nunhashable arguments fall through instead of raising")
    @db.cached_read(ttl_s=30)
    def _takes_list(items: list) -> int:
        return len(items)

    check("list argument handled", _takes_list([1, 2, 3]) == 3)

    print("\noversized results are not cached")
    db.bump_data_version()

    @db.cached_read(ttl_s=30)
    def _big() -> list:
        return [{"i": i} for i in range(db._READ_CACHE_MAX_ROWS + 1)]

    _big()
    check(
        "large result skipped",
        not any(k[0] == "_big" for k in db._READ_CACHE),
    )

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("all cache-correctness checks passed")


if __name__ == "__main__":
    main()
