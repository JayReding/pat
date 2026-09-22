"""Data-import parsing/validation for loading invoice rows into a subcase.

Two ingestion paths share one validation core:

* Template path: workbooks formatted like ``subcase_import_template.xlsx``
  (sheet ``Invoices``) with exact headers and strict value coercion.
* ERP path: arbitrary CSV (or Excel-sheet) exports from systems such as
  NetSuite, Salesforce, QuickBooks, Xero, or Dynamics 365.  Headers are
  mapped onto the template columns (with per-ERP presets), values are
  coerced leniently (many date/number formats), and invoices + payments may
  arrive as two files joined on invoice number.

Any invalid row rejects the whole file: callers should surface the returned
error list and abort the import.

Dates are normalized to plain ``YYYY-MM-DD`` text because the app compares
them as strings when partitioning historical vs preference rows.
"""

import csv
import re
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO

INVOICES_SHEET = "Invoices"

TEMPLATE_COLUMNS = [
    "Transfer Number",
    "Transfer Amount",
    "Invoice Number",
    "Invoice Amount",
    "Check Amount",
    "Payment Date",
    "Invoice Date",
    "Invoice Due",
    "Terms Days",
    "Days Past Due",
    "WDPD",
    "Invoice to Payment",
    "WI2DEL",
    "Age",
    "Unpaid",
    "Check Date",
]

REQUIRED_COLUMNS = {
    "Transfer Number",
    "Transfer Amount",
    "Invoice Number",
    "Invoice Amount",
    "Payment Date",
    "Invoice Date",
}

MONEY_COLUMNS = {"Transfer Amount", "Invoice Amount", "Check Amount"}
FLOAT_COLUMNS = {"WDPD", "WI2DEL"}
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


def _parse_unpaid(raw_val):
    """Return 0/1 for an Unpaid cell, or raise ValueError."""
    text = _cell_text(raw_val)
    if text == "":
        return 0
    if text in ("0", "1"):
        return int(text)
    if isinstance(raw_val, bool):
        return int(raw_val)
    if isinstance(raw_val, (int, float)) and raw_val in (0, 1, 0.0, 1.0):
        return int(raw_val)
    raise ValueError("Unpaid must be blank/0 (paid) or 1 (new value).")


def _parse_int_lenient(value, column):
    """Coerce an ERP export cell to int, or None if blank.

    Falls back to the first integer found in text, so values like
    "Net 30" or "30 days" parse as 30.  Raises ValueError otherwise.
    """
    try:
        return _parse_int(value, column)
    except ValueError:
        pass
    text = _cell_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+", text)
    if match:
        return int(match.group(0))
    raise ValueError(f"'{value}' is not a whole number for {column}.")


def _validate_record(raw, excel_row, date_fn, money_fn, int_fn=_parse_int,
                     synthesize_transfer=False):
    """Validate one raw dict; return (record, row_errors).

    date_fn/money_fn/int_fn are the strict or lenient coercers.  Rows
    flagged Unpaid=1 (new-value shipments) do not require
    payment/transfer fields.  When synthesize_transfer is true (ERP path),
    a blank Transfer Number is auto-generated and a blank Transfer Amount
    defaults to the Invoice Amount instead of erroring.
    """
    try:
        unpaid = _parse_unpaid(raw.get("Unpaid"))
    except ValueError as exc:
        return None, [str(exc)]
    paid = (unpaid != 1)
    required = set(REQUIRED_COLUMNS)
    if not paid:
        required -= {"Payment Date", "Transfer Number", "Transfer Amount"}
    if synthesize_transfer:
        required -= {"Transfer Number", "Transfer Amount"}

    record = {"Unpaid": unpaid}
    row_errors = []
    for col in TEMPLATE_COLUMNS:
        if col == "Unpaid":
            continue
        raw_val = raw.get(col)
        if col in DATE_COLUMNS:
            try:
                parsed = date_fn(raw_val)
            except ValueError as exc:
                row_errors.append(f"{col} {exc}")
                continue
            if col in required and parsed is None:
                row_errors.append(f"{col} is required.")
            record[col] = parsed
        elif col in MONEY_COLUMNS:
            try:
                parsed = money_fn(raw_val)
            except ValueError:
                row_errors.append(
                    f"{col} must be a number (got '{_cell_text(raw_val)}').")
                continue
            if col in required and parsed is None:
                row_errors.append(f"{col} is required.")
            record[col] = parsed
        elif col in INTEGER_COLUMNS:
            try:
                parsed = int_fn(raw_val, col)
            except ValueError as exc:
                row_errors.append(str(exc))
                continue
            record[col] = parsed
        elif col in FLOAT_COLUMNS:
            try:
                parsed = money_fn(raw_val)
            except ValueError:
                row_errors.append(
                    f"{col} must be a number (got '{_cell_text(raw_val)}').")
                continue
            record[col] = parsed
        else:  # text columns: Transfer Number, Invoice Number, Age
            text = _cell_text(raw_val)
            if col in required and not text:
                row_errors.append(f"{col} is required.")
            record[col] = text or None

    if row_errors:
        return None, row_errors
    if paid and not record.get("Transfer Number"):
        record["Transfer Number"] = f"AUTO-{excel_row:04d}"
    _derive_computed_fields(record)
    if paid and record.get("Transfer Amount") is None:
        return None, ["Transfer Amount is required."]
    return record, []


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
        record, row_errors = _validate_record(raw, excel_row, _parse_date, _parse_money)
        if row_errors:
            errors.append(f"Row {excel_row}: " + "; ".join(row_errors))
        else:
            records.append(record)
    return records, errors


def validate_mapped_rows(raw_rows, dayfirst=False):
    """Validate mapped raw rows (ERP path); return (records, errors).

    raw_rows: list of dicts already keyed by TEMPLATE_COLUMNS (see
    apply_column_map).  Uses lenient date/money coercion.  Row numbers in
    errors are 1-based data-row numbers.
    """
    date_fn = lambda v: _parse_date_lenient(v, dayfirst=dayfirst)
    records = []
    errors = []
    for idx, raw in enumerate(raw_rows):
        record, row_errors = _validate_record(
            raw, idx + 1, date_fn, _parse_money_lenient,
            int_fn=_parse_int_lenient, synthesize_transfer=True)
        if row_errors:
            errors.append(f"Row {idx + 1}: " + "; ".join(row_errors))
        else:
            records.append(record)
    return records, errors


def _derive_computed_fields(record):
    """Fill derivable timing fields left blank (in place).

    Invoice to Payment, Days Past Due, Invoice Due, and Terms Days are
    mathematically linked; when the anchor dates exist the missing ones are
    filled so ERP exports that omit them still analyze correctly.  Also
    defaults a missing paid Transfer Amount to the Invoice Amount (1:1
    payment assumption for single-invoice payments).
    """
    if record.get("Unpaid") == 1:
        return
    try:
        inv = datetime.strptime(record["Invoice Date"], "%Y-%m-%d").date() \
            if record.get("Invoice Date") else None
        pay = datetime.strptime(record["Payment Date"], "%Y-%m-%d").date() \
            if record.get("Payment Date") else None
        due = datetime.strptime(record["Invoice Due"], "%Y-%m-%d").date() \
            if record.get("Invoice Due") else None
    except ValueError:
        return
    terms = record.get("Terms Days")
    if record.get("Transfer Amount") is None and record.get("Invoice Amount") is not None:
        record["Transfer Amount"] = record["Invoice Amount"]
    if due is None and inv is not None and terms is not None:
        due = inv + timedelta(days=int(terms))
        record["Invoice Due"] = due.isoformat()
    if record.get("Invoice to Payment") is None and inv is not None and pay is not None:
        record["Invoice to Payment"] = (pay - inv).days
    if record.get("Days Past Due") is None and pay is not None and due is not None:
        record["Days Past Due"] = (pay - due).days
    if record.get("Days Past Due") is None and record.get("Invoice to Payment") is not None \
            and terms is not None:
        record["Days Past Due"] = record["Invoice to Payment"] - int(terms)


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


# ---------------------------------------------------------------------------
# ERP path: generic CSV/Excel exports with column mapping
# ---------------------------------------------------------------------------

def normalize_header(value):
    """Lowercase, strip, and collapse separators for header comparison."""
    text = _cell_text(value).lower()
    text = re.sub(r"[\s_\-]+", " ", text)
    return re.sub(r"[^a-z0-9 #./]", "", text).strip()


_LENIENT_DATE_FORMATS_MDY = (
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
    "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y", "%d-%b-%y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
)

_LENIENT_DATE_FORMATS_DMY = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
    "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y", "%d-%b-%y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
)

_EXCEL_EPOCH = date(1899, 12, 30)


def _parse_date_lenient(value, dayfirst=False):
    """Coerce an ERP export cell to YYYY-MM-DD text, or None if blank.

    Accepts datetime/date objects, strict YYYY-MM-DD, common regional
    formats (month-first by default, day-first when dayfirst=True), ISO
    datetimes, and Excel serial day numbers.  Raises ValueError when
    present but unrecognizable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        raise ValueError(f"'{value}' is not a valid date.")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        serial = int(value)
        if 20000 <= serial <= 60000:  # plausible 1954..2064 serial range
            return (_EXCEL_EPOCH + timedelta(days=serial)).isoformat()
        raise ValueError(f"'{value}' is not a valid date.")
    text = _cell_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    formats = _LENIENT_DATE_FORMATS_DMY if dayfirst else _LENIENT_DATE_FORMATS_MDY
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a recognizable date.")


def _parse_money_lenient(value):
    """Coerce an ERP export cell to float, or None if blank.

    Accepts currency symbols, thousands separators, surrounding whitespace,
    (parenthesized) negatives, and trailing CR/DR indicators.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"'{value}' is not a number.")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return float(value)
    text = _cell_text(value)
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    upper = text.upper().strip()
    if upper.endswith(" CR"):
        text = text[:-3]
    elif upper.endswith(" DR"):
        negative, text = True, text[:-3]
    text = text.replace("$", "").replace("€", "").replace("£", "") \
        .replace(" ", "").replace("'", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # EU: 1.200,00
            text = text.replace(".", "").replace(",", ".")
        else:  # US: 1,200.00
            text = text.replace(",", "")
    elif "," in text:  # EU decimals (100,00) or US thousands (1,200)
        head, _, tail = text.rpartition(",")
        if tail.isdigit() and len(tail) != 3:
            text = f"{head}.{tail}"
        else:
            text = text.replace(",", "")
    try:
        amount = float(text)
    except ValueError:
        raise ValueError(f"'{value}' is not a number.")
    return -amount if negative else amount


def parse_import_csv(data):
    """Read CSV bytes; return (headers, raw_rows).

    Detects encoding (UTF-8-SIG, UTF-8, then Latin-1) and delimiter
    (comma, semicolon, tab, pipe).  headers are the stripped first-row
    values; raw_rows map header -> cell text, skipping fully-blank rows.
    Raises ValueError for undecodable or empty files.
    """
    raw = bytes(data)
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        raise ValueError("Could not decode the CSV file (tried UTF-8 and Latin-1).")
    sample = text[:16384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The CSV file has no header row.")
    headers = [_cell_text(h) for h in reader.fieldnames]
    headers = [h for h in headers if h]
    if not headers:
        raise ValueError("The CSV file has no header row.")
    raw_rows = []
    for row in reader.reader:
        cells = [_cell_text(v) for v in row]
        if all(c == "" for c in cells):
            continue
        raw_rows.append({
            headers[i]: (cells[i] if i < len(cells) else "")
            for i in range(len(headers))
        })
    if not raw_rows:
        raise ValueError("The CSV file has no data rows.")
    return headers, raw_rows


def apply_column_map(raw_rows, mapping):
    """Project arbitrary-header rows onto TEMPLATE_COLUMNS.

    mapping: {template_column: source_header or None}.  Unmapped columns
    become None (filled by derivation or flagged by validation later).
    """
    mapped = []
    for raw in raw_rows:
        mapped.append({col: raw.get(mapping.get(col)) for col in TEMPLATE_COLUMNS})
    return mapped


# Per-ERP header candidates for each template column, used to pre-fill the
# mapping UI.  Matching is done on normalized headers; the template column
# names themselves always match too (so template-shaped files map 1:1).
ERP_PRESETS = {
    "NetSuite": {
        "Transfer Number": ["payment number", "transaction number", "check number", "ref number", "document number payment"],
        "Transfer Amount": ["payment amount", "amount paid", "applied amount", "payment total"],
        "Invoice Number": ["document number", "invoice number", "invoice no", "tranid", "invoice #"],
        "Invoice Amount": ["amount", "invoice amount", "total", "foreign total", "net amount"],
        "Check Amount": ["check amount"],
        "Payment Date": ["payment date", "trandate payment", "date paid", "clearing date", "paid date"],
        "Invoice Date": ["date", "trandate", "invoice date", "date created", "posting date"],
        "Invoice Due": ["due date", "receive by", "due"],
        "Terms Days": ["terms"],
        "Days Past Due": ["days past due", "days overdue"],
        "Invoice to Payment": ["days to pay", "invoice to payment"],
        "Unpaid": ["unpaid", "open"],
        "Check Date": ["check date"],
    },
    "Salesforce": {
        "Transfer Number": ["payment number", "payment name", "payment id", "check number"],
        "Transfer Amount": ["payment amount", "amount paid"],
        "Invoice Number": ["invoice number", "invoice name", "invoice id", "invoice #"],
        "Invoice Amount": ["invoice amount", "amount", "total", "grand total"],
        "Payment Date": ["payment date", "date paid"],
        "Invoice Date": ["invoice date", "created date", "issue date"],
        "Invoice Due": ["due date"],
        "Terms Days": ["payment terms", "terms"],
        "Days Past Due": ["days past due"],
        "Invoice to Payment": ["invoice to payment"],
        "Unpaid": ["unpaid", "is unpaid"],
        "Check Date": ["check date"],
    },
    "QuickBooks": {
        "Transfer Number": ["payment no", "num payment", "check no", "ref no", "payment ref"],
        "Transfer Amount": ["payment amount", "amount received", "amt received"],
        "Invoice Number": ["num", "invoice no", "invoice num", "ref number", "invoice #"],
        "Invoice Amount": ["amount", "invoice amount", "open balance", "original amount"],
        "Check Amount": ["check amount"],
        "Payment Date": ["payment date", "received date", "date deposited"],
        "Invoice Date": ["date", "invoice date", "txn date"],
        "Invoice Due": ["due date"],
        "Terms Days": ["terms"],
        "Days Past Due": ["days past due", "aging"],
        "Invoice to Payment": ["invoice to payment"],
        "Unpaid": ["unpaid"],
        "Check Date": ["check date"],
    },
    "Xero": {
        "Transfer Number": ["payment id", "payment reference", "reference"],
        "Transfer Amount": ["amount paid", "payment amount", "paid"],
        "Invoice Number": ["invoice number", "invoicenumber", "invoice #", "reference"],
        "Invoice Amount": ["total", "amount", "invoice total", "subtotal"],
        "Payment Date": ["payment date", "date paid"],
        "Invoice Date": ["invoice date", "invoicedate", "date issued"],
        "Invoice Due": ["due date", "duedate"],
        "Terms Days": ["terms"],
        "Days Past Due": ["days past due"],
        "Invoice to Payment": ["invoice to payment"],
        "Unpaid": ["unpaid", "status unpaid"],
        "Check Date": ["check date"],
    },
    "Dynamics 365": {
        "Transfer Number": ["payment id", "voucher payment", "payment reference", "journal number"],
        "Transfer Amount": ["payment amount", "amount settled", "settle amount"],
        "Invoice Number": ["invoice", "invoice number", "invoice account voucher", "document number"],
        "Invoice Amount": ["amount", "invoice amount", "original amount", "balance"],
        "Payment Date": ["payment date", "posting date payment", "settlement date"],
        "Invoice Date": ["invoice date", "posting date", "document date"],
        "Invoice Due": ["due date"],
        "Terms Days": ["payment terms", "terms of payment"],
        "Days Past Due": ["days past due"],
        "Invoice to Payment": ["invoice to payment"],
        "Unpaid": ["unpaid", "open"],
        "Check Date": ["check date", "cheque date"],
    },
}

# Generic fallbacks tried for every file (template-shaped exports map 1:1).
_GENERIC_CANDIDATES = {
    "Transfer Number": ["transfer number", "transfer no", "transfer #", "payment id", "payment number", "transaction id"],
    "Transfer Amount": ["transfer amount", "payment amount", "amount paid", "paid amount"],
    "Invoice Number": ["invoice number", "invoice no", "invoice #", "inv number", "inv no", "invoice id"],
    "Invoice Amount": ["invoice amount", "amount", "total", "invoice total"],
    "Check Amount": ["check amount", "cheque amount"],
    "Payment Date": ["payment date", "paid date", "date paid", "pay date"],
    "Invoice Date": ["invoice date", "inv date", "issue date", "billing date"],
    "Invoice Due": ["invoice due", "due date", "payment due"],
    "Terms Days": ["terms days", "terms", "payment terms", "net terms"],
    "Days Past Due": ["days past due", "dpd", "days overdue", "past due"],
    "Invoice to Payment": ["invoice to payment", "days to payment", "dso", "days outstanding"],
    "Unpaid": ["unpaid"],
    "Check Date": ["check date", "cheque date"],
}


def _preset_candidates(preset_name, template_col):
    seen = []
    for cand in list(ERP_PRESETS.get(preset_name, {}).get(template_col, ())) \
            + _GENERIC_CANDIDATES.get(template_col, []) + [template_col]:
        norm = normalize_header(cand)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def suggest_mapping(headers, preset_name=None):
    """Suggest {template_col: header or None} for the given headers.

    When preset_name is None, every preset (plus generic matching) is
    scored and the best one wins.  Returns (preset_name, mapping).
    """
    norm_of = {h: normalize_header(h) for h in headers}
    if all(normalize_header(c) in set(norm_of.values()) for c in TEMPLATE_COLUMNS):
        return "Template", {c: next(h for h in headers
                                   if normalize_header(h) == normalize_header(c))
                             for c in TEMPLATE_COLUMNS}
    names = [preset_name] if preset_name in ERP_PRESETS else list(ERP_PRESETS)
    best, best_map, best_score = "Generic", {}, -1
    for name in names + ([] if names == ["Generic"] else []):
        mapping, score = {}, 0
        for col in TEMPLATE_COLUMNS:
            cands = _preset_candidates(name, col) if name != "Generic" \
                else [normalize_header(c) for c in _GENERIC_CANDIDATES.get(col, []) + [col]]
            hit = next((h for h in headers if norm_of[h] in cands), None)
            mapping[col] = hit
            if hit:
                score += 1
        if score > best_score:
            best, best_map, best_score = name, mapping, score
    return best, best_map


def detect_preset(headers):
    """Return (preset_name, mapping) for the best-scoring ERP preset."""
    return suggest_mapping(headers)


def join_invoices_payments(inv_rows, pay_rows, inv_key, pay_key):
    """Join mapped invoice + payment rows on invoice number.

    inv_rows/pay_rows: dicts keyed by TEMPLATE_COLUMNS (raw cell values).
    Returns (joined_raw_rows, errors).  Each payment row produces one paid
    row (transfer fields from the payment, invoice fields from the invoice);
    invoices with no payment become Unpaid=1 new-value rows; payments
    matching no invoice are errors.
    """
    by_invoice = {}
    for row in inv_rows:
        key = _cell_text(row.get(inv_key))
        if key:
            by_invoice.setdefault(key, []).append(row)
    joined, errors = [], []
    matched = set()
    for idx, pay in enumerate(pay_rows):
        key = _cell_text(pay.get(pay_key))
        if not key:
            errors.append(f"Payments row {idx + 1}: no invoice reference "
                          f"in '{pay_key}'.")
            continue
        invs = by_invoice.get(key)
        if not invs:
            errors.append(f"Payments row {idx + 1}: invoice '{key}' not found "
                          "in the invoices file.")
            continue
        matched.add(key)
        for inv in invs:
            combined = dict(inv)
            for col in ("Transfer Number", "Transfer Amount", "Check Amount",
                        "Payment Date", "Check Date"):
                if _cell_text(pay.get(col)):
                    combined[col] = pay.get(col)
            combined["Unpaid"] = pay.get("Unpaid") or 0
            joined.append(combined)
    for key, invs in by_invoice.items():
        if key not in matched:
            for inv in invs:
                row = dict(inv)
                row["Transfer Number"] = None
                row["Transfer Amount"] = None
                row["Check Amount"] = None
                row["Payment Date"] = None
                row["Check Date"] = None
                row["Unpaid"] = 1
                joined.append(row)
    return joined, errors
