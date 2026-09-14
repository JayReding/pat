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