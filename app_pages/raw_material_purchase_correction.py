import pandas as pd
import streamlit as st

import database as db
from pages_common import empty_percent_input, format_ui_date, show_dataframe

ALL = "All"
SPLIT = "Split into other raw materials"
SCRAP = "Move to scrap"
RETURN = "Return to supplier"

st.title("Raw Material Purchase Correction")
st.caption(
    "Correct a purchased lot after segregation, without changing what was bought or "
    "its invoice value. **Split** reclassifies the lot into several raw materials at "
    "the same cost per kg (new lots stay on the same purchase and point back to this "
    "lot). **Move to scrap** takes iron, plastic, dirt and other contaminants out of "
    "stock into the scrap inventory, traced to the vendor. **Return to supplier** "
    "takes unordered material out of stock and hands it to Accounts for a sales "
    "invoice or debit note. Every correction is logged with who, when and why."
)

flash = st.session_state.pop("rmc_flash", None)
if flash:
    st.success(flash)

_employee = st.session_state.get("auth_employee") or {}
can_finance = db.role_allowed(
    _employee.get("role_name"), _employee.get("role_id"), db.RM_FINANCE_ROLES
)


def _kg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


tab_fix, tab_returns, tab_scrap, tab_history = st.tabs(
    ["Correct a lot", "Returns to suppliers", "Scrap inventory", "Correction history"]
)

# ══ Correct a lot ═════════════════════════════════════════════════════════════
with tab_fix:
    vendors = db.list_vendors()
    vendor_opts = {ALL: None} | {v["Vendor_name"]: v["Vendor_code"] for v in vendors}
    materials = db.list_raw_materials(active_only=False)
    f1, f2, f3 = st.columns([2, 2, 1])
    with f1:
        vendor_pick = st.selectbox("Supplier / vendor", list(vendor_opts), key="rmc_vendor")
    with f2:
        material_pick = st.selectbox("Raw material", [ALL] + materials, key="rmc_material")
    with f3:
        lot_text = st.text_input("Lot ID", key="rmc_lot_search", placeholder="e.g. 1234")
    lot_search = int(lot_text) if lot_text.strip().isdigit() else None
    if lot_text.strip() and lot_search is None:
        st.error("Lot ID is a number.")

    lots = db.list_correctable_lots(
        vendor_code=vendor_opts[vendor_pick],
        material=None if material_pick == ALL else material_pick,
        lot_id=lot_search,
    )
    if not lots:
        st.info("No purchased lot with stock left matches these filters.")
    else:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Lot": r["Lot_id"],
                        "Raw material": r["Raw_Material_Name"],
                        "Vendor": r.get("Vendor_name"),
                        "Invoice": r.get("Supplier_Invoice"),
                        "Received": format_ui_date(r.get("Received_date")),
                        "Received (kg)": round(_kg(r.get("Received_weight")), 2),
                        "Remaining (kg)": round(_kg(r.get("Remaining_Weight")), 2),
                        "Cost/kg": r.get("Cost_per_kg"),
                        "Split from lot": r.get("Parent_lot_id"),
                        "Receipt": r.get("Receipt_type"),
                    }
                    for r in lots[:300]
                ]
            ),
            height=min(38 + 35 * len(lots[:300]), 300),
        )
        if len(lots) > 300:
            st.caption(f"Showing the newest 300 of {len(lots)} lots; narrow the filters.")

        lot_labels = {
            r["Lot_id"]: f"Lot {r['Lot_id']}  |  {r['Raw_Material_Name']}  |  "
            f"{r.get('Vendor_name') or '—'}  |  {_kg(r.get('Remaining_Weight')):,.2f} kg left"
            for r in lots
        }
        if st.session_state.get("rmc_lot") not in lot_labels:
            st.session_state.pop("rmc_lot", None)  # filters changed; pick afresh
        lot_id = st.selectbox(
            "Lot to correct *",
            list(lot_labels),
            format_func=lambda v: lot_labels[v],
            key="rmc_lot",
        )
        lot = next(r for r in lots if r["Lot_id"] == lot_id)
        remaining = _kg(lot.get("Remaining_Weight"))
        cost = lot.get("Cost_per_kg")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Raw material", lot["Raw_Material_Name"])
        m2.metric("Remaining (kg)", f"{remaining:,.2f}")
        m3.metric("Cost per kg", f"{float(cost):,.2f}" if cost is not None else "—")
        m4.metric("Vendor", lot.get("Vendor_name") or "—")
        m5.metric("Invoice", lot.get("Supplier_Invoice") or "—")

        version = int(st.session_state.get(f"rmc_ver_{lot_id}", 0))
        key = f"rmc_{lot_id}_{version}"
        action = st.radio(
            "What do you want to do? *",
            [SPLIT, SCRAP, RETURN],
            horizontal=True,
            key=f"{key}_action",
        )

        def _done(message: str) -> None:
            st.session_state[f"rmc_ver_{lot_id}"] = version + 1
            st.session_state["rmc_flash"] = message
            st.rerun()

        if action == SPLIT:
            st.caption(
                "List what this lot really is. The lines must add up to the "
                f"**{remaining:,.2f} kg** left. A line for **{lot['Raw_Material_Name']}** "
                "stays on this lot; every other line becomes a new lot at the same cost "
                "per kg, on the same purchase."
            )
            n_key = f"{key}_n"
            if n_key not in st.session_state:
                st.session_state[n_key] = 2
                st.session_state[f"{key}_mat_0"] = lot["Raw_Material_Name"]
                st.session_state[f"{key}_wt_0"] = remaining
            n_lines = int(st.session_state[n_key])
            split_lines = []
            for i in range(n_lines):
                c1, c2 = st.columns([3, 1])
                with c1:
                    mat = st.selectbox(
                        f"Raw material {i + 1}",
                        materials,
                        index=None,
                        placeholder="Select raw material",
                        key=f"{key}_mat_{i}",
                    )
                with c2:
                    wt = empty_percent_input(
                        "Weight (kg)", key=f"{key}_wt_{i}", max_value=None, step=1.0
                    )
                split_lines.append({"material": mat or "", "weight": float(wt or 0)})
            b1, b2, _sp = st.columns([1, 1, 3])
            if b1.button("Add line", key=f"{key}_add"):
                st.session_state[n_key] = n_lines + 1
                st.rerun()
            if n_lines > 2 and b2.button("Remove last line", key=f"{key}_rem"):
                st.session_state.pop(f"{key}_mat_{n_lines - 1}", None)
                st.session_state.pop(f"{key}_wt_{n_lines - 1}", None)
                st.session_state[n_key] = n_lines - 1
                st.rerun()
            total = sum(ln["weight"] for ln in split_lines if ln["material"])
            diff = total - remaining
            s1, s2, s3 = st.columns(3)
            s1.metric("Split total (kg)", f"{total:,.2f}")
            s2.metric("Lot remaining (kg)", f"{remaining:,.2f}")
            s3.metric("Difference (kg)", f"{diff:+,.2f}")
            ok = abs(diff) <= 0.005 and any(
                ln["material"] and ln["material"].lower() != lot["Raw_Material_Name"].lower()
                and ln["weight"] > 0
                for ln in split_lines
            )
            if abs(diff) > 0.005:
                st.warning("The split must add up exactly to the remaining weight.")
            reason = st.text_input(
                "Reason (optional)", key=f"{key}_reason",
                placeholder="e.g. Segregated on 05-Oct: extrusion and sheet found",
            )
            if st.button("Save split", type="primary", key=f"{key}_save", disabled=not ok):
                try:
                    cid = db.split_raw_material_lot(
                        lot_id, split_lines, reason=reason, expected_remaining=remaining
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    _done(f"Lot {lot_id} split (correction #{cid}). New lots are on Raw Material Inventory.")

        elif action == SCRAP:
            c1, c2 = st.columns([1, 1])
            with c1:
                qty = empty_percent_input(
                    "Scrap quantity (kg) *", key=f"{key}_qty", max_value=None, step=1.0
                )
            with c2:
                category = st.selectbox(
                    "What is it? *", db.SCRAP_CATEGORIES, index=None,
                    placeholder="Iron, plastic, dirt ...", key=f"{key}_cat",
                )
            q = float(qty or 0)
            if q > remaining + 0.005:
                st.error(f"Only {remaining:,.2f} kg is left on this lot.")
            elif q > 0:
                st.caption(
                    f"Lot {lot_id} will have **{remaining - q:,.2f} kg** left. Scrap value "
                    f"**{q * float(cost or 0):,.2f}** at the lot's cost per kg."
                )
            reason = st.text_input("Notes (optional)", key=f"{key}_reason")
            if st.button(
                "Move to scrap", type="primary", key=f"{key}_save",
                disabled=not (0 < q <= remaining + 0.005 and category),
            ):
                try:
                    sid = db.scrap_raw_material(
                        lot_id, q, category=category, reason=reason,
                        expected_remaining=remaining,
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    _done(f"{q:,.2f} kg of lot {lot_id} moved to scrap (scrap #{sid}).")

        else:
            qty = empty_percent_input(
                "Return quantity (kg) *", key=f"{key}_qty", max_value=None, step=1.0
            )
            q = float(qty or 0)
            if q > remaining + 0.005:
                st.error(f"Only {remaining:,.2f} kg is left on this lot.")
            elif q > 0:
                st.caption(
                    f"Lot {lot_id} will have **{remaining - q:,.2f} kg** left. Value "
                    f"**{q * float(cost or 0):,.2f}** at the lot's cost per kg, for "
                    "Accounts to recover by a sales invoice or debit note."
                )
            reason = st.text_input(
                "Reason *", key=f"{key}_reason",
                placeholder="e.g. Wrong grade supplied / excess material",
            )
            if st.button(
                "Send to Accounts for return", type="primary", key=f"{key}_save",
                disabled=not (0 < q <= remaining + 0.005 and reason.strip()),
            ):
                try:
                    rid = db.return_raw_material(
                        lot_id, q, reason=reason, expected_remaining=remaining
                    )
                except Exception as exc:
                    st.error(str(exc))
                else:
                    _done(
                        f"{q:,.2f} kg of lot {lot_id} sent to Accounts as return #{rid} "
                        "(Pending Finance Action)."
                    )

# ══ Returns to suppliers ══════════════════════════════════════════════════════
with tab_returns:
    st.caption(
        "Material taken out of stock to go back to the supplier. Accounts raises a "
        "**sales invoice** or a **debit note**, then marks the return **Completed**."
        + ("" if can_finance else " Only Accounts, Management and Admin can do that.")
    )
    show_closed = st.checkbox("Show completed returns", key="rmc_show_closed")
    returns = db.list_raw_material_returns(open_only=not show_closed)
    if not returns:
        st.info("No open supplier returns." if not show_closed else "No supplier returns yet.")
    else:
        pending_value = sum(
            _kg(r["Returned_qty"]) * _kg(r.get("Cost_per_kg"))
            for r in returns
            if r["Status"] != db.RM_RETURN_COMPLETED
        )
        st.metric("Value awaiting settlement", f"{pending_value:,.2f}")
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Return": r["Return_id"],
                        "Status": r["Status"],
                        "Vendor": r.get("Vendor_name"),
                        "Purchase invoice": r.get("Supplier_Invoice"),
                        "Lot": r["Lot_id"],
                        "Raw material": r.get("Raw_Material_Name"),
                        "Qty (kg)": round(_kg(r["Returned_qty"]), 2),
                        "Value": round(_kg(r["Returned_qty"]) * _kg(r.get("Cost_per_kg")), 2),
                        "Reason": r.get("Return_reason"),
                        "Sales invoice": r.get("Invoice_number") or "",
                        "Debit note": r.get("Debit_note_number") or "",
                        "Raised by": r.get("Created_by"),
                        "Raised": format_ui_date(r.get("Created_at"), with_time=True),
                        "Completed": format_ui_date(r.get("Completed_at"), with_time=True),
                    }
                    for r in returns
                ]
            )
        )
        if can_finance:
            open_returns = [r for r in returns if r["Status"] != db.RM_RETURN_COMPLETED]
            for r in open_returns:
                rid = r["Return_id"]
                label = (
                    f"Return #{rid} · {r.get('Vendor_name') or '—'} · "
                    f"{r.get('Raw_Material_Name')} · {_kg(r['Returned_qty']):,.2f} kg · {r['Status']}"
                )
                with st.expander(label):
                    if r["Status"] == db.RM_RETURN_PENDING:
                        a1, a2 = st.columns([1, 1])
                        with a1:
                            fin_action = st.radio(
                                "Finance action *",
                                [db.RM_FINANCE_SALES_INVOICE, db.RM_FINANCE_DEBIT_NOTE],
                                index=None, horizontal=True, key=f"rmc_fin_{rid}",
                            )
                        with a2:
                            number = st.text_input(
                                "Sales invoice / debit note number *", key=f"rmc_fin_no_{rid}"
                            )
                        notes = st.text_input("Notes (optional)", key=f"rmc_fin_notes_{rid}")
                        if st.button(
                            "Record", key=f"rmc_fin_save_{rid}",
                            disabled=not (fin_action and number.strip()),
                        ):
                            try:
                                db.record_raw_material_return_finance(
                                    rid, fin_action, number, notes
                                )
                            except Exception as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["rmc_flash"] = (
                                    f"Return #{rid}: {fin_action.lower()} {number.strip()} recorded."
                                )
                                st.rerun()
                    else:
                        doc = r.get("Invoice_number") or r.get("Debit_note_number")
                        st.markdown(
                            f"**{r.get('Finance_action')}** {doc} raised by "
                            f"{r.get('Finance_by') or '—'} on "
                            f"{format_ui_date(r.get('Finance_at'), with_time=True)}."
                        )
                        if st.button("Mark completed", key=f"rmc_complete_{rid}"):
                            try:
                                db.complete_raw_material_return(rid)
                            except Exception as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["rmc_flash"] = f"Return #{rid} completed."
                                st.rerun()

# ══ Scrap inventory ═══════════════════════════════════════════════════════════
with tab_scrap:
    st.caption(
        "All scrap in one place: contaminants found at purchase segregation and charge "
        "material returned as scrap from production, each traced to its lot, purchase "
        "and vendor, valued at the lot's cost per kg."
    )
    scrap = db.list_scrap_inventory()
    if not scrap:
        st.info("No scrap recorded yet.")
    else:
        sdf = pd.DataFrame(scrap)
        sources = [ALL] + sorted(sdf["Source_type"].dropna().unique().tolist())
        g1, g2 = st.columns(2)
        with g1:
            src = st.selectbox("Source", sources, key="rmc_scrap_src")
        with g2:
            vnames = [ALL] + sorted(sdf["Vendor_name"].dropna().unique().tolist())
            ven = st.selectbox("Vendor", vnames, key="rmc_scrap_vendor")
        if src != ALL:
            sdf = sdf[sdf["Source_type"] == src]
        if ven != ALL:
            sdf = sdf[sdf["Vendor_name"] == ven]
        k1, k2, k3 = st.columns(3)
        k1.metric("Scrap (kg)", f"{sdf['Weight'].astype(float).sum():,.2f}")
        k2.metric("Scrap value", f"{sdf['Value'].astype(float).sum():,.2f}")
        k3.metric("Entries", len(sdf))
        view = pd.DataFrame(
            {
                "Scrap": sdf["Scrap_id"],
                "Date": sdf["Created_datetime"].map(lambda v: format_ui_date(v)),
                "Source": sdf["Source_type"],
                "Vendor": sdf["Vendor_name"],
                "Raw material": sdf["Raw_Material_Name"],
                "Lot": sdf["Lot_id"],
                "Purchase invoice": sdf["Supplier_Invoice"],
                "Batch": sdf["Batch_ID"],
                "What": sdf["Scrap_category"],
                "Weight (kg)": sdf["Weight"].astype(float).round(2),
                "Cost/kg": sdf["Cost_per_kg"],
                "Value": sdf["Value"].astype(float).round(2),
                "Notes": sdf["Notes"],
                "By": sdf["Created_by"],
            }
        )
        show_dataframe(view)
        st.download_button(
            "Download CSV",
            data=view.to_csv(index=False).encode("utf-8"),
            file_name=f"scrap_inventory_{db.today_ist().isoformat()}.csv",
            mime="text/csv",
            key="rmc_scrap_csv",
        )

    st.markdown("#### Supplier quality")
    summary = db.vendor_scrap_summary()
    if summary:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Vendor": r["Vendor_name"],
                        "Received (kg)": round(r["Received_kg"], 2),
                        "Segregation scrap (kg)": round(r["Segregation_scrap_kg"], 2),
                        "Scrap %": None if r["Segregation_scrap_pct"] is None
                        else round(r["Segregation_scrap_pct"], 2),
                        "Scrap value": round(r["Segregation_scrap_value"], 2),
                        "Returned (kg)": round(r["Returned_kg"], 2),
                        "Returned %": None if r["Returned_pct"] is None
                        else round(r["Returned_pct"], 2),
                        "Production scrap (kg)": round(r["Production_scrap_kg"], 2),
                    }
                    for r in summary
                ]
            )
        )
        st.caption(
            "Scrap % and Returned % are against all kg received from the vendor. "
            "Production scrap is charge material returned as scrap from heats, by the "
            "vendor of the lot it was charged from."
        )
    else:
        st.caption("No vendor has scrap or returns yet.")

# ══ Correction history ════════════════════════════════════════════════════════
with tab_history:
    h_text = st.text_input("Lot ID (optional)", key="rmc_hist_lot", placeholder="All lots")
    h_lot = int(h_text) if h_text.strip().isdigit() else None
    history = db.list_raw_material_corrections(h_lot, limit=200)
    if not history:
        st.info("No corrections yet.")
    else:
        def _result(c: dict) -> str:
            parts = []
            for ln in c["lines"]:
                if ln.get("New_lot_id"):
                    parts.append(f"{ln['Raw_Material_Name']} {_kg(ln['Weight']):,.2f} kg → lot {ln['New_lot_id']}")
                elif ln.get("Scrap_id"):
                    parts.append(f"{ln['Raw_Material_Name']} {_kg(ln['Weight']):,.2f} kg → scrap #{ln['Scrap_id']}")
                elif ln.get("Return_id"):
                    parts.append(f"{_kg(ln['Weight']):,.2f} kg → return #{ln['Return_id']}")
                else:
                    parts.append(f"{ln['Raw_Material_Name']} {_kg(ln['Weight']):,.2f} kg stays")
            return "; ".join(parts)

        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Correction": c["Correction_id"],
                        "When": format_ui_date(c.get("Corrected_datetime"), with_time=True),
                        "By": c.get("Corrected_by"),
                        "Type": c["Correction_type"],
                        "Lot": c["Lot_id"],
                        "Raw material": c.get("Raw_Material_Name"),
                        "Vendor": c.get("Vendor_name"),
                        "Kg": round(_kg(c["Weight"]), 2),
                        "Remaining before": round(_kg(c.get("Remaining_before")), 2),
                        "Remaining after": round(_kg(c.get("Remaining_after")), 2),
                        "Result": _result(c),
                        "Reason": c.get("Reason") or "",
                    }
                    for c in history
                ]
            )
        )
