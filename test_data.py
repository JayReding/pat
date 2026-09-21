"""Semi-random test-data generator for modeling preference scenarios.

Generates invoice records spanning ~18 months of historical data plus the
90-day preference period ending on the parent main case's petition date.
Records are keyed exactly like ``data_import``/``store`` import dicts so
callers can pass them straight to ``store.import_subcase_invoices``.

Scenarios (Net-30 baseline, DSO = Invoice to Payment):
- ordinary: historical and preference DSO distributions match (~N(32, 7)).
- late:     historical ~N(32, 7), preference shifted later (~N(50, 8)).
- early:    historical ~N(32, 7), preference shifted earlier (~N(20, 6)).

Unpaid preference rows model new-value shipments: no transfer/payment,
``Unpaid=1``, with ``Invoice Date`` inside the preference window.
"""

import random
from datetime import datetime, timedelta

SCENARIOS = ("ordinary", "late", "early")

HISTORICAL_DAYS = 540  # ~18 months before the preference window
PREFERENCE_DAYS = 90

AMOUNT_MIN = 500.0
AMOUNT_MAX = 10000.0
TERMS_DAYS = 30

# (mean, std) of DSO per scenario per period.
_DSO_PARAMS = {
    "ordinary": {"historical": (32.0, 7.0), "preference": (32.0, 7.0)},
    "late": {"historical": (32.0, 7.0), "preference": (50.0, 8.0)},
    "early": {"historical": (32.0, 7.0), "preference": (20.0, 6.0)},
}


def scenario_windows(petition_date):
    """Return (hist_start, pref_start, petition) dates for a petition date."""
    petition = datetime.strptime((petition_date or "").strip(), "%Y-%m-%d").date()
    pref_start = petition - timedelta(days=PREFERENCE_DAYS)
    hist_start = pref_start - timedelta(days=HISTORICAL_DAYS)
    return hist_start, pref_start, petition


def _sample_dso(rng, mean, std):
    return max(0, int(round(rng.gauss(mean, std))))


def _paid_row(rng, seq, payment_date, mean, std):
    amount = round(rng.uniform(AMOUNT_MIN, AMOUNT_MAX), 2)
    dso = _sample_dso(rng, mean, std)
    invoice_date = payment_date - timedelta(days=dso)
    return {
        "Transfer Number": f"T-TEST-{seq:04d}",
        "Transfer Amount": amount,
        "Invoice Number": f"INV-TEST-{seq:04d}",
        "Invoice Amount": amount,
        "Check Amount": None,
        "Payment Date": payment_date.isoformat(),
        "Invoice Date": invoice_date.isoformat(),
        "Invoice Due": (invoice_date + timedelta(days=TERMS_DAYS)).isoformat(),
        "Terms Days": TERMS_DAYS,
        "Days Past Due": dso - TERMS_DAYS,
        "WDPD": None,
        "Invoice to Payment": dso,
        "WI2DEL": None,
        "Age": None,
        "Unpaid": 0,
        "Check Date": payment_date.isoformat(),
    }


def _unpaid_row(rng, seq, pref_start, petition):
    amount = round(rng.uniform(AMOUNT_MIN, AMOUNT_MAX), 2)
    span = (petition - pref_start).days or 1
    invoice_date = pref_start + timedelta(days=rng.randint(0, span))
    return {
        "Transfer Number": None,
        "Transfer Amount": None,
        "Invoice Number": f"INV-TEST-NV-{seq:03d}",
        "Invoice Amount": amount,
        "Check Amount": None,
        "Payment Date": None,
        "Invoice Date": invoice_date.isoformat(),
        "Invoice Due": (invoice_date + timedelta(days=TERMS_DAYS)).isoformat(),
        "Terms Days": TERMS_DAYS,
        "Days Past Due": None,
        "WDPD": None,
        "Invoice to Payment": None,
        "WI2DEL": None,
        "Age": None,
        "Unpaid": 1,
        "Check Date": None,
    }


def generate_test_invoices(petition_date, scenario, total_paid=200,
                           unpaid_count=6, seed=None):
    """Build semi-random invoice records for a petition date + scenario.

    total_paid splits time-proportionally (~171 hist / ~29 pref for 200).
    unpaid_count extra rows land in the preference window as new value.
    """
    if scenario not in _DSO_PARAMS:
        raise ValueError(f"Unknown scenario: {scenario!r} (expected one of {SCENARIOS})")
    total_paid = int(total_paid)
    if total_paid < 2:
        raise ValueError("total_paid must be at least 2.")
    unpaid_count = int(unpaid_count)
    if unpaid_count < 0:
        raise ValueError("unpaid_count cannot be negative.")

    hist_start, pref_start, petition = scenario_windows(petition_date)
    params = _DSO_PARAMS[scenario]
    rng = random.Random(seed)

    hist_n = round(total_paid * HISTORICAL_DAYS / (HISTORICAL_DAYS + PREFERENCE_DAYS))
    hist_n = min(max(hist_n, 1), total_paid - 1)
    pref_n = total_paid - hist_n

    records = []
    seq = 1
    for _ in range(hist_n):
        pay = hist_start + timedelta(days=rng.randint(0, (pref_start - hist_start).days - 1))
        records.append(_paid_row(rng, seq, pay, *params["historical"]))
        seq += 1
    for _ in range(pref_n):
        pay = pref_start + timedelta(days=rng.randint(0, PREFERENCE_DAYS))
        if pay > petition:
            pay = petition
        records.append(_paid_row(rng, seq, pay, *params["preference"]))
        seq += 1
    for i in range(1, unpaid_count + 1):
        records.append(_unpaid_row(rng, i, pref_start, petition))
    return records


def summarize(records, petition_date):
    """Count how records split across frames (mirrors data_import.partition_preview)."""
    from data_import import partition_preview
    return partition_preview(records, petition_date)
