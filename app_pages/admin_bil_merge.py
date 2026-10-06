import json

import pandas as pd
import streamlit as st

import database as db
from pages_common import format_ui_date, show_dataframe

st.title("Merge BIL borings")
st.caption(
    f"One-time change: Brakes India's plant borings (MODULAR BORING, TRANSENERGY "
    f"BORING, ...) become one raw material, **{db.BIL_BORING}**, and each Brakes "
    "India receipt records the **plant** it came from instead. Review the preview, "
    "then **Merge**. It runs in one step (all or nothing) and is logged: it sets each "
    "receipt's plant from its lot names, creates BIL BORING in Raw Material Master "
    "(copying the recovery and spec of the plant boring), renames the plant borings "
    "to BIL BORING wherever they are stored (stock lots, charge lines, charge "
    "returns, scrap, corrections, supplier returns, bill of materials), and marks "
    "the old names Inactive. Their old master and spec rows stay for history."
)

if not db.is_admin_user():
    st.error("Only an Admin can merge raw materials.")
    st.stop()

flash = st.session_state.pop("bilm_flash", None)
if flash:
    st.success(flash)

preview = db.bil_merge_preview()
if not preview["sources"]:
    st.info(f"No plant borings are left to merge; Brakes India borings are {db.BIL_BORING}.")
else:
    st.markdown("**Raw materials that become " + db.BIL_BORING + ":** " + ", ".join(preview["sources"]))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Rows renamed per table**")
        show_dataframe(pd.DataFrame(preview["tables"]))
    with c2:
        st.markdown("**Stock lots under those names**")
        if preview["stock"]:
            show_dataframe(
                pd.DataFrame(
                    [
                        {
                            "Raw material": r["Raw_Material_Name"],
                            "Lots": int(r["Lots"]),
                            "Remaining (kg)": round(float(r["Remaining_kg"] or 0), 2),
                        }
                        for r in preview["stock"]
                    ]
                )
            )
        else:
            st.caption("No stock lots.")
        st.markdown("**Receipts that get their plant**")
        if preview["plants_to_set"]:
            show_dataframe(
                pd.DataFrame(
                    [{"Plant": k, "Receipts": v} for k, v in preview["plants_to_set"].items()]
                )
            )
        else:
            st.caption("None.")
    if preview["mixed_receipts"]:
        st.warning(
            "These receipts have lots from more than one plant, so their plant is not "
            "set automatically; set it below after the merge: "
            + ", ".join(f"#{p}" for p in preview["mixed_receipts"])
        )
    st.caption(
        f"{db.BIL_BORING} is "
        + ("already in Raw Material Master." if preview["target_exists"] else "created in Raw Material Master.")
    )
    sure = st.checkbox(
        f"Merge {', '.join(preview['sources'])} into {db.BIL_BORING}", key="bilm_sure"
    )
    if st.button("Merge", type="primary", key="bilm_go", disabled=not sure):
        try:
            res = db.apply_bil_merge()
        except Exception as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("bilm_sure", None)
            st.session_state["bilm_flash"] = (
                f"Merged {', '.join(res['sources'])} into {db.BIL_BORING}: "
                + ", ".join(f"{k} {v}" for k, v in res["changed"].items())
                + ". Plants set on "
                + str(sum(res["plants_set"].values()))
                + " receipts."
            )
            st.rerun()

# ── Receipts with no plant ────────────────────────────────────────────────────
st.divider()
st.subheader("Brakes India receipts with no plant")
missing = db.list_bil_receipts_without_plant()
if not missing:
    st.caption("Every Brakes India receipt has its plant.")
else:
    for r in missing[:50]:
        pid = int(r["Purchase_id"])
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.markdown(
            f"Receipt **#{pid}** · {r.get('Supplier_Invoice') or '—'} · "
            f"{format_ui_date(r.get('Received_date'))} · {r.get('Receipt_type')} · "
            f"{float(r.get('Received_kg') or 0):,.0f} kg"
        )
        plant = c2.selectbox(
            "Plant", db.BIL_PLANTS, index=None, key=f"bilm_plant_{pid}",
            label_visibility="collapsed", placeholder="Choose plant",
        )
        if c3.button("Set", key=f"bilm_set_{pid}", disabled=not plant):
            db.set_bil_plant(pid, plant)
            st.session_state["bilm_flash"] = f"Receipt #{pid} set to {plant}."
            st.rerun()
    if len(missing) > 50:
        st.caption(f"Showing 50 of {len(missing)}.")

st.divider()
st.subheader("Merges done")
merges = db.list_raw_material_merges()
if not merges:
    st.caption("None yet.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "When": format_ui_date(m.get("Changed_datetime"), with_time=True),
                    "By": m.get("Changed_by"),
                    "Merged": m.get("Source_names"),
                    "Into": m.get("Target_name"),
                    "Rows renamed": ", ".join(
                        f"{k}: {v}" for k, v in json.loads(m.get("Rows_changed") or "{}").items()
                    ),
                    "Plants set": m.get("Plants_set"),
                }
                for m in merges
            ]
        )
    )
