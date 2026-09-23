"""Excel data-import parsing/validation for loading invoice rows into a subcase.

Reads workbooks formatted like ``subcase_import_template.xlsx`` (sheet
``Invoices``) and validates every row.  Any invalid row rejects the whole
file: callers should surface the returned error list and abort the import.

Dates are normalized to plain ``YYYY-MM-DD`` text because the app compares
them as strings when partitioning historical vs preference rows.
"""

from datetime import date, datetime
from io import BytesIO

INVOICES_SHEET = "Invoices"

TEMPLATE_COLUMNS = [
    "Transfer Number",
    "Transfer Amount",
    "Invoice Number",
    "Invoice Amount",
    "Invoice Amount Paid",
    "Check Amount",
    "Payment Date",
    "Invoice Date",
    "Invoice Due",
    "Terms Days",
    "Days Past Due",
    "Weighted Days Past Due",
    "Invoice to Payment",
    "Weighted Invoice to Payment",
    "Age",
    "Unpaid",
    "Check Date",
]

# Weighted timing fields the app calculates on import from the amount paid
# and anchor dates; any user-supplied values are ignored.
CALCULATED_COLUMNS = frozenset({
    "Weighted Days Past Due",
    "Weighted Invoice to Payment",
})

REQUIRED_COLUMNS = {
    "Transfer Number",
    "Transfer Amount",
    "Invoice Number",
    "Invoice Amount",
    "Payment Date",
    "Invoice Date",
}

MONEY_COLUMNS = {"Transfer Amount", "Invoice Amount", "Invoice Amount Paid", "Check Amount"}
FLOAT_COLUMNS = {"Weighted Days Past Due", "Weighted Invoice to Payment"}
INTEGER_COLUMNS = {"Terms Days", "Days Past Due", "Invoice to Payment"}
DATE_COLUMNS = {"Payment Date", "Invoice Date", "Invoice Due", "Check Date"}

IMPORT_MODES = ("append", "replace")


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value != value:  # NaN
        return ""
    return str(value).strip()


def _parse_date(value):
    """Normalize an Excel cell to YYYY-MM-DD text, or return None if blank.

    Raises ValueError if present but not a valid YYYY-MM-DD date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _cell_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"'{text}' is not a valid YYYY-MM-DD date.")


def _parse_money(value):
    """Coerce an Excel cell to float. Raises ValueError if not numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{value}' is not a number.")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    text = _cell_text(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"'{value}' is not a number.")


def _parse_int(value, column):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{value}' is not a whole number for {column}.")
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if not value.is_integer():
            raise ValueError(f"'{value}' is not a whole number for {column}.")
        return int(value)
    if isinstance(value, int):
        return value
    text = _cell_text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            f = float(text)
        except ValueError:
            raise ValueError(f"'{value}' is not a whole number for {column}.")
        if not f.is_integer():
            raise ValueError(f"'{value}' is not a whole number for {column}.")
        return int(f)


def parse_import_workbook(data):
    """Read the Invoices sheet; return (headers, raw_rows).

    headers: list of stripped header names from row 1.
    raw_rows: list of dicts mapping header -> raw cell value (data rows only,
    fully-blank rows skipped).

    Raises ValueError for unreadable files, missing sheet, or missing
    required columns.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError(f"Excel support is unavailable: {exc}")
    try:
        wb = load_workbook(BytesIO(bytes(data)), data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError(f"Could not read Excel file: {exc}")
    try:
        if INVOICES_SHEET not in wb.sheetnames:
            raise ValueError(
                f"Sheet '{INVOICES_SHEET}' not found "
                f"(found: {', '.join(wb.sheetnames) or 'none'}). "
                "Use the subcase import template."
            )
        ws = wb[INVOICES_SHEET]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if not rows or all(v is None or _cell_text(v) == "" for v in rows[0]):
        raise ValueError("The Invoices sheet has no header row.")
    headers = [_cell_text(v) for v in rows[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}."
        )
    raw_rows = []
    for row in rows[1:]:
        if all(v is None or _cell_text(v) == "" for v in row):
            continue
        raw_rows.append({
            headers[i]: (row[i] if i < len(row) else None)
            for i in range(len(headers))
        })
    if not raw_rows:
        raise ValueError("The Invoices sheet has no data rows.")
    return headers, raw_rows


def validate_import_rows(raw_rows):
    """Validate raw rows; return (records, errors).

    records: cleaned dicts keyed by TEMPLATE_COLUMNS (only when errors is
    empty).  errors: list of 'Row N: ...' strings (Excel row numbers, i.e.
    header is row 1).  Any error means the caller must reject the whole file.
    """
    records = []
    errors = []
    for idx, raw in enumerate(raw_rows):
        excel_row = idx + 2
        record = {}
        row_errors = []

        for col in TEMPLATE_COLUMNS:
            if col in CALCULATED_COLUMNS:
                # Calculated on import; any supplied value is ignored.
                record[col] = None
                continue
            raw_val = raw.get(col)

            if col in DATE_COLUMNS:
                try:
                    parsed = _parse_date(raw_val)
                except ValueError as exc:
                    row_errors.append(f"{col} {exc}")
                    continue
                if col in REQUIRED_COLUMNS and parsed is None:
                    row_errors.append(f"{col} is required.")
                record[col] = parsed
            elif col in MONEY_COLUMNS:
                try:
                    parsed = _parse_money(raw_val)
                except ValueError:
                    row_errors.append(
                        f"{col} must be a number (got '{_cell_text(raw_val)}').")
                    continue
                if col in REQUIRED_COLUMNS and parsed is None:
                    row_errors.append(f"{col} is required.")
                record[col] = parsed
            elif col in INTEGER_COLUMNS:
                try:
                    parsed = _parse_int(raw_val, col)
                except ValueError as exc:
                    row_errors.append(str(exc))
                    continue
                record[col] = parsed
            elif col in FLOAT_COLUMNS:
                try:
                    parsed = _parse_money(raw_val)
                except ValueError:
                    row_errors.append(
                        f"{col} must be a number (got '{_cell_text(raw_val)}').")
                    continue
                record[col] = parsed
            elif col == "Unpaid":
                text = _cell_text(raw_val)
                if text == "":
                    record[col] = 0
                elif text in ("0", "1"):
                    record[col] = int(text)
                elif isinstance(raw_val, bool):
                    record[col] = int(raw_val)
                elif isinstance(raw_val, (int, float)) and raw_val in (0, 1, 0.0, 1.0):
                    record[col] = int(raw_val)
                else:
                    row_errors.append(
                        "Unpaid must be blank/0 (paid) or 1 (new value).")
            else:  # text columns: Transfer Number, Invoice Number, Age
                text = _cell_text(raw_val)
                if col in REQUIRED_COLUMNS and not text:
                    row_errors.append(f"{col} is required.")
                record[col] = text or None

        if row_errors:
            errors.append(f"Row {excel_row}: " + "; ".join(row_errors))
        else:
            _calculate_weighted_fields(record)
            records.append(record)
    return records, errors


def calculate_weighted_fields(record):
    """Public wrapper for _calculate_weighted_fields (single formula shared
    with non-import producers such as test_data)."""
    _calculate_weighted_fields(record)


def _calculate_weighted_fields(record):
    """Calculate weighted timing fields from amount paid + dates (in place).

    Weighted Days Past Due = Invoice Amount Paid x (Payment Date - Invoice
    Due); Weighted Invoice to Payment = Invoice Amount Paid x (Payment Date
    - Invoice Date).  A blank Invoice Amount Paid defaults to the Invoice
    Amount.  Unpaid rows always keep Invoice Amount Paid NULL (along with
    NULL weighted fields); rows with missing dates keep NULLs.
    """
    if record.get("Unpaid") == 1:
        record["Invoice Amount Paid"] = None
        return
    if record.get("Invoice Amount Paid") is None:
        record["Invoice Amount Paid"] = record.get("Invoice Amount")
    paid = record.get("Invoice Amount Paid")
    try:
        inv = datetime.strptime(record["Invoice Date"], "%Y-%m-%d").date() \
            if record.get("Invoice Date") else None
        pay = datetime.strptime(record["Payment Date"], "%Y-%m-%d").date() \
            if record.get("Payment Date") else None
        due = datetime.strptime(record["Invoice Due"], "%Y-%m-%d").date() \
            if record.get("Invoice Due") else None
    except ValueError:
        return
    if paid is not None and pay is not None and due is not None:
        record["Weighted Days Past Due"] = round(float(paid) * (pay - due).days, 2)
    if paid is not None and pay is not None and inv is not None:
        record["Weighted Invoice to Payment"] = round(float(paid) * (pay - inv).days, 2)


def partition_preview(records, petition_date):
    """Count how rows split across analysis frames for a petition date.

    petition_date: YYYY-MM-DD text from the parent main case.
    """
    petition = (petition_date or "").strip()
    try:
        from datetime import timedelta
        pref_start = (datetime.strptime(petition, "%Y-%m-%d").date()
                      - timedelta(days=90)).isoformat()
    except ValueError:
        pref_start = None
    counts = {"total": len(records), "historical": 0, "preference": 0,
              "new_value": 0, "outside_window": 0}
    for r in records:
        pay = r.get("Payment Date") or ""
        if pref_start is not None and pay:
            if pay < pref_start:
                counts["historical"] += 1
            elif pay <= petition:
                counts["preference"] += 1
            else:
                counts["outside_window"] += 1
        if r.get("Unpaid") == 1:
            counts["new_value"] += 1
    return counts
