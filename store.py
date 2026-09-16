import sqlite3
import json
import uuid
from datetime import datetime, timezone

import pandas as pd

CASES_DB = 'pat_cases.db'
STATE_DB = 'pat_state.db'
USERS_DB = 'pat_users.db'


def _connect_cases():
    return sqlite3.connect(CASES_DB)


def _connect_state():
    return sqlite3.connect(STATE_DB)


def _migrate_main_case_names(conn):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='master_cases'").fetchone()
    if row is not None:
        conn.execute("ALTER TABLE master_cases RENAME TO main_cases")
    subcols = {r[1] for r in conn.execute("PRAGMA table_info(subcases)").fetchall()}
    if 'master_case_id' in subcols:
        conn.execute("ALTER TABLE subcases RENAME COLUMN master_case_id TO main_case_id")
    conn.commit()


def init_cases_db():
    conn = _connect_cases()
    _migrate_main_case_names(conn)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS main_cases (
            id             INTEGER PRIMARY KEY,
            case_name      TEXT    NOT NULL UNIQUE,
            case_number    TEXT    NOT NULL,
            jurisdiction   TEXT,
            judge          TEXT,
            petition_date  TEXT    NOT NULL,
            created_at     TEXT    NOT NULL,
            firm_id        INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS subcases (
            id                INTEGER PRIMARY KEY,
            main_case_id      INTEGER NOT NULL REFERENCES main_cases(id) ON DELETE CASCADE,
            transferee_name   TEXT    NOT NULL,
            adversary_number  TEXT,
            created_at        TEXT    NOT NULL,
            meta              TEXT,
            firm_id           INTEGER NOT NULL DEFAULT 1,
            UNIQUE(main_case_id, adversary_number)
        );
        CREATE TABLE IF NOT EXISTS invoice_records (
            id                  INTEGER PRIMARY KEY,
            subcase_id          INTEGER NOT NULL REFERENCES subcases(id) ON DELETE CASCADE,
            firm_id             INTEGER NOT NULL DEFAULT 1,
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
    for table in ('main_cases', 'subcases', 'invoice_records'):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if 'firm_id' not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN firm_id INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    conn.executescript('''
        CREATE INDEX IF NOT EXISTS idx_main_cases_firm ON main_cases(firm_id);
        CREATE INDEX IF NOT EXISTS idx_subcases_firm ON subcases(firm_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_firm ON invoice_records(firm_id);
    ''')
    cols = {row[1] for row in conn.execute("PRAGMA table_info(subcases)").fetchall()}
    if 'file_number' not in cols:
        conn.execute("ALTER TABLE subcases ADD COLUMN file_number TEXT")
    if 'filing_date' not in cols:
        conn.execute("ALTER TABLE subcases ADD COLUMN filing_date TEXT")
    conn.commit()
    ensure_file_numbers()
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_subcases_file_number ON subcases(file_number)")
    conn.commit()
    return conn


def next_file_number():
    conn = _connect_cases()
    rows = conn.execute(
        "SELECT file_number FROM subcases WHERE file_number IS NOT NULL AND file_number != ''"
    ).fetchall()
    conn.close()
    nums = []
    for (fn,) in rows:
        try:
            nums.append(int(fn))
        except ValueError:
            continue
    return f"%06d" % (max(nums) + 1 if nums else 1)


def ensure_file_numbers():
    conn = _connect_cases()
    rows = conn.execute(
        "SELECT id FROM subcases WHERE file_number IS NULL OR file_number = '' ORDER BY id"
    ).fetchall()
    used = conn.execute(
        "SELECT file_number FROM subcases WHERE file_number IS NOT NULL AND file_number != ''"
    ).fetchall()
    nums = []
    for (fn,) in used:
        try:
            nums.append(int(fn))
        except ValueError:
            continue
    nxt = max(nums) + 1 if nums else 1
    for (subcase_id,) in rows:
        conn.execute("UPDATE subcases SET file_number = ? WHERE id = ?",
                     ("%06d" % nxt, int(subcase_id)))
        nxt += 1
    conn.commit()
    conn.close()


def _display_number(adversary_number, filing_date, file_number):
    return (adversary_number or "") if filing_date else (file_number or "")


def get_subcase(subcase_id):
    conn = _connect_cases()
    row = conn.execute(
        "SELECT s.id, s.main_case_id, s.transferee_name, s.adversary_number, s.file_number, "
        "s.filing_date, s.meta, m.firm_id "
        "FROM subcases s JOIN main_cases m ON m.id = s.main_case_id "
        "WHERE s.id = ?", (int(subcase_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No subcase with id {subcase_id}")
    meta = json.loads(row[6]) if row[6] else {}
    result = {
        'id': row[0],
        'main_case_id': row[1],
        'transferee_name': row[2],
        'adversary_number': row[3] or '',
        'file_number': row[4] or '',
        'filing_date': row[5] or '',
        'firm_id': row[7],
    }
    result['display_number'] = _display_number(result['adversary_number'], result['filing_date'], result['file_number'])
    for key in (_META_KEYS):
        result[key] = meta.get(key)
    return result


_META_KEYS = [
    'contact_name', 'contact_address', 'contact_address2', 'contact_city', 'contact_state',
    'contact_zip', 'contact_phone', 'contact_email',
    'attorney_name', 'attorney_firm', 'attorney_address', 'attorney_address2', 'attorney_city',
    'attorney_state', 'attorney_zip', 'attorney_phone', 'attorney_email',
    'local_counsel_name', 'local_counsel_firm', 'local_counsel_address', 'local_counsel_address2',
    'local_counsel_city', 'local_counsel_state', 'local_counsel_zip', 'local_counsel_phone',
    'local_counsel_email',
]


def update_subcase_metadata(subcase_id, file_number=None, filing_date=None, **meta_fields):
    file_number = (file_number or '').strip()
    if not file_number:
        file_number = next_file_number()
    meta_fields = {k: (v or '').strip() for k, v in meta_fields.items()}

    if filing_date:
        try:
            datetime.strptime(filing_date, '%Y-%m-%d')
        except ValueError:
            raise ValueError('filing_date must be YYYY-MM-DD.')
    else:
        filing_date = ''

    conn = _connect_cases()
    try:
        dup = conn.execute(
            "SELECT id FROM subcases WHERE file_number = ? AND id != ?",
            (file_number, int(subcase_id)),
        ).fetchone()
        if dup:
            raise ValueError(f"A subcase with file number '{file_number}' already exists.")
        conn.execute(
            "UPDATE subcases SET file_number = ?, filing_date = ?, meta = ? WHERE id = ?",
            (file_number, filing_date, json.dumps(meta_fields), int(subcase_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return file_number


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
        nv_settings TEXT,
        ocb_metric TEXT
    )''')
    scols = {row[1] for row in cur.execute("PRAGMA table_info(case_settings)").fetchall()}
    if 'ocb_metric' not in scols:
        cur.execute("ALTER TABLE case_settings ADD COLUMN ocb_metric TEXT")
    acols = {row[1] for row in cur.execute("PRAGMA table_info(app_state)").fetchall()}
    if acols and 'firm_id' not in acols:
        cur.execute("ALTER TABLE app_state RENAME TO app_state_legacy")
        cur.execute('''CREATE TABLE app_state (
            firm_id          INTEGER PRIMARY KEY,
            active_subcase_id INTEGER
        )''')
        cur.execute(
            "INSERT OR IGNORE INTO app_state (firm_id, active_subcase_id) "
            "SELECT 1, active_subcase_id FROM app_state_legacy WHERE id = 1")
        cur.execute("DROP TABLE app_state_legacy")
    else:
        cur.execute('''CREATE TABLE IF NOT EXISTS app_state (
            firm_id          INTEGER PRIMARY KEY,
            active_subcase_id INTEGER
        )''')
    cur.execute("INSERT OR IGNORE INTO app_state (firm_id, active_subcase_id) VALUES (1, 1)")
    cur.execute("DROP TABLE IF EXISTS cases")
    conn.commit()
    return conn


def active_subcase_id(firm_id=1):
    firm_id = int(firm_id or 1)
    conn = _connect_state()
    row = conn.execute(
        "SELECT active_subcase_id FROM app_state WHERE firm_id = ?", (firm_id,)).fetchone()
    conn.close()
    subcase_id = row[0] if row and row[0] is not None else None
    if subcase_id is not None and _subcase_in_firm(subcase_id, firm_id):
        return subcase_id
    opts = list_subcase_options(firm_id=firm_id)
    return opts[0]["value"] if opts else 1


def _subcase_in_firm(subcase_id, firm_id):
    conn = _connect_cases()
    try:
        row = conn.execute(
            "SELECT m.firm_id FROM subcases s JOIN main_cases m ON m.id = s.main_case_id "
            "WHERE s.id = ?", (int(subcase_id),)).fetchone()
    finally:
        conn.close()
    return bool(row and row[0] == int(firm_id))


def save_app_state(subcase_id, firm_id=1):
    conn = _connect_state()
    conn.execute(
        "INSERT INTO app_state (firm_id, active_subcase_id) VALUES (?, ?) "
        "ON CONFLICT(firm_id) DO UPDATE SET active_subcase_id = excluded.active_subcase_id",
        (int(firm_id or 1), int(subcase_id)))
    conn.commit()
    conn.close()


def subcase_exists(subcase_id):
    conn = _connect_cases()
    row = conn.execute("SELECT COUNT(*) FROM subcases WHERE id = ?", (int(subcase_id),)).fetchone()
    conn.close()
    return bool(row[0])


def list_main_options(firm_id=None):
    conn = _connect_cases()
    sql = 'SELECT id, case_name, case_number FROM main_cases'
    params = ()
    if firm_id is not None:
        sql += ' WHERE firm_id = ?'
        params = (int(firm_id),)
    sql += ' ORDER BY id'
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [{"label": f"{name} (#{number})", "value": mid} for mid, name, number in rows]


def list_subcase_options(main_id=None, firm_id=None):
    conn = _connect_cases()
    sql = '''
        SELECT s.id, s.transferee_name, s.adversary_number, s.file_number, s.filing_date
        FROM subcases s
    '''
    params = []
    where = []
    if firm_id is not None:
        sql += ' JOIN main_cases m ON m.id = s.main_case_id'
        where.append('m.firm_id = ?')
        params.append(int(firm_id))
    if main_id is not None:
        where.append('s.main_case_id = ?')
        params.append(int(main_id))
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY s.id'
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    options = []
    for subcase_id, name, adv, file_number, filing_date in rows:
        id_label = _display_number(adv, filing_date, file_number)
        label = f"{name} ({id_label})" if id_label else name
        options.append({"label": label, "value": subcase_id})
    return options


def get_main_by_subcase(subcase_id):
    conn = _connect_cases()
    row = conn.execute('''
        SELECT m.id, m.case_name, m.case_number, m.jurisdiction, m.judge, m.petition_date,
               s.transferee_name, s.adversary_number, s.file_number, s.filing_date, m.firm_id
        FROM subcases s JOIN main_cases m ON m.id = s.main_case_id
        WHERE s.id = ?
    ''', (int(subcase_id),)).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No subcase with id {subcase_id}")
    return {
        'main_id': row[0],
        'case_name': row[1],
        'case_number': row[2],
        'jurisdiction': row[3],
        'judge': row[4],
        'petition_date': row[5],
        'transferee': row[6],
        'adversary_number': row[7],
        'file_number': row[8],
        'filing_date': row[9],
        'firm_id': row[10],
    }


def load_case_frames(subcase_id, pref_start, petition_date, firm_id=None):
    conn = _connect_cases()
    firm_clause = ''
    firm_params = ()
    if firm_id is not None:
        firm_clause = (
            ' AND EXISTS (SELECT 1 FROM subcases s JOIN main_cases m ON m.id = s.main_case_id '
            'WHERE s.id = invoice_records.subcase_id AND m.firm_id = ?)'
        )
        firm_params = (int(firm_id),)
    historical = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Payment Date" < ?' + firm_clause,
        conn, params=(int(subcase_id), pref_start) + firm_params)
    preference = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Payment Date" >= ? AND "Payment Date" <= ?' + firm_clause,
        conn, params=(int(subcase_id), pref_start, petition_date) + firm_params)
    transfers = pd.read_sql(
        'SELECT DISTINCT "Transfer Number", "Transfer Amount", "Payment Date" FROM invoice_records '
        'WHERE subcase_id = ? AND "Payment Date" >= ? AND "Payment Date" <= ?' + firm_clause + ' ORDER BY "Payment Date"',
        conn, params=(int(subcase_id), pref_start, petition_date) + firm_params)
    newvalue = pd.read_sql(
        'SELECT * FROM invoice_records WHERE subcase_id = ? AND "Unpaid" = 1' + firm_clause,
        conn, params=(int(subcase_id),) + firm_params)
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
        "SELECT ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings, ocb_metric "
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
        'ocb_metric': row[6],
    }


def save_case_settings(subcase_id, ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_settings, ocb_metric=None):
    ocb_range_json = json.dumps(ocb_range) if ocb_range is not None else None
    nv_json = json.dumps(nv_settings) if nv_settings else None
    conn = _connect_state()
    conn.execute(
        """INSERT INTO case_settings (subcase_id, ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings, ocb_metric)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(subcase_id) DO UPDATE SET
               ocb_start=excluded.ocb_start, ocb_end=excluded.ocb_end,
               ocb_step=excluded.ocb_step, ocb_range=excluded.ocb_range,
               ocb_total_flag=excluded.ocb_total_flag, nv_settings=excluded.nv_settings,
               ocb_metric=excluded.ocb_metric""",
        (int(subcase_id), int(ocb_start or 0), int(ocb_end or 100), int(ocb_step or 5),
         ocb_range_json, int(bool(ocb_total_flag)), nv_json, ocb_metric),
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


def _migrate_main_grant_names(conn):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='master_grants'").fetchone()
    if row is not None:
        conn.execute("ALTER TABLE master_grants RENAME TO main_grants")
    cols = {c[1] for c in conn.execute("PRAGMA table_info(main_grants)").fetchall()}
    if 'master_case_id' in cols:
        conn.execute("ALTER TABLE main_grants RENAME COLUMN master_case_id TO main_case_id")
    conn.commit()


def _require_firm(firm_id):
    conn = _connect_users()
    try:
        row = conn.execute("SELECT COUNT(*) FROM firms WHERE id = ?", (int(firm_id),)).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise ValueError(f"No firm with id {firm_id}")


def init_users_db():
    conn = _connect_users()
    _migrate_main_grant_names(conn)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL,
            email         TEXT,
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL,
            firm_id       INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS main_grants (
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            main_case_id    INTEGER NOT NULL,
            PRIMARY KEY (user_id, main_case_id)
        );
        CREATE TABLE IF NOT EXISTS subcase_grants (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subcase_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, subcase_id)
        );
        CREATE TABLE IF NOT EXISTS firms (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            settings   TEXT,
            created_at TEXT NOT NULL
        );
    ''')
    ucols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if 'name' not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT")
    if 'avatar_color' not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_color TEXT")
    if 'firm_id' not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN firm_id INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "INSERT OR IGNORE INTO firms (id, name, settings, created_at) "
        "VALUES (1, 'Default Firm LLP', '{}', ?)",
        (datetime.now(timezone.utc).isoformat(),))
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


def create_user(username, password_hash, role='user', email=None, firm_id=1):
    _require_firm(firm_id)
    conn = _connect_users()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, email, active, created_at, firm_id) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (username, password_hash, role, email, datetime.now(timezone.utc).isoformat(), int(firm_id)),
    )
    conn.commit()
    conn.close()


def list_users(firm_id=None):
    conn = _connect_users()
    sql = "SELECT id, username, role, email, active, created_at, firm_id FROM users"
    params = ()
    if firm_id is not None:
        sql += " WHERE firm_id = ?"
        params = (int(firm_id),)
    sql += " ORDER BY username"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(zip(("id", "username", "role", "email", "active", "created_at", "firm_id"), r)) for r in rows]


def list_firms():
    conn = _connect_users()
    rows = conn.execute("SELECT id, name, settings, created_at FROM firms ORDER BY id").fetchall()
    conn.close()
    return [
        {'id': r[0], 'name': r[1], 'settings': json.loads(r[2]) if r[2] else {}, 'created_at': r[3]}
        for r in rows
    ]


def get_firm(firm_id):
    conn = _connect_users()
    row = conn.execute(
        "SELECT id, name, settings, created_at FROM firms WHERE id = ?", (int(firm_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No firm with id {firm_id}")
    return {
        'id': row[0],
        'name': row[1],
        'settings': json.loads(row[2]) if row[2] else {},
        'created_at': row[3],
    }


def update_firm_settings(firm_id, name=None, settings=None):
    firm = get_firm(int(firm_id))
    n_name = (name or '').strip() or firm['name']
    n_settings = settings if settings is not None else firm['settings']
    conn = _connect_users()
    try:
        conn.execute(
            "UPDATE firms SET name = ?, settings = ? WHERE id = ?",
            (n_name, json.dumps(n_settings), int(firm_id)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A firm named '{n_name}' already exists.")
    finally:
        conn.close()


def create_firm(name=None):
    conn = _connect_users()
    n_name = (name or '').strip()
    try:
        if n_name:
            cur = conn.execute(
                "INSERT INTO firms (name, settings, created_at) VALUES (?, ?, ?)",
                (n_name, '{}', datetime.now(timezone.utc).isoformat()),
            )
            new_id = cur.lastrowid
        else:
            cur = conn.execute(
                "INSERT INTO firms (name, settings, created_at) VALUES (?, ?, ?)",
                (f'__new_{uuid.uuid4().hex}', '{}', datetime.now(timezone.utc).isoformat()),
            )
            new_id = cur.lastrowid
            conn.execute("UPDATE firms SET name = ? WHERE id = ?", (f"New Firm {new_id}", new_id))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A firm named '{n_name}' already exists.")
    finally:
        conn.close()
    return new_id


def delete_firm(firm_id):
    """Delete a firm and all of its data across all databases.

    Raises ValueError if the firm is not found or is the last remaining firm.
    Returns the firm name that was deleted (for status messages).
    """
    firm_id = int(firm_id)
    uc = _connect_users()
    try:
        row = uc.execute("SELECT name FROM firms WHERE id = ?", (firm_id,)).fetchone()
        if not row:
            raise ValueError("Firm not found.")
        old_name = row[0]
        total = uc.execute("SELECT COUNT(*) FROM firms").fetchone()[0]
        if total <= 1:
            raise ValueError("Cannot delete the last firm.")
        cc = _connect_cases()
        sc = _connect_state()
        try:
            main_ids = [r[0] for r in cc.execute(
                "SELECT id FROM main_cases WHERE firm_id = ?", (firm_id,)
            ).fetchall()]
            sub_ids = [r[0] for r in cc.execute(
                "SELECT s.id FROM subcases s "
                "JOIN main_cases m ON m.id = s.main_case_id "
                "WHERE m.firm_id = ?", (firm_id,)
            ).fetchall()]

            if sub_ids:
                cc.executemany(
                    "DELETE FROM invoice_records WHERE subcase_id = ?",
                    [(i,) for i in sub_ids],
                )
            user_ids = [r[0] for r in uc.execute(
                "SELECT id FROM users WHERE firm_id = ?", (firm_id,)
            ).fetchall()]
            if user_ids:
                uc.executemany(
                    "DELETE FROM main_grants WHERE user_id = ?",
                    [(u,) for u in user_ids],
                )
                uc.executemany(
                    "DELETE FROM subcase_grants WHERE user_id = ?",
                    [(u,) for u in user_ids],
                )
            if main_ids:
                uc.executemany(
                    "DELETE FROM main_grants WHERE main_case_id = ?",
                    [(m,) for m in main_ids],
                )
            if sub_ids:
                uc.executemany(
                    "DELETE FROM subcase_grants WHERE subcase_id = ?",
                    [(s,) for s in sub_ids],
                )
            uc.execute("DELETE FROM users WHERE firm_id = ?", (firm_id,))
            if main_ids:
                cc.executemany(
                    "DELETE FROM subcases WHERE main_case_id = ?",
                    [(m,) for m in main_ids],
                )
                cc.executemany(
                    "DELETE FROM main_cases WHERE id = ?",
                    [(m,) for m in main_ids],
                )
            sc.execute("DELETE FROM app_state WHERE firm_id = ?", (firm_id,))
            uc.execute("DELETE FROM firms WHERE id = ?", (firm_id,))

            cc.commit()
            sc.commit()
            uc.commit()
        finally:
            cc.close()
            sc.close()
    finally:
        uc.close()
    return old_name


def set_user_role(user_id, role):
    conn = _connect_users()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, int(user_id)))
    conn.commit()
    conn.close()


def update_user_profile(user_id, name=None, email=None, avatar_color=None):
    conn = _connect_users()
    conn.execute(
        "UPDATE users SET name = ?, email = ?, avatar_color = ? WHERE id = ?",
        (name, email, avatar_color, int(user_id)),
    )
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
    conn.execute("DELETE FROM main_grants WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM subcase_grants WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    conn.commit()
    conn.close()


def get_main_by_id(main_id):
    conn = _connect_cases()
    row = conn.execute(
        "SELECT id, case_name, case_number, jurisdiction, judge, petition_date, created_at, firm_id "
        "FROM main_cases WHERE id = ?", (int(main_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No main case with id {main_id}")
    return {
        'id': row[0],
        'case_name': row[1],
        'case_number': row[2],
        'jurisdiction': row[3],
        'judge': row[4],
        'petition_date': row[5],
        'created_at': row[6],
        'firm_id': row[7],
    }


def _validated_main_fields(case_name, case_number, jurisdiction, judge, petition_date):
    from datetime import datetime
    name = (case_name or '').strip()
    number = (case_number or '').strip()
    petition = (petition_date or '').strip()
    jurisdiction = (jurisdiction or '').strip()
    if not name or not number or not jurisdiction or not petition:
        raise ValueError('Case Name, Case Number, Jurisdiction, and Petition Date are required.')
    try:
        datetime.strptime(petition, '%Y-%m-%d')
    except ValueError:
        raise ValueError('Petition Date must be YYYY-MM-DD.')
    judge = (judge or '').strip() or None
    jurisdiction = jurisdiction or None
    return name, number, jurisdiction, judge, petition


def create_main_case(case_name, case_number, jurisdiction, judge, petition_date, firm_id=1):
    name, number, jurisdiction, judge, petition = _validated_main_fields(
        case_name, case_number, jurisdiction, judge, petition_date)
    _require_firm(firm_id)
    conn = _connect_cases()
    try:
        cur = conn.execute(
            "INSERT INTO main_cases (case_name, case_number, jurisdiction, judge, petition_date, created_at, firm_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, number, jurisdiction, judge, petition,
             datetime.now(timezone.utc).isoformat(), int(firm_id)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A main case named '{name}' already exists.")
    finally:
        conn.close()
    return cur.lastrowid


def update_main_case(main_id, case_name, case_number, jurisdiction, judge, petition_date):
    name, number, jurisdiction, judge, petition = _validated_main_fields(
        case_name, case_number, jurisdiction, judge, petition_date)
    conn = _connect_cases()
    try:
        conn.execute(
            "UPDATE main_cases SET case_name=?, case_number=?, jurisdiction=?, judge=?, petition_date=? "
            "WHERE id=?",
            (name, number, jurisdiction, judge, petition, int(main_id)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A main case named '{name}' already exists.")
    finally:
        conn.close()


def grant_main(user_id, main_case_id):
    conn = _connect_users()
    conn.execute(
        "INSERT OR IGNORE INTO main_grants (user_id, main_case_id) VALUES (?, ?)",
        (int(user_id), int(main_case_id)),
    )
    conn.commit()
    conn.close()


def revoke_main(user_id, main_case_id):
    conn = _connect_users()
    conn.execute(
        "DELETE FROM main_grants WHERE user_id = ? AND main_case_id = ?",
        (int(user_id), int(main_case_id)),
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


def main_grants_for(user_id):
    conn = _connect_users()
    rows = conn.execute(
        "SELECT main_case_id FROM main_grants WHERE user_id = ?", (int(user_id),)
    ).fetchall()
    conn.close()
    return [{'main_case_id': r[0]} for r in rows]


def subcase_grants_for(user_id):
    conn = _connect_users()
    rows = conn.execute(
        "SELECT subcase_id FROM subcase_grants WHERE user_id = ?", (int(user_id),)
    ).fetchall()
    conn.close()
    return [{'subcase_id': r[0]} for r in rows]


def list_all_grants(user_ids=None):
    conn = _connect_users()
    sql = ('SELECT * FROM ('
           'SELECT mg.user_id, u.username, mg.main_case_id, NULL AS subcase_id, "main" AS level '
           'FROM main_grants mg JOIN users u ON u.id = mg.user_id '
           'UNION ALL '
           'SELECT sg.user_id, u.username, NULL, sg.subcase_id, "subcase" AS level '
           'FROM subcase_grants sg JOIN users u ON u.id = sg.user_id'
           ')')
    params = ()
    if user_ids is not None:
        ids = [int(i) for i in user_ids]
        if not ids:
            conn.close()
            return []
        sql += f' WHERE user_id IN ({", ".join("?" for _ in ids)})'
        params = tuple(ids)
    sql += ' ORDER BY username, level, COALESCE(main_case_id, subcase_id)'
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {'user_id': r[0], 'username': r[1], 'main_case_id': r[2], 'subcase_id': r[3], 'level': r[4]}
        for r in rows
    ]