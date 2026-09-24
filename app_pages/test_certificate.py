import re
import base64
import html
from datetime import date, datetime
from pathlib import Path
import pandas as pd
import streamlit as st
import database as db
from pages_common import _as_photo_bytes, _is_blank_date, _pandas_styler, _parse_master_date, _show_db_connection_error, df_from_rows, format_ui_date, parse_any_date, show_dataframe, to_storage_date, ui_date_input

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "nualco_logo.png"
ISO_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "iso_9001_2015.png"
_BRAND_ORANGE = "#F15A22"
_BRAND_INK = "#1A1A1A"


def format_cert_print_date(value: object, empty: str = "—") -> str:
    """Formal certificate dates match the QMS form: DD-MM-YYYY."""
    parsed = parse_any_date(value)
    if parsed is None:
        return empty if _is_blank_date(value) else str(value)
    day = parsed.date() if isinstance(parsed, datetime) else parsed
    return day.strftime("%d-%m-%Y")


def _image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _certificate_logo_data_uri() -> str:
    for path in (LOGO_PATH, LOGO_PATH.with_suffix(".jpg")):
        uri = _image_data_uri(path)
        if uri:
            return uri
    return ""


def _iso_logo_data_uri() -> str:
    return _image_data_uri(ISO_LOGO_PATH)


def _company_letterhead(company: dict) -> dict[str, str]:
    street = " ".join(str(company.get("Address") or "").strip().rstrip(".").split())
    city = str(company.get("City") or "").strip()
    pin = str(company.get("Pincode") or "").strip()
    pin_fmt = f"{pin[:3]} {pin[3:]}" if pin.isdigit() and len(pin) == 6 else pin
    if city and pin_fmt:
        place = f"{city} - {pin_fmt}"
    else:
        place = city or pin_fmt
    address = ", ".join(part for part in (street, place) if part)
    if address:
        address = address.rstrip(".") + "."
    phone = str(company.get("Phone1") or "").strip()
    email = str(company.get("Email1") or "").strip()
    contact_bits = []
    if phone:
        contact_bits.append(f"Phone : {phone}")
    if email:
        contact_bits.append(f"Email : {email}")
    gst = str(company.get("GST") or "").strip()
    return {
        "name": str(company.get("Company_name") or "Nualco Private Limited").strip(),
        "address": address,
        "contact": "  ".join(contact_bits),
        "gst": f"GSTIN : {gst}" if gst else "",
    }


def _tc_sync_editor(lines: list[dict], edited) -> list[dict]:
    """Copy printed kg / heat / selection from the data editor back onto lines."""
    if edited is None:
        return lines
    by_no = {int(row.get("Line_no") or 0): row for row in edited.to_dict("records")}
    out = []
    for line in lines:
        row = by_no.get(int(line["Line_no"]), {})
        item = dict(line)
        if "Heat no" in row:
            item["Display_heat_no"] = db.certificate_display_heat_no(
                row.get("Heat no")
            )
        if "Printed kg" in row:
            item["Weight"] = float(row.get("Printed kg") or 0)
        item["_selected"] = bool(row.get("Select"))
        out.append(item)
    return out


def _pdf_safe_text(value: object) -> str:
    """Helvetica core fonts only cover Latin-1; map common Unicode first."""
    text = str(value if value is not None else "")
    text = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2212", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2026", "...")
        .replace("\u00a0", " ")
        .replace("\u2713", "Y")
        .replace("\u2714", "Y")
        .replace("\u00b7", "-")
    )
    return text.encode("latin-1", "replace").decode("latin-1")


def _certificate_pdf_bytes(payload: dict) -> bytes:
    """Build an A4 PDF of the formal test certificate."""
    from fpdf import FPDF

    orange = (241, 90, 34)
    ink = (26, 26, 26)
    fill = (247, 247, 247)
    line = (122, 122, 122)
    letterhead = _company_letterhead(payload.get("company") or {})
    heats = payload.get("heats") or []
    elements = payload.get("elements") or []
    inspection = payload.get("inspection") or []

    class _CertPDF(FPDF):
        def footer(self) -> None:
            return None

    pdf = _CertPDF(orientation="P", unit="mm", format="A4")
    # One A4 sheet: do not spill onto a second page. 10 mm keeps
    # content inside a typical desktop printer's unprintable edge.
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    pdf.set_text_color(*ink)
    pdf.set_draw_color(*line)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    left = pdf.l_margin
    y = pdf.get_y()
    side = 24

    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=left, y=y, w=20)
    if ISO_LOGO_PATH.exists():
        pdf.image(str(ISO_LOGO_PATH), x=left + page_w - 20, y=y, w=20)

    pdf.set_xy(left + side, y)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(
        page_w - 2 * side,
        6,
        _pdf_safe_text(letterhead["name"]),
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_font("Helvetica", "", 8)
    for line_text in (
        letterhead["address"],
        letterhead["contact"],
        letterhead["gst"],
    ):
        if line_text:
            pdf.set_x(left + side)
            pdf.cell(
                page_w - 2 * side,
                4,
                _pdf_safe_text(line_text),
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )
    pdf.set_y(max(pdf.get_y(), y + 24) + 2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(page_w, 7, "TEST CERTIFICATE", border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    def _fit(text: str, width: float, size: int = 7) -> str:
        pdf.set_font("Helvetica", "", size)
        out = _pdf_safe_text(text)
        while out and pdf.get_string_width(out) > width - 1.4:
            out = out[:-1]
        return out

    def _meta_row(cells: list[tuple[str, float, bool]]) -> None:
        h = 5.6
        x = left
        y0 = pdf.get_y()
        for text, width, is_label in cells:
            pdf.set_xy(x, y0)
            pdf.set_font("Helvetica", "B" if is_label else "", 8)
            if is_label:
                pdf.set_fill_color(*fill)
            shown = _pdf_safe_text(text) if is_label else _fit(str(text), width, 8)
            pdf.cell(width, h, shown, border=1, align="L", fill=is_label)
            x += width
        pdf.set_y(y0 + h)

    lw, vw = page_w * 0.16, page_w * 0.34
    meta_pairs = [
        ("Report No.", payload.get("certificate_no") or "—"),
        ("Grade", payload.get("grade") or "—"),
        ("Report Date", format_cert_print_date(payload.get("issued_date"))),
        ("Colour Code", payload.get("colour_code") or "—"),
        ("Customer", payload.get("customer_name") or "—"),
        ("Customer Reference", payload.get("cust_code") or "—"),
        ("Invoice No", payload.get("invoice_no") or "—"),
        ("Invoice Date", format_cert_print_date(payload.get("invoice_date"))),
        ("P.O No", payload.get("po_no") or "—"),
        ("P.O Date", format_cert_print_date(payload.get("po_date"))),
    ]
    for i in range(0, len(meta_pairs), 2):
        left_label, left_value = meta_pairs[i]
        right_label, right_value = meta_pairs[i + 1]
        _meta_row(
            [
                (str(left_label), lw, True),
                (str(left_value), vw, False),
                (str(right_label), lw, True),
                (str(right_value), vw, False),
            ]
        )
    doc_id = str(payload.get("document_id") or "").strip()
    total_w = f"{db._format_cert_number(payload.get('total_weight'))} Kgs"
    _meta_row(
        [
            ("Total Weight", lw, True),
            (total_w, vw, False),
            (doc_id, lw + vw, False),
        ]
    )

    def _section(title: str) -> None:
        pdf.set_fill_color(*orange)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(
            page_w,
            6,
            _pdf_safe_text(title),
            border=1,
            align="C",
            fill=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(*ink)

    _section("CHEMICAL ANALYSIS REPORT")
    heat_count = max(len(heats), 1)
    elem_w, spec_w = 42.0, 30.0
    heat_w = (page_w - elem_w - spec_w) / heat_count
    head_h = 15.0
    row_h = 5.0
    y0 = pdf.get_y()
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(243, 243, 243)
    pdf.rect(left, y0, elem_w, head_h, style="DF")
    pdf.rect(left + elem_w, y0, spec_w, head_h, style="DF")
    pdf.set_xy(left, y0 + 5)
    pdf.cell(elem_w, 5, "ELEMENTS", align="C")
    pdf.set_xy(left + elem_w, y0 + 5)
    pdf.cell(spec_w, 5, "SPECIFICATION %", align="C")
    for index, heat in enumerate(heats or [{"Heat_no": "—", "Kgs": "", "Pieces": ""}]):
        x = left + elem_w + spec_w + index * heat_w
        pdf.rect(x, y0, heat_w, 5, style="DF")
        pdf.rect(x, y0 + 5, heat_w, 5, style="DF")
        pdf.rect(x, y0 + 10, heat_w, 5, style="DF")
        pdf.set_xy(x, y0)
        pdf.cell(heat_w, 5, _fit(f"Heat No : {heat.get('Heat_no') or '—'}", heat_w), align="C")
        pdf.set_xy(x, y0 + 5)
        kgs = db._format_cert_number(heat.get("Kgs"))
        pcs = str(int(float(heat.get("Pieces") or 0)))
        pdf.cell(heat_w, 5, _fit(f"Kgs : {kgs}   Pcs : {pcs}", heat_w), align="C")
        pdf.set_xy(x, y0 + 10)
        pdf.cell(heat_w, 5, "ACTUAL %", align="C")
    pdf.set_y(y0 + head_h)

    if not elements:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(page_w, row_h, "No customer specification found for this alloy.", border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    for row in elements:
        y1 = pdf.get_y()
        pdf.set_font("Helvetica", "", 7)
        pdf.rect(left, y1, elem_w, row_h)
        pdf.rect(left + elem_w, y1, spec_w, row_h)
        pdf.set_xy(left, y1)
        pdf.cell(elem_w, row_h, _fit(str(row.get("Display_label") or row.get("Element_Name") or ""), elem_w))
        pdf.set_xy(left + elem_w, y1)
        pdf.cell(spec_w, row_h, _fit(str(row.get("Spec_text") or ""), spec_w), align="C")
        actuals = row.get("actuals") or ["—"] * heat_count
        for index in range(heat_count):
            x = left + elem_w + spec_w + index * heat_w
            pdf.rect(x, y1, heat_w, row_h)
            pdf.set_xy(x, y1)
            value = actuals[index] if index < len(actuals) else "—"
            pdf.cell(heat_w, row_h, _fit(str(value), heat_w), align="C")
        pdf.set_y(y1 + row_h)

    _section("INSTRUMENT DETAILS")
    inst_rows = [
        ("Analysis Method", payload.get("analysis_method") or ""),
        ("Instrument", payload.get("instrument") or ""),
        ("Instrument Make", payload.get("instrument_make") or ""),
    ]
    label_w = page_w * 0.28
    for label, value in inst_rows:
        y1 = pdf.get_y()
        pdf.set_fill_color(*fill)
        pdf.set_font("Helvetica", "B", 8)
        pdf.rect(left, y1, label_w, 6, style="DF")
        pdf.set_xy(left, y1)
        pdf.cell(label_w, 6, _pdf_safe_text(label))
        pdf.set_font("Helvetica", "", 8)
        pdf.rect(left + label_w, y1, page_w - label_w, 6)
        pdf.set_xy(left + label_w, y1)
        pdf.cell(page_w - label_w, 6, _pdf_safe_text(value))
        pdf.set_y(y1 + 6)

    _section("VISUAL INSPECTIONS : CUSTOMER REQUIREMENT STATUS")
    q_w, s_w, v_w = page_w * 0.76, page_w * 0.12, page_w * 0.12
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(243, 243, 243)
    pdf.cell(q_w, 6, "Requirement", border=1, align="C", fill=True)
    pdf.cell(s_w, 6, "STATUS", border=1, align="C", fill=True)
    pdf.cell(v_w, 6, "VERIFY", border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    insp_h = 5.3
    for row in inspection:
        y1 = pdf.get_y()
        answer = str(row.get("Answer") or "").strip()
        status = "Ok" if answer.upper() == "OK" else (answer or "-")
        verify = "Yes" if row.get("Verified") else ""
        pdf.set_font("Helvetica", "", 7)
        pdf.rect(left, y1, q_w, insp_h)
        pdf.set_xy(left, y1)
        pdf.cell(q_w, insp_h, _fit(str(row.get("Question_text") or ""), q_w, 7))
        pdf.set_font("Helvetica", "B", 7)
        pdf.rect(left + q_w, y1, s_w, insp_h)
        pdf.set_xy(left + q_w, y1)
        pdf.cell(s_w, insp_h, _pdf_safe_text(status), align="C")
        pdf.rect(left + q_w + s_w, y1, v_w, insp_h)
        pdf.set_xy(left + q_w + s_w, y1)
        pdf.cell(v_w, insp_h, _pdf_safe_text(verify), align="C")
        pdf.set_y(y1 + insp_h)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(
        page_w,
        6,
        _pdf_safe_text(f"Approved by : {payload.get('approved_by') or ''}"),
    )
    return bytes(pdf.output())


def _render_certificate_print(
    header: dict, cert: dict, lines: list[dict], inspection: list[dict] | None = None
) -> dict:
    payload = db.get_test_certificate_print_payload(
        int(header["Packing_list_id"]),
        lines=lines,
        inspection=inspection,
    )
    letterhead = _company_letterhead(payload.get("company") or {})
    logo_src = _certificate_logo_data_uri()
    iso_src = _iso_logo_data_uri()
    logo_html = (
        f'<img class="tc-logo" src="{logo_src}" alt="Nualco">'
        if logo_src
        else '<div class="tc-logo-fallback">NUALCO</div>'
    )
    iso_html = (
        f'<img class="tc-iso" src="{iso_src}" alt="ISO 9001:2015 Certified Company">'
        if iso_src
        else ""
    )
    esc = html.escape
    meta_pairs = [
        ("Report No.", payload.get("certificate_no") or "—"),
        ("Grade", payload.get("grade") or "—"),
        ("Report Date", format_cert_print_date(payload.get("issued_date"))),
        ("Colour Code", payload.get("colour_code") or "—"),
        ("Customer", payload.get("customer_name") or "—"),
        ("Customer Reference", payload.get("cust_code") or "—"),
        ("Invoice No", payload.get("invoice_no") or "—"),
        ("Invoice Date", format_cert_print_date(payload.get("invoice_date"))),
        ("P.O No", payload.get("po_no") or "—"),
        ("P.O Date", format_cert_print_date(payload.get("po_date"))),
    ]
    meta_rows_html = ""
    for i in range(0, len(meta_pairs), 2):
        left_label, left_value = meta_pairs[i]
        right_label, right_value = meta_pairs[i + 1]
        meta_rows_html += (
            "<tr>"
            f"<td class='tc-label'>{esc(left_label)}</td>"
            f"<td class='tc-value'>{esc(str(left_value))}</td>"
            f"<td class='tc-label'>{esc(right_label)}</td>"
            f"<td class='tc-value'>{esc(str(right_value))}</td>"
            "</tr>"
        )
    document_id = str(payload.get("document_id") or "").strip()
    meta_rows_html += (
        "<tr>"
        "<td class='tc-label'>Total Weight</td>"
        f"<td class='tc-value'>{esc(db._format_cert_number(payload.get('total_weight')))} Kgs</td>"
        f"<td class='tc-docid-cell' colspan='2'>{esc(document_id)}</td>"
        "</tr>"
    )
    heats = payload.get("heats") or []
    heat_count = max(len(heats), 1)
    heat_head = ""
    heat_qty = ""
    heat_actual = ""
    for heat in heats:
        heat_head += (
            f"<th class='tc-heat' colspan='1'>"
            f"Heat No : {esc(str(heat.get('Heat_no') or '—'))}</th>"
        )
        heat_qty += (
            "<th class='tc-heat-sub'>"
            f"Kgs : {esc(db._format_cert_number(heat.get('Kgs')))}<br>"
            f"Pcs : {esc(str(int(float(heat.get('Pieces') or 0))))}"
            "</th>"
        )
        heat_actual += "<th class='tc-heat-sub'>ACTUAL %</th>"
    if not heats:
        heat_head = "<th class='tc-heat'>Heat No : —</th>"
        heat_qty = "<th class='tc-heat-sub'>Kgs : — &nbsp; Pcs : —</th>"
        heat_actual = "<th class='tc-heat-sub'>ACTUAL %</th>"
    chem_body = ""
    for row in payload.get("elements") or []:
        cells = "".join(
            f"<td class='tc-actual'>{esc(str(value))}</td>"
            for value in (row.get("actuals") or ["—"] * heat_count)
        )
        chem_body += (
            "<tr>"
            f"<td class='tc-elem'>{esc(str(row.get('Display_label') or row.get('Element_Name') or ''))}</td>"
            f"<td class='tc-spec'>{esc(str(row.get('Spec_text') or ''))}</td>"
            f"{cells}"
            "</tr>"
        )
    if not chem_body:
        chem_body = (
            f"<tr><td colspan='{2 + heat_count}' class='tc-empty'>"
            "No customer specification found for this alloy.</td></tr>"
        )
    insp_body = ""
    for row in payload.get("inspection") or []:
        answer = str(row.get("Answer") or "").strip()
        status = "Ok" if answer.upper() == "OK" else (answer or "—")
        verify = "✓" if row.get("Verified") else ""
        insp_body += (
            "<tr>"
            f"<td class='tc-insp-q'>{esc(str(row.get('Question_text') or ''))}</td>"
            f"<td class='tc-insp-status'>{esc(status)}</td>"
            f"<td class='tc-insp-verify'>{verify}</td>"
            "</tr>"
        )
    st.markdown(
        f"""
        <style>
        .tc-doc {{
            background: #fff; color: {_BRAND_INK};
            border: 1px solid #cfcfcf; padding: 0.85rem 1rem 1.1rem;
            font-family: Arial, Helvetica, sans-serif; font-size: 12.5px;
        }}
        .block-container {{ max-width: 1100px !important; }}
        .tc-head {{
            display: flex; align-items: flex-start;
            gap: 0.45rem; margin: 0 0 0.2rem 0;
        }}
        .tc-head-left, .tc-head-right {{
            flex: 0 0 96px; width: 96px;
        }}
        .tc-head-left {{ text-align: left; }}
        .tc-head-right {{ text-align: center; }}
        .tc-head-center {{
            flex: 1 1 auto; text-align: center; min-width: 0;
            padding-top: 0.15rem;
        }}
        .tc-logo {{ width: 86px; height: auto; display: inline-block; }}
        .tc-logo-fallback {{
            font-weight: 800; color: {_BRAND_ORANGE}; font-size: 1.1rem;
        }}
        .tc-company-name {{
            margin: 0 auto; font-size: 1.2rem; font-weight: 800;
            letter-spacing: 0.02em; text-align: center; line-height: 1.25;
            white-space: nowrap;
        }}
        .tc-company-line {{
            margin: 0.16rem auto 0; line-height: 1.4; text-align: center;
        }}
        .tc-iso {{
            width: 88px; height: auto; display: block;
            margin: 0 auto;
        }}
        .tc-meta .tc-docid-cell {{
            font-weight: 700; text-align: center; letter-spacing: 0.01em;
        }}
        .tc-title {{
            margin: 0.7rem 0 0.45rem; text-align: center;
            font-size: 1.15rem; font-weight: 800;
            border: 1px solid {_BRAND_INK}; padding: 0.28rem 0;
            letter-spacing: 0.06em;
        }}
        .tc-table {{
            width: 100%; border-collapse: collapse; margin: 0;
        }}
        .tc-table th, .tc-table td {{
            border: 1px solid #7a7a7a; padding: 0.28rem 0.4rem;
            vertical-align: middle;
        }}
        .tc-meta .tc-label {{
            width: 16%; font-weight: 700; background: #f7f7f7; white-space: nowrap;
        }}
        .tc-meta .tc-value {{ width: 34%; font-weight: 600; }}
        .tc-section {{
            background: {_BRAND_ORANGE}; color: #fff; font-weight: 800;
            text-align: center; letter-spacing: 0.04em;
            padding: 0.28rem 0.4rem; margin: 0.55rem 0 0;
            border: 1px solid #c24a12;
        }}
        .tc-chem thead th {{
            background: #f3f3f3; font-weight: 700; text-align: center;
        }}
        .tc-chem .tc-elem {{ text-align: left; font-weight: 600; width: 24%; }}
        .tc-chem .tc-spec {{ text-align: center; width: 16%; }}
        .tc-chem .tc-actual {{ text-align: center; font-weight: 600; }}
        .tc-chem .tc-heat, .tc-chem .tc-heat-sub {{ min-width: 7.5rem; }}
        .tc-empty {{ text-align: center; color: #666; }}
        .tc-inst td:first-child {{ width: 22%; font-weight: 700; background: #f7f7f7; }}
        .tc-insp-q {{ text-align: left; }}
        .tc-insp-status, .tc-insp-verify {{
            text-align: center; font-weight: 700; width: 12%;
        }}
        .tc-approve {{
            margin: 0.85rem 0 0; font-weight: 700; font-size: 0.95rem;
        }}
        @media print {{
            [data-testid="stSidebar"], [data-testid="stToolbar"],
            footer, header, .no-print,
            .stAppToolbar, [data-testid="stHeading"],
            [data-testid="stSelectbox"], [data-testid="stCaption"] {{
                display: none !important;
            }}
            .block-container {{ max-width: 100% !important; padding: 0.2rem !important; }}
            .tc-doc {{ border: none; padding: 0; }}
        }}
        </style>
        <div class="tc-doc">
            <div class="tc-head">
                <div class="tc-head-left">{logo_html}</div>
                <div class="tc-head-center">
                    <div class="tc-company-name">{esc(letterhead["name"])}</div>
                    <div class="tc-company-line">{esc(letterhead["address"])}</div>
                    <div class="tc-company-line">{esc(letterhead["contact"])}</div>
                    <div class="tc-company-line">{esc(letterhead["gst"])}</div>
                </div>
                <div class="tc-head-right">{iso_html}</div>
            </div>
            <div class="tc-title">TEST CERTIFICATE</div>
            <table class="tc-table tc-meta">
                <tbody>{meta_rows_html}</tbody>
            </table>
            <div class="tc-section">CHEMICAL ANALYSIS REPORT</div>
            <table class="tc-table tc-chem">
                <thead>
                    <tr>
                        <th rowspan="3">ELEMENTS</th>
                        <th rowspan="3">SPECIFICATION %</th>
                        {heat_head}
                    </tr>
                    <tr>{heat_qty}</tr>
                    <tr>{heat_actual}</tr>
                </thead>
                <tbody>{chem_body}</tbody>
            </table>
            <div class="tc-section">INSTRUMENT DETAILS</div>
            <table class="tc-table tc-inst">
                <tbody>
                    <tr><td>Analysis Method</td><td>{esc(str(payload.get("analysis_method") or ""))}</td></tr>
                    <tr><td>Instrument</td><td>{esc(str(payload.get("instrument") or ""))}</td></tr>
                    <tr><td>Instrument Make</td><td>{esc(str(payload.get("instrument_make") or ""))}</td></tr>
                </tbody>
            </table>
            <div class="tc-section">VISUAL INSPECTIONS : CUSTOMER REQUIREMENT STATUS</div>
            <table class="tc-table tc-insp">
                <thead>
                    <tr>
                        <th>Requirement</th>
                        <th>STATUS</th>
                        <th>VERIFY</th>
                    </tr>
                </thead>
                <tbody>{insp_body}</tbody>
            </table>
            <p class="tc-approve">Approved by : {esc(str(payload.get("approved_by") or ""))}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return payload


def _render_certificate_summary(
    header: dict,
    cert: dict,
    lines: list[dict],
    inspection: list[dict],
) -> None:
    """Plain, printable summary of the certificate as it stands (unsaved draft
    edits included) for looking things up during the physical inspection."""
    st.markdown(
        f"""
        <style>
        .tc-summary-wrap {{
            border: 1px solid #ddd; border-radius: 6px; padding: 1.25rem 1.5rem;
            background: #fff; color: {_BRAND_INK};
        }}
        .tc-summary-title {{
            margin: 0 0 0.25rem 0; color: {_BRAND_INK};
            border-bottom: 2px solid {_BRAND_ORANGE}; padding-bottom: 0.35rem;
        }}
        .tc-summary-table {{
            width: 100%; border-collapse: collapse; margin: 0.75rem 0 0 0; font-size: 0.95rem;
        }}
        .tc-summary-table th, .tc-summary-table td {{
            border: 1px solid #bdbdbd; padding: 0.5rem 0.65rem; text-align: left;
            vertical-align: top;
        }}
        .tc-summary-meta td:first-child {{ width: 30%; font-weight: 600; background: #f7f7f7; }}
        .tc-summary-lines th {{ background: #f0f0f0; font-weight: 700; }}
        .tc-summary-lines td.num, .tc-summary-lines th.num {{ text-align: right; }}
        .tc-summary-lines tr.src td {{ color: #555; font-size: 0.88rem; background: #fcfcfc; }}
        .tc-summary-lines tfoot td {{ font-weight: 700; background: #fafafa; }}
        .tc-summary-h {{ margin: 1.1rem 0 0 0; font-size: 1.05rem; }}
        .tc-summary-bad {{ color: #c62828; font-weight: 700; }}
        .tc-summary-note {{ margin-top: 0.75rem; font-size: 0.85rem; color: #555; }}
        @media print {{
            [data-testid="stSidebar"], [data-testid="stToolbar"], footer, header,
            .no-print {{ display: none !important; }}
            .block-container {{ max-width: 100% !important; padding: 0.25rem !important; }}
            .tc-summary-wrap {{ border: none; padding: 0; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    esc = html.escape
    packed = list(header.get("batches") or [])
    packed_w = sum(float(r.get("Weight") or 0) for r in packed)
    packed_p = sum(int(float(r.get("Pieces") or 0)) for r in packed)
    printed_w = sum(float(r.get("Weight") or 0) for r in lines)
    printed_p = sum(int(float(r.get("Pieces") or 0)) for r in lines)
    weights = db.certificate_weight_summary(packed_w, printed_w)
    heat_by_batch = {str(r.get("Batch_ID")): r.get("Heat_no") for r in packed}

    meta_rows = [
        ("Certificate no", cert.get("Certificate_no") or "—"),
        ("Certificate status", cert.get("Status") or "—"),
        ("Issued date", format_ui_date(cert.get("Issued_date"), empty="—")),
        ("Packing list", f"#{header.get('Packing_list_id')} ({header.get('Packing_list_status') or '—'})"),
        ("Invoice", f"{header.get('Invoice_number') or '—'}  ·  "
                    f"{format_ui_date(header.get('Invoice_date'), empty='—')}"),
        ("P.O. Number", header.get("Customer_PO_No") or "—"),
        ("Customer name", header.get("Customer_name") or "—"),
        ("Alloy name", header.get("Alloy_name") or "—"),
        ("Colour code", header.get("Colour_code") or "—"),
        ("Vehicle No", header.get("Vehicle_no") or "—"),
    ]
    meta_html = "".join(
        f"<tr><td>{esc(label)}</td><td>{esc(str(value))}</td></tr>"
        for label, value in meta_rows
    )

    line_rows = ""
    for line in lines:
        sources = line.get("sources") or []
        source_kg = sum(float(s.get("Source_weight") or 0) for s in sources)
        printed_kg = float(line.get("Weight") or 0)
        extra = printed_kg - source_kg
        line_rows += (
            "<tr>"
            f"<td>{int(line.get('Line_no') or 0)}</td>"
            f"<td><b>{esc(db.certificate_display_heat_no(line.get('Display_heat_no')) or '—')}</b>"
            f"{' (blended)' if line.get('Is_blended') else ''}</td>"
            f"<td class='num'>{source_kg:,.2f}</td>"
            f"<td class='num'><b>{printed_kg:,.2f}</b></td>"
            f"<td class='num'>{extra:+,.2f}</td>"
            f"<td class='num'>{int(float(line.get('Pieces') or 0)):,}</td>"
            "</tr>"
        )
        for src in sources:
            bid = str(src.get("Batch_ID") or "—")
            heat = src.get("Heat_no") or heat_by_batch.get(bid) or "—"
            line_rows += (
                "<tr class='src'>"
                "<td></td>"
                f"<td>&nbsp;&nbsp;↳ {esc(bid)} · heat {esc(str(heat))}</td>"
                f"<td class='num'>{float(src.get('Source_weight') or 0):,.2f}</td>"
                "<td></td><td></td>"
                f"<td class='num'>{int(float(src.get('Source_pieces') or 0)):,}</td>"
                "</tr>"
            )
    if not line_rows:
        line_rows = "<tr><td colspan='6' style='text-align:center;color:#666'>No printed lines</td></tr>"

    pieces_note = (
        "" if printed_p == packed_p
        else f" <span class='tc-summary-bad'>Pieces differ from packed ({packed_p:,}).</span>"
    )
    weight_note = (
        f"Round-up {weights['delta_kg']:+,.2f} kg ({weights['delta_pct']:+.3f}%), "
        f"allowed up to {weights['allowed_kg']:,.2f} kg (+{weights['max_pct']:g}%)."
    )
    if not weights["ok"]:
        weight_note = f"<span class='tc-summary-bad'>{esc(weight_note)} Outside the allowed round-up.</span>"
    else:
        weight_note = esc(weight_note)

    insp_rows = ""
    for row in inspection:
        answer = str(row.get("Answer") or "").strip() or "—"
        insp_rows += (
            "<tr>"
            f"<td>{int(row.get('Question_no') or 0)}</td>"
            f"<td>{esc(str(row.get('Question_text') or ''))}</td>"
            f"<td>{esc(answer)}</td>"
            f"<td>{'Yes' if row.get('Verified') else '—'}</td>"
            "</tr>"
        )

    st.markdown(
        f"""
        <div class="tc-summary-wrap">
            <h2 class="tc-summary-title">Test Certificate Summary — {esc(str(cert.get('Certificate_no') or ''))}</h2>
            <table class="tc-summary-table tc-summary-meta"><tbody>{meta_html}</tbody></table>
            <p class="tc-summary-h"><b>Printed lines</b> (batches under each line are what it is made of)</p>
            <table class="tc-summary-table tc-summary-lines">
                <thead><tr>
                    <th>Line</th><th>Printed heat no / source batches</th>
                    <th class="num">Source kg</th><th class="num">Printed kg</th>
                    <th class="num">Round-up kg</th><th class="num">Pieces</th>
                </tr></thead>
                <tbody>{line_rows}</tbody>
                <tfoot><tr>
                    <td colspan="2">Total ({len(lines)} printed line{'s' if len(lines) != 1 else ''})</td>
                    <td class="num">{packed_w:,.2f}</td>
                    <td class="num">{printed_w:,.2f}</td>
                    <td class="num">{printed_w - packed_w:+,.2f}</td>
                    <td class="num">{printed_p:,}</td>
                </tr></tfoot>
            </table>
            <p class="tc-summary-note">{weight_note}{pieces_note}</p>
            <p class="tc-summary-h"><b>Visual inspection</b></p>
            <table class="tc-summary-table tc-summary-lines">
                <thead><tr><th>#</th><th>Check</th><th>Answer</th><th>Verified</th></tr></thead>
                <tbody>{insp_rows}</tbody>
            </table>
            <p class="tc-summary-note">
                Source kg is the packed weight from the packing list; printed kg is what the
                certificate shows. Check the physical bundles against the source batches.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, _c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("Back to certificate", key="tc_summary_back"):
            st.session_state.pop("tc_show_summary", None)
            st.rerun()
    with c2:
        st.markdown(
            '<button class="no-print" onclick="window.print()" '
            'style="padding:0.45rem 1rem;border:1px solid #ccc;border-radius:0.5rem;'
            'background:#fff;cursor:pointer;font-size:0.9rem;">Print summary</button>',
            unsafe_allow_html=True,
        )


def _style_spec_check_table(data: pd.DataFrame):
    """Highlight rows whose Status starts with Out of spec."""

    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Status") or "")
        if status.startswith("Out of spec"):
            return ["background-color: #fdecea; color: #c62828; font-weight: 700"] * len(
                row
            )
        return [""] * len(row)

    styler = _pandas_styler(data)
    if styler is None:
        return data
    return styler.apply(_row_style, axis=1)


def _render_tc_spec_and_deviation(packing_list_id: int, *, locked: bool) -> tuple[bool, bool]:
    """Show batch chemistry vs alloy spec and the deviation-letter upload."""
    st.markdown("#### Alloy specification check")
    st.caption(
        "Each packed batch is compared to the packing-list alloy master spec. "
        "An element is out of specification when its percentage is at or below min, "
        "or at or above max."
    )
    comparison = db.list_packing_list_chemistry_vs_spec(packing_list_id)
    deviations = [row for row in comparison if row.get("Out_of_spec")]
    has_letter = db.has_packing_list_deviation_letter(packing_list_id)
    if not comparison:
        st.info(
            "No saved batch chemistry to compare against the alloy specification yet."
        )
    else:
        table_rows = [
            {
                "Batch_ID": row.get("Batch_ID"),
                "Heat_no": row.get("Heat_no"),
                "Element": row.get("Element_symbol"),
                "Actual %": db.format_chem_percent(
                    row.get("Percentage"), row.get("Less_than")
                ),
                "Spec": row.get("Spec"),
                "Status": (
                    f"Out of spec — {row.get('Reason')}"
                    if row.get("Out_of_spec")
                    else "Within spec"
                ),
            }
            for row in comparison
        ]
        table = df_from_rows(table_rows)
        st.dataframe(
            _style_spec_check_table(table),
            use_container_width=True,
            hide_index=True,
            height=min(560, 40 + 36 * max(len(table_rows), 3)),
        )
        if deviations:
            st.error(
                f"{len(deviations)} element(s) are outside specification. "
                "Upload the customer acceptance of deviation letter before "
                "creating or issuing the test certificate."
            )
        else:
            st.success(
                "All packed batch elements with recorded chemistry are within specification."
            )

    if deviations or has_letter:
        st.markdown("#### Customer acceptance of deviation")
        if deviations and not has_letter:
            st.warning(
                "A customer acceptance of deviation letter is required because "
                "one or more packed batches are outside the alloy specification."
            )
        u2 = None
        if not locked:
            uploaded = st.file_uploader(
                "Upload deviation letter",
                type=["pdf", "doc", "docx", "jpg", "jpeg", "png"],
                key=f"tc_dev_letter_{packing_list_id}",
                help="PDF, Word, or a scan (JPEG/PNG).",
            )
            u1, u2, _ = st.columns([1.2, 1.2, 3])
            if u1.button(
                "Save letter",
                type="primary",
                disabled=uploaded is None,
                key="tc_dev_save",
            ):
                try:
                    db.save_packing_list_deviation_letter(
                        packing_list_id,
                        uploaded.getvalue(),
                        uploaded.name,
                        content_type=getattr(uploaded, "type", None),
                    )
                    st.success(f"Saved **{uploaded.name}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        letter = db.get_packing_list_deviation_letter(packing_list_id)
        blob = _as_photo_bytes((letter or {}).get("Deviation_letter"))
        if letter and blob:
            st.caption(
                f"Attached: **{letter.get('Deviation_letter_name') or 'deviation letter'}**"
            )
            st.download_button(
                "Download letter",
                data=blob,
                file_name=letter.get("Deviation_letter_name")
                or f"deviation-letter-{packing_list_id}.bin",
                mime=letter.get("Deviation_letter_type") or "application/octet-stream",
                key="tc_dev_download",
            )
            if not locked and u2 is not None and u2.button("Remove letter", key="tc_dev_remove"):
                try:
                    db.clear_packing_list_deviation_letter(packing_list_id)
                    st.success("Deviation letter removed.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        elif not locked:
            st.caption("No deviation letter attached yet.")

    return bool(deviations), has_letter


st.title("Test Certificate")
st.caption(
    "Approving a packing list opens its test certificate as a **Draft**. Inventory, "
    "Management and Admin users merge heats onto one printed line and round **up** "
    f"total kg by at most **{db.CERT_WEIGHT_ROUND_MAX_PCT:g}%** (pieces must stay "
    "exact), then **Submit for verification**. Production, Management and Admin "
    "users complete the **Visual inspection** and **Verify** it, **Return it to "
    "draft** for corrections, or **Reject** it (the packed quantity goes back to "
    "finished goods and the packing list is cancelled). A Verified certificate is "
    "**Issued** as the final dispatch step; only an Admin can cancel it after that."
)

_employee = st.session_state.get("auth_employee") or {}
can_pack = db.role_allowed(
    _employee.get("role_name"), _employee.get("role_id"), db.PACKING_ROLES
)
can_verify = db.role_allowed(
    _employee.get("role_name"), _employee.get("role_id"), db.CERT_VERIFIER_ROLES
)

try:
    cert_lists = db.list_packing_lists_for_certificate()
except Exception as exc:
    _show_db_connection_error(exc)
    st.stop()

if not cert_lists:
    st.info("Approve a packing list first; its test certificate then opens here.")
    st.stop()

list_opts = {
    (
        f"#{r['Packing_list_id']}  |  {r.get('Invoice_number') or '—'}  |  "
        f"{r.get('Customer_name') or '—'}  |  "
        f"{r.get('Certificate_status') or 'no certificate'}"
    ): int(r["Packing_list_id"])
    for r in cert_lists
}
preset_id = st.session_state.get("tc_packing_list_id")
labels = list(list_opts.keys())
default_ix = 0
if preset_id:
    for i, lab in enumerate(labels):
        if list_opts[lab] == int(preset_id):
            default_ix = i
            break
pick = st.selectbox(
    "Packing list",
    options=labels,
    index=default_ix,
    key="tc_list_pick",
)
packing_list_id = list_opts[pick]
if st.session_state.get("tc_packing_list_id") != packing_list_id:
    st.session_state["tc_packing_list_id"] = packing_list_id
    st.session_state.pop("tc_lines", None)
    st.session_state.pop("tc_loaded_id", None)
    st.session_state.pop("tc_show_print", None)
    st.session_state.pop("tc_show_summary", None)

header = db.get_packing_list(packing_list_id)
if not header:
    st.error("That packing list was not found.")
    st.stop()

cert = db.get_packing_list_certificate(packing_list_id)
if not cert:
    st.error("This packing list has no test certificate.")
    st.stop()
if st.session_state.get("tc_show_print"):
    if (
        st.session_state.get("tc_loaded_id") != packing_list_id
        or "tc_lines" not in st.session_state
    ):
        st.session_state["tc_lines"] = [dict(row) for row in (cert.get("lines") or [])]
        st.session_state["tc_loaded_id"] = packing_list_id
    payload = _render_certificate_print(
        header,
        cert,
        st.session_state.get("tc_lines") or cert.get("lines") or [],
    )
    cert_no = str(payload.get("certificate_no") or "test-certificate").strip()
    pdf_name = re.sub(r"[^\w.-]+", "_", cert_no) + ".pdf"
    try:
        pdf_bytes = _certificate_pdf_bytes(payload)
    except Exception as exc:
        pdf_bytes = None
        st.error(f"PDF could not be created: {exc}")
    b1, b2, b3, _b4 = st.columns([1.1, 0.9, 1.3, 2])
    with b1:
        if st.button("Back to editor", key="tc_print_back"):
            st.session_state.pop("tc_show_print", None)
            st.rerun()
    with b2:
        st.markdown(
            '<button class="no-print" onclick="window.print()" '
            'style="padding:0.45rem 1rem;border:1px solid #ccc;border-radius:0.5rem;'
            'background:#fff;cursor:pointer;font-size:0.9rem;">Print</button>',
            unsafe_allow_html=True,
        )
    with b3:
        if pdf_bytes:
            st.download_button(
                "Download PDF",
                data=pdf_bytes,
                file_name=pdf_name,
                mime="application/pdf",
                key="tc_download_pdf",
            )
    st.stop()

cert_status = cert.get("Status") or ""
is_draft = cert_status == db.CERT_STATUS_DRAFT
is_pending = cert_status == db.CERT_STATUS_PENDING
draft_editable = is_draft and can_pack
insp_editable = is_pending and can_verify
dev_editable = (is_draft or is_pending) and (can_pack or can_verify)

packed_batches = list(header.get("batches") or [])
packed_w = sum(float(r.get("Weight") or 0) for r in packed_batches)
packed_p = sum(int(float(r.get("Pieces") or 0)) for r in packed_batches)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Packed kg", f"{packed_w:,.2f}")
m2.metric("Packed pieces", f"{packed_p:,}")
m3.metric("Invoice", header.get("Invoice_number") or "—")
m4.metric("Alloy", header.get("Alloy_name") or "—")
st.caption(
    f"Customer: {header.get('Customer_name') or '—'}  ·  "
    f"PO: {header.get('Customer_PO_No') or '—'}  ·  "
    f"Vehicle: {header.get('Vehicle_no') or '—'}  ·  "
    f"Packing list: {header.get('Packing_list_status') or '—'}"
)

if is_draft:
    st.info(
        "**Draft**: merge, split and round the printed lines, then **Submit for "
        "verification**."
        + ("" if can_pack else " Only Inventory, Management and Admin users can edit it.")
    )
elif is_pending:
    st.info(
        "**Pending verification**: complete the visual inspection and check the printed "
        "lines, then **Verify**, **Return to draft** or **Reject**."
        + ("" if can_verify else " Only Production, Management and Admin users can verify it.")
    )
elif cert_status == db.CERT_STATUS_VERIFIED:
    st.success(
        "**Verified**: the test certificate can be generated. **Issue certificate** is "
        "the final dispatch step."
        + ("" if can_pack else " Only Inventory, Management and Admin users can issue it.")
    )
elif cert_status == db.CERT_STATUS_ISSUED:
    st.info(
        "This certificate is **Issued**. Dispatch is final. An Admin can cancel it "
        "from **Admin → Cancel issued certificate**."
    )
elif cert_status == db.CERT_STATUS_REJECTED:
    st.warning(
        "This certificate was **Rejected**. Its packed quantity is back in finished "
        "goods and the packing list is cancelled."
    )
elif cert_status == db.CERT_STATUS_VOID:
    st.warning(
        "This certificate was cancelled by an Admin (**Void**). Its packed quantity is "
        "back in finished goods and the packing list is cancelled."
    )

if (
    st.session_state.get("tc_loaded_id") != packing_list_id
    or "tc_lines" not in st.session_state
    or not draft_editable
):
    st.session_state["tc_lines"] = [dict(row) for row in (cert.get("lines") or [])]
    st.session_state["tc_loaded_id"] = packing_list_id

if st.session_state.get("tc_show_summary"):
    # Shows the lines as they stand on screen, so unsaved draft merges show too.
    summary_cert = dict(cert)
    if draft_editable and st.session_state.get("tc_cert_no_for") == packing_list_id:
        summary_cert["Certificate_no"] = (
            st.session_state.get("tc_cert_no") or cert.get("Certificate_no")
        )
    _render_certificate_summary(
        header,
        summary_cert,
        list(st.session_state.get("tc_lines") or []),
        db.get_visual_inspection(packing_list_id),
    )
    st.stop()

if "tc_cert_no" not in st.session_state or st.session_state.get("tc_cert_no_for") != packing_list_id:
    st.session_state["tc_cert_no"] = cert.get("Certificate_no") or f"TC-{packing_list_id:04d}"
    st.session_state["tc_cert_no_for"] = packing_list_id
    issued_raw = cert.get("Issued_date")
    st.session_state["tc_issued_date"] = (
        _parse_master_date(issued_raw) if issued_raw else db.today_ist()
    )

h1, h2, h3 = st.columns(3)
with h1:
    certificate_no = st.text_input(
        "Certificate no",
        key="tc_cert_no",
        disabled=not draft_editable,
    )
with h2:
    issued_date = ui_date_input(
        "Issued date",
        key="tc_issued_date",
        disabled=not (draft_editable or (cert_status == db.CERT_STATUS_VERIFIED and can_pack)),
    )
with h3:
    st.text_input(
        "Status",
        value=cert_status,
        disabled=True,
        key=f"tc_status_display_{packing_list_id}_{cert_status}",
    )

lines = list(st.session_state.get("tc_lines") or [])
editor_rows = []
for line in lines:
    sources = line.get("sources") or []
    source_kg = sum(float(src.get("Source_weight") or 0) for src in sources)
    editor_rows.append(
        {
            "Select": bool(line.get("_selected")),
            "Line_no": int(line.get("Line_no") or 0),
            "Heat no": db.certificate_display_heat_no(line.get("Display_heat_no")),
            "Batch IDs": ", ".join(
                str(src.get("Batch_ID") or "")
                for src in sources
                if src.get("Batch_ID")
            ),
            "Source kg": source_kg,
            "Printed kg": float(line.get("Weight") or 0),
            "Pieces": int(float(line.get("Pieces") or 0)),
            "Blended": "Yes" if line.get("Is_blended") else "No",
        }
    )
editor_df = pd.DataFrame(editor_rows)
editor_key = (
    f"tc_editor_{packing_list_id}_{cert_status}_"
    f"{int(st.session_state.get('tc_editor_n') or 0)}_h2"
)
edited = st.data_editor(
    editor_df,
    column_config={
        "Select": st.column_config.CheckboxColumn(
            "Select",
            help="Tick lines to merge, or one merged line to split.",
            disabled=not draft_editable,
        ),
        "Line_no": st.column_config.NumberColumn("Line", format="%d"),
        "Heat no": st.column_config.TextColumn(
            "Heat no",
            help="Printed heat number on the test certificate.",
            disabled=not draft_editable,
        ),
        "Source kg": st.column_config.NumberColumn(format="%.2f"),
        "Printed kg": st.column_config.NumberColumn(
            format="%.2f",
            min_value=0.0,
            help="May be rounded up. Total round-up capped at 0.15%.",
            disabled=not draft_editable,
        ),
        "Pieces": st.column_config.NumberColumn(format="%d"),
    },
    disabled=["Line_no", "Batch IDs", "Source kg", "Pieces", "Blended"],
    hide_index=True,
    use_container_width=True,
    key=editor_key,
)
lines = _tc_sync_editor(lines, edited)
st.session_state["tc_lines"] = lines

printed_w = sum(float(row.get("Weight") or 0) for row in lines)
printed_p = sum(int(float(row.get("Pieces") or 0)) for row in lines)
summary = db.certificate_weight_summary(packed_w, printed_w)
s1, s2, s3, s4 = st.columns(4)
s1.metric("Printed kg", f"{printed_w:,.2f}")
s2.metric("Printed pieces", f"{printed_p:,}")
s3.metric(
    "Weight delta",
    f"{summary['delta_kg']:+.2f} kg",
    delta=f"{summary['delta_pct']:+.3f}%",
    delta_color="off",
)
s4.metric("Max extra kg", f"{summary['allowed_kg']:.2f}")
if printed_p != packed_p:
    st.error(f"Pieces must match packed ({packed_p}). Printed is {printed_p}.")
elif not summary["ok"]:
    st.error(
        "Weight is outside the allowed round-up. Printed kg must be at least "
        f"packed ({packed_w:.2f}) and at most "
        f"{packed_w + summary['allowed_kg']:.2f} "
        f"(+{db.CERT_WEIGHT_ROUND_MAX_PCT:g}%)."
    )
else:
    st.success(
        f"Within cap: {summary['delta_kg']:.2f} kg extra "
        f"({summary['delta_pct']:.3f}% of packed)."
    )

errors = db.validate_certificate_lines(packed_batches, lines)
if errors:
    for msg in errors:
        st.warning(msg)

selected_nos = [
    int(row["Line_no"]) for row in lines if row.get("_selected")
]
if draft_editable:
    st.markdown("#### Merge or split printed lines")
    allow_blend = st.checkbox(
        "Allow blended chemistry (different heat numbers on one printed line)",
        key="tc_allow_blend",
        help="Required when merging batches that do not share the same Heat_no.",
    )
    merge_heat = st.text_input(
        "Merged heat no (optional)",
        key="tc_merge_heat",
        help="Leave blank to keep the shared heat, or BLEND when heats differ.",
    )
    c_merge, c_split, c_reset = st.columns(3)
    with c_merge:
        if st.button("Merge selected", key="tc_merge"):
            try:
                st.session_state["tc_lines"] = db.merge_certificate_lines(
                    lines,
                    selected_nos,
                    display_heat_no=merge_heat,
                    allow_blend=allow_blend,
                )
                st.session_state["tc_editor_n"] = (
                    int(st.session_state.get("tc_editor_n") or 0) + 1
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with c_split:
        if st.button("Split selected", key="tc_split"):
            if len(selected_nos) != 1:
                st.error("Select exactly one merged line to split.")
            else:
                try:
                    st.session_state["tc_lines"] = db.split_certificate_line(
                        lines, selected_nos[0]
                    )
                    st.session_state["tc_editor_n"] = (
                        int(st.session_state.get("tc_editor_n") or 0) + 1
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
    with c_reset:
        if st.button("Reset to packed batches", key="tc_reset"):
            try:
                rebuilt = db.create_packing_list_certificate_draft(
                    packing_list_id,
                    certificate_no=certificate_no,
                )
                st.session_state["tc_lines"] = rebuilt.get("lines") or []
                st.session_state["tc_editor_n"] = (
                    int(st.session_state.get("tc_editor_n") or 0) + 1
                )
                st.success("Draft reset to one printed line per packed batch.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

if selected_nos:
    st.markdown("#### Chemistry for selected line")
    focus = next(
        (row for row in lines if int(row["Line_no"]) == selected_nos[0]),
        None,
    )
    if focus:
        chem = db.blended_certificate_chemistry(focus.get("sources") or [])
        if chem:
            label = (
                "Weighted average (blended heats)"
                if focus.get("Is_blended")
                else "Heat chemistry"
            )
            st.caption(label)
            try:
                alloy_id = int(header.get("Alloy_id") or 0)
            except (TypeError, ValueError):
                alloy_id = 0
            specs = db.get_alloy_specs(alloy_id) if alloy_id else {}
            chem_rows = []
            for row in chem:
                symbol = str(row.get("Element_symbol") or "").strip()
                spec = specs.get(symbol)
                pct = row.get("Percentage")
                oos = bool(
                    spec
                    and db.element_percent_out_of_spec(
                        pct,
                        spec.get("Min_percent"),
                        spec.get("Max_percent"),
                    )
                )
                chem_rows.append(
                    {
                        "Element": symbol,
                        "Actual %": db.format_chem_percent(
                            pct, row.get("Less_than")
                        ),
                        "Spec": (
                            db.format_alloy_spec_percent(
                                spec.get("Min_percent"),
                                spec.get("Max_percent"),
                            )
                            if spec
                            else "—"
                        ),
                        "Status": (
                            "Out of spec"
                            if oos
                            else ("Within spec" if spec else "No spec")
                        ),
                    }
                )
            st.dataframe(
                _style_spec_check_table(df_from_rows(chem_rows)),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No chemistry saved on the source batches yet.")

try:
    inspection = db.get_visual_inspection(packing_list_id)
except Exception as exc:
    st.error(str(exc))
    inspection = [
        {
            "Question_no": index,
            "Question_text": text,
            "Answer": "",
            "Verified": 0,
        }
        for index, text in enumerate(db.VISUAL_INSPECTION_QUESTIONS, start=1)
    ]

st.markdown("#### Visual inspection")
st.caption(
    "Done by Production, Management or Admin users while the certificate is "
    "**Pending verification**. Answer every check **OK** or **NOT OK** and tick "
    "**Verified**; all items must be OK and Verified before the certificate can be "
    "verified."
)
answer_choices = ["", *db.SAMPLE_OK_STATUS]
inspection_rows: list[dict] = []
for row in inspection:
    qno = int(row.get("Question_no") or 0)
    text = str(row.get("Question_text") or "")
    saved_answer = str(row.get("Answer") or "").strip()
    q_col, a_col, v_col = st.columns([5.0, 2.4, 1.8])
    q_col.markdown(f"**{qno}.** {text}")
    answer = a_col.selectbox(
        f"Answer {qno}",
        options=answer_choices,
        index=(
            answer_choices.index(saved_answer)
            if saved_answer in answer_choices
            else 0
        ),
        format_func=lambda value: "Answer" if value == "" else value,
        key=f"tc_insp_ans_{packing_list_id}_{qno}",
        disabled=not insp_editable,
        label_visibility="collapsed",
    )
    verified = v_col.checkbox(
        "Verified",
        value=bool(row.get("Verified")),
        key=f"tc_insp_ver_{packing_list_id}_{qno}",
        disabled=not insp_editable,
    )
    inspection_rows.append(
        {
            "Question_no": qno,
            "Question_text": text,
            "Answer": str(answer or "").strip(),
            "Verified": 1 if verified else 0,
        }
    )
insp_errors = db.visual_inspection_errors(inspection_rows)
insp_ready = not insp_errors
if is_pending:
    if insp_errors:
        for msg in insp_errors:
            st.warning(msg)
    else:
        st.success("Visual inspection is complete.")
if insp_editable:
    if st.button("Save inspection", key="tc_insp_save"):
        try:
            db.save_visual_inspection(packing_list_id, inspection_rows)
            st.success("Visual inspection saved.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

has_deviations, has_letter = _render_tc_spec_and_deviation(
    packing_list_id,
    locked=not dev_editable,
)
spec_blocked = has_deviations and not has_letter

st.divider()
p1, p2, _p3 = st.columns([1, 1, 2])
if p1.button(
    "View summary",
    key="tc_view_summary",
    help="Printable overview of the printed lines, their source batches and the "
    "visual inspection, for the physical check.",
):
    st.session_state["tc_show_summary"] = True
    st.rerun()
if p2.button("View / print", key="tc_view_print"):
    st.session_state["tc_show_print"] = True
    st.rerun()

if is_draft:
    a1, a2, _a3 = st.columns(3)
    save_clicked = a1.button(
        "Save draft", key="tc_save", disabled=not draft_editable, type="primary"
    )
    submit_clicked = a2.button(
        "Submit for verification", key="tc_submit", disabled=not draft_editable
    )
    if save_clicked:
        try:
            saved = db.save_packing_list_certificate_draft(
                packing_list_id,
                lines,
                certificate_no=certificate_no,
                issued_date=to_storage_date(issued_date),
            )
            st.session_state["tc_lines"] = saved.get("lines") or []
            st.success(f"Saved draft **{saved.get('Certificate_no')}**.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if submit_clicked:
        try:
            submitted = db.submit_certificate_for_verification(
                packing_list_id,
                lines,
                certificate_no=certificate_no,
                issued_date=to_storage_date(issued_date),
            )
            st.success(
                f"**{submitted.get('Certificate_no')}** submitted for verification."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
elif is_pending:
    if spec_blocked:
        st.info(
            "Upload the customer acceptance of deviation letter before verifying "
            "the test certificate."
        )
    v1, v2, v3 = st.columns(3)
    verify_clicked = v1.button(
        "Verify",
        key="tc_verify",
        type="primary",
        disabled=not can_verify or not insp_ready or spec_blocked or bool(errors),
    )
    return_clicked = v2.button(
        "Return to draft",
        key="tc_return",
        disabled=not can_verify,
        help="Sends it back to the packing team for corrections; stock stays held.",
    )
    confirm_reject = v3.checkbox(
        "Confirm reject",
        key=f"tc_reject_confirm_{packing_list_id}",
        disabled=not can_verify,
        help="Rejecting returns the packed quantity to finished goods and cancels the packing list.",
    )
    reject_clicked = v3.button(
        "Reject",
        key="tc_reject",
        disabled=not can_verify or not confirm_reject,
    )
    if verify_clicked:
        try:
            db.save_visual_inspection(packing_list_id, inspection_rows)
            done = db.verify_packing_list_certificate(packing_list_id)
            st.success(f"**{done.get('Certificate_no')}** verified.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if return_clicked:
        try:
            db.return_certificate_to_draft(packing_list_id)
            st.success("Returned to draft for corrections.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if reject_clicked:
        try:
            db.reject_packing_list_certificate(packing_list_id)
            st.success(
                "Certificate rejected. The packed quantity is back in finished goods "
                "and the packing list is cancelled."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
elif cert_status == db.CERT_STATUS_VERIFIED:
    if st.button(
        "Issue certificate",
        key="tc_issue",
        type="primary",
        disabled=not can_pack,
    ):
        try:
            issued = db.issue_packing_list_certificate(
                packing_list_id,
                issued_date=to_storage_date(issued_date),
            )
            st.success(f"Issued **{issued.get('Certificate_no')}**. Dispatch is final.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.markdown("#### Packed baseline (not editable)")
show_dataframe(
    df_from_rows(
        [
            {
                "Batch_ID": r.get("Batch_ID"),
                "Heat_no": r.get("Heat_no"),
                "Weight": r.get("Weight"),
                "Pieces": r.get("Pieces"),
            }
            for r in packed_batches
        ]
    )
)
