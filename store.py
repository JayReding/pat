import sqlite3
import json

import pandas as pd

CASES_DB = 'pat_cases.db'
STATE_DB = 'pat_state.db'


def _connect_cases():
    return sqlite3.connect(CASES_DB)


def _connect_state():
    return sqlite3.connect(STATE_DB)


def init_cases_db():
    conn = _connect_cases()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS master_cases (
            id             INTEGER PRIMARY KEY,
            case_name      TEXT    NOT NULL UNIQUE,
            case_number    TEXT    NOT NULL,
            jurisdiction   TEXT,
            judge          TEXT,
            petition_date  TEXT    NOT NULL,
            created_at     TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subcases (
            id                INTEGER PRIMARY KEY,
            master_case_id    INTEGER NOT NULL REFERENCES master_cases(id) ON DELETE CASCADE,
            transferee_name   TEXT    NOT NULL,
            adversary_number  TEXT,
            created_at        TEXT    NOT NULL,
            meta              TEXT,
            UNIQUE(master_case_id, adversary_number)
        );
        CREATE TABLE IF NOT EXISTS invoice_records (
            id                  INTEGER PRIMARY KEY,
            subcase_id          INTEGER NOT NULL REFERENCES subcases(id) ON DELETE CASCADE,
            "Transfer Number"   TEXT,
            "Transfer Amount"   REAL,
            "Invoice Number"    TEXT,
            "Invoice Amount"    REAL,
            "Check Amount"      REAL,
            "Payment Date"      TEXT,
            "Invoice Date"      TEXT,
            "Invoice Due"       TEXT,
            "Terms Days"        REAL,
            "Days Past Due"     INTEGER,
            "WDPD"              REAL,
            "Invoice to Payment" INTEGER,
            "WI2DEL"            REAL,
            "Age"               TEXT,
            "Unpaid"            INTEGER DEFAULT 0,
            "Check Date"        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_invoices_subcase ON invoice_records(subcase_id);
    ''')
    conn.commit()
    return conn


def init_state_db():
    conn = _connect_state()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='case_settings'")
    if cur.fetchone():
        cols = {row[1] for row in cur.execute("PRAGMA table_info(case_settings)").fetchall()}
        if 'case_id' in cols and 'subcase_id' not in cols:
            cur.execute("ALTER TABLE case_settings RENAME COLUMN case_id TO subcase_id")
    cur.execute('''CREATE TABLE IF NOT EXISTS case_settings (
        subcase_id INTEGER PRIMARY KEY,
        ocb_start INTEGER,
        ocb_end INTEGER,
        ocb_step INTEGER,
        ocb_range TEXT,
        ocb_total_flag INTEGER,
        nv_settings TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS app_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        active_subcase_id INTEGER
    )''')
    cur.execute("INSERT OR IGNORE INTO app_state (id, active_subcase_id) VALUES (1, 1)")
    cur.execute("DROP TABLE IF EXISTS cases")
    conn.commit()
    return conn


def active_subcase_id():
    conn = _connect_state()
    row = conn.execute("SELECT active_subcase_id FROM app_state WHERE id = 1").fetchone()
    conn.close()
    subcase_id = row[0] if row and row[0] is not None else 1
    return subcase_id if subcase_exists(subcase_id) else 1


def save_app_state(subcase_id):
    conn = _connect_state()
    conn.execute("UPDATE app_state SET active_subcase_id = ? WHERE id = 1", (int(subcase_id),))
    conn.commit()
    conn.close()


def subcase_exists(subcase_id):
    conn = _connect_cases()
    row = conn.execute("SELECT COUNT(*) FROM subcases WHERE id = ?", (int(subcase_id),)).fetchone()
    conn.close()
    return bool(row[0])


def list_master_options():
    conn = _connect_cases()
    rows = conn.execute('''
        SELECT id, case_name, case_number
        FROM master_cases
        ORDER BY id
    ''').fetchall()
    conn.close()
    return [{"label": f"{name} (#{number})", "value": mid} for mid, name, number in rows]


def list_subcase_options(master_id=None):
    conn = _connect_cases()
    sql = '''
        SELECT s.id, s.transferee_name, s.adversary_number
        FROM subcases s
    '''
    params = ()
    if master_id is not None:
        sql += ' WHERE s.master_case_id = ?'
        params = (int(master_id),)
    sql += ' ORDER BY s.id'
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    options = []
    for subcase_id, name, adv in rows:
        label = f"{name} ({adv})" if adv else name
        options.append({"label": label, "value": subcase_id})
    return options


def get_master_by_subcase(subcase_id):
    conn = _connect_cases()
    row = conn.execute('''
        SELECT m.id, m.case_name, m.case_number, m.jurisdiction, m.judge, m.petition_date,
               s.transferee_name, s.adversary_number
        FROM subcases s JOIN master_cases m ON m.id = s.master_case_id
        WHERE s.id = ?
    ''', (int(subcase_id),)).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No subcase with id {subcase_id}")
    return {
        'master_id': row[0],
        'case_name': row[1],
        'case_number': row[2],
        'jurisdiction': row[3],
        'judge': row[4],
        'petition_date': row[5],
        'transferee': row[6],
        'adversary_number': row[7],
    }


def load_case_frames(subcase_id, pref_start, petition_date):
    conn = _connect_cases()
    historical = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Payment Date" < ?',
        conn, params=(int(subcase_id), pref_start))
    preference = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Payment Date" >= ? AND "Payment Date" <= ?',
        conn, params=(int(subcase_id), pref_start, petition_date))
    transfers = pd.read_sql(
        'SELECT DISTINCT "Transfer Number", "Transfer Amount", "Payment Date" FROM invoice_records '
        'WHERE subcase_id = ? AND "Payment Date" >= ? AND "Payment Date" <= ? ORDER BY "Payment Date"',
        conn, params=(int(subcase_id), pref_start, petition_date))
    newvalue = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Unpaid" = 1',
        conn, params=(int(subcase_id),))
    conn.close()
    return {
        'historical': historical,
        'preference': preference,
        'transfers': transfers,
        'newvalue': newvalue,
    }


def load_case_settings(subcase_id):
    conn = _connect_state()
    cur = conn.cursor()
    cur.execute(
        "SELECT ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings "
        "FROM case_settings WHERE subcase_id = ?", (int(subcase_id),))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {}
    return {
        'ocb_start': row[0],
        'ocb_end': row[1],
        'ocb_step': row[2],
        'ocb_range': json.loads(row[3]) if row[3] else None,
        'ocb_total_flag': bool(row[4]) if row[4] is not None else False,
        'nv_settings': json.loads(row[5]) if row[5] else None,
    }


def save_case_settings(subcase_id, ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_settings):
    ocb_range_json = json.dumps(ocb_range) if ocb_range is not None else None
    nv_json = json.dumps(nv_settings) if nv_settings else None
    conn = _connect_state()
    conn.execute(
        """INSERT INTO case_settings (subcase_id, ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(subcase_id) DO UPDATE SET
               ocb_start=excluded.ocb_start, ocb_end=excluded.ocb_end,
               ocb_step=excluded.ocb_step, ocb_range=excluded.ocb_range,
               ocb_total_flag=excluded.ocb_total_flag, nv_settings=excluded.nv_settings""",
        (int(subcase_id), int(ocb_start or 0), int(ocb_end or 100), int(ocb_step or 5),
         ocb_range_json, int(bool(ocb_total_flag)), nv_json),
    )
    conn.commit()
    conn.close()


def get_subcase_meta(subcase_id):
    conn = _connect_cases()
    row = conn.execute("SELECT meta FROM subcases WHERE id = ?", (int(subcase_id),)).fetchone()
    conn.close()
    return json.loads(row[0]) if row and row[0] else {}


def set_subcase_meta(key, value, subcase_id):
    meta = get_subcase_meta(subcase_id)
    meta[key] = value
    conn = _connect_cases()
    conn.execute("UPDATE subcases SET meta = ? WHERE id = ?", (json.dumps(meta), int(subcase_id)))
    conn.commit()
    conn.close()