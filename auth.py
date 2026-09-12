import os
import secrets
import sys
import functools

from flask import render_template_string, request, redirect
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

import dash_bootstrap_components as dbc

import store

ROLES = ('admin', 'case_manager', 'user')

login_manager = LoginManager()
login_manager.login_view = 'login'


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row['id'])
        self.username = row['username']
        self.password_hash = row['password_hash']
        self.role = row['role']
        self.email = row.get('email')
        self.active = bool(row.get('active', 1))

    @property
    def is_active(self):
        return self.active


@login_manager.user_loader
def _load_user(user_id):
    row = store.get_user_by_id(user_id)
    return User(row) if row else None


def init_login(server):
    login_manager.init_app(server)
    server.secret_key = os.environ.get('PAT_SECRET_KEY') or _dev_secret()

    server.add_url_rule('/login', 'login', login, methods=['GET', 'POST'])
    server.add_url_rule('/logout', 'logout', logout, methods=['POST'])

    wrapped = set()
    for rule in list(server.url_map.iter_rules()):
        if rule.rule in ('/login', '/logout', '/static/<path:filename>'):
            continue
        if rule.endpoint in wrapped:
            continue
        if rule.endpoint in server.view_functions:
            server.view_functions[rule.endpoint] = login_required(server.view_functions[rule.endpoint])
            wrapped.add(rule.endpoint)


def _dev_secret():
    sys.stderr.write(
        "WARNING: PAT_SECRET_KEY not set. Using an ephemeral session secret; "
        "all sessions will be invalidated on restart.\n"
    )
    return secrets.token_hex(32)


def login():
    if current_user.is_authenticated:
        return redirect('/')
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = store.get_user_by_username(username)
        if user and user['active'] and check_password_hash(user['password_hash'], password):
            login_user(User(user))
            nxt = request.args.get('next')
            return redirect(nxt if nxt and nxt.startswith('/') else '/')
        error = 'Invalid username or password.'
    return render_template_string(LOGIN_TEMPLATE, error=error, zephyr=dbc.themes.ZEPHYR)


def logout():
    logout_user()
    return redirect('/login')


def guard(*roles):
    if not current_user.is_authenticated:
        raise NotImplementedError('guard() must not be reached unauthenticated')
    if roles and current_user.role not in roles:
        from dash.exceptions import PreventUpdate
        raise PreventUpdate
    return current_user


def can_edit_master(user, master_id):
    if not user.is_authenticated:
        return False
    if user.role == 'admin':
        return True
    if user.role != 'case_manager':
        return False
    return int(master_id) in {g['master_case_id'] for g in store.master_grants_for(user.id)}


def can_edit_subcase(user, subcase_id):
    if not user.is_authenticated:
        return False
    if user.role == 'admin':
        return True
    if user.role != 'case_manager':
        return False
    sid = int(subcase_id)
    if sid in {g['subcase_id'] for g in store.subcase_grants_for(user.id)}:
        return True
    master_id = store.get_master_by_subcase(sid)['master_id']
    return int(master_id) in {g['master_case_id'] for g in store.master_grants_for(user.id)}


def guard_edit_master(master_id):
    if not can_edit_master(current_user, master_id):
        from dash.exceptions import PreventUpdate
        raise PreventUpdate


def guard_edit_subcase(subcase_id):
    if not can_edit_subcase(current_user, subcase_id):
        from dash.exceptions import PreventUpdate
        raise PreventUpdate


def require_roles(*roles):
    def decorator(fn):
        @functools.wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if roles and current_user.role not in roles:
                from flask import abort
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


LOGIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Sign in — Preference Analysis Tool</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="{{ zephyr }}">
<style>
  body { display: flex; align-items: center; justify-content: center; min-height: 100vh;
         background: #e9ecef; padding: 1rem; }
  .card { width: 100%; max-width: 420px; padding: 2.25rem; margin: auto;
          border: 1px solid #000; border-radius: 0.75rem; background: #fff;
          box-shadow: 0 0.5rem 1rem rgba(0, 0, 0, 0.08); }
  .form-label { font-weight: 700; text-align: right; line-height: 1.2; }
</style>
</head>
<body>
  <div class="card">
    <h2 class="h4 mb-4">Preference Analysis Tool</h2>
    {% if error %}<div class="alert alert-danger py-2 mb-4">{{ error }}</div>{% endif %}
    <form method="post">
      <div class="row align-items-center mb-4">
        <div class="col-4">
          <label class="form-label mb-0" for="username">Username</label>
        </div>
        <div class="col-8">
          <input class="form-control" id="username" name="username" autocomplete="username" required autofocus>
        </div>
      </div>
      <div class="row align-items-center mb-4">
        <div class="col-4">
          <label class="form-label mb-0" for="password">Password</label>
        </div>
        <div class="col-8">
          <input class="form-control" type="password" id="password" name="password" autocomplete="current-password" required>
        </div>
      </div>
      <div class="d-flex justify-content-end pt-2">
        <button class="btn btn-primary" type="submit">Sign in</button>
      </div>
    </form>
  </div>
</body>
</html>
"""