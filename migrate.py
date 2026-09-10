import sqlite3
from datetime import datetime, timezone

import pandas as pd

import store

LEGACY_DB = 'pat_test.db'

MASTER_1 = {
    'case_name': 'In re ABC Corp.',
    'case_number': '26-10012',
    'jurisdiction': 'Bankruptcy Court of the Southern District of New York',
    'judge': 'Judge Roger Smith',
    'petition_date': '2023-10-15',
}
SUBCASE_1 = {
    'transferee_name': 'Legacy Test Transferee',
    'adversary_number': '26-10001',
}

DATE_COLS = ['Payment Date', 'Invoice Date', 'Invoice Due', 'Check Date']


def now():
    return datetime.now(timezone.utc).isoformat()


def already_seeded():
    conn = sqlite3.connect(store.CASES_DB)
    count = conn.execute("SELECT COUNT(*) FROM invoice_records").fetchone()[0]
    subs = conn.execute("SELECT COUNT(*) FROM subcases").fetchone()[0]
    conn.close()
    return count > 0 or subs > 0


def insert_master(conn, data):
    cur = conn.execute(
        "INSERT INTO master_cases (case_name, case_number, jurisdiction, judge, petition_date, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (data['case_name'], data['case_number'], data['jurisdiction'], data['judge'],
         data['petition_date'], now()))
    return cur.lastrowid


def insert_subcase(conn, master_id, transferee_name, adversary_number):
    cur = conn.execute(
        "INSERT INTO subcases (master_case_id, transferee_name, adversary_number, created_at) "
        "VALUES (?, ?, ?, ?)",
        (master_id, transferee_name, adversary_number, now()))
    return cur.lastrowid


def copy_legacy_rows(conn, subcase_id):
    src = sqlite3.connect(LEGACY_DB)
    cursor = src.execute("SELECT * FROM test_data")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    src.close()

    idx = {c: cols.index(c) for c in cols}
    INSERT_COLS = ', '.join('"%s"' % c for c in
                            ['Transfer Number', 'Transfer Amount', 'Invoice Number', 'Invoice Amount',
                             'Check Amount', 'Payment Date', 'Invoice Date', 'Invoice Due', 'Terms Days',
                             'Days Past Due', 'WDPD', 'Invoice to Payment', 'WI2DEL', 'Age', 'Unpaid',
                             'Check Date'])
    placeholders = ', '.join('?' * 16)
    sql = f"INSERT INTO invoice_records (subcase_id, {INSERT_COLS}) VALUES (?, {placeholders})"
    prepared = []
    for row in rows:
        get = lambda c: row[idx[c]]
        prepared.append((
            subcase_id,
            get('Transfer Number'), get('Transfer Amount'), get('Invoice Number'), get('Invoice Amount'),
            get('Check Amount'), get('Payment Date'), get('Invoice Date'), get('Invoice Due'),
            get('Terms Days'), get('Days Past Due'), get('WDPD'), get('Invoice to Payment'),
            get('WI2DEL'), get('Age'), get('Unpaid'), get('Check Date'),
        ))
    conn.executemany(sql, prepared)


def shift_dates(value, offset):
    if value is None:
        return None
    try:
        parsed = pd.Timestamp(str(value).strip())
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return (parsed + offset).strftime('%Y-%m-%d')


def insert_synthetic_rows(conn, subcase_id, offset):
    src = sqlite3.connect(LEGACY_DB)
    cursor = src.execute("SELECT * FROM test_data")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    src.close()

    idx = {c: cols.index(c) for c in cols}
    INSERT_COLS = ', '.join('"%s"' % c for c in
                            ['Transfer Number', 'Transfer Amount', 'Invoice Number', 'Invoice Amount',
                             'Check Amount', 'Payment Date', 'Invoice Date', 'Invoice Due', 'Terms Days',
                             'Days Past Due', 'WDPD', 'Invoice to Payment', 'WI2DEL', 'Age', 'Unpaid',
                             'Check Date'])
    placeholders = ', '.join('?' * 16)
    sql = f"INSERT INTO invoice_records (subcase_id, {INSERT_COLS}) VALUES (?, {placeholders})"

    prepared = []
    for row in rows:
        get = lambda c: row[idx[c]]
        transfer = get('Transfer Number')
        invoice = get('Invoice Number')
        new_row = [
            'TT' + transfer[2:] if isinstance(transfer, str) and transfer.startswith('TR') else transfer,
            get('Transfer Amount'),
            '20' + invoice[2:] if isinstance(invoice, str) and len(invoice) > 2 else invoice,
            get('Invoice Amount'),
            get('Check Amount'),
            shift_dates(get('Payment Date'), offset),
            shift_dates(get('Invoice Date'), offset),
            shift_dates(get('Invoice Due'), offset),
            get('Terms Days'),
            get('Days Past Due'),
            get('WDPD'),
            get('Invoice to Payment'),
            get('WI2DEL'),
            get('Age'),
            get('Unpaid'),
            shift_dates(get('Check Date'), offset),
        ]
        prepared.append((subcase_id, *new_row))
    conn.executemany(sql, prepared)


def main():
    conn = store.init_cases_db()
    if already_seeded():
        print('pat_cases.db already seeded - nothing to do.')
        conn.close()
        return

    master1 = insert_master(conn, MASTER_1)
    sub1 = insert_subcase(conn, master1, SUBCASE_1['transferee_name'], SUBCASE_1['adversary_number'])
    copy_legacy_rows(conn, sub1)
    print(f'Seeded master {master1} / subcase {sub1} with {count_rows(sub1)} invoice rows.')

    offset = pd.Timedelta(days=700)
    petition2 = (pd.Timestamp(MASTER_1['petition_date']) + offset).strftime('%Y-%m-%d')
    master2 = conn.execute(
        "INSERT INTO master_cases (case_name, case_number, jurisdiction, judge, petition_date, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ('In re Synthetic Corp.', '26-00002', 'Bankruptcy Court of the Southern District of New York',
         'Judge Jane Doe', petition2, now())).lastrowid
    sub2 = insert_subcase(conn, master2, 'Demo Transferee, LLC', '00-12345')
    insert_synthetic_rows(conn, sub2, offset)
    print(f'Seeded master {master2} / subcase {sub2} with {count_rows(sub2)} invoice rows.')

    conn.commit()
    conn.close()
    print('Preference start derived for subcase 1:', (pd.Timestamp(MASTER_1['petition_date']) - pd.Timedelta(days=90)).strftime('%Y-%m-%d'))
    print('Preference start derived for subcase 2:', (pd.Timestamp(petition2) - pd.Timedelta(days=90)).strftime('%Y-%m-%d'))


def count_rows(subcase_id):
    conn = sqlite3.connect(store.CASES_DB)
    count = conn.execute("SELECT COUNT(*) FROM invoice_records WHERE subcase_id = ?", (subcase_id,)).fetchone()[0]
    conn.close()
    return count


if __name__ == '__main__':
    main()