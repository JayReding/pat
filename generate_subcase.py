import argparse
import random
import sqlite3
from datetime import datetime, timedelta, timezone

CASES_DB = 'pat_cases.db'
MASTER_CASE_ID = 1
SOURCE_SUBCASE_ID = 1
NAME = 'Bobs Widgets'
ADVERSARY_NUMBER = '26-10056'
SEED = 42
AMOUNT_RANGE = (0.90, 1.10)
DAY_RANGE = (0.90, 1.10)
DATE_JITTER = 0.10


def _parse_date(value):
    if value is None:
        return None
    return datetime.strptime(value, '%Y-%m-%d').date()


def _fmt(date_obj):
    return date_obj.strftime('%Y-%m-%d') if date_obj is not None else None


def _jitter(rng, value, lo, hi):
    if value is None or value == 0:
        return value
    return value * rng.uniform(lo, hi)


def generate(source_rows, petition_date):
    rng = random.Random(SEED)
    petition = datetime.strptime(petition_date, '%Y-%m-%d').date()
    out = []
    for row in source_rows:
        row = dict(row)
        lag = row.get('Invoice to Payment')
        row['Transfer Number'] = 'BW' + str(row['Transfer Number'])[2:]
        row['Invoice Number'] = 'BW' + str(row['Invoice Number'])
        for col in ('Transfer Amount', 'Invoice Amount', 'Check Amount'):
            if row.get(col) is not None:
                row[col] = round(_jitter(rng, row[col], *AMOUNT_RANGE), 2)
        for col in ('WDPD', 'WI2DEL'):
            if row.get(col) is not None:
                row[col] = round(_jitter(rng, row[col], *DAY_RANGE), 2)
        if row.get('Days Past Due') is not None:
            row['Days Past Due'] = int(round(_jitter(rng, row['Days Past Due'], *DAY_RANGE)))
        terms = row.get('Terms Days')
        if terms is not None:
            row['Terms Days'] = int(round(_jitter(rng, terms, *DAY_RANGE)))

        if lag is None:
            out.append(row)
            continue

        new_lag = max(0, round(_jitter(rng, lag, *DAY_RANGE)))
        payment = _parse_date(row.get('Payment Date'))
        if payment is not None:
            shift = round(lag * rng.uniform(-DATE_JITTER, DATE_JITTER))
            payment = payment + timedelta(days=shift)
        if payment is not None and payment > petition:
            payment = petition

        invoice = _parse_date(row.get('Invoice Date'))
        if payment is not None:
            invoice = payment - timedelta(days=new_lag)
        if invoice is not None and invoice > petition:
            invoice = petition

        due_from_terms = None
        if terms is not None:
            due_from_terms = invoice + timedelta(days=int(round(terms)))
        due = _parse_date(row.get('Invoice Due'))
        if terms is not None:
            due = due_from_terms
        if due is not None and due > petition:
            due = petition

        check = _parse_date(row.get('Check Date'))
        if check is not None and payment is not None:
            check = payment
        if check is not None and check > petition:
            check = petition

        row['Payment Date'] = _fmt(payment)
        row['Invoice Date'] = _fmt(invoice)
        row['Invoice Due'] = _fmt(due)
        row['Check Date'] = _fmt(check)
        row['Invoice to Payment'] = new_lag
        row['Age'] = str(new_lag)
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='delete and regenerate Bobs Widgets if it exists')
    args = ap.parse_args()

    conn = sqlite3.connect(CASES_DB)
    cur = conn.cursor()

    master = cur.execute(
        'SELECT id, case_name, petition_date FROM master_cases WHERE id = ?',
        (MASTER_CASE_ID,)).fetchone()
    if master is None:
        raise SystemExit(f'Master case {MASTER_CASE_ID} not found')
    petition_date = master[2]

    cur.execute(
        'SELECT id FROM subcases WHERE master_case_id = ? AND transferee_name = ?',
        (MASTER_CASE_ID, NAME))
    existing = cur.fetchone()
    if existing and not args.force:
        print(f'{NAME} already exists (subcase id {existing[0]}); skipping. Use --force to regenerate.')
        return
    if existing and args.force:
        cur.execute('DELETE FROM subcases WHERE id = ?', (existing[0],))
        conn.commit()
        print(f'Removed existing {NAME} (subcase id {existing[0]})')

    cur.execute(
        'INSERT INTO subcases (master_case_id, transferee_name, adversary_number, created_at, meta) '
        'VALUES (?, ?, ?, ?, ?)',
        (MASTER_CASE_ID, NAME, ADVERSARY_NUMBER, datetime.now(timezone.utc).isoformat(), '{}'))
    new_subcase_id = cur.lastrowid

    source_rows = cur.execute(
        'SELECT * FROM invoice_records WHERE subcase_id = ?', (SOURCE_SUBCASE_ID,)).fetchall()
    cols = [d[1] for d in cur.execute('PRAGMA table_info(invoice_records)').fetchall()]
    rows = generate([dict(zip(cols, r)) for r in source_rows], petition_date)

    insert_sql = f"INSERT INTO invoice_records ({', '.join(f'\"{c}\"' for c in cols[1:])}) " \
                 f"VALUES ({', '.join('?' for _ in cols[1:])})"
    for r in rows:
        r['subcase_id'] = new_subcase_id
        cur.execute(insert_sql, tuple(r[c] for c in cols[1:]))
    conn.commit()

    payments = [r['Payment Date'] for r in rows if r.get('Payment Date')]
    pref_count = sum(1 for p in payments if '2023-07-17' <= p <= '2023-10-15')
    clamped = sum(1 for p in payments if p == '2023-10-15')
    print(f'Generated subcase {NAME} ({ADVERSARY_NUMBER}) -> id {new_subcase_id}')
    print(f'Rows inserted: {len(rows)} (source subcase {SOURCE_SUBCASE_ID})')
    print(f'Payment dates: {min(payments) if payments else None} through {max(payments) if payments else None}')
    print(f'Preference-window payments: {pref_count}')
    print(f'Clamped to petition date 2023-10-15: {clamped}')
    conn.close()


if __name__ == '__main__':
    main()