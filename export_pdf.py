from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, Image,
                                NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HISTORICAL_COLUMNS = [
    "Transfer Number",
    "Transfer Amount",
    "Payment Date",
    "Invoice Number",
    "Invoice Amount",
    "Invoice Amount Paid",
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

_MONEY_COLUMNS = {"Transfer Amount", "Invoice Amount", "Invoice Amount Paid"}

TRANSFER_COLUMNS = [
    "Transfer Number",
    "Transfer Amount",
    "Payment Date",
    "Check Date",
]

# Note: Invoice Amount Paid is intentionally absent here — it is always
# NULL for new-value (unpaid) rows, so the column would carry no information.
NEW_VALUE_COLUMNS = [
    "Transaction Date",
    "Transfer Amount",
    "Payment Date",
    "Invoice Number",
    "Invoice Amount",
    "Invoice Date",
    "Allowed New Value",
    "Net Preference",
]

_NEW_VALUE_MONEY = {"Transfer Amount", "Invoice Amount",
                    "Allowed New Value", "Net Preference"}

_COLUMN_WEIGHTS = {
    "Transfer Number": 1.0,
    "Transfer Amount": 1.5,
    "Payment Date": 1.1,
    "Invoice Number": 1.0,
    "Invoice Amount": 1.4,
    "Invoice Amount Paid": 1.4,
    "Invoice Date": 1.1,
    "Invoice Due": 1.1,
    "Invoice to Payment": 1.2,
    "Days Past Due": 0.9,
    "Transaction Date": 1.4,
    "Check Date": 1.1,
    "Allowed New Value": 1.4,
    "Net Preference": 1.4,
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


def _labeled_caption(caption, exhibit_label):
    """Prefix a caption with its exhibit label when labels are enabled."""
    if exhibit_label:
        return f"Exhibit {exhibit_label} — {caption}"
    return caption


def _exhibit_footer_center(number, caption, exhibit_label):
    """Footer center text, tagged with the exhibit label when enabled."""
    if exhibit_label:
        return f"{number}  |  Exhibit {exhibit_label}  |  {caption}"
    return f"{number}  |  {caption}"


def _titled_doc(info, caption, story, footer_center, title=None):
    """Assemble a standalone landscape exhibit PDF (shared chrome)."""
    transferee = (info.get("transferee") or "").strip()
    footer = _draw_footer(transferee, footer_center)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=title or f"{caption} - {transferee}",
        author=transferee,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _title_block(transferee, number, caption, transferee_style="xfer-name",
                 number_style="case-number", doc_style="doc-title"):
    title_style = ParagraphStyle(
        transferee_style, fontName="Helvetica-Bold", fontSize=16, leading=19)
    num_style = ParagraphStyle(
        number_style, fontName="Helvetica", fontSize=13, leading=16)
    cap_style = ParagraphStyle(
        doc_style, fontName="Helvetica-Bold", fontSize=13, leading=16)
    return [
        Paragraph(transferee, title_style),
        Spacer(1, 6),
        Paragraph(number, num_style),
        Spacer(1, 6),
        Paragraph(caption, cap_style),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=colors.black),
        Spacer(1, 10),
    ]


def _ocb_table(df, selected_rows=None):
    """OCB grid table flowable (shared by standalone + combined builders)."""

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
    return table


def build_doc_from_story(info, caption, story, footer_center):
    """Public standalone-document wrapper (shared by ZIP + combined paths)."""
    return _titled_doc(info, caption, story, footer_center)


def build_ocb_pdf(info, df, caption="Ordinary Course of Business (Subjective)",
                  selected_rows=None, summary=None, exhibit_label=None):
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()
    caption = _labeled_caption(caption, exhibit_label)
    table = _ocb_table(df, selected_rows)
    story = _ocb_story(transferee, number, caption, table, summary)
    return _titled_doc(
        info, caption, story,
        _exhibit_footer_center(number, caption, exhibit_label))


def _ocb_story(transferee, number, caption, table, summary):
    """Title block + summary + table flowables for OCB exhibits."""
    summary_heading_style = ParagraphStyle(
        "ocb-summary-heading", fontName="Helvetica-Bold", fontSize=10, leading=12)
    story = _title_block(transferee, number, caption,
                         transferee_style="ocb-transfer",
                         number_style="ocb-case-number",
                         doc_style="ocb-doc-title")
    story += [
        Paragraph("Selection Summary", summary_heading_style),
        Spacer(1, 4),
        _build_summary_table(summary),
        Spacer(1, 12),
        table,
    ]
    return story


def _build_table_flowable(df, columns, money, weights, header_style_name="hist-header",
                          body_style_name="hist-body"):
    """Generic landscape table flowable shared by the exhibit builders."""
    rows = [list(columns)]
    for _, record in df.iterrows():
        rows.append([
            _cell(record.get(c), money=(c in money)) for c in columns
        ])

    avail = landscape(letter)[0] - 2 * 0.5 * inch
    total_w = sum(weights[c] for c in columns)
    col_widths = [avail * weights[c] / total_w for c in columns]

    header_style = ParagraphStyle(
        header_style_name, fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=1)
    body_style = ParagraphStyle(
        body_style_name, fontName="Helvetica", fontSize=8.5, leading=10, alignment=1)

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
    return table


def _invoices_story(info, df, caption, columns=None, money=None):
    """Title block + table flowables for invoice-style exhibits."""
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()
    columns = list(columns) if columns is not None else list(HISTORICAL_COLUMNS)
    money = set(money) if money is not None else _MONEY_COLUMNS
    table = _build_table_flowable(df, columns, money, _COLUMN_WEIGHTS)
    return _title_block(transferee, number, caption) + [table]


def build_invoices_pdf(info, df, caption="Historical Invoices", columns=None,
                       money=None, exhibit_label=None):
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()
    caption = _labeled_caption(caption, exhibit_label)
    story = _invoices_story(info, df, caption, columns=columns, money=money)
    return _titled_doc(
        info, caption, story,
        _exhibit_footer_center(number, caption, exhibit_label))


GRAPH_BIN_COLUMNS = ["Bin", "Historical %", "Preference %", "Historical #", "Preference #"]

_GRAPH_BIN_WEIGHTS = {
    "Bin": 1.2,
    "Historical %": 1.2,
    "Preference %": 1.2,
    "Historical #": 1.2,
    "Preference #": 1.2,
}


def _graph_story(info, png_bytes, bins_df, metric, caption):
    """Title block + chart + bin table flowables for Graph View exhibits."""
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()

    avail = landscape(letter)[0] - 2 * 0.5 * inch
    chart = Image(BytesIO(png_bytes))
    chart.drawWidth = avail
    chart.drawHeight = min(4.0 * inch, avail * chart.imageHeight / chart.imageWidth)

    table = _build_table_flowable(bins_df, GRAPH_BIN_COLUMNS, set(), _GRAPH_BIN_WEIGHTS)

    sub_style = ParagraphStyle(
        "graph-sub", fontName="Helvetica", fontSize=10, leading=12)

    story = _title_block(transferee, number, caption,
                         transferee_style="graph-name",
                         number_style="graph-case-number",
                         doc_style="graph-doc-title")
    story += [
        Paragraph(f"{metric} distribution — Historical vs Preference (percent of invoices)",
                  sub_style),
        Spacer(1, 6),
        chart,
        Spacer(1, 12),
        table,
    ]
    return story


def build_graph_pdf(info, png_bytes, bins_df, metric, caption="Graph View",
                    exhibit_label=None):
    """Exhibit pairing the overlaid distribution chart with its bin table."""
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()
    caption = _labeled_caption(caption, exhibit_label)
    story = _graph_story(info, png_bytes, bins_df, metric, caption)
    return _titled_doc(
        info, caption, story,
        _exhibit_footer_center(number, caption, exhibit_label))


def build_label_divider(label):
    """Full-page divider flowables announcing one exhibit ('EXHIBIT A')."""
    label_style = ParagraphStyle(
        "exhibit-divider", fontName="Helvetica-Bold", fontSize=72, leading=80,
        alignment=1, textColor=colors.HexColor("#212529"))
    return [
        Spacer(1, 2.5 * inch),
        Paragraph(f"EXHIBIT {label}", label_style),
    ]


def build_combined_pdf(info, sections, title="Exhibits"):
    """Single PDF concatenating exhibit sections with per-section footers.

    sections: list of dicts with keys ``label`` (or None), ``caption``,
    ``story`` (flowables), and ``footer_center``.  Labeled sections are
    preceded by a full-page divider; unlabeled sections concatenate bare.
    Page numbering runs continuously across the whole document.
    """
    transferee = (info.get("transferee") or "").strip()
    number = _display_number(info).strip()

    page_w, page_h = landscape(letter)
    frame = Frame(0.5 * inch, 0.75 * inch,
                  page_w - 1.0 * inch, page_h - 1.25 * inch,
                  id="exhibit-frame")

    templates = [PageTemplate(
        id="divider",
        frames=[frame],
        onPage=_draw_footer(transferee, f"{number}  |  {title}"))]
    for i, sec in enumerate(sections):
        templates.append(PageTemplate(
            id=f"sec-{i}",
            frames=[Frame(0.5 * inch, 0.75 * inch,
                          page_w - 1.0 * inch, page_h - 1.25 * inch,
                          id=f"exhibit-frame-{i}")],
            onPage=_draw_footer(transferee, sec["footer_center"])))

    story = []
    for i, sec in enumerate(sections):
        if sec.get("label"):
            story += [NextPageTemplate("divider"),
                      *build_label_divider(sec["label"]),
                      PageBreak(),
                      NextPageTemplate(f"sec-{i}")]
        else:
            story += [NextPageTemplate(f"sec-{i}")]
        story += sec["story"]
        story.append(PageBreak())
    if story and isinstance(story[-1], PageBreak):
        story.pop()

    buf = BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.75 * inch,
        title=f"{title} - {transferee}",
        author=transferee,
    )
    doc.addPageTemplates(templates)
    doc.build(story)
    return buf.getvalue()