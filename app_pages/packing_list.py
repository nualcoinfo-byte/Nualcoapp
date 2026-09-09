import html
import streamlit as st
import database as db
from datetime import date
import pandas as pd
from pages_common import _parse_master_date, df_from_rows, format_ui_date, show_dataframe, to_storage_date, ui_date_input

_BRAND_ORANGE = "#F15A22"
_BRAND_INK = "#1A1A1A"


def _packing_summary_from_saved(packing_list_id: int) -> dict | None:
    header = db.get_packing_list(packing_list_id)
    if not header:
        return None
    return {
        "packing_list_id": header.get("Packing_list_id"),
        "invoice_date": header.get("Invoice_date"),
        "invoice_number": header.get("Invoice_number") or "",
        "po_no": header.get("Customer_PO_No") or "",
        "customer_name": header.get("Customer_name") or "",
        "alloy_name": header.get("Alloy_name") or "",
        "colour_code": header.get("Colour_code") or "",
        "vehicle_no": header.get("Vehicle_no") or "",
        "status": header.get("Packing_list_status") or "",
        "batches": list(header.get("batches") or []),
    }


def _packing_summary_from_form() -> dict:
    inv_date = st.session_state.get("pl_invoice_date")
    cust_label = str(st.session_state.get("pl_cust") or "")
    customer_name = cust_label.split(" (")[0] if " (" in cust_label else cust_label
    alloy_label = str(st.session_state.get("pl_alloy") or "")
    alloy_name = alloy_label.rsplit(" (#", 1)[0] if " (#" in alloy_label else alloy_label
    alloy_id = st.session_state.get("pl_alloy_id")
    colour_code = ""
    if alloy_id not in (None, ""):
        master = db.get_alloy(alloy_id) or {}
        colour_code = master.get("Colour_code") or ""
    batches: list[dict] = []
    for bid in st.session_state.get("pl_batches") or []:
        bid = str(bid).strip()
        if not bid:
            continue
        qty = (st.session_state.get("pl_batch_qty") or {}).get(bid) or {}
        batches.append(
            {
                "Batch_ID": bid,
                "Heat_no": qty.get("Heat_no"),
                "Weight": float(qty.get("Weight") or 0),
                "Pieces": int(float(qty.get("Pieces") or 0)),
            }
        )
    return {
        "packing_list_id": st.session_state.get("pl_edit_id"),
        "invoice_date": to_storage_date(inv_date) if inv_date else None,
        "invoice_number": str(st.session_state.get("pl_invoice") or "").strip(),
        "po_no": str(st.session_state.get("pl_po") or "").strip(),
        "customer_name": customer_name,
        "alloy_name": alloy_name,
        "colour_code": colour_code,
        "vehicle_no": str(st.session_state.get("pl_vehicle") or "").strip(),
        "status": str(st.session_state.get("pl_status") or "").strip(),
        "batches": batches,
    }


def _render_packing_list_summary(summary: dict) -> None:
    st.markdown(
        f"""
        <style>
        .packing-summary-wrap {{
            border: 1px solid #ddd;
            border-radius: 6px;
            padding: 1.25rem 1.5rem;
            background: #fff;
            color: {_BRAND_INK};
        }}
        .packing-summary-title {{
            margin: 0 0 0.25rem 0;
            color: {_BRAND_INK};
            border-bottom: 2px solid {_BRAND_ORANGE};
            padding-bottom: 0.35rem;
        }}
        .packing-summary-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 0.75rem 0 0 0;
            font-size: 0.95rem;
        }}
        .packing-summary-table th,
        .packing-summary-table td {{
            border: 1px solid #bdbdbd;
            padding: 0.55rem 0.7rem;
            text-align: left;
            vertical-align: top;
        }}
        .packing-summary-meta td:first-child {{
            width: 30%;
            font-weight: 600;
            background: #f7f7f7;
        }}
        .packing-summary-batches th {{
            background: #f0f0f0;
            font-weight: 700;
        }}
        .packing-summary-batches tfoot td {{
            font-weight: 700;
            background: #fafafa;
        }}
        .packing-summary-note {{
            margin-top: 0.75rem;
            font-size: 0.85rem;
            color: #555;
        }}
        @media print {{
            [data-testid="stSidebar"],
            [data-testid="stToolbar"],
            footer,
            header,
            .no-print {{
                display: none !important;
            }}
            .block-container {{
                max-width: 100% !important;
                padding: 0.25rem !important;
            }}
            .packing-summary-wrap {{
                border: none;
                padding: 0;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    list_ref = summary.get("packing_list_id")
    title = "Packing List Summary"
    if list_ref:
        title += f" — #{list_ref}"

    inv_date = format_ui_date(summary.get("invoice_date"), empty="—")
    meta_rows = [
        ("Invoice date", inv_date),
        ("Invoice number", summary.get("invoice_number") or "—"),
        ("P.O. Number", summary.get("po_no") or "—"),
        ("Customer name", summary.get("customer_name") or "—"),
        ("Alloy name", summary.get("alloy_name") or "—"),
        ("Colour code", summary.get("colour_code") or "—"),
        ("Vehicle No", summary.get("vehicle_no") or "—"),
    ]
    if summary.get("status"):
        meta_rows.append(("Status", summary.get("status")))

    meta_html = "".join(
        f"<tr><td>{html.escape(label)}</td>"
        f"<td>{html.escape(str(value))}</td></tr>"
        for label, value in meta_rows
    )

    batches = summary.get("batches") or []
    batch_rows_html = ""
    total_w = 0.0
    total_p = 0
    for row in batches:
        bid = str(row.get("Batch_ID") or "—")
        heat = str(row.get("Heat_no") or "—")
        weight = float(row.get("Weight") or 0)
        pieces = int(float(row.get("Pieces") or 0))
        total_w += weight
        total_p += pieces
        batch_rows_html += (
            "<tr>"
            f"<td>{html.escape(bid)}</td>"
            f"<td>{html.escape(heat)}</td>"
            f"<td style='text-align:right'>{weight:,.2f}</td>"
            f"<td style='text-align:right'>{pieces:,}</td>"
            "</tr>"
        )
    if not batch_rows_html:
        batch_rows_html = (
            "<tr><td colspan='4' style='text-align:center;color:#666'>"
            "No batches selected</td></tr>"
        )
        footer_html = ""
    else:
        footer_html = (
            "<tfoot><tr>"
            "<td colspan='2'>Total</td>"
            f"<td style='text-align:right'>{total_w:,.2f}</td>"
            f"<td style='text-align:right'>{total_p:,}</td>"
            "</tr></tfoot>"
        )

    st.markdown(
        f"""
        <div class="packing-summary-wrap">
            <h2 class="packing-summary-title">{html.escape(title)}</h2>
            <table class="packing-summary-table packing-summary-meta">
                <tbody>{meta_html}</tbody>
            </table>
            <table class="packing-summary-table packing-summary-batches">
                <thead>
                    <tr>
                        <th>Batch ID</th>
                        <th>Heat No</th>
                        <th style="text-align:right">Weight (kg)</th>
                        <th style="text-align:right">Pieces</th>
                    </tr>
                </thead>
                <tbody>{batch_rows_html}</tbody>
                {footer_html}
            </table>
            <p class="packing-summary-note">
                Verify invoice details and batch quantities before dispatch.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, _c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("Back to packing list", key="pl_summary_back", type="secondary"):
            st.session_state.pop("pl_show_summary", None)
            st.session_state.pop("pl_summary_id", None)
            st.rerun()
    with c2:
        st.markdown(
            '<button class="no-print" onclick="window.print()" '
            'style="padding:0.45rem 1rem;border:1px solid #ccc;border-radius:0.5rem;'
            'background:#fff;cursor:pointer;font-size:0.9rem;">Print summary</button>',
            unsafe_allow_html=True,
        )


if st.session_state.get("pl_show_summary"):
    summary_id = st.session_state.get("pl_summary_id")
    if summary_id:
        summary = _packing_summary_from_saved(int(summary_id))
        if not summary:
            st.title("Packing List")
            st.error("That packing list was not found.")
            if st.button("Back to packing list", key="pl_summary_missing_back"):
                st.session_state.pop("pl_show_summary", None)
                st.session_state.pop("pl_summary_id", None)
                st.rerun()
            st.stop()
    else:
        summary = _packing_summary_from_form()
    st.title("Packing List")
    _render_packing_list_summary(summary)
    st.stop()

st.title("Packing List")
st.caption(
    "Enter invoice details, then select **batch_id**s from Available finished goods. "
    "A heat can be packed in part: enter only the kg and pieces on this list. "
    "Saving as **Verified** subtracts that quantity from finished-goods inventory; "
    "the remainder stays Available. **Cancel packing list** returns packed qty to "
    "finished goods. After the test certificate is **Issued**, dispatch is final "
    "and only an Admin can reverse it. Avg piece is packed weight ÷ pieces; typical "
    f"product alloy pieces are **{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** "
    "and show in red if outside that range."
)
try:
    db.backfill_finished_goods_from_output()
except Exception as exc:
    st.warning(f"Could not refresh finished goods from batch output: {exc}")

def _clear_packing_form() -> None:
    keep = {"pl_load_pick"}
    for key in list(st.session_state.keys()):
        if str(key).startswith("pl_") and key not in keep:
            st.session_state.pop(key, None)

def _cust_label(row: dict) -> str:
    return f"{row.get('Customer_name') or '—'} ({row.get('Cust_code') or '—'})"

def _alloy_label(row: dict) -> str:
    return f"{row.get('Alloy_name') or '—'} (#{row.get('Alloy_id')})"

existing_lists = db.list_packing_lists()
po_numbers = db.list_packing_po_numbers()
editing_id = st.session_state.get("pl_edit_id")
edit_cert = (
    db.get_packing_list_certificate(int(editing_id)) if editing_id else None
)
issued_locked = bool(
    edit_cert and edit_cert.get("Status") == db.CERT_STATUS_ISSUED
)

top1, top2, top3 = st.columns([3, 1, 1])
with top1:
    if editing_id:
        if issued_locked:
            st.warning(
                f"Packing list **#{editing_id}** has issued test certificate "
                f"**{edit_cert.get('Certificate_no') or ''}**. Dispatch is final. "
                "Packed batches cannot be changed or cancelled here. An Admin "
                "can reverse this from **Admin → Cancel issued certificate**."
            )
        else:
            st.info(
                f"Editing packing list **#{editing_id}**. "
                "Save to update, or start a new list."
            )
    else:
        st.markdown("#### New packing list")
with top2:
    if st.button("View summary", key="pl_view_summary"):
        st.session_state["pl_show_summary"] = True
        st.session_state.pop("pl_summary_id", None)
        st.rerun()
with top3:
    if st.button("New packing list", key="pl_new"):
        _clear_packing_form()
        st.rerun()

r1c1, r1c2, r1c3 = st.columns(3)
with r1c1:
    invoice_date = ui_date_input(
        "Invoice date *",
        value=date.today(),
        key="pl_invoice_date",
    )
with r1c2:
    invoice_number = st.text_input("Invoice number *", key="pl_invoice")
with r1c3:
    po_no = st.selectbox(
        "P.O. Number *",
        options=[""] + po_numbers,
        key="pl_po",
        help="Customer PO numbers from purchase_order.customer_po_no (not Cancelled).",
    )

if st.session_state.get("pl_po_seen") != po_no:
    previous_po = st.session_state.get("pl_po_seen")
    st.session_state["pl_po_seen"] = po_no
    if previous_po is not None:
        for key in (
            "pl_cust",
            "pl_alloy",
            "pl_batches",
            "pl_batches_labels",
            "pl_batch_qty",
            "pl_cust_seen",
            "pl_alloy_seen",
        ):
            st.session_state.pop(key, None)

customers = db.list_packing_po_customers(po_no) if po_no else []
cust_opts = {_cust_label(c): c for c in customers}
valid_cust = set(cust_opts) | {""}
if st.session_state.get("pl_cust") not in valid_cust:
    wanted = st.session_state.get("pl_cust_code")
    match = next(
        (
            label
            for label, row in cust_opts.items()
            if str(row.get("Cust_code")) == str(wanted)
        ),
        "",
    )
    st.session_state["pl_cust"] = match
if len(cust_opts) == 1 and not st.session_state.get("pl_cust"):
    st.session_state["pl_cust"] = next(iter(cust_opts))

r2c1, r2c2, r2c3 = st.columns(3)
with r2c1:
    cust_label = st.selectbox(
        "Customer name *",
        options=[""] + list(cust_opts.keys()),
        key="pl_cust",
        help="Filled from this P.O. when it has one customer.",
    )
    cust_row = cust_opts.get(cust_label) or {}
    cust_code = cust_row.get("Cust_code")
    customer_name = cust_row.get("Customer_name")
    st.session_state["pl_cust_code"] = cust_code

    if st.session_state.get("pl_cust_seen") != cust_code:
        previous_cust = st.session_state.get("pl_cust_seen")
        st.session_state["pl_cust_seen"] = cust_code
        if previous_cust is not None:
            for key in (
                "pl_alloy",
                "pl_batches",
                "pl_batches_labels",
                "pl_batch_qty",
                "pl_alloy_seen",
            ):
                st.session_state.pop(key, None)

    alloys = (
        db.list_packing_po_alloys(po_no, cust_code)
        if po_no and cust_code
        else []
    )
    alloy_opts = {
        _alloy_label(a): a
        for a in alloys
        if a.get("Alloy_id") not in (None, "")
    }
    valid_alloy = set(alloy_opts) | {""}
    if st.session_state.get("pl_alloy") not in valid_alloy:
        wanted_aid = st.session_state.get("pl_alloy_id")
        match = next(
            (
                label
                for label, row in alloy_opts.items()
                if str(row.get("Alloy_id")) == str(wanted_aid)
            ),
            "",
        )
        st.session_state["pl_alloy"] = match
    if len(alloy_opts) == 1 and not st.session_state.get("pl_alloy"):
        st.session_state["pl_alloy"] = next(iter(alloy_opts))

with r2c2:
    alloy_label = st.selectbox(
        "Alloy Name *",
        options=[""] + list(alloy_opts.keys()),
        key="pl_alloy",
        help="Filled from this P.O. for the selected customer.",
    )
    alloy_row = alloy_opts.get(alloy_label) or {}
    alloy_id = alloy_row.get("Alloy_id")
    st.session_state["pl_alloy_id"] = alloy_id
    if alloy_id not in (None, ""):
        master = db.get_alloy(alloy_id) or alloy_row
        colour_code = master.get("Colour_code") or ""
    else:
        colour_code = ""
with r2c3:
    st.text_input(
        "Colour code",
        value=colour_code,
        disabled=True,
        help="Filled from alloy_master for the selected alloy.",
    )

r3c1, r3c2, r3c3 = st.columns(3)
with r3c1:
    vehicle_no = st.text_input("Vehicle No", key="pl_vehicle")
with r3c2:
    status = st.selectbox(
        "Packing list status *",
        options=db.PACKING_LIST_STATUS,
        key="pl_status",
        disabled=issued_locked,
        help="In-Progress does not take stock. Verified subtracts packed kg and pieces from finished goods.",
    )
with r3c3:
    if editing_id and status == db.PACKING_STATUS_VERIFIED:
        if st.button("Open test certificate", key="pl_open_tc"):
            st.session_state.nav_page = "Test Certificate"
            st.session_state["tc_packing_list_id"] = int(editing_id)
            st.rerun()
    else:
        st.empty()

if st.session_state.get("pl_alloy_seen") != alloy_id:
    previous_alloy = st.session_state.get("pl_alloy_seen")
    st.session_state["pl_alloy_seen"] = alloy_id
    if previous_alloy is not None:
        st.session_state.pop("pl_batches", None)
        st.session_state.pop("pl_batches_labels", None)
        st.session_state.pop("pl_batch_qty", None)

st.markdown("#### Batch IDs")
f1, f2 = st.columns(2)
match_name = f1.checkbox(
    "Filter by alloy name",
    value=True,
    key="pl_match_name",
    help="Include heats whose alloy_name matches the packing-list alloy (or its group).",
)
match_group = f2.checkbox(
    "Filter by alloy group",
    value=True,
    key="pl_match_group",
    help="Include heats whose alloy_group matches the packing-list alloy.",
)

include_ids = [
    str(b) for b in (st.session_state.get("pl_batches") or []) if b
]
dispatchable: list[dict] = []
blocked: list[dict] = []
if alloy_id not in (None, ""):
    try:
        dispatchable, blocked = db.list_packing_batch_candidates(
            int(alloy_id),
            match_name=bool(match_name),
            match_group=bool(match_group),
            include_batch_ids=include_ids,
            packing_list_id=int(editing_id) if editing_id else None,
            packing_list_status=status,
        )
    except Exception as exc:
        st.error(str(exc))

selected_batch_ids: list[str] = []
selected_lines: list[dict] = []
by_id: dict[str, dict] = {
    str(r["Batch_ID"]): r for r in dispatchable
}

if not po_no:
    st.info("Select a P.O. Number to load customers and alloys.")
elif not cust_code:
    st.info("Select the customer on this purchase order.")
elif alloy_id in (None, ""):
    st.info("Select the alloy on this purchase order, then pick batch IDs.")
else:
    if dispatchable:
        st.caption(
            "Tick each heat and enter the **kg** and **pieces** to pack. "
            "On-hand is what is still in finished goods. Saving as **Verified** "
            "subtracts the packed quantity; leftover kg and pieces stay in inventory. "
            f"Avg piece outside **{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg** "
            "is shown in red."
        )
        saved_qty = st.session_state.get("pl_batch_qty") or {}
        pick_rows = []
        for r in dispatchable:
            bid = str(r["Batch_ID"])
            on_hand_w = float(r.get("On_hand_weight") or 0)
            on_hand_p = int(float(r.get("On_hand_pieces") or 0))
            saved = saved_qty.get(bid) or {}
            if saved.get("Weight") not in (None, ""):
                pack_w = float(saved.get("Weight") or 0)
            elif float(r.get("Packed_Weight") or 0) > 0:
                pack_w = float(r.get("Packed_Weight") or 0)
            else:
                pack_w = on_hand_w
            if saved.get("Pieces") not in (None, ""):
                pack_p = int(float(saved.get("Pieces") or 0))
            elif int(float(r.get("Packed_Pieces") or 0)) > 0:
                pack_p = int(float(r.get("Packed_Pieces") or 0))
            else:
                pack_p = on_hand_p
            pick_rows.append(
                {
                    "Select": bid in set(include_ids),
                    "Batch_ID": bid,
                    "Heat_no": str(r.get("Heat_no") or "—"),
                    "On hand (kg)": on_hand_w,
                    "On hand pieces": on_hand_p,
                    "Weight (kg)": pack_w,
                    "Pieces": pack_p,
                    "Avg_piece_kg": db.alloy_piece_avg_kg(pack_w, pack_p),
                    "Alloy": r.get("Alloy_name") or "—",
                    "Alloy_id": r.get("Alloy_id"),
                }
            )
        pick_df = pd.DataFrame(pick_rows)
        editor_key = (
            f"pl_batch_editor_{alloy_id}_"
            f"{int(bool(match_name))}_{int(bool(match_group))}_"
            f"{int(editing_id or 0)}_qty2"
        )
        prev_edit = st.session_state.get(editor_key)
        if isinstance(prev_edit, pd.DataFrame) and not prev_edit.empty:
            if {"Weight (kg)", "Pieces"}.issubset(prev_edit.columns):
                prev_edit = prev_edit.copy()
                prev_edit["Avg_piece_kg"] = [
                    db.alloy_piece_avg_kg(w, p)
                    for w, p in zip(prev_edit["Weight (kg)"], prev_edit["Pieces"])
                ]
                st.session_state[editor_key] = prev_edit
        edited = st.data_editor(
            pick_df,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Include this heat on the packing list.",
                ),
                "On hand (kg)": st.column_config.NumberColumn(
                    format="%.2f",
                    help="Quantity still in finished-goods inventory.",
                ),
                "On hand pieces": st.column_config.NumberColumn(
                    format="%d",
                    help="Pieces still in finished-goods inventory.",
                ),
                "Weight (kg)": st.column_config.NumberColumn(
                    format="%.2f",
                    min_value=0.0,
                    help="Kilograms to pack on this list.",
                ),
                "Pieces": st.column_config.NumberColumn(
                    format="%d",
                    min_value=0,
                    step=1,
                    help="Whole pieces to pack on this list.",
                ),
                "Avg_piece_kg": st.column_config.NumberColumn(
                    "Avg piece (kg)",
                    format="%.2f",
                    help=(
                        f"Packed weight ÷ pieces. Typical range "
                        f"{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg."
                    ),
                ),
                "Alloy_id": None,
            },
            disabled=[
                "Batch_ID",
                "Heat_no",
                "On hand (kg)",
                "On hand pieces",
                "Avg_piece_kg",
                "Alloy",
                "Alloy_id",
            ],
            hide_index=True,
            use_container_width=True,
            key=editor_key,
        )
        for row in edited.to_dict("records"):
            if not row.get("Select"):
                continue
            bid = str(row.get("Batch_ID") or "").strip()
            if not bid:
                continue
            selected_batch_ids.append(bid)
            selected_lines.append(
                {
                    "Batch_ID": bid,
                    "Weight": float(row.get("Weight (kg)") or 0),
                    "Pieces": int(float(row.get("Pieces") or 0)),
                    "Alloy_id": row.get("Alloy_id") or by_id.get(bid, {}).get("Alloy_id"),
                    "Heat_no": row.get("Heat_no"),
                }
            )
        range_warnings = []
        over_max = []
        for line in selected_lines:
            cand = by_id.get(line["Batch_ID"]) or {}
            avg = db.alloy_piece_avg_kg(line.get("Weight"), line.get("Pieces"))
            if db.alloy_piece_avg_out_of_range(avg, line.get("Alloy_id")):
                range_warnings.append(
                    f"{line['Batch_ID']}: {avg:.2f} kg/pc"
                )
            max_w = float(cand.get("Max_weight") or 0)
            max_p = int(float(cand.get("Max_pieces") or 0))
            if line["Weight"] - max_w > 0.0005 or line["Pieces"] > max_p:
                over_max.append(
                    f"{line['Batch_ID']} (max {max_w:g} kg / {max_p} pieces)"
                )
        if range_warnings:
            st.warning(
                "Avg piece is outside "
                f"**{db.ALLOY_PIECE_KG_MIN:g}–{db.ALLOY_PIECE_KG_MAX:g} kg**: "
                + "; ".join(range_warnings)
            )
        if over_max:
            st.error(
                "Packed quantity exceeds remaining finished goods: "
                + "; ".join(over_max)
            )
    else:
        st.warning(
            "No Available finished-goods batches match this alloy name or group. "
            "Mark the heat **Completed**, save product-alloy output on "
            "**Batch Output**, then click **Mark Output as Completed**."
        )

    st.markdown("##### Selected batches")
    if selected_lines:
        selected_df = pd.DataFrame(
            [
                {
                    "Batch_ID": r["Batch_ID"],
                    "Heat_no": str(r.get("Heat_no") or "—"),
                    "Weight (kg)": float(r.get("Weight") or 0),
                    "Pieces": int(float(r.get("Pieces") or 0)),
                    "Avg_piece_kg": db.alloy_piece_avg_kg(
                        r.get("Weight"), r.get("Pieces")
                    ),
                    "Alloy_id": r.get("Alloy_id"),
                }
                for r in selected_lines
            ]
        )
        show_dataframe(selected_df, highlight_avg_piece=True)
        t1, t2, t3 = st.columns(3)
        t1.metric("Batches", len(selected_lines))
        t2.metric(
            "Total weight (kg)",
            f"{float(selected_df['Weight (kg)'].sum()):,.2f}",
        )
        t3.metric(
            "Total pieces",
            f"{int(selected_df['Pieces'].sum())}",
        )
    else:
        st.caption("Tick one or more rows above and enter packed kg and pieces.")

    if blocked:
        with st.expander(
            f"Matching heats not ready to dispatch ({len(blocked)})",
            expanded=True,
        ):
            show_dataframe(
                pd.DataFrame(
                    [
                        {
                            "Batch_ID": r["Batch_ID"],
                            "Heat_no": str(r.get("Heat_no") or "—"),
                            "On hand (kg)": float(r.get("On_hand_weight") or 0),
                            "On hand pieces": int(float(r.get("On_hand_pieces") or 0)),
                            "Status": r.get("Production_status") or "—",
                            "Reason": r.get("Reason") or "",
                        }
                        for r in blocked
                    ]
                )
            )

st.session_state["pl_batches"] = selected_batch_ids
st.session_state["pl_batch_qty"] = {
    str(r["Batch_ID"]): {
        "Weight": r["Weight"],
        "Pieces": r["Pieces"],
        "Heat_no": r.get("Heat_no"),
    }
    for r in selected_lines
}

save_col, cancel_col = st.columns(2)
can_cancel_list = bool(
    editing_id
    and not issued_locked
    and status == db.PACKING_STATUS_VERIFIED
)
with save_col:
    save_clicked = st.button(
        "Save packing list",
        type="primary",
        key="pl_save",
        disabled=issued_locked,
    )
with cancel_col:
    cancel_list_clicked = st.button(
        "Cancel packing list",
        key="pl_cancel_list",
        disabled=not can_cancel_list,
        help="Returns packed kg and pieces to finished goods and sets the list to In-Progress.",
    )

if cancel_list_clicked and editing_id:
    try:
        cancelled = db.cancel_packing_list(int(editing_id))
        st.session_state["pl_status"] = db.PACKING_STATUS_IN_PROGRESS
        st.success(
            f"Packing list **#{cancelled.get('Packing_list_id')}** cancelled. "
            "Packed quantity is back in finished goods."
        )
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

if save_clicked:
    if not invoice_number.strip():
        st.error("Invoice number is required.")
    elif not po_no:
        st.error("P.O. Number is required.")
    elif not cust_code:
        st.error("Customer name is required.")
    elif alloy_id in (None, ""):
        st.error("Alloy Name is required.")
    else:
        over_max = []
        for line in selected_lines:
            cand = by_id.get(line["Batch_ID"]) or {}
            max_w = float(cand.get("Max_weight") or 0)
            max_p = int(float(cand.get("Max_pieces") or 0))
            if line["Weight"] - max_w > 0.0005 or line["Pieces"] > max_p:
                over_max.append(
                    f"{line['Batch_ID']} (max {max_w:g} kg / {max_p} pieces)"
                )
        if over_max:
            st.error(
                "Packed quantity exceeds remaining finished goods: "
                + "; ".join(over_max)
            )
        else:
            try:
                pid = db.save_packing_list(
                    packing_list_id=int(editing_id) if editing_id else None,
                    invoice_date=to_storage_date(invoice_date),
                    invoice_number=invoice_number,
                    customer_po_no=po_no,
                    cust_code=cust_code,
                    customer_name=customer_name,
                    alloy_id=int(alloy_id),
                    colour_code=colour_code or None,
                    vehicle_no=vehicle_no,
                    packing_list_status=status,
                    batch_lines=selected_lines,
                )
                st.session_state["pl_edit_id"] = pid
                st.success(
                    f"Saved packing list **#{pid}** as **{status}** "
                    f"({len(selected_batch_ids)} batch"
                    f"{'' if len(selected_batch_ids) == 1 else 'es'})."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

st.divider()
st.markdown("#### Existing packing lists")
if not existing_lists:
    st.info("No packing lists yet.")
else:
    show_dataframe(df_from_rows(existing_lists))
    load_opts = {
        (
            f"#{r['Packing_list_id']}  |  {r.get('Invoice_number') or '—'}  |  "
            f"{r.get('Customer_name') or '—'}  |  {r.get('Packing_list_status')}"
            f"  |  {r.get('Certificate_status') or 'no certificate'}"
        ): int(r["Packing_list_id"])
        for r in existing_lists
    }
    load_label = st.selectbox(
        "Load packing list",
        options=[""] + list(load_opts.keys()),
        key="pl_load_pick",
    )
    load_col, summary_col = st.columns(2)
    with load_col:
        load_clicked = st.button("Load selected", key="pl_load")
    with summary_col:
        summary_clicked = st.button("View summary", key="pl_view_saved_summary")
    if summary_clicked and load_label:
        st.session_state["pl_show_summary"] = True
        st.session_state["pl_summary_id"] = load_opts[load_label]
        st.rerun()
    if load_clicked and load_label:
        header = db.get_packing_list(load_opts[load_label])
        if not header:
            st.error("That packing list was not found.")
        else:
            _clear_packing_form()
            st.session_state["pl_edit_id"] = int(header["Packing_list_id"])
            st.session_state["pl_invoice_date"] = _parse_master_date(
                header.get("Invoice_date")
            )
            st.session_state["pl_invoice"] = header.get("Invoice_number") or ""
            po_loaded = header.get("Customer_PO_No") or ""
            st.session_state["pl_po"] = po_loaded
            st.session_state["pl_po_seen"] = po_loaded
            st.session_state["pl_cust_code"] = header.get("Cust_code")
            st.session_state["pl_cust"] = _cust_label(
                {
                    "Customer_name": header.get("Customer_name"),
                    "Cust_code": header.get("Cust_code"),
                }
            )
            st.session_state["pl_cust_seen"] = header.get("Cust_code")
            aid = header.get("Alloy_id")
            st.session_state["pl_alloy_id"] = aid
            st.session_state["pl_alloy"] = (
                _alloy_label(
                    {"Alloy_name": header.get("Alloy_name"), "Alloy_id": aid}
                )
                if aid not in (None, "")
                else ""
            )
            st.session_state["pl_alloy_seen"] = aid
            st.session_state["pl_vehicle"] = header.get("Vehicle_no") or ""
            st.session_state["pl_status"] = (
                header.get("Packing_list_status") or db.PACKING_STATUS_IN_PROGRESS
            )
            loaded_batches = header.get("batches") or []
            st.session_state["pl_batches"] = [
                str(r["Batch_ID"]) for r in loaded_batches if r.get("Batch_ID")
            ]
            st.session_state["pl_batch_qty"] = {
                str(r["Batch_ID"]): {
                    "Weight": float(r.get("Weight") or 0),
                    "Pieces": int(float(r.get("Pieces") or 0)),
                    "Heat_no": r.get("Heat_no"),
                }
                for r in loaded_batches
                if r.get("Batch_ID")
            }
            st.rerun()
