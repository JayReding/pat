"""Case backup/restore payload helpers.

A backup is a self-contained gzip-compressed JSON document that carries
either a whole main case (plus all of its subcases and their invoice
records) or a single subcase.  It holds every table row restore needs,
including the original primary keys so restore can remap them.

Grants/permissions are intentionally excluded: they are user/firm
configuration, not case data.

Schema::

    {
        "format": "preference-analysis-tool/backup",
        "version": 1,
        "created_at": "2026-09-19T...",
        "created_by": "username",
        "firm_id": 1,
        "scope": "main" | "subcase",
        "main_cases": [...],
        "subcases": [...],
        "invoice_records": [...],
        "case_settings": [...],
    }
"""

import gzip
import json
import re
from datetime import datetime, timezone

import store

BACKUP_FORMAT = "preference-analysis-tool/backup"
BACKUP_VERSION = 1
BACKUP_EXT = ".json.gz"

_TABLE_KEYS = ("main_cases", "subcases", "invoice_records", "case_settings")
_SCOPES = ("main", "subcase")


def build_backup(scope, target_id, username=None):
    """Fetch every row for a backup and wrap it in the backup envelope."""
    builders = {
        "main": store.build_main_case_backup,
        "subcase": store.build_subcase_backup,
    }
    try:
        builder = builders[scope]
    except KeyError:
        raise ValueError(f"Unknown backup scope: {scope!r}")
    payload = builder(int(target_id))
    main_rows = payload["main_cases"]
    payload.update({
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": username or "",
        "firm_id": main_rows[0].get("firm_id") if main_rows else None,
        "scope": scope,
    })
    return payload


def summary(backup):
    """One-line human summary of a backup payload (for status messages)."""
    counts = {key: len(backup.get(key) or []) for key in _TABLE_KEYS}
    label = ""
    if backup.get("scope") == "main" and backup.get("main_cases"):
        label = backup["main_cases"][0].get("case_name") or ""
    elif backup.get("scope") == "subcase" and backup.get("subcases"):
        label = backup["subcases"][0].get("transferee_name") or ""
    detail = (f"{counts['main_cases']} main case(s), {counts['subcases']} subcase(s), "
              f"{counts['invoice_records']} invoice row(s), "
              f"{counts['case_settings']} settings row(s)")
    return f"{label} — {detail}" if label else detail


def backup_bytes(backup):
    """Serialize a backup payload to gzip-compressed JSON bytes."""
    return gzip.compress(json.dumps(backup).encode("utf-8"))


def load_backup(data):
    """Parse backup bytes back into a payload dict.

    Accepts gzip-compressed JSON (as produced by backup_bytes) or plain
    JSON.  Raises ValueError for anything else.
    """
    try:
        raw = gzip.decompress(bytes(data))
    except OSError:
        raw = bytes(data)
    try:
        backup = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Not a valid backup file: {exc}")
    if not isinstance(backup, dict) or backup.get("format") != BACKUP_FORMAT:
        raise ValueError("Not a valid backup file: unrecognized format.")
    if backup.get("version") != BACKUP_VERSION:
        raise ValueError(f"Unsupported backup version: {backup.get('version')!r}")
    for key in _TABLE_KEYS:
        if key not in backup or not isinstance(backup[key], list):
            raise ValueError(f"Not a valid backup file: missing '{key}'.")
    if backup.get("scope") not in _SCOPES:
        raise ValueError("Not a valid backup file: bad scope.")
    return backup


def safe_label(text, fallback="backup"):
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", (text or "").strip()).strip("_")
    return label or fallback


def backup_filename(scope, label):
    """Build the download filename, e.g. PAT_backup_Subcase_Bobs-Widgets_2026-09-19.json.gz."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kind = "MainCase" if scope == "main" else "Subcase"
    return f"PAT_backup_{kind}_{safe_label(label)}_{stamp}{BACKUP_EXT}"
