import pandas as pd
import streamlit as st

import database as db
from pages_common import empty_percent_input, format_ui_date, show_dataframe, ui_date_input

BORING = db.BIL_BORING
BRIQ = db.BIL_BRIQUETTE

st.title("BIL Briquetting")
st.caption(
    f"**{BORING}** is pressed in house into **{BRIQ}**, which recovers better in the "
    f"furnace. Enter the date and the kg briquetted: that kg moves out of {BORING} "
    f"stock into {BRIQ} stock. It is taken from the oldest {BORING} lots first, and "
    "each new briquette lot stays on its original Brakes India receipt with the same "
    "cost, plant and purchase / conversion type. The weight cannot exceed the "
    f"{BORING} in stock on that date. A briquetting can be undone while its "
    "briquettes are untouched."
)

flash = st.session_state.pop("briq_flash", None)
if flash:
    st.success(flash)


def _kg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


stock = db.bil_briquetting_stock()
m1, m2, m3 = st.columns(3)
m1.metric(f"{BORING} in stock (kg)", f"{stock[BORING]['kg']:,.2f}")
m2.metric(f"{BRIQ} in stock (kg)", f"{stock[BRIQ]['kg']:,.2f}")
m3.metric(f"Open {BORING} lots", stock[BORING]["lots"])

if not stock["briquette_name"]:
    st.error(
        f"**{BRIQ}** is not in Raw Material Master yet. Add it there (with its "
        "recovery % and chemistry) before briquetting."
    )
elif stock["briquette_inactive"]:
    st.error(f"**{stock['briquette_name']}** is Inactive in Raw Material Master.")

lots = db.bil_boring_lots()
with st.expander(f"{BORING} lots in stock ({len(lots)})", expanded=False):
    if lots:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Lot": l["Lot_id"],
                        "Received": l.get("Received_date"),
                        "Invoice": l.get("Supplier_Invoice"),
                        "Plant": l.get("BIL_plant") or "—",
                        "Receipt": l.get("Receipt_type"),
                        "Status": l.get("Raw_Material_Status"),
                        "Bay": l.get("Storage_bay"),
                        "Remaining (kg)": round(l["Remaining_Weight"], 2),
                    }
                    for l in lots
                ]
            )
        )
    else:
        st.caption(f"No {BORING} in stock.")

# ── Briquetting entry ─────────────────────────────────────────────────────────
st.subheader(f"Convert {BORING} to {BRIQ}")
token = int(st.session_state.get("briq_token", 0))
c1, c2 = st.columns(2)
with c1:
    briq_date = ui_date_input(
        "Briquetting date *", value="today", max_value=db.today_ist(), key=f"briq_date_{token}"
    )
plan_avail = db.plan_bil_briquetting(0, briq_date)["available_kg"] if briq_date else 0.0
with c2:
    weight = empty_percent_input(
        f"Weight converted to {BRIQ} (kg) *",
        key=f"briq_weight_{token}",
        max_value=None,
        step=1.0,
        format="%.2f",
        placeholder=f"Up to {plan_avail:,.2f} kg",
    )
c3, c4 = st.columns(2)
with c3:
    bay = st.text_input(
        "Storage bay for the briquettes",
        key=f"briq_bay_{token}",
        placeholder="Leave blank to keep each lot's bay",
    )
with c4:
    notes = st.text_input("Notes", key=f"briq_notes_{token}", placeholder="Optional")

st.caption(
    f"{BORING} in stock on {format_ui_date(briq_date) if briq_date else '—'}: "
    f"**{plan_avail:,.2f} kg**."
)
over = weight is not None and weight > plan_avail + 0.005
if over:
    st.error(
        f"{weight:,.2f} kg is more than the {plan_avail:,.2f} kg of {BORING} in stock "
        "on that date."
    )
elif weight:
    plan = db.plan_bil_briquetting(weight, briq_date)
    st.markdown(
        f"**After saving:** {BORING} {stock[BORING]['kg'] - weight:,.2f} kg · "
        f"{BRIQ} {stock[BRIQ]['kg'] + weight:,.2f} kg"
    )
    show_dataframe(
        pd.DataFrame(
            [
                {
                    f"{BORING} lot": p["Lot_id"],
                    "Received": p.get("Received_date"),
                    "Plant": p.get("BIL_plant") or "—",
                    "Receipt": p.get("Receipt_type"),
                    "Takes (kg)": round(p["Take_kg"], 2),
                    "Lot left (kg)": round(p["Remaining_Weight"] - p["Take_kg"], 2),
                }
                for p in plan["parts"]
            ]
        )
    )

if st.button(
    f"Move to {BRIQ}",
    type="primary",
    key="briq_save",
    disabled=not weight or over or not stock["briquette_name"] or stock["briquette_inactive"],
):
    try:
        res = db.save_bil_briquetting(briq_date, weight, storage_bay=bay, notes=notes)
    except Exception as exc:
        st.error(f"Could not save: {exc}")
    else:
        after = db.bil_briquetting_stock()
        st.session_state["briq_flash"] = (
            f"Briquetting #{res['briquetting_id']} on {format_ui_date(res['date'])}: "
            f"{res['weight']:,.2f} kg moved from {BORING} to {res['target']} "
            f"(new lot{'s' if len(res['lines']) != 1 else ''} "
            f"{', '.join(str(l['New_lot_id']) for l in res['lines'])}). "
            f"{BORING} now {after[BORING]['kg']:,.2f} kg, "
            f"{res['target']} {after[BRIQ]['kg']:,.2f} kg."
        )
        st.session_state["briq_token"] = token + 1
        st.cache_data.clear()
        st.rerun()

# ── History ───────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Briquetting history")
history = db.list_bil_briquettings()
if not history:
    st.caption("No briquetting recorded yet.")
else:
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "#": h["Briquetting_id"],
                    "Date": h["Briquetting_date"],
                    "Weight (kg)": round(_kg(h["Weight"]), 2),
                    "From lots": h["Source_lots"],
                    "Briquette lots": h["New_lots"] if h["Status"] == "Done" else "",
                    "Bay": h.get("Storage_bay") or "",
                    "Notes": h.get("Notes") or "",
                    "Status": h["Status"],
                    "By": h.get("Created_by"),
                    "Entered": format_ui_date(h.get("Created_datetime"), with_time=True),
                    "Undone by": h.get("Reversed_by") or "",
                    "Undo reason": h.get("Reverse_reason") or "",
                }
                for h in history
            ]
        )
    )
    undoable = [h for h in history[:20] if h["Status"] == "Done" and db.bil_briquetting_can_undo(h["Briquetting_id"])]
    if undoable:
        with st.expander("Undo a briquetting entered by mistake"):
            st.caption(
                f"Puts the kg back on the {BORING} lots and removes the {BRIQ} lots it "
                "made. Only possible while none of those briquettes has been charged, "
                "scrapped, split or corrected."
            )
            pick = st.selectbox(
                "Briquetting",
                [h["Briquetting_id"] for h in undoable],
                index=None,
                format_func=lambda i: next(
                    f"#{h['Briquetting_id']} · {format_ui_date(h['Briquetting_date'])} · "
                    f"{_kg(h['Weight']):,.2f} kg · by {h.get('Created_by')}"
                    for h in undoable
                    if h["Briquetting_id"] == i
                ),
                key="briq_undo_pick",
                placeholder="Choose the briquetting to undo",
            )
            why = st.text_input("Reason *", key="briq_undo_reason", placeholder="e.g. Wrong weight entered")
            if st.button("Undo briquetting", key="briq_undo_go", disabled=pick is None or not why.strip()):
                try:
                    db.undo_bil_briquetting(pick, why)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("briq_undo_pick", None)
                    st.session_state.pop("briq_undo_reason", None)
                    st.session_state["briq_flash"] = f"Briquetting #{pick} undone; the kg is back on {BORING}."
                    st.cache_data.clear()
                    st.rerun()
