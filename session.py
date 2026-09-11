import threading
import uuid
from flask import session as flask_session

import state

# Per-session CaseState instances keyed by flask session id; a single browser
# session owns its own working state so concurrent users never clobber each other.
_SESSIONS = {}
_LOCK = threading.Lock()
_LOADER = None


def set_loader(fn):
    """Register a callable that loads the default case into a fresh CaseState.

    app.py registers ``lambda st: load_case(st, store.active_subcase_id())`` so
    that any callback which touches state before the boot callback has lazy,
    correct data to work with.
    """
    global _LOADER
    _LOADER = fn


def get_state():
    """Return the CaseState for the current flask session, loading it on first use."""
    sid = flask_session.get('_sid')
    if sid is None:
        sid = str(uuid.uuid4())
        flask_session['_sid'] = sid

    with _LOCK:
        st = _SESSIONS.get(sid)
        if st is None:
            st = state.CaseState()
            _SESSIONS[sid] = st
        if not st.loaded and _LOADER is not None:
            _LOADER(st)
    return st


def reset_state():
    """Drop the current session's working state (e.g. on logout)."""
    sid = flask_session.get('_sid')
    with _LOCK:
        if sid and sid in _SESSIONS:
            del _SESSIONS[sid]