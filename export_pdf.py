from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HISTORICAL_COLUMNS = [
    "Transfer Number",
    "Transfer Amount",
    "Payment Date",
    "Invoice Number",
    "Invoice Amount",
    "Invoice Date",
    "Invoice Due",
    "Invoice to Payment",
    "Days Past Due",
]

OCB_FIELDS = [
    "date_range",
    "pref_pct",
    "hist_pct",
    "pct_diff",
    "pref_count",
    "hist_count",
    "pref_amount",
    "hist_amount",
]

OCB_HEADERS = {
    "date_range": "Date Range",
    "pref_pct": "Preference % of Invoices",
    "hist_pct": "Historical % of Invoices",
    "pct_diff": "Percentage Difference",
    "pref_count": "Preference Invoice Count",
    "hist_count": "Historical Invoice Count",
    "pref_amount": "Preference Invoice Amount",
    "hist_amount": "Historical Invoice Amount",
}

_OCB_MONEY = {"pref_amount", "hist_amount"}

_OCB_WEIGHTS = {
    "date_range": 1.1,
    "pref_pct": 1.3,
    "hist_pct": 1.3,
    "pct_diff": 1.2,
    "pref_count": 1.2,
    "hist_count": 1.2,
    "pref_amount": 1.4,
    "hist_amount": 1.4,
}

_MONEY_COLUMNS = {"Transfer Amount", "Invoice Amount"}

_COLUMN_WEIGHTS = {
    "Transfer Number": 1.0,
    "Transfer Amount": 1.5,
    "Payment Date": 1.1,
    "Invoice Number": 1.0,
    "Invoice Amount": 1.4,
    "Invoice Date": 1.1,
    "Invoice Due": 1.1,
    "Invoice to Payment": 1.2,
    "Days Past Due": 0.9,
}


def _fmt_money(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return f"(${abs(value):,.2f})"
    return f"${value:,.2f}"


def _cell(value, money=False):
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return _fmt_money(value) if money else str(value)


def _display_number(info):
    if info.get("filing_date"):
        return info.get("adversary_number") or ""
    return info.get("file_number") or ""


def _draw_footer(left, center):
    page_w, page_h = landscape(letter)

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setStrokeColor(colors.HexColor("#adb5bd"))
        canvas.setLineWidth(0.5)
        canvas.line(0.5 * inch, 0.62 * inch, page_w - 0.5 * inch, 0.62 * inch)
        canvas.drawString(0.5 * inch, 0.4 * inch, left)
        canvas.drawCentredString(page_w / 2.0, 0.4 * inch, center)
        canvas.drawRightString(page_w - 0.5 * inch, 0.4 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return draw


def _fmt_pct(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    if value != value:
        return ""
    return f"{value:.2f}%"


_RANGE_FILL = colors.HexColor("#d1e7dd")

SUMMARY_LABELS = {
    "range_label": "Selected Ordinary Course Range",
    "invoice_count": "Ordinary Invoices Found",
    "invoice_amount": "Ordinary Invoice Amount",
}


def _ocb_cell(value, field):
    if value is None:
        return ""
    if field == "date_range":
        return str(value)
    if field in _OCB_MONEY:
        return _fmt_money(value)
    return _fmt_pct(value)


def _build_summary_table(summary):
    rows = []
    for key, label in SUMMARY_LABELS.items():
        value = summary.get(key) if summary else None
        if value is None or (isinstance(value, float) and value != value):
            value = "—"
        if key == "invoice_count":
            value = f"{int(value):,}" if value != "—" else "—"
        elif key == "invoice_amount":
            value = _fmt_money(value) if value != "—" else "—"
        rows.append([Paragraph(label, _ocb_label_style()), Paragraph(str(value), _ocb_value_style())])

    table = Table(rows, colWidths=[3.5 * inch, 6.5 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#adb5bd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _ocb_label_style():
    return ParagraphStyle(
        "ocb-sum-label", fontName="Helvetica-Bold", fontSize=9, leading=11)


def _ocb_value_style():
    return ParagraphStyle(
        "ocb-sum-value", fontName="Helvetica", fontSize=9, leading=11, alignment=2)


def build_ocb_pdf(info, df, caption="Ordinary Course of Business (Subjective)",
                  selected_rows=None, summary=None):
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()

    def render(value, field):
        if value is None:
            return ""
        if field == "date_range":
            return str(value)
        if field in _OCB_MONEY:
            return _fmt_money(value)
        return _fmt_pct(value)

    rows = [[OCB_HEADERS[f] for f in OCB_FIELDS]]
    for _, record in df.iterrows():
        rows.append([render(record[f], f) for f in OCB_FIELDS])

    avail = landscape(letter)[0] - 2 * 0.5 * inch
    total_w = sum(_OCB_WEIGHTS[f] for f in OCB_FIELDS)
    col_widths = [avail * _OCB_WEIGHTS[f] / total_w for f in OCB_FIELDS]

    header_style = ParagraphStyle(
        "ocb-header", fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=1)
    body_style = ParagraphStyle(
        "ocb-body", fontName="Helvetica", fontSize=8.5, leading=10, alignment=1)

    def para(text, style):
        return Paragraph(str(text), style)

    table_data = []
    for i, row in enumerate(rows):
        style = header_style if i == 0 else body_style
        table_data.append([para(value, style) for value in row])

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#adb5bd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if selected_rows:
        a, b = sorted((int(selected_rows[0]), int(selected_rows[1])))
        for row in range(a + 1, min(b + 1, len(table_data)) + 1):
            table_style.append(("BACKGROUND", (0, row), (-1, row), _RANGE_FILL))
    table.setStyle(TableStyle(table_style))

    title_style = ParagraphStyle(
        "ocb-transfer", fontName="Helvetica-Bold", fontSize=16, leading=19)
    number_style = ParagraphStyle(
        "ocb-case-number", fontName="Helvetica", fontSize=13, leading=16)
    doc_style = ParagraphStyle(
        "ocb-doc-title", fontName="Helvetica-Bold", fontSize=13, leading=16)
    summary_heading_style = ParagraphStyle(
        "ocb-summary-heading", fontName="Helvetica-Bold", fontSize=10, leading=12)

    story = [
        Paragraph(transferee, title_style),
        Spacer(1, 6),
        Paragraph(number, number_style),
        Spacer(1, 6),
        Paragraph(caption, doc_style),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=colors.black),
        Spacer(1, 10),
        Paragraph("Selection Summary", summary_heading_style),
        Spacer(1, 4),
        _build_summary_table(summary),
        Spacer(1, 12),
        table,
    ]

    footer = _draw_footer(transferee, f"{number}  |  {caption}")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=f"{caption} - {transferee}",
        author=transferee,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def build_invoices_pdf(info, df, caption="Historical Invoices"):
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()

    rows = [list(HISTORICAL_COLUMNS)]
    for _, record in df.iterrows():
        rows.append([
            _cell(record[c], money=(c in _MONEY_COLUMNS)) for c in HISTORICAL_COLUMNS
        ])

    avail = landscape(letter)[0] - 2 * 0.5 * inch
    total_w = sum(_COLUMN_WEIGHTS[c] for c in HISTORICAL_COLUMNS)
    col_widths = [avail * _COLUMN_WEIGHTS[c] / total_w for c in HISTORICAL_COLUMNS]

    header_style = ParagraphStyle(
        "hist-header", fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=1)
    body_style = ParagraphStyle(
        "hist-body", fontName="Helvetica", fontSize=8.5, leading=10, alignment=1)

    def para(text, style):
        return Paragraph(str(text), style)

    table_data = []
    for row in rows:
        cells = []
        for i, value in enumerate(row):
            style = header_style if len(table_data) == 0 else body_style
            cells.append(para(value, style))
        table_data.append(cells)

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#adb5bd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    title_style = ParagraphStyle(
        "xfer-name", fontName="Helvetica-Bold", fontSize=16, leading=19)
    number_style = ParagraphStyle(
        "case-number", fontName="Helvetica", fontSize=13, leading=16)
    doc_style = ParagraphStyle(
        "doc-title", fontName="Helvetica-Bold", fontSize=13, leading=16)

    story = [
        Paragraph(transferee, title_style),
        Spacer(1, 6),
        Paragraph(number, number_style),
        Spacer(1, 6),
        Paragraph(caption, doc_style),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=colors.black),
        Spacer(1, 10),
        table,
    ]

    footer = _draw_footer(transferee, f"{number}  |  {caption}")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=f"{caption} - {transferee}",
        author=transferee,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()