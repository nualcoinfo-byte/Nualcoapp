import html
from typing import Optional
from datetime import timedelta

import pandas as pd
import streamlit as st

import database as db
from pages_common import _render_dashboard_refresh_bar, format_ui_date, ui_date_input


ALL_FURNACES = "All furnaces"
ALL_SHIFTS = "All shifts"
BY_DATE = "Choose by date"
BY_RANGE = "Choose by date range"

_CSS = """
<style>
.ps-wrap { overflow-x: auto; }
.ps-table { border-collapse: collapse; font-size: 0.82rem; width: 100%; }
.ps-table th, .ps-table td {
  border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.35rem 0.5rem;
  text-align: left; vertical-align: top; white-space: nowrap;
}
.ps-table th { font-weight: 600; background: rgba(128,128,128,0.08); }
.ps-table td.num, .ps-table th.num { text-align: right; }
.ps-table tr.ps-total td { font-weight: 700; border-top: 2px solid rgba(128,128,128,0.5); }
.ps-table input.ps-toggle { display: none; }
.ps-table label.ps-open { cursor: pointer; color: #F15A22; font-weight: 600; }
.ps-table label.ps-open::after { content: " ▸"; }
.ps-table input.ps-toggle:checked + label.ps-open::after { content: " ▾"; }
.ps-table tr.ps-detail { display: none; }
.ps-table tr.ps-row:has(input.ps-in:checked) + tr.ps-detail-in { display: table-row; }
.ps-table tr.ps-row:has(input.ps-out:checked) + tr + tr.ps-detail-out { display: table-row; }
.ps-table tr.ps-detail > td { background: rgba(241,90,34,0.05); padding: 0.4rem 0.6rem 0.6rem; }
.ps-detail-title { font-weight: 600; margin-bottom: 0.25rem; }
.ps-table tr.ps-detail table { font-size: 0.76rem; width: auto; }
.ps-table tr.ps-detail th, .ps-table tr.ps-detail td { padding: 0.2rem 0.5rem; }
.ps-good { color: #1b7a3d; font-weight: 600; }
.ps-table label.ps-cust { cursor: pointer; border-bottom: 1px dotted rgba(128,128,128,0.8); }
.ps-table .ps-cust-full { display: none; }
.ps-table input.ps-toggle:checked + label.ps-cust .ps-cust-short { display: none; }
.ps-table input.ps-toggle:checked + label.ps-cust .ps-cust-full { display: inline; }
.ps-bad { color: #c62828; font-weight: 600; }
</style>
"""


def _num(value: object, fmt: str = "{:,.1f}") -> str:
    if value is None:
        return "—"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return "—"


CUSTOMER_PREFIX_LEN = 4
CUSTOMER_WORD_LEN = 5


def _customer_short_names(names: list[str]) -> dict[str, str]:
    """Every customer as its first CUSTOMER_PREFIX_LEN characters plus "…".

    Customers on screen that share those characters also get the word where
    their names first differ, cut to CUSTOMER_WORD_LEN characters, e.g. "UNIQ…"
    and "UNIQ… P-6", or "ABEE… CAST" and "ABEE… INDUS". The whole word is used
    only where the cut would make two short names identical.
    """
    unique = sorted({n.strip() for n in names if n and n.strip()})
    head = CUSTOMER_PREFIX_LEN

    def _common_len(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a.upper(), b.upper()):
            if x != y:
                break
            n += 1
        return n

    def _label(name: str, word_len: Optional[int]) -> str:
        if len(name) <= head:
            return name
        label = name[:head].rstrip() + "…"
        rivals = [o for o in unique if o != name and o[:head].upper() == name[:head].upper()]
        if rivals:
            differ_at = max(_common_len(name, o) for o in rivals)
            if differ_at < len(name):
                word_start = name.rfind(" ", 0, differ_at + 1) + 1
                if word_start >= head:
                    word = name[word_start:].split(" ", 1)[0]
                    label += " " + (word[:word_len] if word_len else word)
                else:
                    label = name[: max(differ_at + 1, head)].rstrip() + "…"
        if label.replace("…", "").replace(" ", "") == name.replace(" ", ""):
            label = name  # nothing was actually shortened
        return label

    short = {name: _label(name, CUSTOMER_WORD_LEN) for name in unique}
    clashes = {lbl for lbl in short.values() if list(short.values()).count(lbl) > 1}
    for name, lbl in short.items():
        if lbl in clashes:
            short[name] = _label(name, None)
    return short


def _customer_cell(bid: str, name: object, short_names: dict[str, str]) -> str:
    """Short customer name; hover shows the full name, a tap/click toggles it."""
    full = str(name or "").strip()
    if not full:
        return "<td>—</td>"
    short = short_names.get(full, full)
    if short == full:
        return f"<td>{html.escape(full)}</td>"
    tid = f"ps-cust-{html.escape(bid)}"
    return (
        f"<td><input type='checkbox' class='ps-toggle' id='{tid}'>"
        f"<label class='ps-cust' for='{tid}' title='{html.escape(full, quote=True)}'>"
        f"<span class='ps-cust-short'>{html.escape(short)}</span>"
        f"<span class='ps-cust-full'>{html.escape(full)}</span></label></td>"
    )


def _esc(value: object) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _variance_cell(pct: object) -> str:
    if pct is None:
        return '<td class="num">—</td>'
    css = "ps-good" if pct > 1 else ("ps-bad" if pct < -1 else "")
    return f'<td class="num"><span class="{css}">{float(pct):+.2f}%</span></td>'


def _yield_cell(pct: object) -> str:
    if pct is None:
        return '<td class="num">—</td>'
    css = "ps-good" if pct >= db.YIELD_TARGET_PCT else "ps-bad"
    return f'<td class="num"><span class="{css}">{float(pct):.2f}%</span></td>'


N_COLS = 18


def _toggle(kind: str, bid: str, total: float) -> str:
    """Clickable total that opens the batch's input or output detail row."""
    tid = f"ps-{kind}-{html.escape(bid)}"
    return (
        f"<input type='checkbox' class='ps-toggle ps-{kind}' id='{tid}'>"
        f"<label class='ps-open' for='{tid}'>{_num(total)}</label>"
    )


def _detail_row(kind: str, title: str, table_html: str) -> str:
    return (
        f"<tr class='ps-detail ps-detail-{kind}'><td colspan='{N_COLS}'>"
        f"<div class='ps-detail-title'>{title}</div>{table_html}</td></tr>"
    )


def _inputs_detail(bid: str, total: float, lines: list[dict]) -> tuple[str, str]:
    if not lines:
        return _num(total), _detail_row("in", "", "")
    rows = "".join(
        "<tr>"
        f"<td>{_esc(r.get('Raw_Material_Name'))}</td>"
        f"<td class='num'>{_esc(r.get('Lot_id'))}</td>"
        f"<td>{_esc(r.get('Trolley_name'))}</td>"
        f"<td class='num'>{_num(r.get('Weight'))}</td>"
        f"<td class='num'>{_num(r.get('Cost_per_kg'), '{:,.2f}')}</td>"
        f"<td class='num'>{_num(r.get('Recovery_pct'))}</td>"
        f"<td class='num'>{_num(r.get('Estimated_Output'))}</td>"
        f"<td>{_esc(format_ui_date(r.get('Charge_time'), with_time=True))}</td>"
        "</tr>"
        for r in lines
    )
    table = (
        "<table><tr><th>Raw material</th><th class='num'>Lot</th><th>Trolley</th>"
        "<th class='num'>Net kg</th><th class='num'>₹/kg</th><th class='num'>Recovery %</th>"
        "<th class='num'>Est. output kg</th><th>Charged at</th></tr>"
        f"{rows}</table>"
    )
    title = f"Input for {html.escape(bid)}: {len(lines)} charge line(s), {_num(total)} kg"
    return _toggle("in", bid, total), _detail_row("in", title, table)


def _outputs_detail(bid: str, total: float, lines: list[dict]) -> tuple[str, str]:
    if not lines:
        return _num(total), _detail_row("out", "", "")
    rows = "".join(
        "<tr>"
        f"<td>{_esc(r.get('Alloy_name') or r.get('Alloy_id'))}</td>"
        f"<td class='num'>{_esc(r.get('Lines'))}</td>"
        f"<td class='num'>{_num(r.get('Pieces'), '{:,.0f}')}</td>"
        f"<td class='num'>{_num(r.get('Weight'))}</td>"
        f"<td class='num'>{_num(float(r.get('Weight') or 0) / total * 100 if total else None)}</td>"
        "</tr>"
        for r in lines
    )
    table = (
        "<table><tr><th>Output alloy</th><th class='num'>Lines</th><th class='num'>Pieces</th>"
        "<th class='num'>kg</th><th class='num'>% of output</th></tr>"
        f"{rows}</table>"
    )
    title = f"Output for {html.escape(bid)} by alloy: {_num(total)} kg"
    return _toggle("out", bid, total), _detail_row("out", title, table)


st.title("Production Snapshot")
st.caption(
    "Day-wise and shift-wise production at a glance, one row per batch, sorted by "
    "production date, shift, melt no and furnace. Click a **Total input** or **Total "
    "output** figure to expand its charge lines or its output by alloy. "
    "**Estimated output** is each charge line's weight × its material's newest "
    "recovery % (Raw Material Master), as on Production Data Analysis. **Cost/kg** is "
    "the overall ₹/kg saved on the batch's output (material + conversion). Actual "
    "output is the total recorded output. Data refreshes with the Dashboard; use "
    "**Refresh now** for the latest."
)
_render_dashboard_refresh_bar(key_prefix="psnap")

mode = st.radio("View", [BY_DATE, BY_RANGE], horizontal=True, key="psnap_mode")
today = db.today_ist()
f1, f2, f3 = st.columns([2, 1, 1])
with f1:
    if mode == BY_DATE:
        day = ui_date_input("Production date", value=today, key="psnap_date")
        start_date = end_date = day
    else:
        picked = st.date_input(
            "Date range",
            value=(today - timedelta(days=6), today),
            format="DD-MM-YYYY",
            key="psnap_range",
        )
        start_date, end_date = (
            picked if isinstance(picked, (tuple, list)) and len(picked) == 2 else (None, None)
        )
with f2:
    furnace_pick = st.selectbox(
        "Furnace", [ALL_FURNACES] + db.list_furnaces(), key="psnap_furnace"
    )
with f3:
    shift_pick = st.selectbox("Shift", [ALL_SHIFTS] + list(db.SHIFTS), key="psnap_shift")

if start_date is None or end_date is None:
    st.info("Pick both the start and the end of the date range.")
    st.stop()

snap = db.production_snapshot(
    start_date,
    end_date,
    furnace=None if furnace_pick == ALL_FURNACES else furnace_pick,
    shift=None if shift_pick == ALL_SHIFTS else shift_pick,
)
batches = snap["batches"]
period = (
    format_ui_date(start_date)
    if start_date == end_date
    else f"{format_ui_date(start_date)} to {format_ui_date(end_date)}"
)
if not batches:
    st.info(f"No batches for {period} with these filters.")
    st.stop()

total_in = sum(b["Total_Input"] for b in batches)
total_out = sum(b["Total_Output"] for b in batches)
with_output = [b for b in batches if b["Total_Output"] > 0]
est_with_output = sum(b["Estimated_Output"] for b in with_output)
out_with_output = sum(b["Total_Output"] for b in with_output)
in_with_output = sum(b["Total_Input"] for b in with_output)
overall_yield = out_with_output / in_with_output * 100 if in_with_output > 0 else None
overall_var = (
    (out_with_output - est_with_output) / est_with_output * 100
    if est_with_output > 0 and out_with_output > 0
    else None
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Batches", len(batches))
m2.metric("Total input (kg)", f"{total_in:,.1f}")
m3.metric("Total output (kg)", f"{total_out:,.1f}")
m4.metric("Yield %", f"{overall_yield:.2f}%" if overall_yield is not None else "—")
m5.metric(
    "Actual vs estimated",
    f"{overall_var:+.2f}%" if overall_var is not None else "—",
)
st.caption(
    f"{period}. Yield and actual vs estimated count only batches that have output "
    "saved, so heats still in the furnace do not pull them down."
)

header = (
    "<tr><th>Production date</th><th>Batch ID</th><th>Heat no</th><th>Alloy</th><th>Customer</th>"
    "<th class='num'>Melt no</th>"
    "<th>Shift</th><th>Furnace</th><th class='num'>Total input (kg)</th><th>Input status</th>"
    "<th class='num'>Total output (kg)</th><th>Output status</th><th class='num'>Cost/kg (₹)</th>"
    "<th class='num'>Estimated output (kg)</th><th class='num'>Actual output (kg)</th>"
    "<th class='num'>Actual − estimated (kg)</th><th class='num'>Actual vs estimated %</th>"
    "<th class='num'>Actual yield %</th></tr>"
)
customer_short = _customer_short_names([str(b.get("Customer_name") or "") for b in batches])
body = []
for b in batches:
    bid = str(b["Batch_ID"])
    delta = b["Output_vs_Estimate"]
    in_cell, in_row = _inputs_detail(bid, b["Total_Input"], snap["inputs"].get(bid, []))
    out_cell, out_row = _outputs_detail(bid, b["Total_Output"], snap["outputs"].get(bid, []))
    body.append(
        "<tr class='ps-row'>"
        f"<td>{_esc(format_ui_date(b.get('Production_Date')))}</td>"
        f"<td>{_esc(bid)}</td>"
        f"<td>{_esc(b.get('Heat_no'))}</td>"
        f"<td>{_esc(b.get('Alloy_name'))}</td>"
        f"{_customer_cell(bid, b.get('Customer_name'), customer_short)}"
        f"<td class='num'>{_esc(b.get('Melt_No'))}</td>"
        f"<td>{_esc(b.get('Shift'))}</td>"
        f"<td>{_esc(b.get('Furnace'))}</td>"
        f"<td class='num'>{in_cell}</td>"
        f"<td>{_esc(b.get('Production_status') or db.BATCH_STATUS_IN_PROGRESS)}</td>"
        f"<td class='num'>{out_cell}</td>"
        f"<td>{_esc(b.get('Output_status') or db.BATCH_STATUS_IN_PROGRESS)}</td>"
        f"<td class='num'>{_num(b.get('Cost_per_kg'), '{:,.2f}')}</td>"
        f"<td class='num'>{_num(b['Estimated_Output'])}</td>"
        f"<td class='num'>{_num(b['Total_Output'] if b['Total_Output'] > 0 else None)}</td>"
        f"<td class='num'>{_num(delta, '{:+,.1f}')}</td>"
        f"{_variance_cell(b['Output_vs_Estimate_pct'])}"
        f"{_yield_cell(b['Yield_pct'])}"
        "</tr>" + in_row + out_row
    )
body.append(
    "<tr class='ps-total'>"
    f"<td colspan='8'>Total ({len(batches)} batches)</td>"
    f"<td class='num'>{_num(total_in)}</td><td></td>"
    f"<td class='num'>{_num(total_out)}</td><td></td><td></td>"
    f"<td class='num'>{_num(sum(b['Estimated_Output'] for b in batches))}</td>"
    f"<td class='num'>{_num(total_out)}</td>"
    f"<td class='num'>{_num(out_with_output - est_with_output if with_output else None, '{:+,.1f}')}</td>"
    f"{_variance_cell(overall_var)}{_yield_cell(overall_yield)}"
    "</tr>"
)
st.html(
    _CSS + "<div class='ps-wrap'><table class='ps-table'>" + header + "".join(body) + "</table></div>"
)

abbreviated = {full: short for full, short in customer_short.items() if short != full}
if abbreviated:
    st.caption(
        "Customers: "
        + "  ·  ".join(f"**{short}** {full}" for full, short in sorted(abbreviated.items()))
        + ". Hover over or tap a customer in the table to see the full name."
    )

export = pd.DataFrame(
    [
        {
            "Production date": format_ui_date(b.get("Production_Date")),
            "Batch ID": b["Batch_ID"],
            "Heat no": b.get("Heat_no"),
            "Alloy": b.get("Alloy_name"),
            "Customer": b.get("Customer_name"),
            "Melt no": b.get("Melt_No"),
            "Shift": b.get("Shift"),
            "Furnace": b.get("Furnace"),
            "Total input (kg)": round(b["Total_Input"], 2),
            "Input status": b.get("Production_status"),
            "Total output (kg)": round(b["Total_Output"], 2),
            "Output status": b.get("Output_status"),
            "Cost/kg (Rs)": b.get("Cost_per_kg"),
            "Estimated output (kg)": round(b["Estimated_Output"], 2),
            "Actual output (kg)": round(b["Total_Output"], 2),
            "Actual - estimated (kg)": None
            if b["Output_vs_Estimate"] is None
            else round(b["Output_vs_Estimate"], 2),
            "Actual vs estimated %": None
            if b["Output_vs_Estimate_pct"] is None
            else round(b["Output_vs_Estimate_pct"], 2),
            "Actual yield %": None if b["Yield_pct"] is None else round(b["Yield_pct"], 2),
        }
        for b in batches
    ]
)
st.download_button(
    "Download CSV",
    data=export.to_csv(index=False).encode("utf-8"),
    file_name=f"production_snapshot_{start_date.isoformat()}_{end_date.isoformat()}.csv",
    mime="text/csv",
    key="psnap_csv",
)
