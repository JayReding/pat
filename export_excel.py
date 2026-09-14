from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from export_pdf import HISTORICAL_COLUMNS, _MONEY_COLUMNS

_HEADER_FILL = PatternFill("solid", fgColor="E9ECEF")
_HEADER_FONT = Font(bold=True)
_THIN = Side(style="thin", color="ADB5BD")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CURRENCY_FORMAT = "$#,##0.00;[Red]($#,##0.00)"


def _display_value(value, money=False):
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    if money:
        try:
            return float(value)
        except (TypeError, ValueError):
            return ""
    return value


def build_invoices_workbook(df, sheet_title="Historical Invoices"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title

    ws.append(list(HISTORICAL_COLUMNS))
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.border = _BORDER

    for _, record in df.iterrows():
        ws.append([
            _display_value(record[c], money=(c in _MONEY_COLUMNS)) for c in HISTORICAL_COLUMNS
        ])

    currency_cols = {
        HISTORICAL_COLUMNS.index(c) + 1 for c in HISTORICAL_COLUMNS if c in _MONEY_COLUMNS
    }

    for row in ws.iter_rows(min_row=2, max_col=len(HISTORICAL_COLUMNS)):
        for cell in row:
            cell.border = _BORDER
            if cell.column in currency_cols and isinstance(cell.value, (int, float)):
                cell.number_format = _CURRENCY_FORMAT

    for i, column in enumerate(HISTORICAL_COLUMNS, start=1):
        letter = get_column_letter(i)
        width = max(
            len(str(column)),
            *(len(str(ws.cell(row=r, column=i).value) or "") for r in range(2, min(ws.max_row, 50) + 1)),
        ) + 2
        ws.column_dimensions[letter].width = min(width, 24)

    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()