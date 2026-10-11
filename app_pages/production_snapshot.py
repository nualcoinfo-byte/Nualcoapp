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
.ps-table tr.ps-sub td { font-weight: 600; background: rgba(241,90,34,0.07); }
.ps-table td.ps-zero { color: rgba(128,128,128,0.7); }
</style>
"""

# Frozen identity columns (Production date, Heat no, Alloy, Customer) stay on the
# left while the figures scroll sideways. Sticky cells need a solid background
# (the page's own), fixed widths so each knows where the previous one ends, and
# a left offset that depends on whether Production date is shown.
FROZEN_WIDTH_REM = {"date": 8.0, "heat": 6.0, "alloy": 7.5, "cust": 7.0}
_THEME_BG = {"light": ("#ffffff", "#f3f3f4"), "dark": ("#0e1117", "#1f2229")}


def _frozen_css(columns: list[str], theme: str) -> str:
    page_bg, head_bg = _THEME_BG.get(theme, _THEME_BG["light"])
    rules = [
        ".ps-table td.fz, .ps-table th.fz { position: sticky; z-index: 2; "
        f"background: {page_bg}; box-sizing: border-box; overflow: hidden; "
        "text-overflow: ellipsis; }",
        f".ps-table th.fz {{ background: {head_bg}; z-index: 3; }}",
        ".ps-table .fz-last { box-shadow: 2px 0 0 rgba(128,128,128,0.45); }",
    ]
    left = 0.0
    for col in columns:
        w = FROZEN_WIDTH_REM[col]
        rules.append(
            f".ps-table .fz-{col} {{ left: {left:g}rem; width: {w:g}rem; "
            f"min-width: {w:g}rem; max-width: {w:g}rem; }}"
        )
        left += w
    # On a phone the four columns would fill the screen; freeze only Heat no there.
    phone = [
        "@media (max-width: 640px) {",
        "  .ps-table td.fz, .ps-table th.fz { position: static; box-shadow: none; }",
        "  .ps-table td.fz-heat, .ps-table th.fz-heat { position: sticky; left: 0; "
        "box-shadow: 2px 0 0 rgba(128,128,128,0.45); }",
        "  .ps-table td.fz-total { position: static; }",
        "}",
    ]
    total_w = sum(FROZEN_WIDTH_REM[c] for c in columns)
    rules.append(
        f".ps-table .fz-total {{ left: 0; width: {total_w:g}rem; min-width: {total_w:g}rem; "
        f"max-width: {total_w:g}rem; }}"
    )
    return "<style>" + "\n".join(rules + phone) + "</style>"


def _fz(col: str, columns: list[str]) -> str:
    """Class attribute for a frozen cell; the last frozen column gets the divider."""
    last = " fz-last" if col == columns[-1] else ""
    return f" class='fz fz-{col}{last}'"


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


def _customer_cell(
    bid: str, name: object, short_names: dict[str, str], attrs: str = ""
) -> str:
    """Short customer name; hover shows the full name, a tap/click toggles it."""
    full = str(name or "").strip()
    if not full:
        return f"<td{attrs}>—</td>"
    short = short_names.get(full, full)
    if short == full:
        return f"<td{attrs} title='{html.escape(full, quote=True)}'>{html.escape(full)}</td>"
    tid = f"ps-cust-{html.escape(bid)}"
    return (
        f"<td{attrs}><input type='checkbox' class='ps-toggle' id='{tid}'>"
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


# Set once the view is known: Production date is shown only for a date range.
N_COLS = 15


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


def _inputs_detail(
    bid: str, heat: str, total: float, lines: list[dict]
) -> tuple[str, str]:
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
    title = f"Input for heat {html.escape(heat)}: {len(lines)} charge line(s), {_num(total)} kg"
    return _toggle("in", bid, total), _detail_row("in", title, table)


def _outputs_detail(
    bid: str, heat: str, total: float, lines: list[dict]
) -> tuple[str, str]:
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
    title = f"Output for heat {html.escape(heat)} by alloy: {_num(total)} kg"
    return _toggle("out", bid, total), _detail_row("out", title, table)


st.title("Production Snapshot")
st.caption(
    "Day-wise and shift-wise production at a glance, one row per batch, sorted by "
    "production date, shift, melt no and furnace. Click a **Total input** or **Total "
    "output** figure to expand its charge lines or its output by alloy. "
    "**Estimated output** is each charge line's weight × its material's newest "
    "recovery % (Raw Material Master), as on Production Data Analysis. **Cost/kg** is "
    "the overall ₹/kg saved on the batch's output (material + conversion). Actual − "
    "estimated and the yield use **Total output**. **Production date** is shown only "
    "for a date range. Data refreshes with the Dashboard; use **Refresh now** for the "
    "latest."
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

# One day is already named above the table, so its date column would repeat
# the same value on every row.
show_date = mode == BY_RANGE
N_COLS = 15 + (1 if show_date else 0)
frozen = (["date"] if show_date else []) + ["heat", "alloy", "cust"]
try:
    theme_type = str(st.context.theme.type or "light")
except Exception:
    theme_type = "light"
header = (
    "<tr>" + (f"<th{_fz('date', frozen)}>Production date</th>" if show_date else "")
    + f"<th{_fz('heat', frozen)}>Heat no</th><th{_fz('alloy', frozen)}>Alloy</th>"
    f"<th{_fz('cust', frozen)}>Customer</th>"
    "<th class='num'>Melt no</th>"
    "<th>Shift</th><th>Furnace</th><th class='num'>Total input (kg)</th><th>Input status</th>"
    "<th class='num'>Total output (kg)</th><th>Output status</th><th class='num'>Cost/kg (₹)</th>"
    "<th class='num'>Estimated output (kg)</th>"
    "<th class='num'>Actual − estimated (kg)</th><th class='num'>Actual vs estimated %</th>"
    "<th class='num'>Actual yield %</th></tr>"
)
customer_short = _customer_short_names([str(b.get("Customer_name") or "") for b in batches])
body = []
for b in batches:
    bid = str(b["Batch_ID"])
    delta = b["Output_vs_Estimate"]
    heat = str(b.get("Heat_no") or bid)
    in_cell, in_row = _inputs_detail(bid, heat, b["Total_Input"], snap["inputs"].get(bid, []))
    out_cell, out_row = _outputs_detail(
        bid, heat, b["Total_Output"], snap["outputs"].get(bid, [])
    )
    body.append(
        "<tr class='ps-row'>"
        + (
            f"<td{_fz('date', frozen)}>{_esc(format_ui_date(b.get('Production_Date')))}</td>"
            if show_date
            else ""
        )
        + f"<td{_fz('heat', frozen)}>{_esc(b.get('Heat_no'))}</td>"
        f"<td{_fz('alloy', frozen)} title='{html.escape(str(b.get('Alloy_name') or ''), quote=True)}'>"
        f"{_esc(b.get('Alloy_name'))}</td>"
        f"{_customer_cell(bid, b.get('Customer_name'), customer_short, _fz('cust', frozen))}"
        f"<td class='num'>{_esc(b.get('Melt_No'))}</td>"
        f"<td>{_esc(b.get('Shift'))}</td>"
        f"<td>{_esc(b.get('Furnace'))}</td>"
        f"<td class='num'>{in_cell}</td>"
        f"<td>{_esc(b.get('Production_status') or db.BATCH_STATUS_IN_PROGRESS)}</td>"
        f"<td class='num'>{out_cell}</td>"
        f"<td>{_esc(b.get('Output_status') or db.BATCH_STATUS_IN_PROGRESS)}</td>"
        f"<td class='num'>{_num(b.get('Cost_per_kg'), '{:,.2f}')}</td>"
        f"<td class='num'>{_num(b['Estimated_Output'])}</td>"
        f"<td class='num'>{_num(delta, '{:+,.1f}')}</td>"
        f"{_variance_cell(b['Output_vs_Estimate_pct'])}"
        f"{_yield_cell(b['Yield_pct'])}"
        "</tr>" + in_row + out_row
    )
body.append(
    "<tr class='ps-total'>"
    # The label spans exactly the frozen columns so it can stay frozen with them;
    # Melt no, Shift and Furnace get empty cells of their own.
    f"<td colspan='{len(frozen)}' class='fz fz-total fz-last'>Total ({len(batches)} batches)</td>"
    "<td></td><td></td><td></td>"
    f"<td class='num'>{_num(total_in)}</td><td></td>"
    f"<td class='num'>{_num(total_out)}</td><td></td><td></td>"
    f"<td class='num'>{_num(sum(b['Estimated_Output'] for b in batches))}</td>"
    f"<td class='num'>{_num(out_with_output - est_with_output if with_output else None, '{:+,.1f}')}</td>"
    f"{_variance_cell(overall_var)}{_yield_cell(overall_yield)}"
    "</tr>"
)
st.html(
    _CSS + _frozen_css(frozen, theme_type)
    + "<div class='ps-wrap'><table class='ps-table'>" + header + "".join(body) + "</table></div>"
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
            # Kept in the download even for one day, so the file says which day it is.
            "Production date": format_ui_date(b.get("Production_Date")),
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


# ── Output by alloy ──────────────────────────────────────────────────────────
# Finished goods (product alloy) and the non-spec outputs per alloy, for the same
# heats as the table above. Only heats with output saved count, as for the yield
# above, so a heat still in the furnace does not show as 100% melt loss.
ALLOY_CUSTOMER_CHARS = 12
GROUP_NOT_SET = "Not set"


def _cut_customer(name: object) -> str:
    """First ALLOY_CUSTOMER_CHARS characters of the customer name, then "..."."""
    text = str(name or "").strip()
    if not text:
        return "—"
    return text if len(text) <= ALLOY_CUSTOMER_CHARS else text[:ALLOY_CUSTOMER_CHARS] + "..."


def _kg_td(value: float) -> str:
    if abs(value) < 0.05:
        return "<td class='num ps-zero'>–</td>"
    return f"<td class='num'>{value:,.1f}</td>"


def _pct_td(part: float, whole: float) -> str:
    if whole <= 0.05:
        return "<td class='num'>—</td>"
    pct = part / whole * 100.0
    if abs(pct) < 0.005:
        return "<td class='num ps-zero'>–</td>"
    return f"<td class='num'>{pct:,.2f}%</td>"


st.subheader("Output by alloy")
st.caption(
    "Finished goods (product alloy) and non-spec output for the heats above, one row per "
    "alloy, in **alloy group** order, then alloy name and customer. **Melt loss** = input − "
    "total output. The **Percentage** table shows each as a share of that alloy's input, so "
    "a row adds up to 100% and **Total output** is the yield. Only heats with output saved "
    "are counted. Alloy group comes from the **Alloy Master**."
)

alloy_master = {int(a["Alloy_id"]): a for a in db.list_alloys() if a.get("Alloy_id") is not None}
side_ids = [int(x) for x in db.SIDESTREAM_ALLOY_IDS]
side_names = {
    sid: str((alloy_master.get(sid) or {}).get("Alloy_name") or f"Alloy {sid}") for sid in side_ids
}
done = [b for b in batches if b["Total_Output"] > 0]
waiting = len(batches) - len(done)

groups: dict[object, dict] = {}
for b in done:
    aid = b.get("Alloy_id")
    info = alloy_master.get(int(aid)) if aid is not None else None
    g = groups.setdefault(aid, {
        "group": (str((info or {}).get("Alloy_group") or "").strip() or GROUP_NOT_SET),
        "alloy": str(b.get("Alloy_name") or (info or {}).get("Alloy_name") or "Not set"),
        "customer": str(b.get("Customer_name") or (info or {}).get("Customer_name") or ""),
        "heats": 0, "input": 0.0, "fg": 0.0, "pcs": 0.0,
        "side": {sid: 0.0 for sid in side_ids}, "output": 0.0,
    })
    g["heats"] += 1
    g["input"] += b["Total_Input"]
    g["output"] += b["Total_Output"]
    for o in snap["outputs"].get(str(b["Batch_ID"]), []):
        oid = int(o.get("Alloy_id") or 0)
        kg = float(o.get("Weight") or 0)
        if oid in g["side"]:
            g["side"][oid] += kg
        else:
            g["fg"] += kg
            g["pcs"] += float(o.get("Pieces") or 0)

if not groups:
    st.info(f"No heat in {period} has output saved yet.")
else:
    ordered = sorted(
        groups.values(),
        key=lambda g: (g["group"] == GROUP_NOT_SET, g["group"].casefold(),
                       g["alloy"].casefold(), g["customer"].casefold()),
    )

    def _sum(rows: list[dict]) -> dict:
        return {
            "heats": sum(r["heats"] for r in rows),
            "input": sum(r["input"] for r in rows),
            "fg": sum(r["fg"] for r in rows),
            "pcs": sum(r["pcs"] for r in rows),
            "side": {sid: sum(r["side"][sid] for r in rows) for sid in side_ids},
            "output": sum(r["output"] for r in rows),
        }

    # (kind, label cells, figures) in display order: alloys, a subtotal after each
    # alloy group with more than one alloy, and the grand total.
    lines: list[tuple[str, tuple[str, str, str, str], dict]] = []
    by_group: dict[str, list[dict]] = {}
    for g in ordered:
        by_group.setdefault(g["group"], []).append(g)
    for group, members in by_group.items():
        for g in members:
            lines.append(("alloy", (group, g["alloy"], _cut_customer(g["customer"]),
                                    g["customer"]), g))
        if len(members) > 1:
            lines.append(("sub", (group, f"{group} subtotal", "", ""), _sum(members)))
    lines.append(("total", ("Total", "", "", ""), _sum(ordered)))

    def _label_tds(kind: str, labels: tuple[str, str, str, str]) -> str:
        group, alloy, cust, full = labels
        if kind == "total":
            return "<td colspan='3'>Total</td>"
        title = html.escape(full, quote=True)
        return (f"<td>{_esc(group)}</td><td>{_esc(alloy)}</td>"
                f"<td title='{title}'>{html.escape(cust)}</td>")

    row_class = {"alloy": "", "sub": " class='ps-sub'", "total": " class='ps-total'"}
    side_heads = "".join(f"<th class='num'>{_esc(side_names[sid])} (kg)</th>" for sid in side_ids)
    act_head = (
        "<tr><th>Alloy group</th><th>Alloy</th><th>Customer</th><th class='num'>Heats</th>"
        "<th class='num'>Input (kg)</th><th class='num'>Finished goods (kg)</th>"
        "<th class='num'>FG pieces</th><th class='num'>Avg piece (kg)</th>" + side_heads
        + "<th class='num'>Total output (kg)</th><th class='num'>Melt loss (kg)</th>"
        "<th class='num'>Yield %</th></tr>"
    )
    act_body = []
    for kind, labels, f in lines:
        loss = f["input"] - f["output"]
        avg_piece = f["fg"] / f["pcs"] if f["pcs"] > 0 else None
        act_body.append(
            f"<tr{row_class[kind]}>" + _label_tds(kind, labels)
            + f"<td class='num'>{f['heats']:,}</td>" + _kg_td(f["input"]) + _kg_td(f["fg"])
            + (f"<td class='num'>{f['pcs']:,.0f}</td>" if f["pcs"] > 0
               else "<td class='num ps-zero'>–</td>")
            + f"<td class='num'>{_num(avg_piece, '{:,.2f}')}</td>"
            + "".join(_kg_td(f["side"][sid]) for sid in side_ids)
            + _kg_td(f["output"]) + _kg_td(loss)
            + _yield_cell(f["output"] / f["input"] * 100 if f["input"] > 0.05 else None)
            + "</tr>"
        )
    side_pct_heads = "".join(f"<th class='num'>{_esc(side_names[sid])} %</th>" for sid in side_ids)
    pct_head = (
        "<tr><th>Alloy group</th><th>Alloy</th><th>Customer</th>"
        "<th class='num'>Finished goods %</th>" + side_pct_heads
        + "<th class='num'>Total output (yield) %</th><th class='num'>Melt loss %</th>"
        "<th class='num'>Input %</th></tr>"
    )
    pct_body = []
    for kind, labels, f in lines:
        whole = f["input"]
        pct_body.append(
            f"<tr{row_class[kind]}>" + _label_tds(kind, labels)
            + _pct_td(f["fg"], whole)
            + "".join(_pct_td(f["side"][sid], whole) for sid in side_ids)
            + _pct_td(f["output"], whole) + _pct_td(whole - f["output"], whole)
            + _pct_td(whole, whole)
            + "</tr>"
        )

    st.markdown("**Actual**")
    st.html(_CSS + "<div class='ps-wrap'><table class='ps-table'>" + act_head
            + "".join(act_body) + "</table></div>")
    st.markdown("**Percentage of input**")
    st.html(_CSS + "<div class='ps-wrap'><table class='ps-table'>" + pct_head
            + "".join(pct_body) + "</table></div>")
    notes = [
        f"Yield in **green** meets the {db.YIELD_TARGET_PCT:g}% target, **red** is below it.",
        "Hover over a customer to see the full name.",
    ]
    if waiting:
        notes.append(f"{waiting} heat(s) with no output saved yet are left out.")
    st.caption(" ".join(notes))

    alloy_export = []
    for kind, labels, f in lines:
        whole = f["input"]
        row = {
            "Row": {"alloy": "Alloy", "sub": "Group subtotal", "total": "Total"}[kind],
            "Alloy group": labels[0] if kind != "total" else "",
            "Alloy": labels[1],
            "Customer": labels[3],
            "Heats": f["heats"],
            "Input (kg)": round(whole, 2),
            "Finished goods (kg)": round(f["fg"], 2),
            "FG pieces": int(f["pcs"]),
        }
        for sid in side_ids:
            row[f"{side_names[sid]} (kg)"] = round(f["side"][sid], 2)
        row["Total output (kg)"] = round(f["output"], 2)
        row["Melt loss (kg)"] = round(whole - f["output"], 2)
        pct = (lambda v: round(v / whole * 100, 2) if whole > 0.05 else None)
        row["Finished goods %"] = pct(f["fg"])
        for sid in side_ids:
            row[f"{side_names[sid]} %"] = pct(f["side"][sid])
        row["Total output (yield) %"] = pct(f["output"])
        row["Melt loss %"] = pct(whole - f["output"])
        alloy_export.append(row)
    st.download_button(
        "Download output by alloy (CSV)",
        data=pd.DataFrame(alloy_export).to_csv(index=False).encode("utf-8"),
        file_name=f"output_by_alloy_{start_date.isoformat()}_{end_date.isoformat()}.csv",
        mime="text/csv",
        key="psnap_alloy_csv",
    )
