import sqlite3
import json
from datetime import datetime, timezone

import pandas as pd

CASES_DB = 'pat_cases.db'
STATE_DB = 'pat_state.db'
USERS_DB = 'pat_users.db'


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


def _connect_users():
    conn = sqlite3.connect(USERS_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_users_db():
    conn = _connect_users()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL,
            email         TEXT,
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS master_grants (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            master_case_id  INTEGER NOT NULL,
            PRIMARY KEY (user_id, master_case_id)
        );
        CREATE TABLE IF NOT EXISTS subcase_grants (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subcase_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, subcase_id)
        );
    ''')
    conn.commit()
    return conn


def _row_to_dict(columns, row):
    return dict(zip(columns, row)) if row else None


def get_user_by_username(username):
    conn = _connect_users()
    cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
    columns = [d[0] for d in cur.description]
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(columns, row)


def get_user_by_id(user_id):
    conn = _connect_users()
    cur = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),))
    columns = [d[0] for d in cur.description]
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(columns, row)


def create_user(username, password_hash, role='user', email=None):
    conn = _connect_users()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, email, active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (username, password_hash, role, email, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def list_users():
    conn = _connect_users()
    rows = conn.execute(
        "SELECT id, username, role, email, active, created_at FROM users ORDER BY username"
    ).fetchall()
    conn.close()
    return [dict(zip(("id", "username", "role", "email", "active", "created_at"), r)) for r in rows]


def set_user_role(user_id, role):
    conn = _connect_users()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, int(user_id)))
    conn.commit()
    conn.close()


def set_user_active(user_id, active):
    conn = _connect_users()
    conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(bool(active)), int(user_id)))
    conn.commit()
    conn.close()


def reset_user_password(user_id, password_hash):
    conn = _connect_users()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, int(user_id)))
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = _connect_users()
    conn.execute("DELETE FROM master_grants WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM subcase_grants WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    conn.commit()
    conn.close()


def get_master_by_id(master_id):
    conn = _connect_cases()
    row = conn.execute(
        "SELECT id, case_name, case_number, jurisdiction, judge, petition_date, created_at "
        "FROM master_cases WHERE id = ?", (int(master_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No master case with id {master_id}")
    return {
        'id': row[0],
        'case_name': row[1],
        'case_number': row[2],
        'jurisdiction': row[3],
        'judge': row[4],
        'petition_date': row[5],
        'created_at': row[6],
    }


def update_master_case(master_id, case_name, case_number, jurisdiction, judge, petition_date):
    from datetime import datetime
    name = (case_name or '').strip()
    number = (case_number or '').strip()
    petition = (petition_date or '').strip()
    if not name or not number or not petition:
        raise ValueError('case_name, case_number, and petition_date are required.')
    try:
        datetime.strptime(petition, '%Y-%m-%d')
    except ValueError:
        raise ValueError('petition_date must be YYYY-MM-DD.')
    judge = (judge or '').strip() or None
    jurisdiction = (jurisdiction or '').strip() or None
    conn = _connect_cases()
    try:
        conn.execute(
            "UPDATE master_cases SET case_name=?, case_number=?, jurisdiction=?, judge=?, petition_date=? "
            "WHERE id=?",
            (name, number, jurisdiction, judge, petition, int(master_id)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A master case named '{name}' already exists.")
    finally:
        conn.close()


def grant_master(user_id, master_case_id):
    conn = _connect_users()
    conn.execute(
        "INSERT OR IGNORE INTO master_grants (user_id, master_case_id) VALUES (?, ?)",
        (int(user_id), int(master_case_id)),
    )
    conn.commit()
    conn.close()


def revoke_master(user_id, master_case_id):
    conn = _connect_users()
    conn.execute(
        "DELETE FROM master_grants WHERE user_id = ? AND master_case_id = ?",
        (int(user_id), int(master_case_id)),
    )
    conn.commit()
    conn.close()


def grant_subcase(user_id, subcase_id):
    conn = _connect_users()
    conn.execute(
        "INSERT OR IGNORE INTO subcase_grants (user_id, subcase_id) VALUES (?, ?)",
        (int(user_id), int(subcase_id)),
    )
    conn.commit()
    conn.close()


def revoke_subcase(user_id, subcase_id):
    conn = _connect_users()
    conn.execute(
        "DELETE FROM subcase_grants WHERE user_id = ? AND subcase_id = ?",
        (int(user_id), int(subcase_id)),
    )
    conn.commit()
    conn.close()


def master_grants_for(user_id):
    conn = _connect_users()
    rows = conn.execute(
        "SELECT master_case_id FROM master_grants WHERE user_id = ?", (int(user_id),)
    ).fetchall()
    conn.close()
    return [{'master_case_id': r[0]} for r in rows]


def subcase_grants_for(user_id):
    conn = _connect_users()
    rows = conn.execute(
        "SELECT subcase_id FROM subcase_grants WHERE user_id = ?", (int(user_id),)
    ).fetchall()
    conn.close()
    return [{'subcase_id': r[0]} for r in rows]


def list_all_grants():
    conn = _connect_users()
    rows = conn.execute(
        'SELECT * FROM ('
        'SELECT mg.user_id, u.username, mg.master_case_id, NULL AS subcase_id, "master" AS level '
        'FROM master_grants mg JOIN users u ON u.id = mg.user_id '
        'UNION ALL '
        'SELECT sg.user_id, u.username, NULL, sg.subcase_id, "subcase" AS level '
        'FROM subcase_grants sg JOIN users u ON u.id = sg.user_id'
        ') ORDER BY username, level, COALESCE(master_case_id, subcase_id)'
    ).fetchall()
    conn.close()
    return [
        {'user_id': r[0], 'username': r[1], 'master_case_id': r[2], 'subcase_id': r[3], 'level': r[4]}
        for r in rows
    ]