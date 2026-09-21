import re
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
            client_name    TEXT,
            client_contact TEXT,
            client_address TEXT,
            client_address2 TEXT,
            client_city    TEXT,
            client_state   TEXT,
            client_zip     TEXT,
            client_phone   TEXT,
            client_email   TEXT,
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
            case_caption      TEXT,
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
    if 'case_caption' not in cols:
        conn.execute("ALTER TABLE subcases ADD COLUMN case_caption TEXT")
    _backfill_case_captions(conn)
    mcols = {row[1] for row in conn.execute("PRAGMA table_info(main_cases)").fetchall()}
    for mcol in ("client_name", "client_contact", "client_address", "client_address2",
                 "client_city", "client_state", "client_zip", "client_phone", "client_email"):
        if mcol not in mcols:
            conn.execute(f"ALTER TABLE main_cases ADD COLUMN {mcol} TEXT")
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


def _make_case_caption(client_name, transferee_name):
    """Default case caption: '<client> v. <transferee>', or just the
    transferee name when the client name is blank."""
    client = (client_name or "").strip()
    transferee = (transferee_name or "").strip()
    if not client:
        return transferee
    return f"{client} v. {transferee}"


def _backfill_case_captions(conn):
    """Fill NULL/blank case_caption rows with the default formula."""
    rows = conn.execute(
        "SELECT s.id, m.client_name, s.transferee_name "
        "FROM subcases s JOIN main_cases m ON m.id = s.main_case_id "
        "WHERE s.case_caption IS NULL OR s.case_caption = ''").fetchall()
    for sub_id, client_name, transferee_name in rows:
        conn.execute("UPDATE subcases SET case_caption = ? WHERE id = ?",
                     (_make_case_caption(client_name, transferee_name), int(sub_id)))


def get_subcase(subcase_id):
    conn = _connect_cases()
    row = conn.execute(
        "SELECT s.id, s.main_case_id, s.transferee_name, s.adversary_number, s.file_number, "
        "s.filing_date, s.meta, m.firm_id, s.case_caption "
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
        'case_caption': row[8] or '',
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


def create_subcase(main_case_id, transferee_name, firm_id=1):
    transferee_name = (transferee_name or '').strip()
    if not transferee_name:
        raise ValueError('Transferee name is required.')
    _require_firm(firm_id)
    conn = _connect_cases()
    try:
        client_row = conn.execute(
            "SELECT client_name FROM main_cases WHERE id = ?", (int(main_case_id),)
        ).fetchone()
        if client_row is None:
            raise ValueError(f"No main case with id {main_case_id}")
        cur = conn.execute(
            "INSERT INTO subcases (main_case_id, transferee_name, adversary_number, "
            "created_at, meta, firm_id, case_caption) VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (int(main_case_id), transferee_name, datetime.now(timezone.utc).isoformat(),
              '{}', int(firm_id), _make_case_caption(client_row[0], transferee_name)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A subcase named '{transferee_name}' already exists.")
    finally:
        conn.close()
    return cur.lastrowid


def _pick_next_subcase(firm_id, main_case_id, excluding):
    """Smallest remaining subcase id for the same main case, else the firm, else None."""
    conn = _connect_cases()
    try:
        for sql in (
            "SELECT id FROM subcases WHERE main_case_id = ? AND id != ? ORDER BY id LIMIT 1",
            "SELECT id FROM subcases WHERE firm_id = ? AND id != ? ORDER BY id LIMIT 1",
        ):
            if 'firm_id' in sql:
                row = conn.execute(sql, (int(firm_id), int(excluding))).fetchone()
            else:
                row = conn.execute(sql, (int(main_case_id), int(excluding))).fetchone()
            if row:
                return row[0]
        return None
    finally:
        conn.close()


def delete_subcase(subcase_id):
    """Delete a subcase and every row that references it.

    Removes invoice records, case_settings, subcase_grants, and the subcase
    itself. If the firm's ``app_state.active_subcase_id`` pointed at this
    subcase, repoint it at the next subcase of the same main case (or the
    firm), or NULL if none remain.

    Returns a display label for the deleted subcase (for status messages).
    """
    subcase_id = int(subcase_id)
    cc = _connect_cases()
    try:
        row = cc.execute(
            "SELECT s.id, s.main_case_id, s.transferee_name, s.file_number, s.adversary_number, "
            "s.filing_date, m.firm_id "
            "FROM subcases s JOIN main_cases m ON m.id = s.main_case_id WHERE s.id = ?",
            (subcase_id,)).fetchone()
        if row is None:
            raise ValueError(f"No subcase with id {subcase_id}")
        _, main_case_id, name, file_number, adversary_number, filing_date, firm_id = row
        display = _display_number(adversary_number, filing_date, file_number)
        label = f"{name} ({display})" if display else name

        cc.execute("DELETE FROM invoice_records WHERE subcase_id = ?", (subcase_id,))
        cc.execute("DELETE FROM subcases WHERE id = ?", (subcase_id,))
        cc.commit()
    finally:
        cc.close()

    uc = _connect_users()
    uc.execute("DELETE FROM subcase_grants WHERE subcase_id = ?", (subcase_id,))
    uc.commit()
    uc.close()

    sc = _connect_state()
    try:
        sc.execute("DELETE FROM case_settings WHERE subcase_id = ?", (subcase_id,))
        next_id = _pick_next_subcase(firm_id, main_case_id, subcase_id)
        cur = sc.execute(
            "UPDATE app_state SET active_subcase_id = ? WHERE active_subcase_id = ?",
            (next_id, subcase_id))
        if cur.rowcount == 0:
            sc.execute(
                "INSERT INTO app_state (firm_id, active_subcase_id) VALUES (?, ?) "
                "ON CONFLICT(firm_id) DO UPDATE SET active_subcase_id = excluded.active_subcase_id",
                (int(firm_id), next_id))
        sc.commit()
    finally:
        sc.close()
    return label


def update_subcase_metadata(subcase_id, file_number=None, filing_date=None, transferee_name=None,
                            case_caption=None, **meta_fields):
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

    transferee = (transferee_name or '').strip() if transferee_name else None
    if transferee_name is not None and not transferee:
        raise ValueError('Transferee name cannot be blank.')

    conn = _connect_cases()
    try:
        dup = conn.execute(
            "SELECT id FROM subcases WHERE file_number = ? AND id != ?",
            (file_number, int(subcase_id)),
        ).fetchone()
        if dup:
            raise ValueError(f"A subcase with file number '{file_number}' already exists.")
        current = conn.execute(
            "SELECT s.transferee_name, s.case_caption, m.client_name "
            "FROM subcases s JOIN main_cases m ON m.id = s.main_case_id "
            "WHERE s.id = ?", (int(subcase_id),)).fetchone()
        set_parts = ["file_number = ?", "filing_date = ?", "meta = ?"]
        vals = [file_number, filing_date, json.dumps(meta_fields)]
        if transferee is not None:
            set_parts.append("transferee_name = ?")
            vals.append(transferee)
        if current is not None:
            old_transferee, old_caption, client_name = current
            new_transferee = transferee if transferee is not None else (old_transferee or "")
            old_formula = _make_case_caption(client_name, old_transferee)
            new_formula = _make_case_caption(client_name, new_transferee)
            caption_in = (case_caption or "").strip() if case_caption is not None else None
            if caption_in is not None:
                if not caption_in or caption_in == old_formula:
                    resolved = new_formula
                else:
                    resolved = caption_in
                set_parts.append("case_caption = ?")
                vals.append(resolved)
            elif transferee is not None and (old_caption or "") == old_formula:
                set_parts.append("case_caption = ?")
                vals.append(new_formula)
        vals.append(int(subcase_id))
        conn.execute(
            f"UPDATE subcases SET {', '.join(set_parts)} WHERE id = ?",
            vals,
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
    cur.execute('''CREATE TABLE IF NOT EXISTS global_settings (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        smtp_host     TEXT,
        smtp_port     INTEGER,
        smtp_username TEXT,
        smtp_password TEXT,
        smtp_from     TEXT,
        smtp_use_auth INTEGER NOT NULL DEFAULT 1,
        updated_at    TEXT
    )''')
    gcols = {row[1] for row in cur.execute("PRAGMA table_info(global_settings)").fetchall()}
    if 'smtp_use_auth' not in gcols:
        cur.execute("ALTER TABLE global_settings ADD COLUMN smtp_use_auth INTEGER NOT NULL DEFAULT 1")
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


_EMAIL_FIELDS = ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_use_auth")


def get_email_settings():
    conn = _connect_state()
    row = conn.execute(
        "SELECT smtp_host, smtp_port, smtp_username, smtp_password, smtp_from, smtp_use_auth "
        "FROM global_settings WHERE id = 1"
    ).fetchone()
    conn.close()
    if row is None:
        return {"smtp_host": "", "smtp_port": None, "smtp_username": "",
                "smtp_password": "", "smtp_from": "", "smtp_use_auth": True}
    data = dict(zip(_EMAIL_FIELDS, row))
    data["smtp_host"] = data.get("smtp_host") or ""
    data["smtp_username"] = data.get("smtp_username") or ""
    data["smtp_password"] = data.get("smtp_password") or ""
    data["smtp_from"] = data.get("smtp_from") or ""
    use_auth = data.get("smtp_use_auth")
    data["smtp_use_auth"] = True if use_auth is None else bool(use_auth)
    return data


def _validated_email_settings(smtp_host, smtp_port, smtp_username, smtp_from, existing_password, password):
    host = (smtp_host or "").strip()
    if not host:
        raise ValueError("SMTP server (FQDN or IP) is required.")
    try:
        port = int(smtp_port)
    except (TypeError, ValueError):
        raise ValueError("SMTP port must be a whole number.")
    if not (1 <= port <= 65535):
        raise ValueError("SMTP port must be between 1 and 65535.")
    user = (smtp_username or "").strip()
    frm = (smtp_from or "").strip()
    if frm and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", frm):
        raise ValueError("From address does not look like a valid email address.")
    stored_password = password if (password or "").strip() else (existing_password or "")
    return host, port, user, stored_password, frm


def save_email_settings(smtp_host, smtp_port, smtp_username, smtp_password, smtp_from, smtp_use_auth=None):
    existing = get_email_settings()
    host, port, user, password, frm = _validated_email_settings(
        smtp_host, smtp_port, smtp_username, smtp_from,
        existing["smtp_password"], smtp_password)
    use_auth = existing["smtp_use_auth"] if smtp_use_auth is None else bool(smtp_use_auth)
    conn = _connect_state()
    conn.execute(
        """INSERT INTO global_settings (id, smtp_host, smtp_port, smtp_username, smtp_password, smtp_from, smtp_use_auth, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               smtp_host=excluded.smtp_host, smtp_port=excluded.smtp_port,
               smtp_username=excluded.smtp_username, smtp_password=excluded.smtp_password,
               smtp_from=excluded.smtp_from, smtp_use_auth=excluded.smtp_use_auth,
               updated_at=excluded.updated_at""",
        (host, port, user, password, frm, 1 if use_auth else 0, datetime.now(timezone.utc).isoformat()),
    )
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


def list_visible_main_options(user_id, role, firm_id=None):
    """Main cases a user has rights to view.

    admin -> all main cases; firm_admin -> the firm's main cases; anything
    else (case_manager) -> main cases granted directly or via a subcase
    grant, scoped to the firm.
    """
    if role == 'admin':
        return list_main_options(None)
    if role == 'firm_admin':
        return list_main_options(firm_id)
    main_ids = {int(g['main_case_id']) for g in main_grants_for(user_id)}
    sub_ids = [int(s['subcase_id']) for s in subcase_grants_for(user_id)]
    if sub_ids:
        conn = _connect_cases()
        marks = ','.join('?' for _ in sub_ids)
        rows = conn.execute(
            f'SELECT DISTINCT main_case_id FROM subcases WHERE id IN ({marks})',
            tuple(sub_ids),
        ).fetchall()
        conn.close()
        main_ids |= {int(r[0]) for r in rows}
    source = list_main_options(None) if firm_id is None else list_main_options(firm_id)
    return [o for o in source if int(o['value']) in main_ids]


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


def set_user_firm(user_id, firm_id):
    _require_firm(firm_id)
    conn = _connect_users()
    conn.execute("UPDATE users SET firm_id = ? WHERE id = ?", (int(firm_id), int(user_id)))
    conn.commit()
    conn.close()


def clear_user_grants(user_id):
    conn = _connect_users()
    conn.execute("DELETE FROM main_grants WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM subcase_grants WHERE user_id = ?", (int(user_id),))
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
    cols = ["id", "case_name", "case_number", "jurisdiction", "judge", "petition_date",
            *_CLIENT_FIELDS, "created_at", "firm_id"]
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM main_cases WHERE id = ?", (int(main_id),)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No main case with id {main_id}")
    return dict(zip(cols, row))


_CLIENT_FIELDS = ("client_name", "client_contact", "client_address", "client_address2",
                  "client_city", "client_state", "client_zip", "client_phone", "client_email")


def _validated_main_fields(case_name, case_number, jurisdiction, judge, petition_date,
                           client_name=None, client_contact=None, client_address=None,
                           client_address2=None, client_city=None, client_state=None,
                           client_zip=None, client_phone=None, client_email=None):
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
    clients = {}
    for key, value in [
        ('client_name', client_name), ('client_contact', client_contact),
        ('client_address', client_address), ('client_address2', client_address2),
        ('client_city', client_city), ('client_state', client_state),
        ('client_zip', client_zip), ('client_phone', client_phone),
        ('client_email', client_email),
    ]:
        norm = (str(value) if value is not None else '').strip()
        clients[key] = norm or None
    return name, number, jurisdiction, judge, petition, clients


def create_main_case(case_name, case_number, jurisdiction, judge, petition_date, firm_id=1, **client_fields):
    name, number, jurisdiction, judge, petition, clients = _validated_main_fields(
        case_name, case_number, jurisdiction, judge, petition_date, **client_fields)
    _require_firm(firm_id)
    conn = _connect_cases()
    try:
        cols = ["case_name", "case_number", "jurisdiction", "judge", "petition_date",
                *_CLIENT_FIELDS, "created_at", "firm_id"]
        vals = [name, number, jurisdiction, judge, petition,
                *(clients[k] for k in _CLIENT_FIELDS),
                datetime.now(timezone.utc).isoformat(), int(firm_id)]
        cur = conn.execute(
            f"INSERT INTO main_cases ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            vals,
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A main case named '{name}' already exists.")
    finally:
        conn.close()
    return cur.lastrowid


def update_main_case(main_id, case_name, case_number, jurisdiction, judge, petition_date, **client_fields):
    name, number, jurisdiction, judge, petition, clients = _validated_main_fields(
        case_name, case_number, jurisdiction, judge, petition_date, **client_fields)
    conn = _connect_cases()
    try:
        old_row = conn.execute("SELECT client_name FROM main_cases WHERE id = ?",
                               (int(main_id),)).fetchone()
        old_client = ((old_row[0] or "").strip()) if old_row else ""
        new_client = (clients.get("client_name") or "").strip()
        subs = conn.execute(
            "SELECT id, transferee_name, case_caption FROM subcases WHERE main_case_id = ?",
            (int(main_id),)).fetchall()
        set_parts = ["case_name=?", "case_number=?", "jurisdiction=?", "judge=?", "petition_date=?"]
        set_parts += [f"{k}=?" for k in _CLIENT_FIELDS]
        vals = [name, number, jurisdiction, judge, petition]
        vals += [clients[k] for k in _CLIENT_FIELDS]
        vals.append(int(main_id))
        conn.execute(
            f"UPDATE main_cases SET {', '.join(set_parts)} WHERE id=?", vals)
        if new_client != old_client:
            for sub_id, transferee, caption in subs:
                if (caption or "") == _make_case_caption(old_client, transferee):
                    conn.execute(
                        "UPDATE subcases SET case_caption = ? WHERE id = ?",
                        (_make_case_caption(new_client, transferee), int(sub_id)))
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


def _rows_to_dicts(conn, sql, params=()):
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def build_main_case_backup(main_id):
    """Collect every row a main-case backup must carry (read-only).

    Includes the main_cases row, all of its subcases (incl. meta),
    their invoice records, and their per-subcase analysis settings.
    Grants/permissions are intentionally excluded.
    """
    main_id = int(main_id)
    cc = _connect_cases()
    try:
        main_rows = _rows_to_dicts(cc, "SELECT * FROM main_cases WHERE id = ?", (main_id,))
        if not main_rows:
            raise ValueError(f"No main case with id {main_id}")
        sub_rows = _rows_to_dicts(
            cc, "SELECT * FROM subcases WHERE main_case_id = ? ORDER BY id", (main_id,))
        sub_ids = [r["id"] for r in sub_rows]
        if sub_ids:
            placeholders = ", ".join("?" for _ in sub_ids)
            inv_rows = _rows_to_dicts(
                cc,
                f"SELECT * FROM invoice_records WHERE subcase_id IN ({placeholders}) "
                "ORDER BY subcase_id, id",
                tuple(sub_ids),
            )
        else:
            inv_rows = []
    finally:
        cc.close()
    sc = _connect_state()
    try:
        if sub_ids:
            placeholders = ", ".join("?" for _ in sub_ids)
            set_rows = _rows_to_dicts(
                sc,
                f"SELECT * FROM case_settings WHERE subcase_id IN ({placeholders})",
                tuple(sub_ids),
            )
        else:
            set_rows = []
    finally:
        sc.close()
    return {
        "main_cases": main_rows,
        "subcases": sub_rows,
        "invoice_records": inv_rows,
        "case_settings": set_rows,
    }


def build_subcase_backup(subcase_id):
    """Collect every row a single-subcase backup must carry (read-only).

    Includes the parent main_cases row (restore context), the subcase
    row (incl. meta), its invoice records, and its analysis settings.
    Grants/permissions are intentionally excluded.
    """
    subcase_id = int(subcase_id)
    cc = _connect_cases()
    try:
        sub_rows = _rows_to_dicts(cc, "SELECT * FROM subcases WHERE id = ?", (subcase_id,))
        if not sub_rows:
            raise ValueError(f"No subcase with id {subcase_id}")
        main_rows = _rows_to_dicts(
            cc, "SELECT * FROM main_cases WHERE id = ?", (sub_rows[0]["main_case_id"],))
        inv_rows = _rows_to_dicts(
            cc, "SELECT * FROM invoice_records WHERE subcase_id = ? ORDER BY id",
            (subcase_id,),
        )
    finally:
        cc.close()
    sc = _connect_state()
    try:
        set_rows = _rows_to_dicts(
            sc, "SELECT * FROM case_settings WHERE subcase_id = ?", (subcase_id,))
    finally:
        sc.close()
    return {
        "main_cases": main_rows,
        "subcases": sub_rows,
        "invoice_records": inv_rows,
        "case_settings": set_rows,
    }


def _table_columns(conn, table):
    return [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _insert_dict_rows(conn, table, rows, defaults=None, exclude=("id",)):
    """Insert a list of dict rows; returns new rowids in order.

    Columns come from the live table (quoted); ``exclude``d columns are
    skipped (primary keys auto-assign); ``defaults`` override every row.
    """
    skip = set(exclude or ())
    cols = [c for c in _table_columns(conn, table) if c not in skip]
    quoted = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    new_ids = []
    for row in rows:
        merged = dict(row)
        if defaults:
            merged.update(defaults)
        cur = conn.execute(
            f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
            [merged.get(c) for c in cols],
        )
        new_ids.append(cur.lastrowid)
    return new_ids


def _existing_file_numbers(conn, exclude_subcase_id=None):
    sql = "SELECT file_number FROM subcases WHERE file_number IS NOT NULL AND file_number != ''"
    params = ()
    if exclude_subcase_id is not None:
        sql += " AND id != ?"
        params = (int(exclude_subcase_id),)
    return {r[0] for r in conn.execute(sql, params).fetchall()}


def _sanitize_file_numbers(conn, rows, exclude_subcase_id=None):
    """Null out backup file_numbers that collide with live ones.

    Returns the rows (copies) with colliding file_numbers cleared; call
    ensure_file_numbers() afterwards to back-fill them.
    """
    taken = _existing_file_numbers(conn, exclude_subcase_id)
    out = []
    for row in rows:
        row = dict(row)
        fn = (row.get("file_number") or "").strip()
        if fn and fn in taken:
            row["file_number"] = None
        elif fn:
            taken.add(fn)
        out.append(row)
    return out


def _remap_child_rows(rows, sub_id_map):
    """Rewrite subcase_id on invoice/settings rows; drop orphans."""
    out = []
    for row in rows:
        new_sid = sub_id_map.get(row.get("subcase_id"))
        if new_sid is None:
            continue
        row = dict(row)
        row["subcase_id"] = new_sid
        out.append(row)
    return out


def _check_child_rows(invs, settings):
    for row in list(invs) + list(settings):
        if not isinstance(row, dict) or "subcase_id" not in row:
            raise ValueError("Backup rows are malformed.")


def restore_main_case(payload, mode, firm_id=None, target_main_id=None):
    """Write a main-case backup back into the database.

    mode 'create' inserts a brand-new main case owned by firm_id with all
    of the backup's subcases, invoice records, and settings (fresh ids).
    mode 'overwrite' keeps the target main case's own details but replaces
    its subcases, invoice records, and settings with the backup's rows.

    Returns a summary dict with ids, label, and row counts.
    """
    mains = list(payload.get("main_cases") or [])
    if len(mains) != 1:
        raise ValueError("Backup must contain exactly one main case.")
    if mode not in ("create", "overwrite"):
        raise ValueError(f"Unknown restore mode: {mode!r}")
    subs = [dict(r) for r in (payload.get("subcases") or [])]
    invs = [dict(r) for r in (payload.get("invoice_records") or [])]
    settings = [dict(r) for r in (payload.get("case_settings") or [])]
    _check_child_rows(invs, settings)
    label = (mains[0].get("case_name") or "").strip() or "main case"

    cc = _connect_cases()
    try:
        if mode == "create":
            if firm_id is None:
                raise ValueError("Select a firm to restore into.")
            try:
                main_id = _insert_dict_rows(
                    cc, "main_cases", [mains[0]],
                    defaults={"firm_id": int(firm_id)})[0]
            except sqlite3.IntegrityError:
                cc.rollback()
                raise ValueError(f"A main case named '{label}' already exists.")
            keep_firm = int(firm_id)
        else:
            if target_main_id is None:
                raise ValueError("Select a main case to overwrite.")
            target = cc.execute(
                "SELECT id, firm_id FROM main_cases WHERE id = ?",
                (int(target_main_id),)).fetchone()
            if target is None:
                raise ValueError(f"No main case with id {target_main_id}")
            main_id = int(target[0])
            keep_firm = int(target[1])
            old_sub_ids = [r[0] for r in cc.execute(
                "SELECT id FROM subcases WHERE main_case_id = ?", (main_id,)).fetchall()]
            if old_sub_ids:
                cc.executemany("DELETE FROM invoice_records WHERE subcase_id = ?",
                               [(i,) for i in old_sub_ids])
                cc.executemany("DELETE FROM subcases WHERE id = ?",
                               [(i,) for i in old_sub_ids])
            sc0 = _connect_state()
            try:
                if old_sub_ids:
                    sc0.executemany("DELETE FROM case_settings WHERE subcase_id = ?",
                                    [(i,) for i in old_sub_ids])
                sc0.commit()
            finally:
                sc0.close()

        subs = _sanitize_file_numbers(cc, subs)
        sub_id_map = {}
        try:
            for row in subs:
                new_id = _insert_dict_rows(
                    cc, "subcases", [row],
                    defaults={"main_case_id": main_id, "firm_id": keep_firm})[0]
                sub_id_map[int(row["id"])] = new_id
        except sqlite3.IntegrityError:
            cc.rollback()
            raise ValueError(
                "A subcase with the same adversary number already exists "
                "in the target main case.")
        mapped_invs = _remap_child_rows(invs, sub_id_map)
        if mapped_invs:
            _insert_dict_rows(cc, "invoice_records", mapped_invs,
                              defaults={"firm_id": keep_firm})
        _backfill_case_captions(cc)
        cc.commit()
    except Exception:
        try:
            cc.rollback()
        except Exception:
            pass
        raise
    finally:
        cc.close()

    mapped_settings = _remap_child_rows(settings, sub_id_map)
    sc = _connect_state()
    try:
        if mapped_settings:
            _insert_dict_rows(sc, "case_settings", mapped_settings)
        sc.commit()
    finally:
        sc.close()
    ensure_file_numbers()

    return {
        "mode": mode, "scope": "main", "label": label,
        "main_case_id": main_id,
        "subcase_ids": [sub_id_map[int(r["id"])] for r in subs
                        if int(r["id"]) in sub_id_map],
        "counts": {"subcases": len(sub_id_map),
                   "invoice_records": len(mapped_invs),
                   "case_settings": len(mapped_settings)},
    }


def restore_subcase(payload, mode, firm_id=None, target_main_id=None,
                    target_subcase_id=None):
    """Write a single-subcase backup back into the database.

    mode 'create' attaches a new subcase (with the backup's invoices and
    settings) under target_main_id.  mode 'overwrite' keeps the target
    subcase's identity (id, main case, firm, created_at) but replaces its
    details, invoice records, and settings with the backup's rows.

    Returns a summary dict with ids, label, and row counts.
    """
    subs = list(payload.get("subcases") or [])
    if len(subs) != 1:
        raise ValueError("Subcase backup must contain exactly one subcase.")
    if mode not in ("create", "overwrite"):
        raise ValueError(f"Unknown restore mode: {mode!r}")
    sub = dict(subs[0])
    invs = [dict(r) for r in (payload.get("invoice_records") or [])]
    settings = [dict(r) for r in (payload.get("case_settings") or [])]
    _check_child_rows(invs, settings)
    label = (sub.get("transferee_name") or "").strip() or "subcase"

    cc = _connect_cases()
    try:
        if mode == "create":
            if target_main_id is None:
                raise ValueError("Select a main case to attach the restored subcase to.")
            parent = cc.execute(
                "SELECT id, firm_id FROM main_cases WHERE id = ?",
                (int(target_main_id),)).fetchone()
            if parent is None:
                raise ValueError(f"No main case with id {target_main_id}")
            main_id = int(parent[0])
            keep_firm = int(parent[1]) if firm_id is None else int(firm_id)
            new_sub_id = _insert_dict_rows(
                cc, "subcases",
                _sanitize_file_numbers(cc, [sub]),
                defaults={"main_case_id": main_id, "firm_id": keep_firm})[0]
        else:
            if target_subcase_id is None:
                raise ValueError("Select a subcase to overwrite.")
            target = cc.execute(
                "SELECT id, main_case_id, firm_id FROM subcases WHERE id = ?",
                (int(target_subcase_id),)).fetchone()
            if target is None:
                raise ValueError(f"No subcase with id {target_subcase_id}")
            new_sub_id = int(target[0])
            main_id = int(target[1])
            keep_firm = int(target[2])
            cc.execute("DELETE FROM invoice_records WHERE subcase_id = ?",
                       (new_sub_id,))
            sc0 = _connect_state()
            try:
                sc0.execute("DELETE FROM case_settings WHERE subcase_id = ?",
                            (new_sub_id,))
                sc0.commit()
            finally:
                sc0.close()
            incoming_fn = (sub.get("file_number") or "").strip()
            taken = _existing_file_numbers(cc, exclude_subcase_id=new_sub_id)
            file_number = incoming_fn if incoming_fn and incoming_fn not in taken else None
            parent_client = cc.execute(
                "SELECT client_name FROM main_cases WHERE id = ?", (main_id,)).fetchone()
            resolved_caption = ((sub.get("case_caption") or "").strip()
                                or _make_case_caption(
                                    parent_client[0] if parent_client else None,
                                    sub.get("transferee_name")))
            try:
                cc.execute(
                    "UPDATE subcases SET transferee_name = ?, adversary_number = ?, "
                    "filing_date = ?, file_number = ?, meta = ?, case_caption = ? WHERE id = ?",
                    (sub.get("transferee_name"), sub.get("adversary_number"),
                      sub.get("filing_date"), file_number, sub.get("meta"),
                      resolved_caption, new_sub_id),
                )
            except sqlite3.IntegrityError:
                cc.rollback()
                raise ValueError(
                    "A subcase with the same adversary number already exists "
                    "in the target main case.")
        sub_id_map = {int(sub["id"]): new_sub_id}
        mapped_invs = _remap_child_rows(invs, sub_id_map)
        if mapped_invs:
            _insert_dict_rows(cc, "invoice_records", mapped_invs,
                              defaults={"firm_id": keep_firm})
        _backfill_case_captions(cc)
        cc.commit()
    except sqlite3.IntegrityError:
        try:
            cc.rollback()
        except Exception:
            pass
        raise ValueError(
            "A subcase with the same adversary number already exists "
            "in the target main case.")
    except Exception:
        try:
            cc.rollback()
        except Exception:
            pass
        raise
    finally:
        cc.close()

    mapped_settings = _remap_child_rows(settings, sub_id_map)
    sc = _connect_state()
    try:
        if mapped_settings:
            _insert_dict_rows(sc, "case_settings", mapped_settings)
        sc.commit()
    finally:
        sc.close()
    ensure_file_numbers()

    return {
        "mode": mode, "scope": "subcase", "label": label,
        "main_case_id": main_id,
        "subcase_ids": [new_sub_id],
        "counts": {"subcases": 1,
                   "invoice_records": len(mapped_invs),
                   "case_settings": len(mapped_settings)},
    }


_INVOICE_IMPORT_COLUMNS = (
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
)


def count_subcase_invoices(subcase_id):
    conn = _connect_cases()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM invoice_records WHERE subcase_id = ?",
            (int(subcase_id),)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def import_subcase_invoices(subcase_id, records, mode):
    """Insert validated invoice records into an existing subcase.

    records: list of dicts keyed by _INVOICE_IMPORT_COLUMNS (as produced by
    data_import.validate_import_rows).  mode 'append' adds rows; mode
    'replace' deletes the subcase's existing invoice records first.

    Returns {"imported": N, "deleted": M}.  Raises ValueError for bad mode,
    unknown subcase, or empty records.
    """
    if mode not in ("append", "replace"):
        raise ValueError(f"Unknown import mode: {mode!r}")
    records = list(records or [])
    if not records:
        raise ValueError("No invoice rows to import.")
    subcase_id = int(subcase_id)
    conn = _connect_cases()
    try:
        parent = conn.execute(
            "SELECT m.firm_id FROM subcases s "
            "JOIN main_cases m ON m.id = s.main_case_id WHERE s.id = ?",
            (subcase_id,)).fetchone()
        if parent is None:
            raise ValueError(f"No subcase with id {subcase_id}")
        firm_id = int(parent[0])
        deleted = 0
        if mode == "replace":
            cur = conn.execute(
                "DELETE FROM invoice_records WHERE subcase_id = ?",
                (subcase_id,))
            deleted = cur.rowcount or 0
        cols = list(_INVOICE_IMPORT_COLUMNS)
        quoted = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        for record in records:
            conn.execute(
                f"INSERT INTO invoice_records "
                f"(subcase_id, firm_id, {quoted}) "
                f"VALUES (?, ?, {placeholders})",
                (subcase_id, firm_id, *[record.get(c) for c in cols]),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return {"imported": len(records), "deleted": deleted}
