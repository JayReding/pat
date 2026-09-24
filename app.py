# Import packages
import json
import os
import re
import secrets
from werkzeug.security import check_password_hash, generate_password_hash
from dash import Dash, html, dcc, callback, clientside_callback, Output, Input, State, ALL, MATCH
from dash.exceptions import PreventUpdate
import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta
from plotly import graph_objects as go

import analysis
import backup
import data_import
import export_excel
import export_pdf
import portfolio
import store
import test_data
import auth
import session
import mailer as smtp_mail

# Incorporate data
store.init_cases_db()
store.init_state_db()
store.init_users_db()


def _bootstrap_admin():
    username = os.environ.get('PAT_ADMIN_USERNAME', 'admin')
    if store.list_users() or store.get_user_by_username(username):
        return
    password = secrets.token_urlsafe(12)
    store.create_user(username, generate_password_hash(password), role='admin')
    print("=" * 58)
    print("First run detected. Created admin user.")
    print(f"  Username: {username}")
    print(f"  Password: {password}")
    print("Save these credentials; they will not be shown again.")
    print("=" * 58)


_bootstrap_admin()

_COURTS = json.load(open('courts.json'))
COURT_OPTIONS = sorted(
    ({"label": c["Name"], "value": c["Name"]} for c in _COURTS),
    key=lambda o: o["label"].lower(),
)

_STATES = json.load(open('states.json'))
STATE_OPTIONS = sorted(_STATES.values(), key=str.lower)
STATE_SET = set(STATE_OPTIONS)
CUSTOM_STATE_KEY = "__type_your_own__"

AVATAR_COLORS = [
    '#b02a37', '#0d6efd', '#198754', '#fd7e14', '#6f42c1',
    '#20c997', '#d63384', '#0dcaf0',
]


def _initials(name, username):
    source = (name or '').strip() or (username or 'U')
    words = [w for w in source.split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    return source[:2].upper()


def _avatar_color_for(username, stored=None):
    if stored:
        return stored
    return AVATAR_COLORS[sum(ord(c) for c in (username or '')) % len(AVATAR_COLORS)]


def _avatar_circle(name, username, color, size):
    return html.Div(
        _initials(name, username),
        style={
            'width': f'{size}px', 'height': f'{size}px', 'borderRadius': '50%',
            'backgroundColor': _avatar_color_for(username, color), 'color': '#fff',
            'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
            'fontWeight': '700', 'fontSize': f'{max(10, size // 2)}px',
            'lineHeight': 1, 'userSelect': 'none', 'flexShrink': 0,
        },
    )


def _user_badge(u):
    label = (u.name or '').strip() or u.username
    return html.Div(style={'display': 'flex', 'alignItems': 'center', 'gap': '8px'}, children=[
        _avatar_circle(u.name, u.username, u.avatar_color, 30),
        dcc.Link(label, href='/manage', className='text-white fw-semibold',
                 style={'textDecoration': 'none'}),
    ])


def _db_custom_states():
    conn = store._connect_cases()
    try:
        rows = conn.execute("SELECT meta FROM subcases").fetchall()
    finally:
        conn.close()
    extras = set()
    for (m,) in rows:
        meta = json.loads(m or '{}')
        for key in ('contact_state', 'attorney_state', 'local_counsel_state'):
            v = meta.get(key)
            if v and v not in STATE_SET and v != CUSTOM_STATE_KEY:
                extras.add(v)
    return extras


def _db_custom_states_main():
    conn = store._connect_cases()
    try:
        rows = conn.execute("SELECT client_state FROM main_cases").fetchall()
    finally:
        conn.close()
    extras = set()
    for (state,) in rows:
        v = (state or '').strip()
        if v and v not in STATE_SET and v != CUSTOM_STATE_KEY:
            extras.add(v)
    return extras


def _state_options_for(extra):
    values = sorted(set(STATE_OPTIONS) | set(extra or []), key=str.lower)
    return values + [{"label": "✏️ Type your own state…", "value": CUSTOM_STATE_KEY}]


def load_case(st, subcase_id, firm_id=None):
    if firm_id is None:
        try:
            firm_id = auth.firm_scope(auth.current_user)
        except Exception:
            pass
    main = store.get_main_by_subcase(subcase_id)
    if firm_id is not None and main['firm_id'] != firm_id:
        raise PreventUpdate
    petition_date = pd.Timestamp(main['petition_date'])
    pref_start = petition_date - pd.Timedelta(days=90)
    s = pref_start.strftime('%Y-%m-%d')
    p = petition_date.strftime('%Y-%m-%d')

    frames = store.load_case_frames(subcase_id, s, p, firm_id=firm_id)
    st.df_historical = frames['historical']
    st.df_preference = frames['preference']
    st.df_transfers = frames['transfers']
    st.df_newvalue = frames['newvalue']
    st.active_subcase_id = int(subcase_id)
    st.loaded = True

    st.df_preference['Ordinary'] = 0

    settings = store.load_case_settings(subcase_id)
    ocb_range = settings.get('ocb_range')
    if ocb_range is not None and ocb_range.get('end') is None:
        ocb_range = None
    ocb_start = settings.get('ocb_start') if settings.get('ocb_start') is not None else 0
    ocb_end = settings.get('ocb_end') if settings.get('ocb_end') is not None else 100
    ocb_step = settings.get('ocb_step') if settings.get('ocb_step') is not None else 5
    ocb_total_flag = bool(settings.get('ocb_total_flag', False))
    st.ocb_metric = settings.get('ocb_metric') or "Invoice to Payment"

    ordinary_inv = []
    if ocb_range is not None:
        a, b = sorted((ocb_range['start'], ocb_range['end']))
        df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, ocb_start, ocb_end, ocb_step, st.ocb_metric)
        s_label = df_cur['date_range'].iloc[a]
        e_label = df_cur['date_range'].iloc[b]
        lower, _ = analysis.ocb_label_bounds(s_label, ocb_start, ocb_end)
        _, upper = analysis.ocb_label_bounds(e_label, ocb_start, ocb_end)
        days = st.df_preference[st.ocb_metric]
        mask = pd.Series(True, index=st.df_preference.index)
        if lower is not None:
            mask &= days >= lower
        if upper is not None:
            mask &= days <= upper
        st.df_preference.loc[mask, 'Ordinary'] = 1
        ordinary_inv = st.df_preference.loc[mask, 'Invoice Number'].dropna().tolist()

    st.df_snv = analysis.calculate_new_value(pd.concat([st.df_transfers, st.df_newvalue], ignore_index=True))
    nv_settings = settings.get('nv_settings')
    if nv_settings:
        nv_lookup = {s['invoice_number']: s for s in nv_settings if s.get('invoice_number') is not None}
        st.df_snv['Remove'] = st.df_snv['Invoice Number'].map(
            lambda inv: nv_lookup.get(inv, {}).get('remove', False) if pd.notna(inv) else False
        )
        st.df_snv['Ordinary Exclusion'] = st.df_snv['Invoice Number'].map(
            lambda inv: (nv_lookup.get(inv, {}).get('reason') or '') if pd.notna(inv) else ''
        )
    st.df_snv = analysis.sync_new_value(st.df_snv, ordinary_inv)
    st.df_snv = analysis.finalize_new_value(st.df_snv)
    st.df_ocb = analysis.build_ocb_data(st.df_preference, st.df_historical, metric=st.ocb_metric)

    hist_dates = pd.to_datetime(st.df_historical['Payment Date'])
    pref_dates = pd.to_datetime(st.df_preference['Payment Date'])
    hist_title = f'Historical Period: {hist_dates.min().strftime("%m/%d/%Y")} through {hist_dates.max().strftime("%m/%d/%Y")}' if not st.df_historical.empty else 'Historical Period: No data'
    pref_title = f'Preference Period: {s} through {p}'

    info = {
        'subcase_id': int(subcase_id),
        'main_id': main['main_id'],
        'main_name': main['case_name'],
        'main_number': main['case_number'],
        'jurisdiction': main['jurisdiction'],
        'judge': main['judge'],
        'petition_date': petition_date.strftime('%m/%d/%Y'),
        'pref_start': pref_start.strftime('%m/%d/%Y'),
        'transferee': main['transferee'],
        'adversary_number': main['adversary_number'] or '',
        'file_number': main['file_number'] or '',
        'filing_date': main['filing_date'] or '',
        'subcase_display': (main['adversary_number'] or '') if main['filing_date'] else (main['file_number'] or ''),
        'subcase_id_label': 'Adversary Number' if main['filing_date'] else 'File Number',
        'hist_title': hist_title,
        'pref_title': pref_title,
        'hist_count': f'Historical Period Invoice Count: {len(hist_dates)}',
        'pref_count': f'Preference Period Invoice Count: {len(pref_dates)}',
        'ocb_range': ocb_range,
        'ocb_total_flag': ocb_total_flag,
        'ocb_start': ocb_start,
        'ocb_end': ocb_end,
        'ocb_step': ocb_step,
        'ordinary_inv': ordinary_inv,
    }
    st.meta = info
    return info


def _session_loader(st):
    try:
        scope = auth.firm_scope(auth.current_user)
    except Exception:
        scope = 1
    if store.list_subcase_options(firm_id=scope):
        load_case(st, store.active_subcase_id(firm_id=scope), firm_id=scope)


session.set_loader(_session_loader)


# Initialize the app
app = Dash(use_pages=True, pages_folder="", external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.FONT_AWESOME])
app.title = "Preference Analysis Tool (PAT)"


def _callout(figure_id, label, icon_class, color_var="var(--bs-primary)",
             background=None, border="none", text_color="#fff", icon_id=None):
    return html.Div(style={
        "flex": "0 0 260px", "display": "flex", "alignItems": "center",
        "justifyContent": "space-between", "gap": "20px", "padding": "20px 24px",
        "borderRadius": "12px", "backgroundColor": background or color_var,
        "border": border, "color": text_color,
    }, children=[
        html.Div([
            html.Div(id=figure_id, style={"fontSize": "1.75rem", "fontWeight": "700"}),
            html.Div(label, style={"opacity": ".9"}),
        ]),
        html.I(**(dict(id=icon_id) if icon_id else {}), className=icon_class, **{"aria-hidden": "true"}, style={"opacity": ".8", "fontSize": "3rem"}),
    ])


_ANALYSIS_VIEWS = [
    ("summary", "Case Summary", "fa-solid fa-clipboard-list"),
    ("insights", "Case Insights", "fa-solid fa-lightbulb"),
    ("historical", "Historical Period", "fa-solid fa-clock-rotate-left"),
    ("preference", "Preference Period", "fa-solid fa-calendar-days"),
    ("graph", "Graph View", "fa-solid fa-chart-column"),
    ("exhibits", "Exhibits", "fa-solid fa-file-pdf"),
    ("new-value", "New Value", "fa-solid fa-hand-holding-dollar"),
    ("ocb", "Ordinary Course", "fa-solid fa-chart-line"),
]


def _content_footer():
    return html.Div("Preference Analysis Tool (PAT) - \u00a92026 Jay Reding - PAT is not intended to provide legal or financial advice.",
                    className="content-footer")


def _analysis_nav_link(view, label, icon):
    return dbc.NavLink(
        [html.I(className=f"{icon} fa-fw me-3"), label],
        id=f"nav-{view}",
        active=(view == "summary"),
        className="analysis-nav-link",
    )


def _main_sidebar(active):
    """Shared main sidebar for the analysis (/) and portfolio views.

    ``active == "analysis"`` renders the in-page view-switcher links;
    ``active == "portfolio"`` renders the identical sidebar but the analysis
    links become plain navigation back to the analysis page.

    Graph View lives under its own "Presentation" heading: it stays in
    _ANALYSIS_VIEWS (so view-switching is untouched) but its link is
    partitioned out of the Analysis section.  The Presentation section is
    ungated, so it is available to all roles.
    """
    presentation_keys = {"graph", "exhibits"}
    if active == "analysis":
        analysis_links = [_analysis_nav_link(v, lab, ico)
                          for v, lab, ico in _ANALYSIS_VIEWS if v not in presentation_keys]
        presentation_links = [_analysis_nav_link(v, lab, ico)
                              for v, lab, ico in _ANALYSIS_VIEWS if v in presentation_keys]
    else:
        analysis_links = [
            dbc.NavLink(
                [html.I(className=f"{ico} fa-fw me-3"), lab],
                href="/", className="analysis-nav-link",
            )
            for v, lab, ico in _ANALYSIS_VIEWS if v not in presentation_keys
        ]
        presentation_links = [
            dbc.NavLink(
                [html.I(className=f"{ico} fa-fw me-3"), lab],
                href="/", className="analysis-nav-link",
            )
            for v, lab, ico in _ANALYSIS_VIEWS if v in presentation_keys
        ]
    if active == "portfolio":
        portfolio_link = dbc.NavLink(
            [html.I(className="fa-solid fa-briefcase fa-fw me-3"), "Portfolio View"],
            href="/portfolio", className="analysis-nav-link", active=True,
        )
    else:
        portfolio_link = dbc.NavLink(
            [html.I(className="fa-solid fa-briefcase fa-fw me-3"), "Portfolio View"],
            href="/portfolio", className="analysis-nav-link",
        )
    return html.Aside(className="navbar navbar-dark bg-dark analysis-sidebar", children=[
        html.Div("Analysis", className="analysis-sidebar-heading"),
        dbc.Nav(analysis_links, vertical=True, className="navbar-nav w-100"),
        html.Hr(className="analysis-sidebar-divider"),
        html.Div("Presentation", className="analysis-sidebar-heading"),
        dbc.Nav(presentation_links, vertical=True, className="navbar-nav w-100"),
        html.Hr(className="analysis-sidebar-divider"),
        html.Div(id="portfolio-nav-section", style={"display": "none"}, children=[
            html.Div("Portfolio", className="analysis-sidebar-heading"),
            dbc.Nav([portfolio_link], vertical=True, className="navbar-nav w-100"),
            html.Hr(className="analysis-sidebar-divider"),
        ]),
        html.Div("Management", className="analysis-sidebar-heading"),
        dbc.Nav(
            [dbc.NavLink(
                [html.I(className="fa-solid fa-gears fa-fw me-3"), "Settings"],
                href="/manage", className="analysis-nav-link",
            )],
            vertical=True, className="navbar-nav w-100",
        ),
    ])


def _analysis_page():
    return html.Div(className="analysis-shell d-flex", children=[
        _main_sidebar("analysis"),
        html.Div(className="analysis-content", children=[
            html.Div(className="analysis-topstrip", children=[
                html.Span(
                    [html.I(className="fa-solid fa-chart-column me-2"), "Preference Analysis Tool (PAT)"],
                    className="fw-semibold text-white fs-4",
                ),
                html.Div(className="d-flex align-items-center gap-3 ms-auto", children=[
                    html.Div(id="user-badge-strip", className="text-white-50 small"),
                    html.Form(dbc.Button("Log out", color="light", size="sm", className="px-3"),
                              action="/logout", method="POST"),
                ]),
            ]),
            html.Div(className="analysis-body", children=[
    html.Div(style={
        "display": "flex",
        "justifyContent": "space-between",
        "alignItems": "center",
        "gap": "20px",
        "flexWrap": "wrap",
        "padding": "12px 16px",
        "borderRadius": "0.375rem",
        "marginBottom": "20px",
        "border": "1px solid var(--bs-border-color)",
        "backgroundColor": "var(--bs-tertiary-bg)",
    }, children=[
        html.Div(id="main-info", children=[]),
        html.Div(style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap"}, children=[
html.Div(children=[
                    html.Label('Main Bankruptcy Case', htmlFor="main-selector"),
                    dcc.Dropdown(
                        id="main-selector",
                        options=[],
                        value=None,
                        clearable=False,
                        style={"width": "300px"},
                    ),
                ]),
            html.Div(children=[
                html.Label('Subcase', htmlFor="subcase-selector"),
                dcc.Dropdown(
                    id="subcase-selector",
                    options=[],
                    value=None,
                    clearable=False,
                    style={"width": "300px"},
                ),
            ]),
        ]),
        dcc.Store(id="auth-boot", data=True),
        dcc.Store(id="analysis-boot", data=True),
    ]),
    dcc.Store(id="analysis-view", data="summary"),
    html.Div(id="view-summary", children=[
            html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "20px", "alignItems": "flex-start"}, children=[
            html.Div(style={"flex": "1 1 0", "minWidth": "0"}, children=[
            html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "20px", "marginBottom": "20px"}, children=[
                _callout("cs-net-pref-figure", "Net Preference", "fa-solid fa-dollar-sign fa-3x"),
                _callout("cs-total-transfers", "Total Transfers", "fa-solid fa-money-bill-transfer fa-3x", color_var="var(--bs-info)"),
                _callout("cs-total-new-value", "Total New Value", "fa-solid fa-hand-holding-dollar fa-3x", color_var="var(--bs-info)"),
                _callout("cs-ordinary-course", "Ordinary Course", "fa-solid fa-chart-simple fa-3x", color_var="var(--bs-info)"),
            ]),
            html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "20px", "marginBottom": "20px"}, children=[
                _callout("cs-dso-diff-figure", "Change in Days Outstanding", "fa-solid fa-arrow-trend-up fa-3x",
                         background="var(--bs-body-bg)", border="1px solid #000", text_color="#000", icon_id="cs-dso-diff-icon"),
                _callout("cs-dpd-diff-figure", "Change in Days Past Due", "fa-solid fa-arrow-trend-up fa-3x",
                         background="var(--bs-body-bg)", border="1px solid #000", text_color="#000", icon_id="cs-dpd-diff-icon"),
            ]),
            html.H3("Transfers in the Preference Period",
                    style={"margin": "24px 0 8px", "fontSize": "1.1rem", "fontWeight": "600"}),
            dag.AgGrid(
                id="transfers-pref",
                rowData=[],
                getRowStyle=analysis.ocb_default_style(),
                style={"flex": "1 1 0", "minWidth": "0",
                       "height": "max(300px, calc(100vh - 450px))"},
                columnDefs=[
                    {"field": "Transfer Number"},
                    {"field": "Transfer Amount",
                     "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Payment Date"},
                    {"field": "Check Date"},
                ],
            ),
            ]),
            html.Div(style={"flex": "1 1 0", "minWidth": "0"}, children=[
                html.Div(id="contact-panel", children=[
                    html.Div("Select a subcase to view contact details.",
                             style={"color": "var(--bs-secondary-color)"}),
                ]),
            ]),
            ]),
    ]),
    html.Div(id="view-insights", style={"display": "none"}, children=[
        html.H3(children='Case Insights'),
        html.P('Historical vs preference period comparison. Differences over 10% are '
               'highlighted; differences of 20% or more use the danger color.',
               className="text-muted"),
        html.Div(id="insights-table", children=""),
    ]),
    html.Div(id="view-historical", style={"display": "none"}, children=[
            html.Div(style={"display": "flex", "gap": "20px", "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
            html.Div(style={"flex": "1 1 0", "display": "flex", "flexDirection": "column", "gap": "12px"}, children=[
                html.Div(style={"border": "1px solid var(--bs-border-color)",
                                "borderRadius": "0.375rem", "backgroundColor": "var(--bs-tertiary-bg)"}, children=[
                    html.Div(id="hist-stats-table", children=""),
                ]),
                html.Div(style={"display": "flex", "gap": "30px"}, children=[
                    dbc.Button("Export Excel", id="export-hist-excel-btn", color="primary", size="sm"),
                    dbc.Button("Export PDF", id="export-hist-pdf-btn", color="primary", size="sm"),
                    dcc.Download(id="download-hist-pdf"),
                    dcc.Download(id="download-hist-excel"),
                ]),
            ]),
            dcc.Graph(
                id="hist-distribution-graph",
                style={"flex": "1 1 0", "minWidth": "0", "height": "420px"},
                figure={},
                config={"displaylogo": False, "responsive": True}
            ),
            ]),
            html.Div(style={"display": "flex", "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
            dag.AgGrid(
                id="historical",
                rowData=[],
                getRowStyle=analysis.ocb_default_style(),
                style={"flex": "1 1 0", "minWidth": "0", "height": "600px"},
                columnDefs=[
                    {"field": "Transfer Number"},
                    {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Payment Date"},
                    {"field": "Invoice Number"},
                    {"field": "Invoice Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Invoice Amount Paid", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Invoice Date"},
                    {"field": "Invoice Due"},
                    {"field": "Invoice to Payment"},
                    {"field": "Days Past Due"},
                ]
            ),
            ])
    ]),
    html.Div(id="view-preference", style={"display": "none"}, children=[
            html.Div(style={"display": "flex", "gap": "20px", "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
            html.Div(style={"flex": "1 1 0", "display": "flex", "flexDirection": "column", "gap": "12px"}, children=[
                html.Div(style={"border": "1px solid var(--bs-border-color)",
                                "borderRadius": "0.375rem", "backgroundColor": "var(--bs-tertiary-bg)"}, children=[
                    html.Div(id="pref-stats-table", children=""),
                ]),
                html.Div(style={"display": "flex", "gap": "30px"}, children=[
                    dbc.Button("Export Excel", id="export-pref-excel-btn", color="primary", size="sm"),
                    dbc.Button("Export PDF", id="export-pref-pdf-btn", color="primary", size="sm"),
                    dcc.Download(id="download-pref-pdf"),
                    dcc.Download(id="download-pref-excel"),
                ]),
            ]),
            dcc.Graph(
                id="pref-distribution-graph",
                style={"flex": "1 1 0", "minWidth": "0", "height": "420px"},
                figure={},
                config={"displaylogo": False, "responsive": True}
            ),
            ]),
            html.Div(style={"display": "flex", "alignItems": "flex-start", "flexWrap": "wrap"}, children=[
            dag.AgGrid(
              id="preference",
              rowData=[],
              getRowStyle=analysis.ocb_default_style(),
              style={"flex": "1 1 0", "minWidth": "0", "height": "600px"},
              columnDefs=[
                {"field": "Transfer Number"},
                {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                {"field": "Payment Date"},
                {"field": "Invoice Number"},
                {"field": "Invoice Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                {"field": "Invoice Amount Paid", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                {"field": "Invoice Date"},
                {"field": "Invoice Due"},
                {"field": "Invoice to Payment"},
                {"field": "Days Past Due"},
            ]
        ),
        ])
    ]),
    html.Div(id="view-graph", style={"display": "none"}, children=[
        html.H3(children='Graph View'),
        html.P('Historical vs preference period distributions overlaid. Both periods '
               'are scaled to percent so their shapes can be compared directly.',
               className="text-muted"),
        html.Div(className="mb-3", style={"maxWidth": "340px"}, children=[
            html.Label('Metric', htmlFor="graph-metric"),
            dcc.Dropdown(id="graph-metric", options=[
                {"label": "Invoice to Payment", "value": "Invoice to Payment"},
                {"label": "Days Past Due", "value": "Days Past Due"},
            ], value="Invoice to Payment", clearable=False, searchable=False),
        ]),
        dcc.Graph(
            id="graph-distribution-graph",
            style={"height": "480px"},
            figure={},
            config={"displaylogo": False, "responsive": True}
        ),
    ]),
    html.Div(id="view-exhibits", style={"display": "none"}, children=[
        html.H3(children='Exhibits'),
        html.P('Drag rows to order the exhibits (or use the arrows), toggle '
               'inclusion, and edit exhibit labels. Then generate a ZIP of '
               'individual PDFs or a single combined PDF.',
               className="text-muted"),
        dcc.Store(id="exhibits-order",
                  data=["transfers", "historical", "preference",
                        "new-value", "ocb", "graph-view"]),
        dcc.Store(id="exhibits-include", data={}),
        dcc.Store(id="exhibits-dnd-ready", data=0),
        html.Div(id="exhibits-rows", className="mb-3"),
        html.Div(className="mb-3", style={"maxWidth": "340px"}, children=[
            dbc.Switch(id="exhibits-labels-toggle",
                       label="Exhibit labels (A, B, C…)",
                       value=True,
                       className="form-switch"),
            html.Small("When on, each exhibit gets a full-page label in the "
                       "combined PDF and its letter in every title and footer.",
                       className="text-muted d-block"),
        ]),
        html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}, children=[
            dbc.Button([html.I(className="fa-solid fa-file-zipper me-2"),
                        "Generate Exhibits ZIP"],
                       id="exhibits-generate-btn", color="primary", n_clicks=0),
            dcc.Download(id="download-exhibits-zip"),
            dbc.Button([html.I(className="fa-solid fa-file-pdf me-2"),
                        "Generate Combined PDF"],
                       id="exhibits-combined-btn", color="secondary", n_clicks=0),
            dcc.Download(id="download-exhibits-combined"),
        ]),
        html.Div(id="exhibits-status", className="mt-3"),
    ]),
    html.Div(id="view-new-value", style={"display": "none"}, children=[
            dag.AgGrid(
                id="new_value",
                rowData=[],
                getRowStyle=analysis.ocb_default_style(),
                style={"height": "600px"},
                columnDefs=[
                    {"field": "Transaction Date"},
                    {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Payment Date"},
                    {"field": "Invoice Number"},
                    {"field": "Invoice Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Invoice Date"},
                    {"field": "Allowed New Value", "valueFormatter":{"function": "params.value ? d3.format('$,.2f')(params.value) : null"}},
                    {"field": "Net Preference", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"headerName": "Exclude New Value", "field": "Remove", "editable": {"function": "params.data['Invoice Amount'] != null"}, "cellEditor": "agCheckboxCellEditor", "cellEditorParams": {"values": [True, False]}, "valueSetter": {"function": "params.data['Remove'] = params.newValue; return true;"}},
                    {"headerName": "Exclusion Reason", "field": "Ordinary Exclusion", "editable": False}
                ]
            ),
    ]),
    html.Div(id="view-ocb", style={"display": "none"}, children=[
            html.H3(children='Ordinary Course'),
            dcc.Store(id="ocb-range", data=None),
            dcc.Store(id="ocb-total-range-flag", data=False),
            dcc.Store(id="ocb-ordinary-invoices", data=None),
            dcc.Store(id="ocb-restore", data=None),
            dbc.Row([
                dbc.Col([
                    html.Div(className="mb-3", children=[
                        html.Strong("Net Preference (Defenses Applied): "),
                        html.Span(id="ocb-net-pref-defenses"),
                    ]),
                    html.Div(id="ocb-range-status", className="mb-3"),
                    html.Div(id="ocb-hist-coverage", className="mb-3"),
                    html.Div(id="ocb-range-warning", className="mb-3"),
                    html.H6('Display Options'),
                    html.Div(className="mb-3", children=[
                        html.Label('Binning Metric', htmlFor="ocb-metric"),
                        dcc.Dropdown(id="ocb-metric", options=[
                            {"label": "Invoice to Payment", "value": "Invoice to Payment"},
                            {"label": "Days Past Due", "value": "Days Past Due"},
                        ], value="Invoice to Payment", clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('Start Range', htmlFor="ocb-start"),
                        dcc.Dropdown(id="ocb-start", options=[{"label": str(i), "value": i} for i in range(-5, 21)], value=None, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('End Range', htmlFor="ocb-end"),
                        dcc.Dropdown(id="ocb-end", options=[{"label": str(i), "value": i} for i in range(100, 301)], value=None, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('Step Size', htmlFor="ocb-step"),
                        dbc.Select(id="ocb-step", options=[{"label": str(i), "value": i} for i in range(1, 11)], value=None),
                    ]),
                    dbc.Button("+/- 15 Days", id="ocb-plus15", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Total Range", id="ocb-total-range", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Clear OCB Range", id="ocb-clear", color="primary", className="mt-2 w-100"),
                    html.Div(style={"display": "flex", "gap": "10px", "marginTop": "12px"}, children=[
                        dbc.Button("Export Excel", id="export-ocb-excel-btn", color="primary", size="sm"),
                        dbc.Button("Export PDF", id="export-ocb-pdf-btn", color="primary", size="sm"),
                        dcc.Download(id="download-ocb-pdf"),
                        dcc.Download(id="download-ocb-excel"),
                    ]),
                    html.Div(id="autosave-status", className="mt-3 text-muted small"),
                ], width=3),
                dbc.Col([
                    dag.AgGrid(
                        id="ocb_grid",
                        rowData=[],
                        columnDefs=[
                            {"field": "date_range", "headerName": "Date Range"},
                            {"field": "pref_pct", "headerName": "Preference % of Invoices", "valueFormatter": {"function": "d3.format('.2f')(params.value) + '%'"}},
                            {"field": "hist_pct", "headerName": "Historical % of Invoices", "valueFormatter": {"function": "d3.format('.2f')(params.value) + '%'"}},
                            {"field": "pct_diff", "headerName": "Percentage Difference", "valueFormatter": {"function": "params.value != null ? d3.format('.2f')(params.value) + '%' : ''"}},
                            {"field": "pref_count", "headerName": "Preference Invoice Count"},
                            {"field": "hist_count", "headerName": "Historical Invoice Count"},
                            {"field": "pref_amount", "headerName": "Preference Invoice Amount", "valueFormatter": {"function": "d3.format('$,.2f')(params.value)"}},
                            {"field": "hist_amount", "headerName": "Historical Invoice Amount", "valueFormatter": {"function": "d3.format('$,.2f')(params.value)"}},
                        ],
                        style={"height": None},
                        dashGridOptions={"domLayout": "autoHeight"},
                    ),
                ], width=9),
            ]),
    ]),
dbc.Modal(
    [
        dbc.ModalHeader(dbc.ModalTitle("Welcome to the Preference Analysis Tool (PAT)")),
        dbc.ModalBody([
            html.P("This appears to be the first time you are running the Preference Analysis Tool, or the case database is empty. In order to start using the Preference Analysis Tool you need to create a new main bankruptcy case. Once you have created a new case, you can begin importing your data into potential or filed preference cases."),
            html.P("If you are seeing this message in error, contact your administrator or technical support."),
            html.Div(style={"textAlign": "center", "marginTop": "24px"}, children=[
                dcc.Link(dbc.Button("Proceed", color="primary", className="px-5"), href="/manage"),
                html.Div(style={"marginTop": "12px"}, children=[
                    dcc.Link("Technical Support", href="https://github.com/JayReding/pat", target="_blank"),
                ]),
            ]),
        ]),
    ],
    id="welcome-modal",
    is_open=False,
    centered=True,
    backdrop="static",
    keyboard=False,
),
            ]),
            _content_footer(),
        ]),
    ])


def _cm_field(label, cid, ftype="text"):
    return html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
        html.Label(label, htmlFor=cid, style={"fontWeight": "600"}),
        dbc.Input(id=cid, type=ftype, className="form-control",
                  style={"height": "50px", "fontSize": "1rem"}),
    ])


def _cm_state_field(label, cid, cid_custom, cid_wrap, options=None):
    if options is None:
        options = _state_options_for(_db_custom_states())
    return html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
        html.Label(label, htmlFor=cid, style={"fontWeight": "600"}),
        dcc.Dropdown(id=cid, options=options,
                     clearable=False, searchable=True, style={"fontSize": "1rem"}),
        html.Div(id=cid_wrap, style={"display": "none", "marginTop": "4px"}, children=[
            dbc.Input(id=cid_custom, className="form-control",
                      style={"height": "50px", "fontSize": "1rem"}),
        ]),
    ])


_CM_CLIENT_FIELD_DEFS = [
    ("cm-client-name",     "client_name"),
    ("cm-client-contact",  "client_contact"),
    ("cm-client-address",  "client_address"),
    ("cm-client-address2", "client_address2"),
    ("cm-client-city",     "client_city"),
    ("cm-client-state",    "client_state"),
    ("cm-client-zip",      "client_zip"),
    ("cm-client-phone",    "client_phone"),
    ("cm-client-email",    "client_email"),
]


_SETTINGS_LINKS = [
    ("My Account", [
        ("account", "Account Settings", "/manage/account", "fa-solid fa-user-gear", None, False),
        ("firm-options", "Firm Options", "/manage/firm-options", "fa-solid fa-building", "firm-options-nav-btn", True),
    ]),
    ("Case Management", [
        ("main", "Edit Main Cases", "/manage", "fa-solid fa-briefcase", None, False),
        ("subcase", "Edit Subcases", "/manage/subcases", "fa-solid fa-folder-tree", None, False),
        ("import", "Data Import", "/manage/import", "fa-solid fa-file-import", "import-nav-btn", True),
        ("backup", "Backup and Restore", "/manage/backup", "fa-solid fa-box-archive", None, False),
    ]),
    ("Administration", [
        ("users", "User Management", "/manage/users", "fa-solid fa-users", "admin-nav-btn", True),
        ("email-settings", "Email Settings", "/manage/email-settings", "fa-solid fa-envelope", "email-settings-nav-btn", True),
        ("test-data", "Test Data Generator", "/manage/test-data", "fa-solid fa-flask", "test-data-nav-btn", True),
    ]),
]


def _settings_nav_link(key, label, href, icon, nav_id, gated, active):
    kwargs = dict(id=nav_id) if nav_id else {}
    if gated:
        # Hidden until manage_admin_gate reveals it for authorized roles,
        # so unauthorized users never see a flash of these links.
        kwargs["style"] = {"display": "none"}
    return dbc.NavLink(
        [html.I(className=f"{icon} fa-fw me-3"), label],
        href=href, active=(active == key), className="analysis-nav-link", **kwargs,
    )


def _settings_sidebar(active):
    sections = []
    for heading, links in _SETTINGS_LINKS:
        block = [
            html.Div(heading, className="analysis-sidebar-heading"),
            dbc.Nav(
                [_settings_nav_link(k, lab, href, ico, nid, gated, active)
                 for k, lab, href, ico, nid, gated in links],
                vertical=True, className="navbar-nav w-100",
            ),
        ]
        if heading == "Administration":
            # Hidden until manage_admin_gate reveals it; keeps regular
            # users and case managers from seeing an empty header.
            sections.append(html.Div(
                id="admin-nav-section",
                style={"display": "none"},
                children=block,
            ))
        elif heading == "Case Management":
            # Hidden until manage_admin_gate reveals it; viewers
            # (role == "user") must not see this section at all.
            sections.append(html.Div(
                id="case-mgmt-nav-section",
                style={"display": "none"},
                children=block,
            ))
        else:
            sections.extend(block)
    sections.append(html.Div(className="mt-auto", children=[
        html.Hr(className="analysis-sidebar-divider"),
        dbc.Nav(
            [dbc.NavLink(
                [html.I(className="fa-solid fa-arrow-left fa-fw me-3"), "Back to Analysis"],
                href="/", className="analysis-nav-link",
            )],
            vertical=True, className="navbar-nav w-100",
        ),
    ]))
    return html.Aside(className="navbar navbar-dark bg-dark analysis-sidebar", children=sections)


def _settings_chrome(active, *content):
    return html.Div(className="analysis-shell d-flex", children=[
        _settings_sidebar(active),
        html.Div(className="analysis-content", children=[
            html.Div(className="analysis-topstrip", children=[
                html.Span("Settings", className="fw-semibold text-white fs-4"),
                html.Div(className="d-flex align-items-center gap-3 ms-auto", children=[
                    html.Div(id="user-badge-strip", className="text-white-50 small"),
                    html.Form(dbc.Button("Log out", color="light", size="sm", className="px-3"),
                              action="/logout", method="POST"),
                ]),
            ]),
            html.Div(className="analysis-body manage-console", children=list(content)),
            _content_footer(),
        ]),
    ])


def _portfolio_page():
    return html.Div(className="analysis-shell d-flex", children=[
        _main_sidebar("portfolio"),
        html.Div(className="analysis-content", children=[
            html.Div(className="analysis-topstrip", children=[
                html.Span(
                    [html.I(className="fa-solid fa-chart-column me-2"), "Preference Analysis Tool (PAT)"],
                    className="fw-semibold text-white fs-4",
                ),
                html.Div(className="d-flex align-items-center gap-3 ms-auto", children=[
                    html.Div(id="user-badge-strip", className="text-white-50 small"),
                    html.Form(dbc.Button("Log out", color="light", size="sm", className="px-3"),
                              action="/logout", method="POST"),
                ]),
            ]),
            html.Div(className="analysis-body manage-console", children=[
                dcc.Store(id="portfolio-boot", data=True),
                html.H3("Portfolio View", style={"margin": 0}),
                html.Div(className="text-muted", style={"marginBottom": "8px"}, children=[
                    "Choose a main bankruptcy case to see the preference outlook across its subcases.",
                ]),
                html.Div(style={"maxWidth": "640px", "marginBottom": "16px"}, children=[
                    html.Label("Main Bankruptcy Case", htmlFor="portfolio-main-select", style={"fontWeight": "600"}),
                    dcc.Dropdown(id="portfolio-main-select", options=[], value=None, clearable=False),
                ]),
                html.Div(id="portfolio-feedback", className="text-muted small mb-2"),
                html.Div(id="portfolio-summary-header", children=[]),
                html.Div(id="portfolio-table", style={"marginTop": "16px"}, children=[]),
            ]),
            _content_footer(),
        ]),
    ])


def _firm_picker(prefix, label="Firm"):
    return html.Div(
        id=f"{prefix}-firm-wrap",
        style={"display": "none", "maxWidth": "640px", "marginBottom": "16px"},
        children=[
            html.Label(label, htmlFor=f"{prefix}-firm-select", style={"fontWeight": "600"}),
            dcc.Dropdown(id=f"{prefix}-firm-select", clearable=False),
        ],
    )


def _make_firm_init(prefix):
    @callback(
        Output(f"{prefix}-firm-select", "options"),
        Output(f"{prefix}-firm-select", "value"),
        Output(f"{prefix}-firm-wrap", "style"),
        Input("manage-boot", "data"),
        prevent_initial_call='initial_duplicate',
    )
    def _firm_init(_):
        u = auth.current_user
        if u.role == "admin":
            firms = store.list_firms()
            opts = [{"label": f["name"], "value": f["id"]} for f in firms]
            value = firms[0]["id"] if firms else 1
            wrap = {"display": "block", "maxWidth": "640px", "marginBottom": "16px"}
        elif u.role == "firm_admin":
            fid = auth.firm_scope(u)
            firm = store.get_firm(fid)
            opts = [{"label": firm["name"], "value": fid}]
            value = fid
            wrap = {"display": "none"}
        else:
            opts, value, wrap = [], None, {"display": "none"}
        return opts, value, wrap
    return _firm_init


def _make_firm_options(prefix, out_id):
    @callback(
        Output(out_id, "options"),
        Output(out_id, "value", allow_duplicate=True),
        Input(f"{prefix}-firm-select", "value"),
        State(out_id, "value"),
        prevent_initial_call='initial_duplicate',
    )
    def _firm_options(selected, current):
        u = auth.current_user
        scope = selected if u.role == "admin" else auth.firm_scope(u)
        opts = store.list_main_options(firm_id=scope)
        ids = {o["value"] for o in opts}
        new_value = current if current in ids else None
        return opts, new_value
    return _firm_options

_make_firm_init("cm")
_make_firm_init("sc")
_make_firm_init("users")
_make_firm_init("bk")
_make_firm_init("imp")
_make_firm_init("td")
_make_firm_options("cm", "cm-main")
_make_firm_options("sc", "sc-main")
_make_firm_options("users", "grant-main")
_make_firm_options("td", "td-main")


def _maincase_page():
    return _settings_chrome("main",
        dcc.Store(id="manage-boot", data=True),
                html.H3(children='Edit Main Cases'),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Select Case", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _firm_picker("cm"),
                        html.Label('Main Bankruptcy Case', htmlFor="cm-main",
                                   style={"fontWeight": "600"}),
                        dcc.Dropdown(id="cm-main", options=[], clearable=False, style={"maxWidth": "640px", "marginBottom": "16px"}),
                        html.Div(id="cm-new", children=[
                            dbc.Button("New Main Case", id="cm-new-btn", n_clicks=0, color="secondary"),
                        ]),
                    ]),
                ]),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Main Case Details", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _cm_field('Case Name', "cm-case-name"),
                        _cm_field('Case Number', "cm-case-number"),
                        html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                            html.Label('Jurisdiction', htmlFor="cm-jurisdiction", style={"fontWeight": "600"}),
                            dcc.Dropdown(id="cm-jurisdiction", options=COURT_OPTIONS, clearable=False, searchable=True,
                                         style={"height": "50px", "fontSize": "1rem"}),
                        ]),
                        _cm_field('Judge', "cm-judge"),
                        _cm_field('Petition Date (YYYY-MM-DD)', "cm-petition-date", "date"),
                    ]),
                ]),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Client Information", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _cm_field('Client Name', "cm-client-name"),
                        _cm_field('Client Contact', "cm-client-contact"),
                        _cm_field('Client Address 1', "cm-client-address"),
                        _cm_field('Client Address 2', "cm-client-address2"),
                        html.Div(className="d-flex", style={"gap": "8px", "maxWidth": "640px"}, children=[
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('City', "cm-client-city")]),
                            html.Div(style={"flex": "1 1 0"}, children=[
                                _cm_state_field('State', "cm-client-state", "cm-client-state-custom", "cm-client-state-custom-wrap",
                                                options=_state_options_for(_db_custom_states_main()))
                            ]),
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('ZIP', "cm-client-zip")]),
                        ]),
                        html.Div(className="d-flex flex-wrap gap-2", children=[
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Client Phone', "cm-client-phone")]),
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Client Email', "cm-client-email", "email")]),
                        ]),
                    ]),
                ]),
                html.Div(id="cm-status", className="mt-3"),
                html.Div(className="sticky-action-bar", children=[
                    dbc.Button("Save Changes", id="cm-save", n_clicks=0, color="primary"),
                ]),
                dcc.Store(id="cm-create-mode", data=False),
    )


def _subcase_page():
    return _settings_chrome("subcase",
        dcc.Store(id="manage-boot", data=True),
                html.H3(children='Edit Subcases'),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Select Case", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _firm_picker("sc"),
                        html.Label('Main Bankruptcy Case', htmlFor="sc-main",
                                   style={"fontWeight": "600"}),
                        dcc.Dropdown(id="sc-main", options=[], clearable=False, style={"maxWidth": "640px", "marginBottom": "16px"}),
                        html.Label('Subcase', htmlFor="sc-subcase",
                                   style={"fontWeight": "600"}),
                        dcc.Dropdown(id="sc-subcase", options=[], clearable=False, style={"maxWidth": "640px", "marginBottom": "16px"}),
                        dbc.Button("New Subcase", id="sc-new-subcase-btn", n_clicks=0, color="success",
                                   style={"display": "none", "marginBottom": "8px"}),
                    ]),
                ]),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Subcase Details", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _cm_field('Transferee Name', "sc-transferee-name"),
                        _cm_field('Case Caption', "sc-case-caption"),
                        _cm_field('File Number (blank = auto-assign)', "sc-file-number"),
                        _cm_field('Adversary Number (blank = none)', "sc-adversary-number"),
                        _cm_field('Filing Date (YYYY-MM-DD)', "sc-filing-date", "date"),
                    ]),
                ]),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Contact", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        _cm_field('Contact Name', "sc-contact-name"),
                        _cm_field('Contact Address', "sc-contact-address"),
                        _cm_field('Contact Address 2', "sc-contact-address2"),
                        html.Div(className="d-flex flex-wrap gap-2", children=[
                            _cm_field('City', "sc-contact-city"),
                            _cm_state_field('State', "sc-contact-state", "sc-contact-state-custom", "sc-contact-state-custom-wrap"),
                            _cm_field('ZIP', "sc-contact-zip"),
                        ]),
                        html.Div(className="d-flex flex-wrap gap-2", children=[
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Contact Phone', "sc-contact-phone")]),
                            html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Contact Email', "sc-contact-email", "email")]),
                        ]),
                    ]),
                ]),
                dbc.Card(className="mb-3 sc-form-card", children=[
                    dbc.CardHeader("Counsel", class_name="text-bg-info fs-5 fw-bold"),
                    dbc.CardBody(children=[
                        dbc.Row([
                            dbc.Col(width=6, children=[
                                html.H4('Attorney'),
                                html.Div(className="ps-4", children=[
                                    _cm_field('Attorney Name', "sc-attorney-name"),
                                    _cm_field('Attorney Firm', "sc-attorney-firm"),
                                    _cm_field('Attorney Address', "sc-attorney-address"),
                                    _cm_field('Attorney Address 2', "sc-attorney-address2"),
                                    html.Div(className="d-flex flex-wrap gap-2", children=[
                                        _cm_field('City', "sc-attorney-city"),
                                        _cm_state_field('State', "sc-attorney-state", "sc-attorney-state-custom", "sc-attorney-state-custom-wrap"),
                                        _cm_field('ZIP', "sc-attorney-zip"),
                                    ]),
                                    html.Div(className="d-flex flex-wrap gap-2", children=[
                                        html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Attorney Phone', "sc-attorney-phone")]),
                                        html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Attorney Email', "sc-attorney-email", "email")]),
                                    ]),
                                ]),
                            ]),
                            dbc.Col(width=6, children=[
                                html.H4('Local Counsel'),
                                html.Div(className="ps-4", children=[
                                    _cm_field('Local Counsel Name', "sc-local-counsel-name"),
                                    _cm_field('Local Counsel Firm', "sc-local-counsel-firm"),
                                    _cm_field('Local Counsel Address', "sc-local-counsel-address"),
                                    _cm_field('Local Counsel Address 2', "sc-local-counsel-address2"),
                                    html.Div(className="d-flex flex-wrap gap-2", children=[
                                        _cm_field('City', "sc-local-counsel-city"),
                                        _cm_state_field('State', "sc-local-counsel-state", "sc-local-counsel-state-custom", "sc-local-counsel-state-custom-wrap"),
                                        _cm_field('ZIP', "sc-local-counsel-zip"),
                                    ]),
                                    html.Div(className="d-flex flex-wrap gap-2", children=[
                                        html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Local Counsel Phone', "sc-local-counsel-phone")]),
                                        html.Div(style={"flex": "1 1 0"}, children=[_cm_field('Local Counsel Email', "sc-local-counsel-email", "email")]),
                                    ]),
                                ]),
                            ]),
                        ]),
                    ]),
                ]),
                html.Div(id="sc-subcase-status", className="mt-3"),
                html.Div(className="sc-sticky-bar", children=[
                    dbc.Button("Save Subcase", id="sc-save-subcase", n_clicks=0, color="primary"),
                    dbc.Button("Delete Subcase", id="sc-delete-subcase", n_clicks=0, color="danger",
                               style={"display": "none", "marginLeft": "auto"}),
                ]),
                dbc.Modal(
                    id="sc-delete-modal",
                    is_open=False,
                    centered=True,
                    children=[
                        dbc.ModalHeader(dbc.ModalTitle("Delete subcase?")),
                        dbc.ModalBody([
                            html.P('You are about to permanently delete subcase'),
                            html.Blockquote(html.Span(id="sc-delete-target", style={"fontWeight": "600"})),
                            html.P('This removes all invoice records, case settings, and user '
                                   'grants for this subcase. This action cannot be undone.',
                                   className="text-muted"),
                        ]),
                        dbc.ModalFooter([
                            dbc.Button("Cancel Deletion", id="sc-delete-cancel", className="ms-auto", color="secondary"),
                            dbc.Button("Delete Subcase", id="sc-delete-confirm", color="danger"),
                        ]),
                    ],
                ),
    )


def _backup_page():
    return _settings_chrome("backup",
        dcc.Store(id="manage-boot", data=True),
                html.H3("Backup and Restore", className="mb-3"),
                dbc.Card(className="mb-4", children=[
                    dbc.CardHeader(html.Span([
                        html.I(className="fa-solid fa-download me-2"), "Backup",
                    ])),
                    dbc.CardBody(children=[
                        _firm_picker("bk"),
                        html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                            html.Label("Backup scope", style={"fontWeight": "600"}),
                            dbc.RadioItems(
                                id="bk-scope",
                                options=[
                                    {"label": "Main case (includes all of its subcases)", "value": "main"},
                                    {"label": "Single subcase", "value": "subcase"},
                                ],
                                value="main",
                                inline=True,
                            ),
                        ]),
                        html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                            html.Label("Case to back up", htmlFor="bk-target",
                                       style={"fontWeight": "600"}),
                            dcc.Dropdown(id="bk-target", placeholder="Select a case…"),
                        ]),
                        html.P("Backups include the case metadata, all invoice records, "
                               "and per-subcase analysis settings.",
                               className="text-muted"),
                        dbc.Button([html.I(className="fa-solid fa-download me-2"),
                                    "Create Backup"],
                                   id="bk-backup-btn", color="primary", n_clicks=0),
                        html.Div(id="bk-status", className="mt-3"),
                        dcc.Download(id="bk-download"),
                    ]),
                ]),
                dbc.Card(children=[
                    dbc.CardHeader(html.Span([
                        html.I(className="fa-solid fa-upload me-2"), "Restore",
                    ])),
                    dbc.CardBody(children=[
                        dcc.Upload(
                            id="bk-upload",
                            accept=".json.gz,.gz,.json",
                            multiple=False,
                            style={
                                "width": "100%", "borderWidth": "2px",
                                "borderStyle": "dashed", "borderRadius": "8px",
                                "textAlign": "center", "cursor": "pointer",
                                "padding": "28px 16px",
                                "backgroundColor": "var(--bs-light)",
                            },
                            children=html.Div([
                                html.I(className="fa-solid fa-cloud-arrow-up fa-2x mb-2"),
                                html.Div(dbc.Button(
                                    [html.I(className="fa-solid fa-folder-open me-2"),
                                     "Choose backup file…"],
                                    color="primary", size="lg"),
                                    className="mb-2"),
                                html.Div("or drag and drop a .json.gz backup file here",
                                         className="text-muted"),
                            ]),
                        ),
                        html.Div(id="bk-restore-summary", className="mt-3"),
                        dcc.Store(id="bk-restore-pending", data=None),
                        html.Div(id="bk-restore-start-wrap", style={"display": "none"}, children=[
                            html.Hr(),
                            html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                                html.Label("Restore mode", style={"fontWeight": "600"}),
                                dbc.RadioItems(
                                    id="bk-restore-mode",
                                    options=[
                                        {"label": "Create as new", "value": "create"},
                                        {"label": "Overwrite an existing case", "value": "overwrite"},
                                    ],
                                    value="create",
                                    inline=True,
                                ),
                            ]),
                            html.Div(id="bk-restore-main-wrap",
                                     className="mb-2",
                                     style={"display": "none", "maxWidth": "640px"}, children=[
                                html.Label("Existing main case to overwrite",
                                           htmlFor="bk-restore-main-target",
                                           style={"fontWeight": "600"}),
                                dcc.Dropdown(id="bk-restore-main-target",
                                             placeholder="Select a main case…"),
                            ]),
                            html.Div(id="bk-restore-sub-main-wrap",
                                     className="mb-2",
                                     style={"display": "none", "maxWidth": "640px"}, children=[
                                html.Label("Main case to attach the restored subcase to",
                                           htmlFor="bk-restore-sub-main",
                                           style={"fontWeight": "600"}),
                                dcc.Dropdown(id="bk-restore-sub-main",
                                             placeholder="Select a main case…"),
                            ]),
                            html.Div(id="bk-restore-sub-wrap",
                                     className="mb-2",
                                     style={"display": "none", "maxWidth": "640px"}, children=[
                                html.Label("Existing subcase to overwrite",
                                           htmlFor="bk-restore-sub-target",
                                           style={"fontWeight": "600"}),
                                dcc.Dropdown(id="bk-restore-sub-target",
                                             placeholder="Select a subcase…"),
                            ]),
                            dbc.Button([html.I(className="fa-solid fa-upload me-2"),
                                        "Review & Restore"],
                                       id="bk-restore-btn", color="primary", n_clicks=0),
                            html.Div(id="bk-restore-status", className="mt-3"),
                        ]),
                        dbc.Modal(
                            id="bk-restore-modal",
                            is_open=False,
                            centered=True,
                            children=[
                                dbc.ModalHeader(dbc.ModalTitle("Confirm restore")),
                                dbc.ModalBody(html.Div(id="bk-restore-modal-body")),
                                dbc.ModalFooter([
                                    dbc.Button("Cancel", id="bk-restore-cancel",
                                               className="ms-auto", color="secondary"),
                                    dbc.Button("Restore", id="bk-restore-confirm", color="danger"),
                                ]),
                            ],
                        ),
                    ]),
                ]),
    )


def _import_page():
    return _settings_chrome("import",
        dcc.Store(id="manage-boot", data=True),
                html.H3("Data Import", className="mb-3"),
                html.P("Import invoice rows from an Excel workbook formatted like the "
                       "subcase import template into an existing subcase. "
                       "Upload a file to preview it; nothing is written until you confirm.",
                       className="text-muted"),
                _firm_picker("imp"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Main Bankruptcy Case", htmlFor="imp-main",
                               style={"fontWeight": "600"}),
                    dcc.Dropdown(id="imp-main", placeholder="Select a main case…"),
                ]),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Subcase to import into", htmlFor="imp-subcase",
                               style={"fontWeight": "600"}),
                    dcc.Dropdown(id="imp-subcase", placeholder="Select a subcase…"),
                ]),
                html.Div(id="imp-target-info", className="text-muted small mb-2"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Import mode", style={"fontWeight": "600"}),
                    dbc.RadioItems(
                        id="imp-mode",
                        options=[
                            {"label": "Append (keep existing rows)", "value": "append"},
                            {"label": "Replace (delete existing rows first)", "value": "replace"},
                        ],
                        value="append",
                        inline=True,
                    ),
                    html.Small("Replace deletes ALL existing invoice records in the "
                               "subcase before importing. This cannot be undone.",
                               className="text-muted d-block"),
                ]),
                html.Div(className="mb-3", style={"maxWidth": "640px"}, children=[
                    dbc.Button([html.I(className="fa-solid fa-download me-2"),
                                "Download template"],
                               id="imp-template-btn", color="secondary", n_clicks=0),
                    dcc.Download(id="imp-template-download"),
                ]),
                dcc.Upload(
                    id="imp-upload",
                    accept=".xlsx",
                    multiple=False,
                    style={
                        "width": "100%", "borderWidth": "2px",
                        "borderStyle": "dashed", "borderRadius": "8px",
                        "textAlign": "center", "cursor": "pointer",
                        "padding": "28px 16px",
                        "backgroundColor": "var(--bs-light)",
                    },
                    children=html.Div([
                        html.I(className="fa-solid fa-cloud-arrow-up fa-2x mb-2"),
                        html.Div(dbc.Button(
                            [html.I(className="fa-solid fa-folder-open me-2"),
                             "Choose Excel file…"],
                            color="primary", size="lg"),
                            className="mb-2"),
                        html.Div("or drag and drop a .xlsx file here",
                                 className="text-muted"),
                    ]),
                ),
                html.Div(id="imp-preview", className="mt-3"),
                dcc.Store(id="imp-pending", data=None),
                html.Div(id="imp-review-wrap", style={"display": "none"}, children=[
                    html.Hr(),
                    dbc.Button([html.I(className="fa-solid fa-upload me-2"),
                                "Review & Import"],
                               id="imp-review-btn", color="primary", n_clicks=0),
                ]),
                html.Div(id="imp-status", className="mt-3"),
                dbc.Modal(
                    id="imp-confirm-modal",
                    is_open=False,
                    centered=True,
                    children=[
                        dbc.ModalHeader(dbc.ModalTitle("Confirm import")),
                        dbc.ModalBody(html.Div(id="imp-confirm-body")),
                        dbc.ModalFooter([
                            dbc.Button("Cancel", id="imp-confirm-cancel",
                                       className="ms-auto", color="secondary"),
                            dbc.Button("Import", id="imp-confirm-yes", color="danger"),
                        ]),
                    ],
                ),
    )


def _test_data_page():
    return _settings_chrome("test-data",
        dcc.Store(id="manage-boot", data=True),
                html.H3("Test Data Generator", className="mb-3"),
                html.P("Generate semi-random invoice data for a subcase to model "
                       "ordinary-course, late-payment, and early-payment scenarios. "
                       "Covers ~18 months of historical data plus the 90-day preference "
                       "period ending on the main case's petition date. "
                       "Nothing is written until you confirm.",
                        className="text-muted"),
                _firm_picker("td"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Main Bankruptcy Case", htmlFor="td-main",
                               style={"fontWeight": "600"}),
                    dcc.Dropdown(id="td-main", placeholder="Select a main case…"),
                ]),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Subcase to generate into", htmlFor="td-subcase",
                               style={"fontWeight": "600"}),
                    dcc.Dropdown(id="td-subcase", placeholder="Select a subcase…"),
                ]),
                html.Div(id="td-target-info", className="text-muted small mb-2"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Scenario", htmlFor="td-scenario",
                               style={"fontWeight": "600"}),
                    dcc.Dropdown(
                        id="td-scenario",
                        options=[
                            {"label": "Ordinary Course — preference and historical periods substantially similar",
                             "value": "ordinary"},
                            {"label": "Late Payment — invoices paid later in the preference period",
                             "value": "late"},
                            {"label": "Early Payment — invoices paid earlier in the preference period",
                             "value": "early"},
                        ],
                        value="ordinary",
                        clearable=False,
                    ),
                    html.Small("Net-30 baseline: ordinary ~32d DSO in both periods; "
                               "late shifts preference to ~50d; early shifts preference to ~20d.",
                               className="text-muted d-block"),
                ]),
                html.Div(className="d-flex gap-3 flex-wrap", style={"maxWidth": "640px"}, children=[
                    html.Div(className="mb-2", style={"flex": "1 1 150px"}, children=[
                        html.Label("Paid invoices (total)", htmlFor="td-total",
                                   style={"fontWeight": "600"}),
                        dbc.Input(id="td-total", type="number", min=2, max=2000,
                                  value=200, step=1),
                        html.Small("Split time-proportionally across ~21 months.",
                                   className="text-muted d-block"),
                    ]),
                    html.Div(className="mb-2", style={"flex": "1 1 150px"}, children=[
                        html.Label("Unpaid (new value)", htmlFor="td-unpaid",
                                   style={"fontWeight": "600"}),
                        dbc.Input(id="td-unpaid", type="number", min=0, max=100,
                                  value=6, step=1),
                        html.Small("Extra rows in the preference period.",
                                   className="text-muted d-block"),
                    ]),
                    html.Div(className="mb-2", style={"flex": "1 1 150px"}, children=[
                        html.Label("Random seed (optional)", htmlFor="td-seed",
                                   style={"fontWeight": "600"}),
                        dbc.Input(id="td-seed", type="text",
                                  placeholder="Blank = random"),
                    ]),
                ]),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label("Write mode", style={"fontWeight": "600"}),
                    dbc.RadioItems(
                        id="td-mode",
                        options=[
                            {"label": "Append (keep existing rows)", "value": "append"},
                            {"label": "Replace (delete existing rows first)", "value": "replace"},
                        ],
                        value="append",
                        inline=True,
                    ),
                    html.Small("Replace deletes ALL existing invoice records in the "
                               "subcase before generating. This cannot be undone.",
                               className="text-muted d-block"),
                ]),
                dbc.Button([html.I(className="fa-solid fa-flask me-2"),
                            "Generate Preview"],
                           id="td-generate-btn", color="primary", n_clicks=0,
                           className="mb-2"),
                html.Div(id="td-preview", className="mt-3"),
                dcc.Store(id="td-pending", data=None),
                html.Div(id="td-review-wrap", style={"display": "none"}, children=[
                    html.Hr(),
                    dbc.Button([html.I(className="fa-solid fa-upload me-2"),
                                "Review & Generate"],
                               id="td-review-btn", color="primary", n_clicks=0),
                ]),
                html.Div(id="td-status", className="mt-3"),
                dbc.Modal(
                    id="td-confirm-modal",
                    is_open=False,
                    centered=True,
                    children=[
                        dbc.ModalHeader(dbc.ModalTitle("Confirm test data generation")),
                        dbc.ModalBody(html.Div(id="td-confirm-body")),
                        dbc.ModalFooter([
                            dbc.Button("Cancel", id="td-confirm-cancel",
                                       className="ms-auto", color="secondary"),
                            dbc.Button("Generate", id="td-confirm-yes", color="danger"),
                        ]),
                    ],
                ),
    )


def _users_page():
    return _settings_chrome("users",
        dcc.Store(id="manage-boot", data=True),
        dcc.Store(id="users-boot", data=True),
                html.H3(children='User Management'),
                html.Div(className="text-muted mb-3", id="admin-notice"),
                _firm_picker("users"),
                dag.AgGrid(
                    id="admin-users-grid",
                    dashGridOptions={"rowSelection": "single"},
                    columnDefs=[
                        {"field": "username", "headerName": "Username"},
                        {"field": "role", "headerName": "Role"},
                        {"field": "firm_name", "headerName": "Firm"},
                        {"field": "email", "headerName": "Email"},
                        {"field": "active", "headerName": "Active"},
                        {"field": "created_at", "headerName": "Created At"},
                    ],
                ),
                html.Hr(),
                html.H6('Create user'),
                html.Div(className="mt-2", style={"display": "flex", "flexDirection": "column", "alignItems": "flex-start", "gap": "12px"}, children=[
                    html.Div(children=[html.Label('Username', htmlFor="new-user-username"), dcc.Input(id="new-user-username", type="text", className="form-control", style={"maxWidth": "320px", "width": "320px"})]),
                    html.Div(children=[html.Label('Password', htmlFor="new-user-password"), dcc.Input(id="new-user-password", type="password", className="form-control", style={"maxWidth": "320px", "width": "320px"})]),
                    html.Div(children=[
                        html.Label('Email', htmlFor="new-user-email"),
                        dcc.Input(id="new-user-email", type="email", className="form-control", style={"maxWidth": "320px", "width": "320px"}),
                        html.Div(id="new-user-email-error", className="text-danger", style={"fontSize": "0.85rem"}),
                    ]),
                    html.Div(children=[html.Label('Role', htmlFor="new-user-role"), dcc.Dropdown(id="new-user-role", options=[{"label": r, "value": r} for r in auth.ROLES], value="user", style={"minWidth": "220px"})]),
                    dbc.Button("Create User", id="admin-create-btn", color="primary"),
                ]),
                html.Hr(),
                html.H6('Actions on selected user'),
                html.Div(className="mt-2", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
                    html.Div(children=[html.Label('New role', htmlFor="admin-role-select"), dcc.Dropdown(id="admin-role-select", options=[{"label": r, "value": r} for r in auth.ROLES], style={"minWidth": "140px"})]),
                    html.Div(children=[html.Label('New password (reset)', htmlFor="admin-reset-pw"), dcc.Input(id="admin-reset-pw", type="password", className="form-control")]),
                    html.Div(id="admin-firm-wrap", children=[html.Label('New firm', htmlFor="admin-firm-select"), dcc.Dropdown(id="admin-firm-select", style={"minWidth": "180px"})]),
                    dbc.Button("Set Role", id="admin-set-role-btn", color="secondary"),
                    dbc.Button("Reset Password", id="admin-reset-btn", color="secondary"),
                    dbc.Button("Activate/Deactivate", id="admin-toggle-btn", color="secondary"),
                    dbc.Button("Change Firm", id="admin-firm-btn", color="secondary"),
                    dbc.Button("Delete User", id="admin-delete-btn", color="danger"),
                ]),
                html.Div(id="admin-status", className="mt-3"),
                html.Hr(),
                html.H6('Case Access (grants)'),
                html.P('Grant a user access to a main case (covers all its subcases) or to a single subcase. Roles: case_manager may edit granted cases; user may view.', className="text-muted small"),
                html.Div(className="mt-2", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
                    html.Div(children=[html.Label('User', htmlFor="grant-user"), dcc.Dropdown(id="grant-user", style={"minWidth": "160px"})]),
                    html.Div(children=[html.Label('Main case', htmlFor="grant-main"), dcc.Dropdown(id="grant-main", options=[], style={"minWidth": "260px"})]),
                    html.Div(children=[html.Label('Subcase', htmlFor="grant-subcase"), dcc.Dropdown(id="grant-subcase", style={"minWidth": "220px"})]),
                    dbc.Button("Grant Main Case", id="grant-main-btn", color="secondary"),
                    dbc.Button("Revoke Main Case", id="revoke-main-btn", color="secondary"),
                    dbc.Button("Grant Subcase", id="grant-subcase-btn", color="secondary"),
                    dbc.Button("Revoke Subcase", id="revoke-subcase-btn", color="danger"),
                ]),
                html.Div(id="grant-status", className="mt-2"),
                dag.AgGrid(
                    id="grants-grid",
                    columnDefs=[
                        {"field": "username", "headerName": "User"},
                        {"field": "level", "headerName": "Level"},
                        {"field": "main_case_id", "headerName": "Main Case ID"},
                        {"field": "subcase_id", "headerName": "Subcase ID"},
                    ],
                ),
    )


def _account_page():
    return _settings_chrome("account",
        dcc.Store(id="manage-boot", data=True),
        dcc.Store(id="account-boot", data=True),
        dcc.Store(id="account-avatar-color", data=None),
        dcc.Store(id="account-save-trigger", data=0),
                html.H3(children='Account Settings'),
                html.P('Set your display name, contact email, and avatar color. Your avatar shows your initials on a colored circle.', className="text-muted"),
                html.Div(id="account-avatar-preview", className="mb-3"),
                html.H6('Avatar color'),
                html.Div(id="avatar-swatches", className="d-flex flex-wrap gap-2 mb-3"),
                _cm_field('Name', "account-name"),
                _cm_field('Email', "account-email", "email"),
                dbc.Button("Save Settings", id="account-save-btn", n_clicks=0, color="primary", className="mt-2"),
                html.Div(id="account-status", className="mt-3"),
                html.Hr(style={"margin": "24px 0"}),
                html.H5('Change Password'),
                html.P('Strength is advisory only and never blocks saving. It measures overall entropy, not character types.',
                       className="text-muted"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label('Current password', htmlFor="account-current-pw", style={"fontWeight": "600"}),
                    dbc.Input(id="account-current-pw", type="password", className="form-control",
                              autoComplete="current-password",
                              style={"height": "50px", "fontSize": "1rem"}),
                ]),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label('New password', htmlFor="account-new-pw", style={"fontWeight": "600"}),
                    dbc.Input(id="account-new-pw", type="password", className="form-control",
                              autoComplete="new-password",
                              style={"height": "50px", "fontSize": "1rem"}),
                ]),
                html.Div(id="account-pw-strength", className="mb-2"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label('Confirm new password', htmlFor="account-confirm-pw", style={"fontWeight": "600"}),
                    dbc.Input(id="account-confirm-pw", type="password", className="form-control",
                              autoComplete="new-password",
                              style={"height": "50px", "fontSize": "1rem"}),
                ]),
                dbc.Button("Change Password", id="account-pw-btn", n_clicks=0, color="secondary", className="mt-2"),
                html.Div(id="account-pw-status", className="mt-3"),
    )


def _email_settings_page():
    return _settings_chrome("email-settings",
        dcc.Store(id="manage-boot", data=True),
        dcc.Store(id="email-boot", data=True),
                html.H3(children='Email Settings'),
                html.P('Configure the SMTP server the application uses to send notification emails. '
                       'These settings are system-wide.', className="text-muted"),
                _cm_field('SMTP server (FQDN or IP)', "email-smtp-host"),
                html.Div(className="mb-2", style={"maxWidth": "320px"}, children=[
                    html.Label('Server port', htmlFor="email-smtp-port", style={"fontWeight": "600"}),
                    dbc.Input(id="email-smtp-port", type="number", min=1, max=65535,
                              className="form-control", placeholder="587",
                              style={"height": "50px", "fontSize": "1rem"}),
                ]),
                _cm_field('Username', "email-smtp-username"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    html.Label('Password', htmlFor="email-smtp-password", style={"fontWeight": "600"}),
                    dbc.Input(id="email-smtp-password", type="password",
                              className="form-control", placeholder="Leave blank to keep current",
                              style={"height": "50px", "fontSize": "1rem"}),
                ]),
                _cm_field('From address', "email-smtp-from", "email"),
                html.Div(className="mb-2", style={"maxWidth": "640px"}, children=[
                    dbc.Switch(id="email-smtp-use-auth",
                               label="Use SMTP AUTH",
                               value=True,
                               className="form-switch",
                               style={"marginBottom": "8px"}),
                    html.Small("Disable for relays or test servers that do not require authentication.",
                               className="text-muted d-block"),
                ]),
                html.Div(style={"display": "flex", "gap": "10px", "flexWrap": "wrap",
                                 "marginTop": "16px"}, children=[
                    dbc.Button("Save Settings", id="email-save-btn", n_clicks=0, color="primary"),
                    dbc.Button("Send test email", id="email-test-btn", n_clicks=0, color="secondary"),
                ]),
                html.Div(id="email-status", className="mt-3"),
    )


def _firm_options_page():
    return _settings_chrome("firm-options",
        dcc.Store(id="manage-boot", data=True),
        dcc.Store(id="firm-options-boot", data=True),
        dcc.Store(id="firm-save-trigger", data=0),
                html.H3(children='Firm Options'),
                html.P('Manage your firm settings. Changes apply only to the selected firm.', className="text-muted"),
                html.Div(id="firm-options-firm-wrap", style={"display": "none"}, children=[
                    html.Label('Firm', htmlFor="firm-options-firm", style={"fontWeight": "600"}),
                    dcc.Dropdown(id="firm-options-firm", clearable=False,
                                 style={"maxWidth": "640px", "marginBottom": "16px"}),
                ]),
                html.Div(id="firm-options-new-wrap", style={"display": "none"}, children=[
                    html.H6('Create firm'),
                    html.Div(className="d-flex gap-2 align-items-center flex-wrap", children=[
                        dbc.Button("Create Firm", id="firm-options-new-btn", color="secondary"),
                        html.Span('New firms are created as "New Firm <id>" and renamed below.',
                                  className="text-muted"),
                    ]),
                ]),
                _cm_field('Firm Name', "firm-options-name"),
                dbc.Button("Save Firm Options", id="firm-options-save", n_clicks=0, color="primary", className="mt-2"),
                html.Div(id="firm-options-status", className="mt-3"),
                html.Div(id="firm-delete-wrap", style={"display": "none", "marginTop": "28px"}, children=[
                    html.Hr(),
                    html.H6('Delete firm'),
                    html.P('Permanently removes the firm and all of its users and case data. '
                           'This cannot be undone.', className="text-muted"),
                    dbc.Button("Delete Firm", id="firm-delete-btn", color="danger"),
                ]),
        dbc.Modal(
            id="firm-delete-modal",
            is_open=False,
            centered=True,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("Confirm delete firm")),
                dbc.ModalBody([
                    html.P('Are you sure you want to permanently delete '),
                    html.Blockquote(html.Span(id="firm-delete-firm-name", style={"fontWeight": "600"})),
                    html.P('This will remove the firm, all of its users, main cases, subcases, '
                           'invoices, grants, and per-firm state. It cannot be undone.',
                           className="text-muted"),
                ]),
                dbc.ModalFooter([
                    dbc.Button("Cancel", id="firm-delete-cancel", className="ms-auto", color="secondary"),
                    dbc.Button("Delete Firm", id="firm-delete-confirm", color="danger"),
                ]),
            ],
        ),
    )


@callback(
    Output("analysis-view", "data"),
    [Output(f"nav-{view}", "active") for view, _, _ in _ANALYSIS_VIEWS],
    [Output(f"view-{view}", "style") for view, _, _ in _ANALYSIS_VIEWS],
    [Input(f"nav-{view}", "n_clicks") for view, _, _ in _ANALYSIS_VIEWS],
    State("analysis-view", "data"),
    prevent_initial_call=True,
)
def switch_analysis_view(*args):
    current = args[-1]
    trig = dash.callback_context.triggered_id
    view = trig[4:] if isinstance(trig, str) and trig.startswith("nav-") else current
    valid = {v for v, _, _ in _ANALYSIS_VIEWS}
    if view not in valid:
        view = "summary"
    return (
        view,
        *(v == view for v, _, _ in _ANALYSIS_VIEWS),
        *({"display": "block"} if v == view else {"display": "none"}
          for v, _, _ in _ANALYSIS_VIEWS),
    )


def _shell():
    return html.Div(children=[
        dcc.Location(id="shell-url", refresh=False),
        html.Div(id="shell-navbar-wrap", children=[
            dbc.Navbar(
            dbc.Container(
                [
                    dbc.NavbarBrand(
                        [html.I(className="fa-solid fa-chart-column me-2"), "Preference Analysis Tool"],
                        href="/", className="fw-semibold",
                    ),
                    html.Div(className="ms-auto d-flex align-items-center gap-3", children=[
                        dcc.Store(id="shell-boot", data=True),
                        dcc.Store(id="account-saved", data=None),
                        html.Div(id="user-badge", className="navbar-text text-white-50"),
                        html.Form(dbc.Button("Log out", color="light", size="sm", className="px-3"),
                                  action="/logout", method="POST"),
                    ]),
                ],
                fluid=True,
            ),
            color="primary", dark=True, sticky="top", className="mb-3",
            ),
        ]),
        html.Div(id="shell-content", style={"padding": "20px"}, children=[dash.page_container]),
    ])


dash.register_page("analysis", path="/", layout=_analysis_page(), title="Preference Analysis Tool", name="Analysis")
dash.register_page("portfolio", path="/portfolio", layout=_portfolio_page(), title="Portfolio View", name="Portfolio View")
dash.register_page("account", path="/manage/account", layout=_account_page(), title="Account Settings", name="Account Settings")
dash.register_page("firm-options", path="/manage/firm-options", layout=_firm_options_page(), title="Firm Options", name="Firm Options")
dash.register_page("maincase", path="/manage", layout=_maincase_page(), title="Main Case Management", name="Main Case Management")
dash.register_page("subcases", path="/manage/subcases", layout=_subcase_page(), title="Subcase Management", name="Subcase Management")
dash.register_page("import", path="/manage/import", layout=_import_page(), title="Data Import", name="Data Import")
dash.register_page("backup", path="/manage/backup", layout=_backup_page(), title="Backup and Restore", name="Backup and Restore")
dash.register_page("users", path="/manage/users", layout=_users_page(), title="User Management", name="User Management")
dash.register_page("email-settings", path="/manage/email-settings", layout=_email_settings_page(), title="Email Settings", name="Email Settings")
dash.register_page("test-data", path="/manage/test-data", layout=_test_data_page(), title="Test Data Generator", name="Test Data Generator")

app.layout = _shell()


@callback(
    Output("shell-navbar-wrap", "style"),
    Output("shell-content", "style"),
    Input("shell-url", "pathname"),
)
def shell_chrome(pathname):
    # Every Dash page (analysis + settings) renders its own sidebar and
    # top strip; the global navbar stays mounted but hidden so its stores
    # (shell-boot, account-saved) and user badge keep working.
    return {"display": "none"}, {"padding": "0"}

@callback(
    Output("new_value", "rowData", allow_duplicate=True),
    Input("new_value", "cellValueChanged"),
    Input("ocb-ordinary-invoices", "data"),
    State("new_value", "rowData"),
    prevent_initial_call=True
)
def update_new_value(cellChange, ordinary_invoices, rowData):
    df = pd.DataFrame(rowData or [])
    if df.empty:
        return []
    trig = dash.callback_context.triggered_id
    if trig in ("ocb-ordinary-invoices", None):
        df = analysis.sync_new_value(df, ordinary_invoices)
    df = analysis.finalize_new_value(df)
    return df.to_dict("records")

def _ocb_transfer_shares(st):
    tot = st.df_preference.groupby("Transfer Number")["Invoice Amount"].sum()
    ord_tot = st.df_preference[st.df_preference["Ordinary"] == 1].groupby("Transfer Number")["Invoice Amount"].sum()
    return tot, ord_tot


@callback(
    Output("ocb-net-pref-defenses", "children"),
    Output("cs-net-pref-figure", "children"),
    Output("cs-total-transfers", "children"),
    Output("cs-total-new-value", "children"),
    Output("cs-ordinary-course", "children"),
    Output("cs-dso-diff-figure", "children"),
    Output("cs-dso-diff-icon", "className"),
    Output("cs-dpd-diff-figure", "children"),
    Output("cs-dpd-diff-icon", "className"),
    Input("new_value", "rowData"),
    Input("ocb-ordinary-invoices", "data"),
    Input("ocb-range", "data"),
    prevent_initial_call=True
)
def update_summary(nv_rowData, ordinary_invoices, ocb_range):
    st = session.get_state()
    if st.df_transfers is None or st.df_historical is None or st.df_preference is None:
        return dash.no_update
    df_nv = pd.DataFrame(nv_rowData or [])
    net_new_value = df_nv["Net Preference"].iloc[-1] if not df_nv.empty else 0.0
    total_transfers = st.df_transfers["Transfer Amount"].sum()
    hist_wavg = analysis.calc_weighted_dso(st.df_historical)
    pref_wavg = analysis.calc_weighted_dso(st.df_preference)
    diff = (pref_wavg - hist_wavg) / hist_wavg * 100 if hist_wavg else 0.0
    hist_dpd = analysis.calc_weighted_dpd(st.df_historical)
    pref_dpd = analysis.calc_weighted_dpd(st.df_preference)
    dpd_diff = (pref_dpd - hist_dpd) / hist_dpd * 100 if hist_dpd else 0.0

    tot_shares, ord_shares = _ocb_transfer_shares(st)
    net_pref_defenses, total_new_value = analysis.calc_net_pref_defenses(df_nv, tot_shares, ord_shares)

    tr_amt = st.df_transfers.groupby("Transfer Number")["Transfer Amount"].sum()
    ordinary_course_amount = (tr_amt * (ord_shares / tot_shares).reindex(tr_amt.index).fillna(0.0)).sum()
    if abs(total_transfers - total_new_value - net_pref_defenses - ordinary_course_amount) > 0.01:
        print(f"OCA identity off by ${abs(total_transfers - total_new_value - net_pref_defenses - ordinary_course_amount):,.2f}")

    return (f"${net_pref_defenses:,.2f}", f"${net_pref_defenses:,.2f}", f"${total_transfers:,.2f}", f"${total_new_value:,.2f}",
                f"${ordinary_course_amount:,.2f}", f"{diff:.2f}%",
"fa-solid fa-arrow-trend-up fa-3x" if diff >= 0 else "fa-solid fa-arrow-trend-down fa-3x",
                f"{dpd_diff:.2f}%",
                "fa-solid fa-arrow-trend-up fa-3x" if dpd_diff >= 0 else "fa-solid fa-arrow-trend-down fa-3x")


@callback(
    Output("transfers-pref", "rowData"),
    Input("new_value", "rowData"),
    prevent_initial_call=True,
)
def update_transfers_pref(nv_rowData):
    st = session.get_state()
    if st.df_preference is None:
        return []
    df = st.df_preference.drop_duplicates(
        subset=["Transfer Number", "Transfer Amount", "Payment Date"]
    )[["Transfer Number", "Transfer Amount", "Payment Date", "Check Date"]]
    df = df.sort_values("Payment Date")
    return df.to_dict("records")


_CONTACT_GROUP_DEFS = [
    ("Transferee Contact", (
        "contact_name", "contact_address", "contact_address2", "contact_city",
        "contact_state", "contact_zip", "contact_phone", "contact_email",
    )),
    ("Attorney", (
        "attorney_name", "attorney_firm", "attorney_address", "attorney_address2",
        "attorney_city", "attorney_state", "attorney_zip", "attorney_phone",
        "attorney_email",
    )),
    ("Local Counsel", (
        "local_counsel_name", "local_counsel_firm", "local_counsel_address",
        "local_counsel_address2", "local_counsel_city", "local_counsel_state",
        "local_counsel_zip", "local_counsel_phone", "local_counsel_email",
    )),
]

_FIELD_LABELS = {
    "contact_name": "Name", "contact_address": "Address", "contact_address2": "Address 2",
    "contact_city": "City", "contact_state": "State", "contact_zip": "ZIP",
    "contact_phone": "Phone", "contact_email": "Email",
    "attorney_name": "Name", "attorney_firm": "Firm", "attorney_address": "Address",
    "attorney_address2": "Address 2", "attorney_city": "City", "attorney_state": "State",
    "attorney_zip": "ZIP", "attorney_phone": "Phone", "attorney_email": "Email",
    "local_counsel_name": "Name", "local_counsel_firm": "Firm",
    "local_counsel_address": "Address", "local_counsel_address2": "Address 2",
    "local_counsel_city": "City", "local_counsel_state": "State",
    "local_counsel_zip": "ZIP", "local_counsel_phone": "Phone",
    "local_counsel_email": "Email",
}


def _contact_value(key, value):
    if key.endswith("_email") and value:
        return html.A(value, href=f"mailto:{value}",
                      style={"color": "var(--bs-primary)", "textDecoration": "none"})
    return value


def _contact_section(header, keys, sub):
    fields = []
    for key in keys:
        value = (sub.get(key) or "").strip()
        if not value:
            continue
        fields.append(html.Div([
            html.Div(_FIELD_LABELS[key],
                     style={"fontSize": "0.72rem", "textTransform": "uppercase",
                            "color": "var(--bs-secondary-color)", "fontWeight": "600"}),
            html.Div(_contact_value(key, value)),
        ], style={"marginBottom": "8px"}))
    return html.Div([
        html.Div(header,
                 style={"fontWeight": "700", "fontSize": "1rem", "marginBottom": "8px",
                        "borderBottom": "1px solid var(--bs-border-color)",
                        "paddingBottom": "4px"}),
        html.Div(style={"paddingLeft": "16px"}, children=fields),
    ], style={"marginBottom": "20px"})


def _build_contact_panel(subcase_id):
    sub = store.get_subcase(subcase_id)
    by_group = {header: keys for header, keys in _CONTACT_GROUP_DEFS}
    return [
        _contact_section("Transferee Contact", by_group["Transferee Contact"], sub),
        html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "20px"}, children=[
            html.Div(style={"flex": "1 1 280px", "minWidth": "0"},
                     children=[_contact_section("Attorney", by_group["Attorney"], sub)]),
            html.Div(style={"flex": "1 1 280px", "minWidth": "0"},
                     children=[_contact_section("Local Counsel", by_group["Local Counsel"], sub)]),
        ]),
    ]


@callback(
    Output("contact-panel", "children"),
    Input("subcase-selector", "value"),
    prevent_initial_call=True,
)
def update_contact_panel(subcase_id):
    if subcase_id is None:
        return html.Div("Select a subcase to view contact details.",
                        style={"color": "var(--bs-secondary-color)"})
    return _build_contact_panel(subcase_id)


@callback(
    Output("ocb_grid", "rowData"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-metric", "value")
)
def update_ocb_grid(start, end, step, metric):
    st = session.get_state()
    if st.df_preference is None:
        return []
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    metric = metric or "Invoice to Payment"
    st.ocb_metric = metric
    df = analysis.build_ocb_data(st.df_preference, st.df_historical, start, end, step, metric)
    return df.to_dict("records")

@callback(
    Output("ocb-range", "data"),
    Output("ocb-start", "value"),
    Output("ocb-end", "value"),
    Output("ocb-step", "value"),
    Output("ocb-total-range-flag", "data"),
    Input("ocb-total-range", "n_clicks"),
    Input("ocb-plus15", "n_clicks"),
    Input("ocb_grid", "cellClicked"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-clear", "n_clicks"),
    Input("ocb-restore", "data"),
    State("ocb-range", "data"),
    State("ocb-total-range-flag", "data"),
    State("ocb-metric", "value"),
    prevent_initial_call=True
)
def manage_ocb_range(n_total, n_plus15, click, start, end, step, n_clicks, restore, sel, flag, metric):
    st = session.get_state()
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    metric = metric or "Invoice to Payment"
    st.ocb_metric = metric
    trig = dash.callback_context.triggered[0]["prop_id"]

    if trig == "ocb-restore.data":
        r = restore or {}
        return (r.get('range'), r.get('start'), r.get('end'),
                int(r.get('step') or step), bool(r.get('total')))

    if trig == "ocb-plus15.n_clicks":
        anchor = round(analysis.calc_weighted(st.df_historical, metric))
        min_days = anchor - 15
        max_days = anchor + 15
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, new_start, new_end, step, metric)
        r0 = analysis.ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = analysis.ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, step, flag_out

    if trig == "ocb-total-range.n_clicks":
        if st.df_historical.empty:
            return dash.no_update, start, end, step, dash.no_update
        min_days = int(st.df_historical[metric].min())
        max_days = int(st.df_historical[metric].max())
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, new_start, new_end, step, metric)
        r0 = analysis.ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = analysis.ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, step, flag_out

    if trig == "ocb-clear.n_clicks":
        return None, start, end, step, False

    if trig != "ocb_grid.cellClicked":
        return dash.no_update, start, end, step, dash.no_update

    idx = int(click["rowIndex"])
    if sel is None or sel.get("end") is not None:
        return {"start": idx, "end": None}, start, end, step, False
    return {"start": sel["start"], "end": idx}, start, end, step, False

def _main_info_lines(info, subcase_id):
    """Shared analysis header lines (transferee, caption, main case).

    Single source for every main-info producer so the Case Caption line
    cannot drift out of one path again.  A missing subcase degrades to an
    empty caption instead of crashing the callback.
    """
    try:
        caption = store.get_subcase(subcase_id).get("case_caption") or ""
    except ValueError:
        caption = ""
    return [
        html.Div(f"Transferee: {info['transferee']}   |   {info['subcase_id_label']}: {info['subcase_display']}", style={"fontWeight": "bold", "fontSize": "1.25rem"}),
        html.Div(f"Case Caption: {caption}", style={"fontWeight": "bold", "fontSize": "1.1rem"}),
        html.Div(f"Main Case: {info['main_name']}   |   Preference Period: {info['pref_start']} - {info['petition_date']}"),
    ]


def _load_subcase_payload(st, subcase_id):
    scope = auth.firm_scope(auth.current_user)
    info = load_case(st, subcase_id, firm_id=scope)
    store.save_app_state(subcase_id, firm_id=scope or 1)
    st = session.get_state()
    main_info = _main_info_lines(info, subcase_id)
    return (
        main_info,
        st.df_historical.to_dict('records'),
        st.df_preference.to_dict('records'),
        st.df_snv.to_dict('records'),
        st.df_ocb.to_dict('records'),
        {'range': info['ocb_range'], 'start': info['ocb_start'], 'end': info['ocb_end'],
         'step': info['ocb_step'], 'total': info['ocb_total_flag']},
        st.ocb_metric,
        # Restored range is output directly (not only via ocb-restore) so a
        # trailing autosave can never persist the previous case's range.
        info['ocb_range'],
    )


@callback(
    Output("subcase-selector", "options"),
    Output("subcase-selector", "value"),
    Output("main-info", "children"),
    Output("historical", "rowData"),
    Output("preference", "rowData"),
    Output("new_value", "rowData", allow_duplicate=True),
    Output("ocb_grid", "rowData", allow_duplicate=True),
    Output("ocb-restore", "data"),
    Output("ocb-metric", "value", allow_duplicate=True),
    Output("ocb-range", "data", allow_duplicate=True),
    Input("main-selector", "value"),
    Input("subcase-selector", "value"),
    prevent_initial_call=True
)
def selection_changed(main_id, subcase_id):
    trig = dash.callback_context.triggered[0]["prop_id"]
    no_update = dash.no_update
    scope = auth.firm_scope(auth.current_user)

    if trig == "main-selector.value":
        opts = store.list_subcase_options(main_id, firm_id=scope)
        st = session.get_state()
        sub_ids = {o["value"] for o in opts}
        if st.meta and st.meta.get("main_id") == main_id and st.active_subcase_id in sub_ids:
            return (no_update,) * 10
        if st.subcase_by_main is None:
            st.subcase_by_main = {}
        target = st.subcase_by_main.get(main_id)
        if target not in sub_ids:
            target = opts[0]["value"] if opts else None
        if target is None:
            return opts, None, no_update, no_update, no_update, \
                   no_update, no_update, no_update, no_update, no_update
        st.subcase_by_main[main_id] = target
        payload = _load_subcase_payload(st, target)
        return (opts, target) + payload

    if subcase_id is None:
        raise PreventUpdate

    st = session.get_state()
    if st.meta and st.meta.get("subcase_id") == subcase_id:
        return (no_update,) * 10
    if st.subcase_by_main is None:
        st.subcase_by_main = {}
    st.subcase_by_main[main_id] = subcase_id
    payload = _load_subcase_payload(st, subcase_id)
    return (no_update, no_update) + payload

@callback(
    Output("ocb_grid", "getRowStyle"),
    Output("ocb-range-status", "children"),
    Output("ocb-hist-coverage", "children"),
    Output("ocb-ordinary-invoices", "data"),
    Input("ocb-range", "data"),
    Input("ocb-metric", "value"),
    State("ocb-start", "value"),
    State("ocb-end", "value"),
    State("ocb-step", "value")
)
def apply_ocb_range(sel, metric, start, end, step):
    st = session.get_state()
    if st.df_preference is None:
        return analysis.ocb_default_style(), "No case loaded.", "Historical invoices captured in range: —", []
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    metric = metric or "Invoice to Payment"
    st.ocb_metric = metric
    if sel is None:
        st.df_preference["Ordinary"] = 0
        return analysis.ocb_default_style(), "No OCB range selected.", "Historical invoices captured in range: —", []
    if sel.get("end") is None:
        st.df_preference["Ordinary"] = 0
        idx = int(sel["start"])
        df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, start, end, step, metric)
        label = df_cur["date_range"].iloc[idx]
        status = f"Range start selected: {label} — click a second row to complete the range."
        return analysis.ocb_pending_style(idx), status, "Historical invoices captured in range: —", []
    a, b = sorted((int(sel["start"]), int(sel["end"])))
    df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, start, end, step, metric)
    s_label = df_cur["date_range"].iloc[a]
    e_label = df_cur["date_range"].iloc[b]
    lower, _ = analysis.ocb_label_bounds(s_label, start, end)
    _, upper = analysis.ocb_label_bounds(e_label, start, end)
    days = st.df_preference[metric]
    mask = pd.Series(True, index=st.df_preference.index)
    if lower is not None:
        mask &= days >= lower
    if upper is not None:
        mask &= days <= upper
    st.df_preference["Ordinary"] = 0
    st.df_preference.loc[mask, "Ordinary"] = 1
    lo = lower if lower is not None else start
    hi = upper if upper is not None else end
    status = f"OCB range: {lo} to {hi} days — {int(mask.sum())} preference invoice(s) marked as Ordinary."
    hist_days = st.df_historical[metric]
    hist_mask = pd.Series(True, index=st.df_historical.index)
    if lower is not None:
        hist_mask &= hist_days >= lower
    if upper is not None:
        hist_mask &= hist_days <= upper
    coverage = f"Historical invoices captured in range: {int(hist_mask.sum())} of {len(st.df_historical)} ({hist_mask.mean() * 100:.2f}%)"
    ordinary_inv = st.df_preference.loc[mask, "Invoice Number"].dropna().tolist()
    return analysis.ocb_range_style(a, b), status, coverage, ordinary_inv

@callback(
    Output("ocb-range-warning", "children"),
    Input("ocb-range", "data"),
    State("ocb-start", "value"),
    State("ocb-end", "value"),
    State("ocb-step", "value")
)
def update_ocb_warning(sel, start, end, step):
    if sel is None or sel.get("end") is None:
        return ""
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    a, b = sorted((int(sel["start"]), int(sel["end"])))
    st = session.get_state()
    df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, start, end, step)
    lower, _ = analysis.ocb_label_bounds(df_cur["date_range"].iloc[a], start, end)
    _, upper = analysis.ocb_label_bounds(df_cur["date_range"].iloc[b], start, end)
    if lower is not None and upper is not None:
        total_days = upper - lower
    else:
        total_days = float("inf")
    if total_days > 100:
        return html.Span("Total ranges broader than 100 days may be unordinary in many jurisdictions.", style={"color": "red", "fontWeight": "bold"})
    return ""
 
def _stats_table(rows):
    return dbc.Table(
        [
            html.Tbody([
                html.Tr([
                    html.Th(label, style={"fontWeight": "600", "width": "55%"}),
                    html.Td(value),
                ]) for label, value in rows
            ])
        ],
        size="sm",
        borderless=True,
        hover=True,
        className="mb-0",
        style={"width": "100%"},
    )


@callback(
    Output("hist-stats-table", "children"),
    Input("historical", "rowData"),
    prevent_initial_call=True
)
def update_hist_stats(rowData):
    df = pd.DataFrame(rowData)
    if df.empty:
        return ""
    dates = pd.to_datetime(df["Payment Date"])
    begin = dates.min()
    end = dates.max()
    length = relativedelta(end, begin)

    def plural(value, unit):
        return f"{value} {unit}{'s' if value != 1 else ''}"

    weighted_dso = analysis.calc_weighted_dso(df)
    weighted_dpd = analysis.calc_weighted_dpd(df)
    skew = df["Invoice to Payment"].skew()
    if skew > 1 or skew < -1:
        skew_warning = " (Warning: Skew is outside the range of -1 to 1, indicating a non-normal distribution)"
    else:
        skew_warning = ""
    avg_invoice = df["Invoice Amount"].mean()
    avg_transfer = df["Transfer Amount"].mean()
    invoices_per_transfer = df.groupby("Transfer Number").size().mean()

    rows = [
        ("Beginning of Historical Period", begin.strftime("%m/%d/%Y")),
        ("Historical Period Length (years, months, days)", f"{plural(length.years, 'year')}, {plural(length.months, 'month')}, {plural(length.days, 'day')}"),
        ("Number of Invoices in Historical Period", str(len(df))),
        ("Historical Weighted Average Days Outstanding", f"{weighted_dso:.2f}"),
        ("Historical Weighted Days Past Due", f"{weighted_dpd:.2f}"),
        ("Historical Period Skew", f"{skew:.2f}{skew_warning}"),
        ("Average Amount of Invoices", f"${avg_invoice:,.2f}"),
        ("Average Amount of Transfers", f"${avg_transfer:,.2f}"),
        ("Average Number of Invoices Per Transfer", f"{invoices_per_transfer:.2f}"),
    ]

    return _stats_table(rows)


def _apply_distribution_style(fig, title, x_title, y_title, x_range=None):
    """Shared styling for the period distribution graphs (single + overlay)."""
    fig.update_layout(
        title={"text": title, "x": 0.0, "font": {"size": 13}},
        xaxis_title=x_title,
        yaxis_title=y_title,
        margin={"l": 50, "r": 20, "t": 50, "b": 45},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#343a40"},
        showlegend=True,
        legend={"font": {"color": "#000000"}, "bgcolor": "#ffffff",
                "borderwidth": 1, "bordercolor": "#adb5bd"},
        autosize=True,
    )
    fig.update_xaxes(
        showgrid=False, zeroline=False,
        showline=True, linewidth=1, linecolor="#adb5bd",
        range=x_range,
    )
    fig.update_yaxes(showgrid=False, zeroline=False, showline=True, linewidth=1, linecolor="#adb5bd")
    return fig


@callback(
    Output("hist-distribution-graph", "figure"),
    Input("historical", "rowData"),
    prevent_initial_call=True
)
def update_hist_distribution(rowData):
    df = pd.DataFrame(rowData)
    if df.empty or "Invoice to Payment" not in df:
        return go.Figure()
    x = df["Invoice to Payment"].dropna().to_numpy(dtype=float)
    if x.size == 0:
        return go.Figure()
    cap = float(np.ceil(np.quantile(x, 0.99) / 5.0) * 5.0)
    x = x[x <= cap]
    if x.size == 0:
        return go.Figure()
    n = x.size
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=x,
        name="Invoice Distribution",
        xbins={"start": 0, "end": cap, "size": 5},
        marker={"color": "#3459e6", "line": {"color": "#d8deea", "width": 1}},
        hovertemplate="Invoice to Payment: %{x} days<br>Number of Invoices: %{y}<extra></extra>"
    ))
    return _apply_distribution_style(
        fig,
        f"Invoice to Payment Distribution ({n} invoices)",
        "Invoice to Payment (days)",
        "Number of Invoices",
        x_range=[0, cap],
    )


@callback(
    Output("pref-distribution-graph", "figure"),
    Input("preference", "rowData"),
    prevent_initial_call=True
)
def update_pref_distribution(rowData):
    df = pd.DataFrame(rowData)
    if df.empty or "Invoice to Payment" not in df:
        return go.Figure()
    x = df["Invoice to Payment"].dropna().to_numpy(dtype=float)
    if x.size == 0:
        return go.Figure()
    cap = float(np.ceil(np.quantile(x, 0.99) / 5.0) * 5.0)
    x = x[x <= cap]
    if x.size == 0:
        return go.Figure()
    n = x.size
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=x,
        name="Invoice Distribution",
        xbins={"start": 0, "end": cap, "size": 5},
        marker={"color": "#3459e6", "line": {"color": "#d8deea", "width": 1}},
        hovertemplate="Invoice to Payment: %{x} days<br>Number of Invoices: %{y}<extra></extra>"
    ))
    return _apply_distribution_style(
        fig,
        f"Invoice to Payment Distribution ({n} invoices)",
        "Invoice to Payment (days)",
        "Number of Invoices",
        x_range=[0, cap],
    )


def build_overlay_figure(hist_df, pref_df, metric):
    """Overlaid Historical vs Preference distribution figure + bin table.

    Returns (fig, bins_df); bins_df has GRAPH_BIN_COLUMNS-compatible
    columns ("Bin", "Historical %", "Preference %", "Historical #",
    "Preference #") for the Exhibits PDF.  Empty input yields an empty
    figure and empty table.
    """
    metric = metric or "Invoice to Payment"
    empty_bins = pd.DataFrame(columns=["Bin", "Historical %", "Preference %",
                                       "Historical #", "Preference #"])
    if metric not in hist_df.columns and metric not in pref_df.columns:
        return go.Figure(), empty_bins
    x_hist = (hist_df[metric].dropna().to_numpy(dtype=float)
              if metric in hist_df.columns else np.array([]))
    x_pref = (pref_df[metric].dropna().to_numpy(dtype=float)
              if metric in pref_df.columns else np.array([]))
    if x_hist.size == 0 and x_pref.size == 0:
        return go.Figure(), empty_bins
    # Shared bins so the two periods are directly comparable: cap covers
    # the 99th percentile of both periods; start accommodates negatives
    # (e.g. Days Past Due paid early).
    combined = np.concatenate([a for a in (x_hist, x_pref) if a.size])
    cap = float(np.ceil(np.quantile(combined, 0.99) / 5.0) * 5.0)
    start = float(min(0.0, np.floor(combined.min() / 5.0) * 5.0))
    x_hist = x_hist[x_hist <= cap]
    x_pref = x_pref[x_pref <= cap]
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=x_hist,
        name=f"Historical ({x_hist.size} invoices)",
        xbins={"start": start, "end": cap, "size": 5},
        histnorm="percent",
        opacity=0.6,
        marker={"color": "#3459e6", "line": {"color": "#d8deea", "width": 1}},
        hovertemplate=f"{metric}: %{{x}} days<br>Historical: %{{y:.1f}}%<extra></extra>"
    ))
    fig.add_trace(go.Histogram(
        x=x_pref,
        name=f"Preference ({x_pref.size} invoices)",
        xbins={"start": start, "end": cap, "size": 5},
        histnorm="percent",
        opacity=0.6,
        marker={"color": "#e6531f", "line": {"color": "#f5d6c6", "width": 1}},
        hovertemplate=f"{metric}: %{{x}} days<br>Preference: %{{y:.1f}}%<extra></extra>"
    ))
    fig.update_layout(barmode="overlay")
    fig = _apply_distribution_style(
        fig,
        f"{metric} Distribution — Historical vs Preference",
        f"{metric} (days)",
        "Percent of Invoices",
        x_range=[start, cap],
    )
    edges = np.arange(start, cap + 5, 5)
    hist_counts, _ = np.histogram(x_hist, bins=edges)
    pref_counts, _ = np.histogram(x_pref, bins=edges)
    bins_df = pd.DataFrame([{
        "Bin": f"{edges[i]:.0f}–{edges[i + 1]:.0f}",
        "Historical %": round(float(hist_counts[i]) / max(len(x_hist), 1) * 100, 1),
        "Preference %": round(float(pref_counts[i]) / max(len(x_pref), 1) * 100, 1),
        "Historical #": int(hist_counts[i]),
        "Preference #": int(pref_counts[i]),
    } for i in range(len(edges) - 1)])
    return fig, bins_df


@callback(
    Output("graph-distribution-graph", "figure"),
    Input("historical", "rowData"),
    Input("preference", "rowData"),
    Input("graph-metric", "value"),
)
def update_graph_distribution(hist_rowData, pref_rowData, metric):
    fig, _ = build_overlay_figure(
        pd.DataFrame(hist_rowData or []),
        pd.DataFrame(pref_rowData or []),
        metric,
    )
    return fig


@callback(
    Output("download-hist-pdf", "data"),
    Input("export-hist-pdf-btn", "n_clicks"),
    prevent_initial_call=True
)
def export_hist_pdf(n_clicks):
    import re
    st = session.get_state()
    if not st.loaded or st.df_historical is None or st.df_historical.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip() if st.meta else ''
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Historical_Invoices'
    pdf_bytes = export_pdf.build_invoices_pdf(st.meta, st.df_historical)
    return dcc.send_bytes(lambda buf: buf.write(pdf_bytes), f"{safe_name}_Historical_Invoices.pdf")


@callback(
    Output("download-hist-excel", "data"),
    Input("export-hist-excel-btn", "n_clicks"),
    prevent_initial_call=True
)
def export_hist_excel(n_clicks):
    import re
    st = session.get_state()
    if not st.loaded or st.df_historical is None or st.df_historical.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip() if st.meta else ''
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Historical_Invoices'
    excel_bytes = export_excel.build_invoices_workbook(st.df_historical)
    return dcc.send_bytes(lambda buf: buf.write(excel_bytes), f"{safe_name}_Historical_Invoices.xlsx")


@callback(
    Output("download-pref-pdf", "data"),
    Input("export-pref-pdf-btn", "n_clicks"),
    prevent_initial_call=True
)
def export_pref_pdf(n_clicks):
    import re
    st = session.get_state()
    if not st.loaded or st.df_preference is None or st.df_preference.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip() if st.meta else ''
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Preference_Period_Invoices'
    pdf_bytes = export_pdf.build_invoices_pdf(st.meta, st.df_preference, caption="Preference Period Invoices")
    return dcc.send_bytes(lambda buf: buf.write(pdf_bytes), f"{safe_name}_Preference_Period_Invoices.pdf")


@callback(
    Output("download-pref-excel", "data"),
    Input("export-pref-excel-btn", "n_clicks"),
    prevent_initial_call=True
)
def export_pref_excel(n_clicks):
    import re
    st = session.get_state()
    if not st.loaded or st.df_preference is None or st.df_preference.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip() if st.meta else ''
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Preference_Period_Invoices'
    excel_bytes = export_excel.build_invoices_workbook(st.df_preference, sheet_title="Preference Period Invoices")
    return dcc.send_bytes(lambda buf: buf.write(excel_bytes), f"{safe_name}_Preference_Period_Invoices.xlsx")


def _ocb_summary_and_rows(st, sel, start, end, step):
    summary = {"range_label": "—", "invoice_count": "—", "invoice_amount": None}
    if sel is None or sel.get("end") is None:
        return None, summary
    a, b = sorted((int(sel["start"]), int(sel["end"])))
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    metric = st.ocb_metric or "Invoice to Payment"
    df_cur = analysis.build_ocb_data(st.df_preference, st.df_historical, start, end, step, metric)
    lower, _ = analysis.ocb_label_bounds(df_cur["date_range"].iloc[a], start, end)
    _, upper = analysis.ocb_label_bounds(df_cur["date_range"].iloc[b], start, end)
    lo = lower if lower is not None else start
    hi = upper if upper is not None else end
    days = st.df_preference[metric]
    mask = pd.Series(True, index=st.df_preference.index)
    if lower is not None:
        mask &= days >= lower
    if upper is not None:
        mask &= days <= upper
    summary = {
        "range_label": f"{lo} to {hi} days",
        "invoice_count": int(mask.sum()),
        "invoice_amount": float(st.df_preference.loc[mask, "Invoice Amount"].sum()),
    }
    return (a, b), summary


@callback(
    Output("download-ocb-pdf", "data"),
    Input("export-ocb-pdf-btn", "n_clicks"),
    State("ocb_grid", "rowData"),
    State("ocb-range", "data"),
    State("ocb-start", "value"),
    State("ocb-end", "value"),
    State("ocb-step", "value"),
    prevent_initial_call=True
)
def export_ocb_pdf(n_clicks, row_data, ocb_range, start, end, step):
    import re
    st = session.get_state()
    if not st.loaded or st.meta is None:
        raise PreventUpdate
    df = pd.DataFrame(row_data or [])
    if df.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip()
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Ordinary_Course'
    rows, summary = _ocb_summary_and_rows(st, ocb_range, start, end, step)
    pdf_bytes = export_pdf.build_ocb_pdf(st.meta, df, selected_rows=rows, summary=summary)
    return dcc.send_bytes(lambda buf: buf.write(pdf_bytes), f"{safe_name}_Ordinary_Course.pdf")


@callback(
    Output("download-ocb-excel", "data"),
    Input("export-ocb-excel-btn", "n_clicks"),
    State("ocb_grid", "rowData"),
    State("ocb-range", "data"),
    State("ocb-start", "value"),
    State("ocb-end", "value"),
    State("ocb-step", "value"),
    prevent_initial_call=True
)
def export_ocb_excel(n_clicks, row_data, ocb_range, start, end, step):
    import re
    st = session.get_state()
    if not st.loaded or st.meta is None:
        raise PreventUpdate
    df = pd.DataFrame(row_data or [])
    if df.empty:
        raise PreventUpdate
    transferee = (st.meta.get('transferee') or '').strip()
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Ordinary_Course'
    rows, summary = _ocb_summary_and_rows(st, ocb_range, start, end, step)
    excel_bytes = export_excel.build_ocb_workbook(df, selected_rows=rows, summary=summary)
    return dcc.send_bytes(lambda buf: buf.write(excel_bytes), f"{safe_name}_Ordinary_Course.xlsx")


_EXHIBIT_IDS = ("transfers", "historical", "preference", "new-value", "ocb", "graph-view")

_EXHIBIT_TITLES = {
    "transfers": "Transfers in the Preference Period",
    "historical": "Historical Period invoices and payments",
    "preference": "Preference Period invoices and payments",
    "new-value": "New Value",
    "ocb": "Ordinary Course",
    "graph-view": "Graph View",
}

_EXHIBIT_FILENAMES = {
    "transfers": "Transfers_Preference_Period.pdf",
    "historical": "Historical_Period.pdf",
    "preference": "Preference_Period.pdf",
    "new-value": "New_Value.pdf",
    "ocb": "Ordinary_Course.pdf",
    "graph-view": "Graph_View.pdf",
}


def _sanitize_order(order):
    """Normalize an order list to a deduped permutation of known exhibit ids."""
    seen = []
    for k in (order or []):
        if k in _EXHIBIT_IDS and k not in seen:
            seen.append(k)
    return seen + [k for k in _EXHIBIT_IDS if k not in seen]


def reorder_keys(order, key, direction):
    """Move key one step up/down; returns the new order (pure, testable)."""
    order = _sanitize_order(order)
    if key not in order:
        return order
    i = order.index(key)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(order):
        return order
    order[i], order[j] = order[j], order[i]
    return order


def _exhibit_row(key, position_label, included):
    return html.Div(
        className="exhibit-row",
        draggable="true",
        **{"data-key": key},
        children=[
            html.Span(html.I(className="fa-solid fa-grip-vertical"),
                      className="exhibit-grip", title="Drag to reorder"),
            dbc.Button(html.I(className="fa-solid fa-chevron-up"),
                       id={"type": "exhibit-up", "key": key},
                       size="sm", color="secondary", title="Move up"),
            dbc.Button(html.I(className="fa-solid fa-chevron-down"),
                       id={"type": "exhibit-down", "key": key},
                       size="sm", color="secondary", title="Move down"),
            dbc.Checkbox(id={"type": "exhibit-include", "key": key},
                         value=bool(included), className="ms-1"),
            html.Span(position_label, className="exhibit-letter"),
            html.Span(_EXHIBIT_TITLES[key], className="exhibit-name"),
        ],
    )


@callback(
    Output("exhibits-rows", "children"),
    Input("exhibits-order", "data"),
    Input("exhibits-include", "data"),
)
def render_exhibit_rows(order, include):
    order = _sanitize_order(order)
    include = include or {}
    rows = []
    pos = 0
    for k in order:
        if include.get(k, True):
            rows.append(_exhibit_row(k, chr(65 + pos), True))
            pos += 1
        else:
            rows.append(_exhibit_row(k, "–", False))
    return rows


@callback(
    Output("exhibits-order", "data", allow_duplicate=True),
    Input({"type": "exhibit-up", "key": MATCH}, "n_clicks"),
    Input({"type": "exhibit-down", "key": MATCH}, "n_clicks"),
    State("exhibits-order", "data"),
    prevent_initial_call=True,
)
def move_exhibit(up, down, order):
    trig = dash.callback_context.triggered_id or {}
    if not trig.get("key"):
        raise PreventUpdate
    new_order = reorder_keys(
        order, trig["key"], "up" if trig.get("type") == "exhibit-up" else "down")
    if new_order == _sanitize_order(order):
        raise PreventUpdate
    return new_order


@callback(
    Output("exhibits-include", "data", allow_duplicate=True),
    Input({"type": "exhibit-include", "key": ALL}, "value"),
    State("exhibits-order", "data"),
    State("exhibits-include", "data"),
    prevent_initial_call=True,
)
def sync_exhibit_include(values, order, current):
    order = _sanitize_order(order)
    if not values or len(values) != len(order):
        raise PreventUpdate
    new = dict(current or {})
    for k, v in zip(order, values):
        new[k] = bool(v)
    if new == (current or {}):
        raise PreventUpdate
    return new


clientside_callback(
    """
    function(children) {
        const container = document.getElementById('exhibits-rows');
        if (!container || container.dataset.dndBound) { return Date.now(); }
        container.dataset.dndBound = '1';
        // Firefox may deliver drag/mouse events with a Text node as the
        // target (which has no .closest); resolve to the parent element.
        const el = (n) => ((n && n.nodeType === 1) ? n : (n && n.parentElement)) || null;
        const rowOf = (n) => { const t = el(n); return t ? t.closest('.exhibit-row') : null; };
        let dragKey = null;
        container.addEventListener('dragstart', (e) => {
            const src = el(e.target);
            const row = rowOf(e.target);
            // setData first: Firefox requires it during dragstart, and it
            // must run before any early return that could throw.
            try {
                e.dataTransfer.setData('text/plain', (row && row.dataset.key) || '');
                e.dataTransfer.setData('text', (row && row.dataset.key) || '');
            } catch (err) {}
            if (!row || !(src && src.closest('.exhibit-grip'))) { e.preventDefault(); return; }
            dragKey = row.dataset.key;
            e.dataTransfer.effectAllowed = 'move';
            row.classList.add('exhibit-row-dragging');
        });
        container.addEventListener('dragend', () => {
            dragKey = null;
            container.querySelectorAll('.exhibit-row-dragging').forEach(
                r => r.classList.remove('exhibit-row-dragging'));
            container.querySelectorAll('.exhibit-drop-before').forEach(
                r => r.classList.remove('exhibit-drop-before'));
        });
        const allowDrop = (e) => {
            if (!dragKey) { return false; }
            e.preventDefault();
            try { e.dataTransfer.dropEffect = 'move'; } catch (err) {}
            return true;
        };
        container.addEventListener('dragenter', (e) => { allowDrop(e); });
        container.addEventListener('dragover', (e) => {
            if (!allowDrop(e)) { return; }
            const over = rowOf(e.target);
            container.querySelectorAll('.exhibit-drop-before').forEach(
                r => { if (r !== over) { r.classList.remove('exhibit-drop-before'); } });
            if (over && over.dataset.key !== dragKey) { over.classList.add('exhibit-drop-before'); }
        });
        container.addEventListener('drop', (e) => {
            e.preventDefault();
            const rows = Array.from(container.querySelectorAll('.exhibit-row'));
            let keys = rows.map(r => r.dataset.key);
            const target = rowOf(e.target);
            if (!dragKey || keys.indexOf(dragKey) === -1) { dragKey = null; return; }
            keys = keys.filter(k => k !== dragKey);
            if (target && target.dataset.key !== dragKey) {
                keys.splice(Math.max(keys.indexOf(target.dataset.key), 0), 0, dragKey);
            } else {
                keys.push(dragKey);
            }
            dragKey = null;
            dash_clientside.setProps('exhibits-order', {data: keys});
        });
        return Date.now();
    }
    """,
    Output("exhibits-dnd-ready", "data"),
    Input("exhibits-rows", "children"),
)


def _resolve_exhibit_plan(order, include, labels_on):
    """Sanitized [(key, label_or_None)] for included exhibits.

    Labels recompute from position: the first included exhibit is always
    A, the second B, and so on, irrespective of exhibit identity or the
    original ordering.
    """
    order = _sanitize_order(order)
    include = include or {}
    plan = []
    pos = 0
    for k in order:
        if not include.get(k, True):
            continue
        plan.append((k, chr(65 + pos) if labels_on else None))
        pos += 1
    return plan


def _build_exhibit_specs(st, plan, transfers_rows, hist_rows, pref_rows,
                         nv_rows, ocb_rows, ocb_range, start, end, step,
                         ocb_metric, graph_metric):
    """Build one spec per exhibit: caption/filename/story/footer.

    Returns (specs, skipped).  Raises ValueError when the Graph View chart
    cannot be rendered.  Shared by the ZIP and combined-PDF paths.
    """
    import export_pdf as _epdf
    transferee = (st.meta.get('transferee') or '').strip()
    number = _epdf._display_number(st.meta).strip()
    specs = []
    skipped = []

    def _table_spec(key, label, caption, filename, df, **kwargs):
        if df is None or df.empty:
            skipped.append(key)
            return
        labeled = _epdf._labeled_caption(caption, label)
        specs.append({
            "key": key,
            "label": label,
            "caption": labeled,
            "filename": filename,
            "story": _epdf._invoices_story(st.meta, df, labeled, **kwargs),
            "footer_center": _epdf._exhibit_footer_center(number, caption, label),
        })

    for key, label in plan:
        if key == "transfers":
            _table_spec(key, label, "Transfers in the Preference Period",
                        "Transfers_Preference_Period.pdf",
                        pd.DataFrame(transfers_rows or []),
                        columns=_epdf.TRANSFER_COLUMNS, money={"Transfer Amount"})
        elif key == "historical":
            _table_spec(key, label, "Historical Period — Invoices and Payments",
                        "Historical_Period.pdf", pd.DataFrame(hist_rows or []))
        elif key == "preference":
            _table_spec(key, label, "Preference Period — Invoices and Payments",
                        "Preference_Period.pdf", pd.DataFrame(pref_rows or []))
        elif key == "new-value":
            _table_spec(key, label, "New Value", "New_Value.pdf",
                        pd.DataFrame(nv_rows or []),
                        columns=_epdf.NEW_VALUE_COLUMNS,
                        money=_epdf._NEW_VALUE_MONEY)
        elif key == "ocb":
            df = pd.DataFrame(ocb_rows or [])
            if df.empty:
                skipped.append(key)
                continue
            st.ocb_metric = ocb_metric or st.ocb_metric or "Invoice to Payment"
            sel_rows, summary = _ocb_summary_and_rows(st, ocb_range, start, end, step)
            caption = "Ordinary Course of Business"
            labeled = _epdf._labeled_caption(caption, label)
            specs.append({
                "key": key,
                "label": label,
                "caption": labeled,
                "filename": "Ordinary_Course.pdf",
                "story": _epdf._ocb_story(
                    transferee, number, labeled,
                    _epdf._ocb_table(df, sel_rows), summary),
                "footer_center": _epdf._exhibit_footer_center(number, caption, label),
            })
        elif key == "graph-view":
            metric = graph_metric or "Invoice to Payment"
            fig, bins_df = build_overlay_figure(
                pd.DataFrame(hist_rows or []), pd.DataFrame(pref_rows or []), metric)
            if not fig.data:
                skipped.append(key)
                continue
            try:
                png = fig.to_image(format="png", width=1200, height=600)
            except Exception as e:
                raise ValueError(f"Could not render the Graph View chart: {e}")
            caption = "Graph View"
            labeled = _epdf._labeled_caption(caption, label)
            specs.append({
                "key": key,
                "label": label,
                "caption": labeled,
                "filename": "Graph_View.pdf",
                "story": _epdf._graph_story(st.meta, png, bins_df, metric, labeled),
                "footer_center": _epdf._exhibit_footer_center(number, caption, label),
            })
    return specs, skipped


def _exhibit_status_msg(files, skipped):
    included = ", ".join(f for _, f in files)
    msg = f"Generated {len(files)} exhibit(s): {included}."
    if skipped:
        msg += f" Skipped (no data): {', '.join(sorted(set(skipped)))}."
    return dbc.Alert(msg, color="success")


def _exhibits_common_inputs():
    return [
        State("exhibits-order", "data"),
        State("exhibits-include", "data"),
        State("exhibits-labels-toggle", "value"),
        State("transfers-pref", "rowData"),
        State("historical", "rowData"),
        State("preference", "rowData"),
        State("new_value", "rowData"),
        State("ocb_grid", "rowData"),
        State("ocb-range", "data"),
        State("ocb-start", "value"),
        State("ocb-end", "value"),
        State("ocb-step", "value"),
        State("ocb-metric", "value"),
        State("graph-metric", "value"),
    ]


@callback(
    Output("download-exhibits-zip", "data"),
    Output("exhibits-status", "children"),
    Input("exhibits-generate-btn", "n_clicks"),
    *_exhibits_common_inputs(),
    prevent_initial_call=True
)
def generate_exhibits_zip(n_clicks, order, include, labels_on,
                          transfers_rows, hist_rows, pref_rows, nv_rows,
                          ocb_rows, ocb_range, start, end, step, ocb_metric,
                          graph_metric):
    import re
    import zipfile
    from io import BytesIO
    st = session.get_state()
    if not st.loaded or st.meta is None:
        return dash.no_update, dbc.Alert(
            "Load a subcase before generating exhibits.", color="warning")
    plan = _resolve_exhibit_plan(order, include, labels_on)
    if not plan:
        return dash.no_update, dbc.Alert(
            "Select at least one exhibit to include.", color="warning")
    transferee = (st.meta.get('transferee') or '').strip()
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Exhibits'
    try:
        specs, skipped = _build_exhibit_specs(
            st, plan, transfers_rows, hist_rows, pref_rows, nv_rows,
            ocb_rows, ocb_range, start, end, step, ocb_metric, graph_metric)
    except ValueError as e:
        return dash.no_update, dbc.Alert(str(e), color="danger")
    if not specs:
        return dash.no_update, dbc.Alert(
            "Nothing to include — the selected exhibits have no data "
            "for this subcase.", color="warning")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for spec in specs:
            pdf_bytes = export_pdf.build_doc_from_story(
                st.meta, spec["caption"], spec["story"], spec["footer_center"])
            zf.writestr(f"{safe_name}_{spec['filename']}", pdf_bytes)
    zip_bytes = buf.getvalue()
    files = [(s["key"], f"{safe_name}_{s['filename']}") for s in specs]
    return (dcc.send_bytes(lambda b: b.write(zip_bytes), f"{safe_name}_Exhibits.zip"),
            _exhibit_status_msg(files, skipped))


@callback(
    Output("download-exhibits-combined", "data"),
    Output("exhibits-status", "children"),
    Input("exhibits-combined-btn", "n_clicks"),
    *_exhibits_common_inputs(),
    prevent_initial_call=True
)
def generate_exhibits_combined(n_clicks, order, include, labels_on,
                               transfers_rows, hist_rows, pref_rows, nv_rows,
                               ocb_rows, ocb_range, start, end, step,
                               ocb_metric, graph_metric):
    import re
    st = session.get_state()
    if not st.loaded or st.meta is None:
        return dash.no_update, dbc.Alert(
            "Load a subcase before generating exhibits.", color="warning")
    plan = _resolve_exhibit_plan(order, include, labels_on)
    if not plan:
        return dash.no_update, dbc.Alert(
            "Select at least one exhibit to include.", color="warning")
    transferee = (st.meta.get('transferee') or '').strip()
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', transferee) or 'Exhibits'
    try:
        specs, skipped = _build_exhibit_specs(
            st, plan, transfers_rows, hist_rows, pref_rows, nv_rows,
            ocb_rows, ocb_range, start, end, step, ocb_metric, graph_metric)
    except ValueError as e:
        return dash.no_update, dbc.Alert(str(e), color="danger")
    if not specs:
        return dash.no_update, dbc.Alert(
            "Nothing to include — the selected exhibits have no data "
            "for this subcase.", color="warning")
    pdf_bytes = export_pdf.build_combined_pdf(
        st.meta,
        [{"label": s["label"], "caption": s["caption"], "story": s["story"],
          "footer_center": s["footer_center"]} for s in specs])
    files = [(s["key"], s["caption"]) for s in specs]
    return (dcc.send_bytes(lambda b: b.write(pdf_bytes), f"{safe_name}_Exhibits.pdf"),
            _exhibit_status_msg(files, skipped))


@callback(
    Output("pref-stats-table", "children"),
    Input("preference", "rowData"),
    prevent_initial_call=True
)

def update_pref_stats(rowData):
    st = session.get_state()
    df = pd.DataFrame(rowData)
    if df.empty or st.df_historical is None or st.df_historical.empty:
        return ""
    weighted_dso = analysis.calc_weighted_dso(df)
    weighted_dpd = analysis.calc_weighted_dpd(df)
    dso_diff = analysis.compare_hist_pref(st.df_historical, df)
    dpd_diff = analysis.compare_hist_pref(st.df_historical, df, metric="Days Past Due")
    avg_invoice = df["Invoice Amount"].mean()
    avg_transfer = df["Transfer Amount"].mean()
    invoices_per_transfer = df.groupby("Transfer Number").size().mean()

    rows = [
        ("Number of Invoices in Preference Period", str(len(df))),
        ("Weighted Average Days Outstanding", f"{weighted_dso:.2f}"),
        ("Percentage Difference from Historical Weighted Average Days Outstanding", f"{dso_diff:.2f}%"),
        ("Weighted Average Days Past Due", f"{weighted_dpd:.2f}"),
        ("Percentage Difference from Historical Weighted Days Past Due", f"{dpd_diff:.2f}%"),
        ("Average Amount of Invoices", f"${avg_invoice:,.2f}"),
        ("Average Amount of Transfers", f"${avg_transfer:,.2f}"),
        ("Number of Invoices Per Transfer", f"{invoices_per_transfer:.2f}"),
    ]

    return _stats_table(rows)


_INSIGHT_FLAG_STYLES = {
    "warning": {"backgroundColor": "var(--bs-warning)", "color": "#000", "fontWeight": "600"},
    "danger": {"backgroundColor": "var(--bs-danger)", "color": "#fff", "fontWeight": "600"},
}


@callback(
    Output("insights-table", "children"),
    Input("historical", "rowData"),
    Input("preference", "rowData"),
    prevent_initial_call=True,
)
def update_insights(hist_rows, pref_rows):
    hist_df = pd.DataFrame(hist_rows or [])
    pref_df = pd.DataFrame(pref_rows or [])
    rows = analysis.build_insights(hist_df, pref_df)
    if not rows:
        return ""
    body = []
    for row in rows:
        diff_cell = html.Td(row["diff"])
        if row["flag"] in _INSIGHT_FLAG_STYLES:
            diff_cell = html.Td(row["diff"], style=_INSIGHT_FLAG_STYLES[row["flag"]])
        body.append(html.Tr([
            html.Th(row["metric"], style={"fontWeight": "600"}),
            html.Td(row["hist"]),
            html.Td(row["pref"]),
            diff_cell,
        ]))
    return dbc.Table(
        [
            html.Thead(html.Tr([
                html.Th("Metric"),
                html.Th("Historical Period"),
                html.Th("Preference Period"),
                html.Th("Difference"),
            ])),
            html.Tbody(body),
        ],
        bordered=True,
        hover=True,
        responsive=True,
        className="mb-0",
        style={"width": "100%"},
    )

@callback(
    Output("autosave-status", "children"),
    Input("ocb-range", "data"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-total-range-flag", "data"),
    Input("new_value", "rowData"),
    Input("ocb-metric", "value"),
    prevent_initial_call=True
)
def autosave_settings(ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_rowData, ocb_metric):
    st = session.get_state()
    auth.guard_edit_subcase(st.active_subcase_id)
    from datetime import datetime
    # A mid-gesture range (start clicked, end not yet) must never be
    # persisted: the loader discards end-less ranges, so saving one would
    # read back as a reset.  Normalize to an explicit clear instead.
    if ocb_range is not None and ocb_range.get("end") is None:
        ocb_range = None
    nv_settings = []
    for row in nv_rowData or []:
        inv = row.get("Invoice Number")
        if pd.notna(inv):
            reason = row.get('Ordinary Exclusion')
            if reason is not None and isinstance(reason, float) and np.isnan(reason):
                reason = None
            nv_settings.append({
                'invoice_number': inv,
                'remove': bool(row.get('Remove', False)),
                'reason': reason or None,
            })
    store.save_case_settings(st.active_subcase_id, ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_settings, ocb_metric)
    if ocb_range is not None and ocb_range.get("end") is not None:
        a, b = sorted((int(ocb_range["start"]), int(ocb_range["end"])))
        range_desc = f"range rows {a}–{b}"
    else:
        range_desc = "range none"
    return (f"Saved {datetime.now().strftime('%H:%M:%S')} · "
            f"subcase {st.active_subcase_id} · {range_desc}")


@callback(
    Output("user-badge", "children"),
    Output("user-badge-strip", "children"),
    Input("shell-boot", "data"),
    Input("account-saved", "data"),
)
def update_user_badge(_boot, _saved):
    badge = _user_badge(auth.current_user)
    return badge, badge


@callback(
    Output("admin-nav-btn", "style"),
    Output("firm-options-nav-btn", "style"),
    Output("email-settings-nav-btn", "style"),
    Output("import-nav-btn", "style"),
    Output("test-data-nav-btn", "style"),
    Output("admin-nav-section", "style"),
    Output("case-mgmt-nav-section", "style"),
    Input("manage-boot", "data"),
)
def manage_admin_gate(_):
    u = auth.current_user
    show = {"display": "block"}
    hide = {"display": "none"}
    if u.role == "admin":
        return show, show, show, show, show, show, show
    if u.role == "firm_admin":
        return show, show, hide, show, hide, show, show
    if u.role == "case_manager":
        return hide, hide, hide, show, hide, hide, show
    return hide, hide, hide, hide, hide, hide, hide


@callback(
    Output("portfolio-nav-section", "style"),
    Input("shell-boot", "data"),
)
def analysis_portfolio_gate(_):
    # Portfolio is for case managers and above; hide the whole section
    # (header + link + divider) so regular users see no empty header.
    if auth.current_user.role == "user":
        return {"display": "none"}
    return {"display": "block"}


def _portfolio_stat(label, value, icon):
    return html.Div(style={
        "flex": "1 1 0", "minWidth": "180px", "padding": "14px 18px",
        "borderRadius": "0.375rem", "border": "1px solid var(--bs-border-color)",
        "backgroundColor": "var(--bs-tertiary-bg)",
    }, children=[
        html.Div(value, style={"fontSize": "1.35rem", "fontWeight": "700"}),
        html.Div(children=[
            html.I(className=f"{icon} me-2", style={"opacity": ".8"}),
            html.Span(label, className="text-muted"),
        ]),
    ])


@callback(
    Output("portfolio-main-select", "options"),
    Output("portfolio-main-select", "value"),
    Input("portfolio-boot", "data"),
)
def portfolio_boot(_):
    u = auth.guard("admin", "firm_admin", "case_manager")
    opts = store.list_visible_main_options(u.id, u.role, firm_id=auth.firm_scope(u))
    value = opts[0]["value"] if opts else None
    return opts, value


@callback(
    Output("portfolio-feedback", "children"),
    Output("portfolio-summary-header", "children"),
    Output("portfolio-table", "children"),
    Input("portfolio-main-select", "value"),
)
def portfolio_load(main_id):
    u = auth.guard("admin", "firm_admin", "case_manager")
    scope = auth.firm_scope(u)
    allowed = {int(o["value"]) for o in store.list_visible_main_options(u.id, u.role, firm_id=scope)}
    if main_id is None:
        return "Select a main bankruptcy case to view its portfolio.", [], []
    if int(main_id) not in allowed:
        return "You do not have rights to view this case.", [], []
    rows = portfolio.main_case_rollup(int(main_id), firm_id=scope)
    if not rows:
        return "This main case has no subcases yet.", [], []
    total_transfers = sum(r["total_transfers"] for r in rows)
    total_net = sum(r["net_preference"] for r in rows)
    recoverable = (total_net / total_transfers * 100) if total_transfers else None
    header = html.Div(style={"display": "flex", "flexWrap": "wrap", "gap": "16px"}, children=[
        _portfolio_stat("Total Transfers (all subcases)",
                        f"${total_transfers:,.2f}", "fa-solid fa-money-bill-transfer"),
        _portfolio_stat("Total Net Preference",
                        f"${total_net:,.2f}", "fa-solid fa-dollar-sign"),
        _portfolio_stat("Estimated % of Transfers Recoverable",
                        f"{recoverable:.1f}%" if recoverable is not None else "\u2014",
                        "fa-solid fa-percent"),
        _portfolio_stat("Subcases", str(len(rows)), "fa-solid fa-folder-open"),
    ])
    body = [
        html.Tr([html.Td(r["case_name"]),
                 html.Td(f"${r['total_transfers']:,.2f}", className="text-end"),
                 html.Td(f"${r['total_new_value']:,.2f}", className="text-end"),
                 html.Td(f"${r['ordinary_course']:,.2f}", className="text-end"),
                 html.Td(f"${r['net_preference']:,.2f}", className="text-end")])
        for r in rows
    ]
    table = html.Table(className="table table-striped table-hover", children=[
        html.Thead(html.Tr([
            html.Th("Case Name"),
            html.Th("Total Transfers", className="text-end"),
            html.Th("Total New Value", className="text-end"),
            html.Th("Total Ordinary Course", className="text-end"),
            html.Th("Total Net Preference", className="text-end"),
        ])),
        html.Tbody(body),
    ])
    return "", header, table


def _avatar_swatches(selected_color):
    return [
        dbc.Button(html.Div(), id={"type": "avatar-swatch", "index": color},
                   n_clicks=0, className="avatar-swatch" + (" avatar-swatch-selected" if color == selected_color else ""),
                   style={"backgroundColor": color})
        for color in AVATAR_COLORS
    ]


def _password_entropy_bits(pw):
    """Total Shannon entropy (bits) of a password string.

    Case-sensitive and distribution-based: -sum(p(c) * log2(p(c))) * len.
    Empty input scores 0.0. Advisory only; never blocks saving.
    """
    import math
    from collections import Counter
    if not pw:
        return 0.0
    n = len(pw)
    return max(0.0, -sum((c / n) * math.log2(c / n) for c in Counter(pw).values()) * n)


def _password_strength(bits):
    """Map entropy bits to (label, badge color): Poor red, Marginal amber, Good green."""
    if bits is None or bits < 28:
        return "Poor", "danger"
    if bits <= 50:
        return "Marginal", "warning"
    return "Good", "success"


def _password_strength_display(pw):
    bits = _password_entropy_bits(pw or "")
    if not pw:
        return html.Span("Enter a new password to see its strength.", className="text-muted")
    label, color = _password_strength(bits)
    return html.Span(children=[
        dbc.Badge(label, color=color, className="me-2"),
        html.Span(f"~{bits:.0f} bits of entropy", className="text-muted"),
    ])


@callback(
    Output("account-avatar-color", "data"),
    Output("account-name", "value"),
    Output("account-email", "value"),
    Output("account-avatar-preview", "children"),
    Output("avatar-swatches", "children"),
    Input("account-boot", "data"),
)
def account_load(_):
    u = auth.current_user
    color = _avatar_color_for(u.username, u.avatar_color)
    return color, (u.name or ""), (u.email or ""), _avatar_circle(u.name, u.username, color, 64), _avatar_swatches(color)


@callback(
    Output("account-avatar-color", "data", allow_duplicate=True),
    Output("account-avatar-preview", "children", allow_duplicate=True),
    Output("avatar-swatches", "children", allow_duplicate=True),
    Input({"type": "avatar-swatch", "index": ALL}, "n_clicks"),
    State("account-name", "value"),
    prevent_initial_call=True,
)
def account_pick_color(n_clicks_values, name):
    if not any((n or 0) > 0 for n in (n_clicks_values or [])):
        raise PreventUpdate
    trig = dash.callback_context.triggered[0]['prop_id']
    index = None
    if '.' in trig:
        try:
            index = json.loads(trig.split('.')[0])['index']
        except (ValueError, KeyError):
            index = None
    u = auth.current_user
    color = _avatar_color_for(u.username, index)
    return color, _avatar_circle(name, u.username, color, 64), _avatar_swatches(color)


@callback(
    Output("account-status", "children"),
    Output("account-saved", "data"),
    Input("account-save-trigger", "data"),
    State("account-name", "value"),
    State("account-email", "value"),
    State("account-avatar-color", "data"),
    prevent_initial_call=True,
)
def account_save(trigger, name, email, avatar_color):
    if not trigger:
        raise PreventUpdate
    u = auth.current_user
    from datetime import datetime
    n_name = (name or "").strip()
    n_email = (email or "").strip()
    n_color = avatar_color if avatar_color in AVATAR_COLORS else None
    store.update_user_profile(u.id, name=n_name, email=n_email, avatar_color=n_color)
    return dbc.Alert("Settings saved.", color="success"), datetime.now().isoformat()


@callback(
    Output("account-save-trigger", "data"),
    Input("account-save-btn", "n_clicks"),
    prevent_initial_call=True,
)
def _arm_account_save(n):
    return n or 0


@callback(
    Output("account-pw-strength", "children"),
    Input("account-new-pw", "value"),
)
def account_pw_meter(new_pw):
    return _password_strength_display(new_pw)


@callback(
    Output("account-pw-status", "children"),
    Output("account-current-pw", "value"),
    Output("account-new-pw", "value"),
    Output("account-confirm-pw", "value"),
    Input("account-pw-btn", "n_clicks"),
    State("account-current-pw", "value"),
    State("account-new-pw", "value"),
    State("account-confirm-pw", "value"),
    prevent_initial_call=True,
)
def account_change_password(n, current_pw, new_pw, confirm_pw):
    if not n:
        raise PreventUpdate
    if not current_pw or not new_pw or not confirm_pw:
        return dbc.Alert("Enter your current password and type the new password twice.", color="warning"), dash.no_update, dash.no_update, dash.no_update
    if not (new_pw or "").strip():
        return dbc.Alert("New password cannot be blank.", color="warning"), dash.no_update, dash.no_update, dash.no_update
    # Re-read the user row so verification uses the live hash, not a stale session copy.
    row = store.get_user_by_id(auth.current_user.id)
    if row is None or not check_password_hash(row["password_hash"], current_pw):
        return dbc.Alert("Current password is incorrect.", color="danger"), dash.no_update, dash.no_update, dash.no_update
    if new_pw != confirm_pw:
        return dbc.Alert("New passwords do not match.", color="warning"), dash.no_update, dash.no_update, dash.no_update
    store.reset_user_password(row["id"], generate_password_hash(new_pw))
    bits = _password_entropy_bits(new_pw)
    label, _ = _password_strength(bits)
    return dbc.Alert(f"Password changed. Strength: {label} (~{bits:.0f} bits).", color="success"), "", "", ""


@callback(
    Output("email-smtp-host", "value"),
    Output("email-smtp-port", "value"),
    Output("email-smtp-username", "value"),
    Output("email-smtp-from", "value"),
    Output("email-smtp-use-auth", "value"),
    Input("email-boot", "data"),
)
def email_load(_):
    auth.guard("admin")
    s = store.get_email_settings()
    return (
        s["smtp_host"],
        s["smtp_port"],
        s["smtp_username"],
        s["smtp_from"],
        s["smtp_use_auth"],
    )


@callback(
    Output("email-status", "children"),
    Input("email-save-btn", "n_clicks"),
    State("email-smtp-host", "value"),
    State("email-smtp-port", "value"),
    State("email-smtp-username", "value"),
    State("email-smtp-password", "value"),
    State("email-smtp-from", "value"),
    State("email-smtp-use-auth", "value"),
    prevent_initial_call=True,
)
def email_save(n, host, port, username, password, frm, use_auth):
    if not n:
        raise PreventUpdate
    auth.guard("admin")
    try:
        store.save_email_settings(host, port, username, password, frm, smtp_use_auth=use_auth)
    except ValueError as e:
        return dbc.Alert(str(e), color="danger")
    return dbc.Alert("Email settings saved.", color="success")


@callback(
    Output("email-status", "children", allow_duplicate=True),
    Input("email-test-btn", "n_clicks"),
    State("email-smtp-host", "value"),
    State("email-smtp-port", "value"),
    State("email-smtp-username", "value"),
    State("email-smtp-password", "value"),
    State("email-smtp-from", "value"),
    State("email-smtp-use-auth", "value"),
    prevent_initial_call=True,
)
def email_test(n, host, port, username, password, frm, use_auth):
    auth.guard("admin")
    host = (host or "").strip()
    if not host:
        return dbc.Alert("Enter an SMTP server before testing.", color="warning")
    if port is None or str(port).strip() == "":
        return dbc.Alert("Enter an SMTP port before testing.", color="warning")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return dbc.Alert("SMTP port must be a whole number.", color="warning")
    sender = (frm or "").strip()
    recipient = (auth.current_user.email or "").strip()
    if not sender:
        return dbc.Alert("Enter a 'From address' before sending a test email.", color="warning")
    if not recipient:
        return dbc.Alert("Your account has no email address; set one on the Account page first.", color="warning")
    try:
        smtp_mail.send_email(
            host, port, username, password,
            sender=sender, to=recipient,
            subject="PAT application email test",
            body=("This is a test email from the PAT application. "
                  f"Sent to {recipient} via {host}:{port}."),
            use_auth=bool(use_auth),
        )
    except Exception as e:
        return dbc.Alert(f"Test email failed: {e}", color="danger")
    return dbc.Alert(f"Test email sent to {recipient}.", color="success")


@callback(
    Output("admin-users-grid", "rowData", allow_duplicate=True),
    Output("admin-notice", "children"),
    Output("grants-grid", "rowData", allow_duplicate=True),
    Output("grant-user", "options"),
    Input("users-boot", "data"),
    Input("users-firm-select", "value"),
    prevent_initial_call=True
)
def admin_load(_, users_firm):
    u = auth.guard("admin", "firm_admin")
    if u.role == "admin":
        if not users_firm:
            return dash.no_update, dash.no_update, dash.no_update, dash.no_update
        scope = int(users_firm)
    else:
        scope = auth.firm_scope(u)
    users = store.list_users(firm_id=scope)
    firms = {f["id"]: f["name"] for f in store.list_firms()}
    for row in users:
        row["firm_name"] = firms.get(row["firm_id"], "")
    user_map = [{"label": u2["username"], "value": u2["id"]} for u2 in users]
    return users, f"Managing users as {auth.current_user.username}.", store.list_all_grants(user_ids=[x["id"] for x in users]), user_map


@callback(
    Output("new-user-role", "options"),
    Output("admin-role-select", "options"),
    Output("admin-firm-select", "options"),
    Output("admin-firm-select", "value"),
    Output("admin-firm-wrap", "style"),
    Input("users-boot", "data"),
)
def users_role_options(_):
    u = auth.current_user
    if u.role == "firm_admin":
        opts = [{"label": r, "value": r} for r in auth.ROLES if r != "admin"]
        return opts, opts, [], None, {"display": "none"}
    opts = [{"label": r, "value": r} for r in auth.ROLES]
    firms = store.list_firms()
    firm_opts = [{"label": f["name"], "value": f["id"]} for f in firms]
    value = firms[0]["id"] if firms else None
    return opts, opts, firm_opts, value, {"display": "block"}


@callback(
    Output("firm-options-firm", "options"),
    Output("firm-options-firm", "value"),
    Output("firm-options-firm-wrap", "style"),
    Output("firm-options-name", "value", allow_duplicate=True),
    Output("firm-options-new-wrap", "style"),
    Output("firm-delete-wrap", "style"),
    Input("firm-options-boot", "data"),
    prevent_initial_call='initial_duplicate',
)
def firm_options_load(_):
    u = auth.current_user
    if u.role == "admin":
        firms = store.list_firms()
        opts = [{"label": f["name"], "value": f["id"]} for f in firms]
        val = firms[0]["id"] if firms else 1
        wrap = {"display": "block", "maxWidth": "640px", "marginBottom": "16px"}
        new_wrap = {"display": "block"}
        del_wrap = {"display": "block"}
    elif u.role == "firm_admin":
        fid = auth.firm_scope(u)
        opts = [{"label": store.get_firm(fid)["name"], "value": fid}]
        val = fid
        wrap = {"display": "none"}
        new_wrap = {"display": "none"}
        del_wrap = {"display": "none"}
    else:
        opts, val, wrap, new_wrap, del_wrap = (
            [], None, {"display": "none"}, {"display": "none"}, {"display": "none"},
        )
    return opts, val, wrap, dash.no_update, new_wrap, del_wrap


@callback(
    Output("firm-options-name", "value"),
    Input("firm-options-firm", "value"),
    prevent_initial_call='initial_duplicate',
)
def firm_options_pick(firm_id):
    if firm_id is None:
        return ""
    firm = store.get_firm(firm_id)
    return firm["name"] if firm else ""


@callback(
    Output("firm-save-trigger", "data", allow_duplicate=True),
    Input("firm-options-save", "n_clicks"),
    prevent_initial_call=True,
)
def arm_firm_save(n):
    return n


@callback(
    Output("firm-options-status", "children"),
    Output("firm-options-firm", "options", allow_duplicate=True),
    Output("firm-options-firm", "value", allow_duplicate=True),
    Input("firm-save-trigger", "data"),
    State("firm-options-firm", "value"),
    State("firm-options-name", "value"),
    prevent_initial_call=True,
)
def firm_options_save(trigger, firm_id, name):
    if not trigger:
        raise PreventUpdate
    u = auth.guard("admin", "firm_admin")
    fid = firm_id if u.role == "admin" and firm_id else auth.firm_scope(u)
    if fid is None:
        return dbc.Alert("No firm selected.", color="warning"), dash.no_update, dash.no_update
    if not name or not name.strip():
        return dbc.Alert("Firm name cannot be empty.", color="danger"), dash.no_update, dash.no_update
    try:
        store.update_firm_settings(fid, name=name.strip())
    except ValueError as e:
        return dbc.Alert(str(e), color="danger"), dash.no_update, dash.no_update
    opts = [{"label": f["name"], "value": f["id"]} for f in store.list_firms()]
    return dbc.Alert("Firm options saved.", color="success"), opts, fid


@callback(
    Output("firm-options-status", "children", allow_duplicate=True),
    Output("firm-options-firm", "options", allow_duplicate=True),
    Output("firm-options-firm", "value", allow_duplicate=True),
    Output("firm-options-name", "value", allow_duplicate=True),
    Input("firm-options-new-btn", "n_clicks"),
    prevent_initial_call=True,
)
def create_firm(n):
    u = auth.guard("admin")
    try:
        new_id = store.create_firm()
    except ValueError as e:
        return dbc.Alert(str(e), color="danger"), dash.no_update, dash.no_update, dash.no_update
    firms = store.list_firms()
    opts = [{"label": f["name"], "value": f["id"]} for f in firms]
    firm = store.get_firm(new_id)
    return dbc.Alert(f"Firm created (ID {new_id}). Rename it in the Firm Name field.", color="success"), opts, new_id, firm["name"]


@callback(
    Output("firm-delete-modal", "is_open"),
    Output("firm-delete-firm-name", "children"),
    Input("firm-delete-btn", "n_clicks"),
    State("firm-options-firm", "value"),
    prevent_initial_call=True,
)
def open_firm_delete(n, firm_id):
    auth.guard("admin")
    if not n:
        raise PreventUpdate
    firm = store.get_firm(firm_id)
    return True, firm["name"] if firm else "Unknown firm"


@callback(
    Output("firm-delete-modal", "is_open", allow_duplicate=True),
    Input("firm-delete-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def close_firm_delete(n):
    auth.guard("admin")
    if not n:
        raise PreventUpdate
    return False


@callback(
    Output("firm-options-status", "children", allow_duplicate=True),
    Output("firm-options-firm", "options", allow_duplicate=True),
    Output("firm-options-firm", "value", allow_duplicate=True),
    Output("firm-options-name", "value", allow_duplicate=True),
    Output("firm-delete-modal", "is_open", allow_duplicate=True),
    Input("firm-delete-confirm", "n_clicks"),
    State("firm-options-firm", "value"),
    prevent_initial_call=True,
)
def delete_firm_cb(n, firm_id):
    auth.guard("admin")
    if not n:
        raise PreventUpdate
    if firm_id is None:
        return (dbc.Alert("No firm selected.", color="warning"),
                dash.no_update, dash.no_update, dash.no_update, False)
    if int(auth.current_user.firm_id) == int(firm_id):
        return (dbc.Alert("You cannot delete the firm you belong to.", color="danger"),
                dash.no_update, dash.no_update, dash.no_update, False)
    try:
        old_name = store.delete_firm(firm_id)
    except ValueError as e:
        return (dbc.Alert(str(e), color="danger"),
                dash.no_update, dash.no_update, dash.no_update, False)
    firms = store.list_firms()
    opts = [{"label": f["name"], "value": f["id"]} for f in firms]
    val = firms[0]["id"] if firms else None
    nxt_name = store.get_firm(val)["name"] if val else ""
    return (dbc.Alert(f"Deleted firm '{old_name}'.", color="success"),
            opts, val, nxt_name, False)


@callback(
    Output("new-user-email-error", "children"),
    Input("new-user-email", "value"),
)
def new_user_email_validation(value):
    value = (value or "").strip()
    if not value:
        return "Email is required."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return "Enter a valid email address."
    return ""


@callback(
    Output("admin-users-grid", "rowData"),
    Output("admin-status", "children"),
    Output("users-boot", "data", allow_duplicate=True),
    Input("admin-create-btn", "n_clicks"),
    Input("admin-set-role-btn", "n_clicks"),
    Input("admin-reset-btn", "n_clicks"),
    Input("admin-toggle-btn", "n_clicks"),
    Input("admin-firm-btn", "n_clicks"),
    Input("admin-delete-btn", "n_clicks"),
    State("new-user-username", "value"),
    State("new-user-password", "value"),
    State("new-user-email", "value"),
    State("new-user-role", "value"),
    State("admin-role-select", "value"),
    State("admin-reset-pw", "value"),
    State("admin-firm-select", "value"),
    State("admin-users-grid", "selectedRows"),
    State("users-firm-select", "value"),
    prevent_initial_call=True
)
def manage_users(c_create, c_role, c_reset, c_toggle, c_firm, c_delete,
                 new_name, new_pw, new_email, new_role, sel_role, reset_pw, sel_firm, selected, users_firm):
    u = auth.guard("admin", "firm_admin")
    trig = dash.callback_context.triggered_id
    uid = uname = None
    if selected:
        uid, uname = selected[0].get("id"), selected[0].get("username")
    status = ""
    bump = dash.no_update

    if trig == "admin-create-btn" and new_name and new_pw:
        name = new_name.strip()
        email = (new_email or "").strip()
        if store.get_user_by_username(name):
            status = f"Username '{name}' already exists."
        elif u.role == "firm_admin" and (new_role or "user") == "admin":
            status = "Firm admins cannot create admin users."
        elif not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            status = "Enter a valid email address."
        else:
            fid = int(users_firm) if u.role == "admin" and users_firm else auth.firm_scope(u)
            store.create_user(name, generate_password_hash(new_pw), new_role or "user", email, firm_id=fid)
            status = f"Created user '{name}' ({new_role or 'user'})."
    elif trig == "admin-create-btn":
        status = "Enter a username, password, and a valid email address to create a user."
    elif trig == "admin-set-role-btn":
        if u.role == "firm_admin" and sel_role == "admin":
            status = "Firm admins cannot assign the admin role."
        elif uid is not None and sel_role:
            store.set_user_role(uid, sel_role)
            status = f"Role set to '{sel_role}' for '{uname}'."
        else:
            status = "Select a user and a role."
    elif trig == "admin-reset-btn":
        if uid is not None and reset_pw:
            store.reset_user_password(uid, generate_password_hash(reset_pw))
            status = f"Password reset for '{uname}'."
        else:
            status = "Select a user and enter a new password."
    elif trig == "admin-toggle-btn":
        if uid is not None:
            state = store.get_user_by_id(uid)
            new_active = not bool(state['active'])
            store.set_user_active(uid, new_active)
            status = f"'{uname}' {'activated' if new_active else 'deactivated'}."
        else:
            status = "Select a user."
    elif trig == "admin-firm-btn":
        if u.role != "admin":
            status = "Only admins can change a user's firm."
        elif uid is None or not sel_firm:
            status = "Select a user and a firm."
        else:
            target = int(sel_firm)
            current = store.get_user_by_id(uid)
            if current is None:
                status = "Select a user."
            elif str(current["id"]) == str(auth.current_user.id):
                status = "You cannot change your own firm."
            elif int(current["firm_id"]) == target:
                status = f"'{uname}' is already in that firm."
            else:
                store.set_user_firm(uid, target)
                store.clear_user_grants(uid)
                firm_name = store.get_firm(target)["name"]
                status = f"Moved '{uname}' to '{firm_name}' (grants cleared)."
                bump = (users_firm or 0) + 1
    elif trig == "admin-delete-btn":
        if uid is not None:
            if str(auth.current_user.id) == str(uid):
                status = "You cannot delete your own account."
            else:
                store.delete_user(uid)
                status = f"Deleted user '{uname}'."
        else:
            status = "Select a user."

    ret_fid = int(users_firm) if u.role == "admin" and users_firm else auth.firm_scope(u)
    return store.list_users(firm_id=ret_fid), status, bump


@callback(
    Output("cm-create-mode", "data"),
    Output("cm-main", "value"),
    Output("cm-case-name", "value"),
    Output("cm-case-number", "value"),
    Output("cm-jurisdiction", "value"),
    Output("cm-judge", "value"),
    Output("cm-petition-date", "value"),
    *[Output(cid, "value") for cid, _ in _CM_CLIENT_FIELD_DEFS],
    Output("cm-client-state-custom", "value"),
    Input("cm-new-btn", "n_clicks"),
    prevent_initial_call=True,
)
def start_new_main(n_clicks):
    return (True, None, "", "", "", "", "") + ("",) * len(_CM_CLIENT_FIELD_DEFS) + ("",)


@callback(
    Output("cm-create-mode", "data", allow_duplicate=True),
    Input("cm-main", "value"),
    prevent_initial_call=True,
)
def reset_create_mode(main_id):
    if main_id is not None:
        return False
    raise PreventUpdate


@callback(
    Output("cm-case-name", "value"),
    Output("cm-case-number", "value"),
    Output("cm-jurisdiction", "value"),
    Output("cm-judge", "value"),
    Output("cm-petition-date", "value"),
    *[Output(cid, "value") for cid, _ in _CM_CLIENT_FIELD_DEFS],
    Output("cm-case-name", "disabled"),
    Output("cm-case-number", "disabled"),
    Output("cm-jurisdiction", "disabled"),
    Output("cm-judge", "disabled"),
    Output("cm-petition-date", "disabled"),
    *[Output(cid, "disabled") for cid, _ in _CM_CLIENT_FIELD_DEFS],
    Output("cm-client-state-custom", "value"),
    Output("cm-client-state-custom", "disabled"),
    Output("cm-client-state", "options"),
    Output("cm-save", "disabled"),
    Output("cm-save", "children"),
    Input("cm-main", "value"),
    Input("cm-create-mode", "data"),
)
def build_cm_details(main_id, create_mode):
    base_opts = _state_options_for(_db_custom_states_main())
    client_n = len(_CM_CLIENT_FIELD_DEFS)
    if main_id is None:
        if create_mode:
            return ("",) * 5 + ("",) * client_n + (False,) * 5 + (False,) * client_n + \
                   ("", False, base_opts, False, "Create Main Case")
        return (None,) * 5 + (None,) * client_n + (True,) * 5 + (True,) * client_n + \
               ("", True, base_opts, False, "Save Changes")
    m = store.get_main_by_id(main_id)
    can_edit = auth.can_edit_main(auth.current_user, main_id)
    case_values = (m["case_name"], m["case_number"], m.get("jurisdiction") or "",
                   m.get("judge") or "", m.get("petition_date") or "")
    client_values = tuple(m.get(key) or "" for _, key in _CM_CLIENT_FIELD_DEFS)
    case_disabled = (not can_edit,) * 5
    client_disabled = (not can_edit,) * client_n
    stored_state = m.get("client_state") or ""
    extra_opts = base_opts
    if stored_state and stored_state not in STATE_SET:
        extra_opts = _state_options_for(_db_custom_states_main() | {stored_state})
    return case_values + client_values + case_disabled + client_disabled + \
           ("", not can_edit, extra_opts, not can_edit, "Save Changes")


@callback(
    Output("cm-client-state-custom-wrap", "style"),
    Input("cm-client-state", "value"),
)
def toggle_cm_state_custom(client_state):
    return {"display": "block"} if client_state == CUSTOM_STATE_KEY else {"display": "none"}


@callback(
    Output("cm-new", "style"),
    Input("manage-boot", "data"),
)
def maincase_create_gate(_):
    if auth.current_user.role in ("admin", "firm_admin", "case_manager"):
        return dash.no_update
    return {"display": "none"}


@callback(
    Output("cm-create-mode", "data", allow_duplicate=True),
    Input("manage-boot", "data"),
    prevent_initial_call='initial_duplicate',
)
def cm_boot_create_mode(_):
    if auth.current_user.role not in ("admin", "firm_admin", "case_manager"):
        return dash.no_update
    if store.list_main_options(firm_id=auth.firm_scope(auth.current_user)):
        return dash.no_update
    return True


_SC_SUBCASE_FIELD_DEFS = [
    ("sc-transferee-name", "transferee_name"),
    ("sc-case-caption", "case_caption"),
    ("sc-file-number", "file_number"),
    ("sc-adversary-number", "adversary_number"),
    ("sc-filing-date", "filing_date"),
    ("sc-contact-name", "contact_name"),
    ("sc-contact-address", "contact_address"),
    ("sc-contact-address2", "contact_address2"),
    ("sc-contact-city", "contact_city"),
    ("sc-contact-state", "contact_state"),
    ("sc-contact-zip", "contact_zip"),
    ("sc-contact-phone", "contact_phone"),
    ("sc-contact-email", "contact_email"),
    ("sc-attorney-name", "attorney_name"),
    ("sc-attorney-firm", "attorney_firm"),
    ("sc-attorney-address", "attorney_address"),
    ("sc-attorney-address2", "attorney_address2"),
    ("sc-attorney-city", "attorney_city"),
    ("sc-attorney-state", "attorney_state"),
    ("sc-attorney-zip", "attorney_zip"),
    ("sc-attorney-phone", "attorney_phone"),
    ("sc-attorney-email", "attorney_email"),
    ("sc-local-counsel-name", "local_counsel_name"),
    ("sc-local-counsel-firm", "local_counsel_firm"),
    ("sc-local-counsel-address", "local_counsel_address"),
    ("sc-local-counsel-address2", "local_counsel_address2"),
    ("sc-local-counsel-city", "local_counsel_city"),
    ("sc-local-counsel-state", "local_counsel_state"),
    ("sc-local-counsel-zip", "local_counsel_zip"),
    ("sc-local-counsel-phone", "local_counsel_phone"),
    ("sc-local-counsel-email", "local_counsel_email"),
]


@callback(
    Output("sc-subcase", "options"),
    Output("sc-subcase", "value"),
    Input("sc-main", "value"),
)
def populate_sc_subcase(main_id):
    if main_id is None:
        return [], None
    opts = store.list_subcase_options(main_id)
    return opts, opts[0]["value"] if opts else None


@callback(
    Output("sc-new-subcase-btn", "style"),
    Input("manage-boot", "data"),
    prevent_initial_call='initial_duplicate',
)
def sc_new_subcase_visibility(_):
    u = auth.current_user
    if u.role in ("admin", "firm_admin", "case_manager"):
        return {"display": "block", "marginBottom": "8px"}
    return {"display": "none"}


@callback(
    Output("sc-subcase", "options", allow_duplicate=True),
    Output("sc-subcase", "value", allow_duplicate=True),
    Output("sc-subcase-status", "children", allow_duplicate=True),
    Input("sc-new-subcase-btn", "n_clicks"),
    State("sc-main", "value"),
    prevent_initial_call=True,
)
def sc_new_subcase(n, main_id):
    if not n:
        raise PreventUpdate
    user = auth.guard("admin", "firm_admin", "case_manager")
    if main_id is None:
        return dash.no_update, dash.no_update, dbc.Alert(
            "Select a main case first.", color="warning")
    try:
        firm_id = store.get_main_by_id(main_id)["firm_id"]
        new_id = store.create_subcase(main_id, "New Transferee", firm_id=firm_id)
    except ValueError as e:
        return dash.no_update, dash.no_update, dbc.Alert(str(e), color="danger")
    if user.role == "case_manager":
        store.grant_subcase(user.id, new_id)
    opts = store.list_subcase_options(main_id)
    return opts, new_id, dbc.Alert(
        f"Created subcase 'New Transferee' (ID {new_id}).", color="success")


@callback(
    [Output(cid, "value") for cid, _ in _SC_SUBCASE_FIELD_DEFS] +
    [Output(cid, "disabled") for cid, _ in _SC_SUBCASE_FIELD_DEFS] +
    [Output("sc-contact-state", "options"),
     Output("sc-attorney-state", "options"),
     Output("sc-local-counsel-state", "options"),
     Output("sc-contact-state-custom", "value"),
     Output("sc-attorney-state-custom", "value"),
     Output("sc-local-counsel-state-custom", "value"),
     Output("sc-contact-state-custom", "disabled"),
     Output("sc-attorney-state-custom", "disabled"),
     Output("sc-local-counsel-state-custom", "disabled")],
    Input("sc-subcase", "value"),
)
def populate_sc_subcase_fields(subcase_id):
    if subcase_id is None:
        base_opts = _state_options_for(_db_custom_states())
        return (None,) * (len(_SC_SUBCASE_FIELD_DEFS) * 2) + (base_opts, base_opts, base_opts, "", "", "", False, False, False)
    can_edit = auth.can_edit_subcase(auth.current_user, subcase_id)
    sub = store.get_subcase(subcase_id)
    values = tuple(sub.get(key) or "" for _, key in _SC_SUBCASE_FIELD_DEFS)
    disabled = tuple(not can_edit for _ in _SC_SUBCASE_FIELD_DEFS)
    extras = {sub.get("contact_state") or "", sub.get("attorney_state") or "", sub.get("local_counsel_state") or ""}
    options = _state_options_for(extras)
    return values + disabled + (options, options, options, "", "", "", not can_edit, not can_edit, not can_edit)


@callback(
    Output("sc-contact-state-custom-wrap", "style"),
    Output("sc-attorney-state-custom-wrap", "style"),
    Output("sc-local-counsel-state-custom-wrap", "style"),
    Input("sc-contact-state", "value"),
    Input("sc-attorney-state", "value"),
    Input("sc-local-counsel-state", "value"),
)
def toggle_state_custom(contact_state, attorney_state, local_counsel_state):
    def wrap_style(value):
        return {"display": "block"} if value == CUSTOM_STATE_KEY else {"display": "none"}
    return wrap_style(contact_state), wrap_style(attorney_state), wrap_style(local_counsel_state)


@callback(
    Output("sc-subcase-status", "children"),
    Output("sc-subcase", "options", allow_duplicate=True),
    Output("sc-subcase", "value", allow_duplicate=True),
    Input("sc-save-subcase", "n_clicks"),
    State("sc-subcase", "value"),
    State("sc-main", "value"),
    State("sc-transferee-name", "value"),
    State("sc-case-caption", "value"),
    State("sc-file-number", "value"),
    State("sc-adversary-number", "value"),
    State("sc-filing-date", "value"),
    State("sc-contact-name", "value"),
    State("sc-contact-address", "value"),
    State("sc-contact-address2", "value"),
    State("sc-contact-city", "value"),
    State("sc-contact-state", "value"),
    State("sc-contact-state-custom", "value"),
    State("sc-contact-zip", "value"),
    State("sc-contact-phone", "value"),
    State("sc-contact-email", "value"),
    State("sc-attorney-name", "value"),
    State("sc-attorney-firm", "value"),
    State("sc-attorney-address", "value"),
    State("sc-attorney-address2", "value"),
    State("sc-attorney-city", "value"),
    State("sc-attorney-state", "value"),
    State("sc-attorney-state-custom", "value"),
    State("sc-attorney-zip", "value"),
    State("sc-attorney-phone", "value"),
    State("sc-attorney-email", "value"),
    State("sc-local-counsel-name", "value"),
    State("sc-local-counsel-firm", "value"),
    State("sc-local-counsel-address", "value"),
    State("sc-local-counsel-address2", "value"),
    State("sc-local-counsel-city", "value"),
    State("sc-local-counsel-state", "value"),
    State("sc-local-counsel-state-custom", "value"),
    State("sc-local-counsel-zip", "value"),
    State("sc-local-counsel-phone", "value"),
    State("sc-local-counsel-email", "value"),
    prevent_initial_call=True,
)
def save_sc_subcase(n,
                    subcase_id, main_id,
                    transferee_name, case_caption, file_number, adversary_number, filing_date,
                    contact_name, contact_address, contact_address2, contact_city, contact_state,
                    contact_state_custom, contact_zip, contact_phone, contact_email,
                    attorney_name, attorney_firm, attorney_address, attorney_address2, attorney_city,
                    attorney_state, attorney_state_custom, attorney_zip, attorney_phone, attorney_email,
                    local_counsel_name, local_counsel_firm, local_counsel_address, local_counsel_address2,
                    local_counsel_city, local_counsel_state, local_counsel_state_custom, local_counsel_zip,
                    local_counsel_phone, local_counsel_email):
    if subcase_id is None:
        return dbc.Alert("Select a subcase first.", color="warning"), dash.no_update, dash.no_update
    auth.guard_edit_subcase(subcase_id)
    contact_state = (contact_state_custom or "").strip() if contact_state == CUSTOM_STATE_KEY else contact_state
    attorney_state = (attorney_state_custom or "").strip() if attorney_state == CUSTOM_STATE_KEY else attorney_state
    local_counsel_state = (local_counsel_state_custom or "").strip() if local_counsel_state == CUSTOM_STATE_KEY else local_counsel_state
    try:
        fn = store.update_subcase_metadata(
            subcase_id,
            transferee_name=transferee_name, case_caption=case_caption,
            file_number=file_number, adversary_number=adversary_number,
            filing_date=filing_date,
            contact_name=contact_name, contact_address=contact_address,
            contact_address2=contact_address2, contact_city=contact_city,
            contact_state=contact_state, contact_zip=contact_zip,
            contact_phone=contact_phone, contact_email=contact_email,
            attorney_name=attorney_name, attorney_firm=attorney_firm,
            attorney_address=attorney_address, attorney_address2=attorney_address2,
            attorney_city=attorney_city, attorney_state=attorney_state,
            attorney_zip=attorney_zip, attorney_phone=attorney_phone,
            attorney_email=attorney_email,
            local_counsel_name=local_counsel_name, local_counsel_firm=local_counsel_firm,
            local_counsel_address=local_counsel_address,
            local_counsel_address2=local_counsel_address2,
            local_counsel_city=local_counsel_city, local_counsel_state=local_counsel_state,
            local_counsel_zip=local_counsel_zip, local_counsel_phone=local_counsel_phone,
            local_counsel_email=local_counsel_email,
        )
        opts = store.list_subcase_options(main_id) if main_id is not None else dash.no_update
        return (dbc.Alert(f"Saved. File number: {fn}", color="success"),
                opts, subcase_id)
    except ValueError as e:
        return dbc.Alert(str(e), color="danger"), dash.no_update, dash.no_update


@callback(
    Output("sc-delete-subcase", "style"),
    Input("manage-boot", "data"),
    prevent_initial_call='initial_duplicate',
)
def sc_delete_subcase_visibility(_):
    u = auth.current_user
    if u and u.role in ("admin", "firm_admin", "case_manager"):
        return {"display": "block", "marginLeft": "auto"}
    return {"display": "none"}


@callback(
    Output("sc-delete-modal", "is_open"),
    Output("sc-delete-target", "children"),
    Input("sc-delete-subcase", "n_clicks"),
    State("sc-subcase", "value"),
    prevent_initial_call=True,
)
def open_sc_delete(n, subcase_id):
    if not n:
        raise PreventUpdate
    auth.guard("admin", "firm_admin", "case_manager")
    if subcase_id is None:
        raise PreventUpdate
    sc = store.get_subcase(subcase_id)
    label = f"{sc['transferee_name']} ({sc['display_number']})" if sc['display_number'] else sc['transferee_name']
    return True, label


@callback(
    Output("sc-delete-modal", "is_open", allow_duplicate=True),
    Input("sc-delete-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def close_sc_delete(n):
    if not n:
        raise PreventUpdate
    return False


@callback(
    Output("sc-subcase-status", "children", allow_duplicate=True),
    Output("sc-subcase", "options", allow_duplicate=True),
    Output("sc-subcase", "value", allow_duplicate=True),
    Output("sc-delete-modal", "is_open", allow_duplicate=True),
    Input("sc-delete-confirm", "n_clicks"),
    State("sc-subcase", "value"),
    State("sc-main", "value"),
    prevent_initial_call=True,
)
def delete_sc_subcase(n, subcase_id, main_id):
    if not n:
        raise PreventUpdate
    if subcase_id is None:
        return (dbc.Alert("No subcase selected to delete.", color="warning"),
                dash.no_update, dash.no_update, False)
    auth.guard_edit_subcase(subcase_id)
    try:
        label = store.delete_subcase(subcase_id)
        main_opts = store.list_subcase_options(main_id) if main_id is not None else []
        main_val = main_opts[0]["value"] if main_opts else None
        session.reset_state()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return (dbc.Alert(str(e) or "Failed to delete subcase.", color="danger"),
                dash.no_update, dash.no_update, False)
    return (dbc.Alert(f"Deleted subcase '{label}'.", color="success"),
            main_opts, main_val, False)


def _cm_error_alert(message):
    return dbc.Alert(
        [html.I(className="fa-solid fa-circle-xmark me-2"), message],
        color="danger",
    )


@callback(
    Output("cm-status", "children"),
    Output("cm-main", "value", allow_duplicate=True),
    Output("cm-main", "options"),
    Output("cm-create-mode", "data", allow_duplicate=True),
    Input("cm-save", "n_clicks"),
    State("cm-main", "value"),
    State("cm-create-mode", "data"),
    State("cm-case-name", "value"),
    State("cm-case-number", "value"),
    State("cm-jurisdiction", "value"),
    State("cm-judge", "value"),
    State("cm-petition-date", "value"),
    State("cm-client-name", "value"),
    State("cm-client-contact", "value"),
    State("cm-client-address", "value"),
    State("cm-client-address2", "value"),
    State("cm-client-city", "value"),
    State("cm-client-state", "value"),
    State("cm-client-zip", "value"),
    State("cm-client-phone", "value"),
    State("cm-client-email", "value"),
    State("cm-client-state-custom", "value"),
    State("cm-firm-select", "value"),
    prevent_initial_call=True
)
def save_main(n_clicks, main_id, create_mode, name, number, jurisdiction, judge, petition,
              client_name, client_contact, client_address, client_address2,
              client_city, client_state, client_zip, client_phone, client_email,
              client_state_custom, cm_firm):
    client_state = (client_state_custom or "").strip() if client_state == CUSTOM_STATE_KEY else (client_state or "").strip()
    if create_mode:
        user = auth.guard("admin", "firm_admin", "case_manager")
        try:
            fid = int(cm_firm) if user.role == "admin" and cm_firm else auth.firm_scope(user)
            new_id = store.create_main_case(name, number, jurisdiction, judge, petition, firm_id=fid,
                                            client_name=client_name, client_contact=client_contact,
                                            client_address=client_address, client_address2=client_address2,
                                            client_city=client_city, client_state=client_state,
                                            client_zip=client_zip, client_phone=client_phone,
                                            client_email=client_email)
        except ValueError as e:
            status = _cm_error_alert(str(e))
            return status, dash.no_update, dash.no_update, dash.no_update
        if user.role == "case_manager":
            store.grant_main(user.id, new_id)
        status = dbc.Alert(f"Created main case. (ID {new_id})", color="success")
        scope_opts = store.list_main_options(
            firm_id=(int(cm_firm) if user.role == "admin" and cm_firm else auth.firm_scope(user)))
        return status, new_id, scope_opts, False
    if main_id is None:
        return dbc.Alert("Select a main case first.", color="warning"), \
            dash.no_update, dash.no_update, dash.no_update
    auth.guard_edit_main(main_id)
    try:
        store.update_main_case(main_id, name, number, jurisdiction, judge, petition,
                               client_name=client_name, client_contact=client_contact,
                               client_address=client_address, client_address2=client_address2,
                               client_city=client_city, client_state=client_state,
                               client_zip=client_zip, client_phone=client_phone,
                               client_email=client_email)
    except ValueError as e:
        status = _cm_error_alert(str(e))
        return status, dash.no_update, dash.no_update, dash.no_update
    return dbc.Alert("Saved.", color="success"), \
        dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("grant-subcase", "options"),
    Output("grant-subcase", "value"),
    Input("grant-main", "value"),
)
def grant_subcase_options(main_id):
    if main_id is None:
        return [], None
    return store.list_subcase_options(main_id), None


@callback(
    Output("grants-grid", "rowData"),
    Output("grant-status", "children"),
    Input("grant-main-btn", "n_clicks"),
    Input("revoke-main-btn", "n_clicks"),
    Input("grant-subcase-btn", "n_clicks"),
    Input("revoke-subcase-btn", "n_clicks"),
    State("grant-user", "value"),
    State("grant-main", "value"),
    State("grant-subcase", "value"),
    State("users-firm-select", "value"),
    prevent_initial_call=True
)
def manage_grants(c_gm, c_rm, c_gs, c_rs, user_id, main_id, subcase_id, users_firm):
    u = auth.guard("admin", "firm_admin")
    trig = dash.callback_context.triggered_id
    if trig == "grant-main-btn" and user_id is not None and main_id is not None:
        store.grant_main(user_id, main_id)
        status = "Granted main case access (covers all its subcases)."
    elif trig == "revoke-main-btn" and user_id is not None and main_id is not None:
        store.revoke_main(user_id, main_id)
        status = "Revoked main case access."
    elif trig == "grant-subcase-btn" and user_id is not None and subcase_id is not None:
        store.grant_subcase(user_id, subcase_id)
        status = "Granted subcase access."
    elif trig == "revoke-subcase-btn" and user_id is not None and subcase_id is not None:
        store.revoke_subcase(user_id, subcase_id)
        status = "Revoked subcase access."
    else:
        status = "Select a user and a target before clicking an action."
    scope_fid = int(users_firm) if u.role == "admin" and users_firm else auth.firm_scope(u)
    scope_users = [x["id"] for x in store.list_users(firm_id=scope_fid)]
    return store.list_all_grants(user_ids=scope_users), status


@callback(
    Output("bk-target", "options", allow_duplicate=True),
    Output("bk-target", "value"),
    Input("bk-firm-select", "value"),
    Input("bk-scope", "value"),
    prevent_initial_call="initial_duplicate",
)
def bk_target_options(bk_firm, scope):
    u = auth.current_user
    firm_id = int(bk_firm) if u.role == "admin" and bk_firm else auth.firm_scope(u)
    if scope == "subcase":
        opts = store.list_subcase_options(firm_id=firm_id)
    else:
        opts = store.list_main_options(firm_id=firm_id)
    return opts, None


@callback(
    Output("bk-status", "children"),
    Output("bk-download", "data"),
    Input("bk-backup-btn", "n_clicks"),
    State("bk-scope", "value"),
    State("bk-target", "value"),
    State("bk-firm-select", "value"),
    prevent_initial_call=True,
)
def bk_backup(n_clicks, scope, target_id, bk_firm):
    user = auth.guard("admin", "firm_admin", "case_manager")
    if scope not in ("main", "subcase"):
        return dbc.Alert("Select a backup scope first.", color="warning"), dash.no_update
    if target_id is None:
        return dbc.Alert("Select a case to back up first.", color="warning"), dash.no_update
    firm_id = int(bk_firm) if user.role == "admin" and bk_firm else auth.firm_scope(user)
    try:
        if scope == "main":
            main = store.get_main_by_id(target_id)
            label = main["case_name"]
            if firm_id is not None and int(main["firm_id"]) != int(firm_id):
                raise ValueError("That main case is outside your firm.")
        else:
            sub = store.get_subcase(target_id)
            label = sub["transferee_name"]
            if firm_id is not None and int(sub["firm_id"]) != int(firm_id):
                raise ValueError("That subcase is outside your firm.")
        payload = backup.build_backup(scope, target_id, username=user.username)
        blob = backup.backup_bytes(payload)
    except ValueError as e:
        return _cm_error_alert(str(e)), dash.no_update
    status = dbc.Alert(f"Backed up {backup.summary(payload)}.", color="success")
    return status, dcc.send_bytes(lambda buf: buf.write(blob),
                                  backup.backup_filename(scope, label))


def _bk_restore_plan(pending, mode, main_target, sub_main, sub_target, bk_firm):
    """Validate restore selections against a parsed backup payload.

    Returns (plan, error_alert); plan holds scope/mode/firm_id/target ids,
    a label, and a human-readable detail string for the confirm dialog.
    """
    user = auth.guard("admin", "firm_admin", "case_manager")
    if not pending or pending.get("scope") not in ("main", "subcase"):
        return None, dbc.Alert("Upload a valid backup file first.", color="warning")
    if mode not in ("create", "overwrite"):
        return None, dbc.Alert("Select a restore mode first.", color="warning")
    scope = pending["scope"]
    firm_id = int(bk_firm) if user.role == "admin" and bk_firm else auth.firm_scope(user)
    counts = {k: len(pending.get(k) or [])
              for k in ("main_cases", "subcases", "invoice_records", "case_settings")}
    what = (f"{counts['subcases']} subcase(s), {counts['invoice_records']} invoice row(s), "
            f"{counts['case_settings']} settings row(s)")
    if scope == "main":
        mains = pending.get("main_cases") or []
        name = (mains[0].get("case_name") if mains else "") or "main case"
        if mode == "create":
            if firm_id is None:
                return None, dbc.Alert("Select a firm to restore into first.", color="warning")
            return {"scope": scope, "mode": mode, "firm_id": firm_id, "label": name,
                    "detail": f"This will create a new main case '{name}' with {what}."}, None
        if main_target is None:
            return None, dbc.Alert("Select the main case to overwrite.", color="warning")
        try:
            target = store.get_main_by_id(main_target)
        except ValueError as e:
            return None, _cm_error_alert(str(e))
        if firm_id is not None and int(target["firm_id"]) != int(firm_id):
            return None, _cm_error_alert("That main case is outside your firm.")
        if not auth.can_edit_main(user, int(main_target)):
            return None, _cm_error_alert("You do not have permission to modify that main case.")
        return {"scope": scope, "mode": mode, "firm_id": int(target["firm_id"]),
                "target_main_id": int(main_target), "label": target["case_name"],
                "detail": (f"This will replace ALL data in main case '{target['case_name']}' "
                           f"with the backup of '{name}' ({what}). The target's existing "
                           f"subcases, invoice records, and settings will be deleted.")}, None
    subs = pending.get("subcases") or []
    sub_name = (subs[0].get("transferee_name") if subs else "") or "subcase"
    if mode == "create":
        if sub_main is None:
            return None, dbc.Alert(
                "Select the main case to attach the restored subcase to.", color="warning")
        try:
            parent = store.get_main_by_id(sub_main)
        except ValueError as e:
            return None, _cm_error_alert(str(e))
        if firm_id is not None and int(parent["firm_id"]) != int(firm_id):
            return None, _cm_error_alert("That main case is outside your firm.")
        if not auth.can_edit_main(user, int(sub_main)):
            return None, _cm_error_alert("You do not have permission to modify that main case.")
        return {"scope": scope, "mode": mode, "firm_id": int(parent["firm_id"]),
                "target_main_id": int(sub_main), "label": sub_name,
                "detail": (f"This will create a new subcase '{sub_name}' under main case "
                           f"'{parent['case_name']}' with {what}.")}, None
    if sub_target is None:
        return None, dbc.Alert("Select the subcase to overwrite.", color="warning")
    try:
        target = store.get_subcase(sub_target)
    except ValueError as e:
        return None, _cm_error_alert(str(e))
    if firm_id is not None and int(target["firm_id"]) != int(firm_id):
        return None, _cm_error_alert("That subcase is outside your firm.")
    if not auth.can_edit_subcase(user, int(sub_target)):
        return None, _cm_error_alert("You do not have permission to modify that subcase.")
    return {"scope": scope, "mode": mode, "firm_id": int(target["firm_id"]),
            "target_subcase_id": int(sub_target), "label": target["transferee_name"],
            "detail": (f"This will replace ALL data in subcase '{target['transferee_name']}' "
                       f"with the backup of '{sub_name}' ({what}). Its existing invoice "
                       f"records and settings will be deleted.")}, None


@callback(
    Output("bk-restore-pending", "data", allow_duplicate=True),
    Output("bk-restore-summary", "children"),
    Output("bk-restore-start-wrap", "style", allow_duplicate=True),
    Input("bk-upload", "contents"),
    State("bk-upload", "filename"),
    prevent_initial_call=True,
)
def bk_restore_upload(contents, filename):
    import base64
    if not contents:
        raise PreventUpdate
    try:
        _, content_string = contents.split(",", 1)
    except ValueError:
        content_string = contents
    try:
        payload = backup.load_backup(base64.b64decode(content_string))
    except Exception as e:
        return None, dbc.Alert(f"Could not read {filename or 'file'}: {e}",
                               color="danger"), {"display": "none"}
    detail = f"Backup of {backup.summary(payload)}."
    created = payload.get("created_at") or "unknown date"
    by = payload.get("created_by")
    detail += f" Created {created}" + (f" by {by}." if by else ".")
    return (payload,
            dbc.Alert([html.Strong(f"Loaded {filename}. "), detail], color="success"),
            {"display": "block"})


@callback(
    Output("bk-restore-main-wrap", "style"),
    Output("bk-restore-sub-main-wrap", "style"),
    Output("bk-restore-sub-wrap", "style"),
    Output("bk-restore-main-target", "options"),
    Output("bk-restore-main-target", "value"),
    Output("bk-restore-sub-main", "options"),
    Output("bk-restore-sub-main", "value"),
    Output("bk-restore-sub-target", "options"),
    Output("bk-restore-sub-target", "value"),
    Input("bk-restore-pending", "data"),
    Input("bk-restore-mode", "value"),
    Input("bk-firm-select", "value"),
)
def bk_restore_options(pending, mode, bk_firm):
    hidden = {"display": "none"}
    shown = {"display": "block", "maxWidth": "640px", "marginBottom": "8px"}
    if not pending:
        return hidden, hidden, hidden, [], None, [], None, [], None
    u = auth.current_user
    firm_id = int(bk_firm) if u.role == "admin" and bk_firm else auth.firm_scope(u)
    scope = pending.get("scope")
    if scope == "main" and mode == "overwrite":
        return shown, hidden, hidden, store.list_main_options(firm_id=firm_id), None, [], None, [], None
    if scope == "subcase" and mode == "create":
        return hidden, shown, hidden, [], None, store.list_main_options(firm_id=firm_id), None, [], None
    if scope == "subcase" and mode == "overwrite":
        return hidden, hidden, shown, [], None, [], None, \
            store.list_subcase_options(firm_id=firm_id), None
    return hidden, hidden, hidden, [], None, [], None, [], None


@callback(
    Output("bk-restore-status", "children", allow_duplicate=True),
    Output("bk-restore-modal", "is_open", allow_duplicate=True),
    Output("bk-restore-modal-body", "children"),
    Input("bk-restore-btn", "n_clicks"),
    State("bk-restore-pending", "data"),
    State("bk-restore-mode", "value"),
    State("bk-restore-main-target", "value"),
    State("bk-restore-sub-main", "value"),
    State("bk-restore-sub-target", "value"),
    State("bk-firm-select", "value"),
    prevent_initial_call=True,
)
def bk_restore_arm(n_clicks, pending, mode, main_target, sub_main, sub_target, bk_firm):
    plan, error = _bk_restore_plan(pending, mode, main_target, sub_main, sub_target, bk_firm)
    if error is not None:
        return error, False, dash.no_update
    body = [html.P(plan["detail"]),
            html.P("This action cannot be undone.", className="text-muted")]
    return dash.no_update, True, body


@callback(
    Output("bk-restore-modal", "is_open", allow_duplicate=True),
    Input("bk-restore-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def bk_restore_cancel(_):
    return False


@callback(
    Output("bk-restore-status", "children", allow_duplicate=True),
    Output("bk-restore-modal", "is_open", allow_duplicate=True),
    Output("bk-restore-pending", "data", allow_duplicate=True),
    Output("bk-restore-start-wrap", "style", allow_duplicate=True),
    Output("bk-target", "options", allow_duplicate=True),
    Input("bk-restore-confirm", "n_clicks"),
    State("bk-restore-pending", "data"),
    State("bk-restore-mode", "value"),
    State("bk-restore-main-target", "value"),
    State("bk-restore-sub-main", "value"),
    State("bk-restore-sub-target", "value"),
    State("bk-firm-select", "value"),
    State("bk-scope", "value"),
    prevent_initial_call=True,
)
def bk_restore_confirm(n_clicks, pending, mode, main_target, sub_main, sub_target,
                       bk_firm, bk_scope):
    hidden = {"display": "none"}
    try:
        plan, error = _bk_restore_plan(pending, mode, main_target, sub_main,
                                       sub_target, bk_firm)
        if error is not None:
            return error, False, dash.no_update, dash.no_update, dash.no_update
        if plan["scope"] == "main":
            result = store.restore_main_case(
                pending, plan["mode"], firm_id=plan["firm_id"],
                target_main_id=plan.get("target_main_id"))
        else:
            result = store.restore_subcase(
                pending, plan["mode"], firm_id=plan["firm_id"],
                target_main_id=plan.get("target_main_id"),
                target_subcase_id=plan.get("target_subcase_id"))
    except ValueError as e:
        return _cm_error_alert(str(e)), False, dash.no_update, dash.no_update, dash.no_update
    counts = result["counts"]
    status = dbc.Alert(
        f"Restored {result['label']} — {counts['subcases']} subcase(s), "
        f"{counts['invoice_records']} invoice row(s), "
        f"{counts['case_settings']} settings row(s).",
        color="success")
    u = auth.current_user
    firm = int(bk_firm) if u.role == "admin" and bk_firm else auth.firm_scope(u)
    if bk_scope == "subcase":
        opts = store.list_subcase_options(firm_id=firm)
    else:
        opts = store.list_main_options(firm_id=firm)
    return status, False, None, hidden, opts


@callback(
    Output("imp-main", "options"),
    Output("imp-main", "value"),
    Input("manage-boot", "data"),
    Input("imp-firm-select", "value"),
    prevent_initial_call='initial_duplicate',
)
def imp_main_options(_, imp_firm):
    u = auth.guard("admin", "firm_admin", "case_manager")
    scope = int(imp_firm) if u.role == "admin" and imp_firm else auth.firm_scope(u)
    if u.role == "case_manager":
        opts = store.list_visible_main_options(u.id, u.role, firm_id=scope)
    else:
        opts = store.list_main_options(firm_id=scope)
    return opts, None


@callback(
    Output("imp-subcase", "options"),
    Output("imp-subcase", "value"),
    Input("imp-main", "value"),
)
def imp_subcase_options(main_id):
    if main_id is None:
        return [], None
    u = auth.guard("admin", "firm_admin", "case_manager")
    scope = auth.firm_scope(u)
    opts = [o for o in store.list_subcase_options(main_id, firm_id=scope)
            if auth.can_edit_subcase(u, o["value"])]
    return opts, opts[0]["value"] if opts else None


@callback(
    Output("imp-target-info", "children"),
    Input("imp-subcase", "value"),
)
def imp_target_info(subcase_id):
    if subcase_id is None:
        return "Select a subcase to import into."
    try:
        sub = store.get_subcase(subcase_id)
    except ValueError as e:
        return str(e)
    existing = store.count_subcase_invoices(subcase_id)
    label = sub.get("display_number")
    name = sub.get("transferee_name") or ""
    who = f"{name} ({label})" if label else name
    return f"Target: {who} — {existing} existing invoice row(s)."


@callback(
    Output("imp-template-download", "data"),
    Input("imp-template-btn", "n_clicks"),
    prevent_initial_call=True,
)
def imp_template_download(n_clicks):
    from io import BytesIO
    import generate_sample_import
    buf = BytesIO()
    generate_sample_import.build_sample_workbook().save(buf)
    return dcc.send_bytes(lambda b: b.write(buf.getvalue()),
                          "subcase_import_template.xlsx")


def _imp_preview_table(records, petition_date):
    counts = data_import.partition_preview(records, petition_date)
    summary = (f"{counts['total']} valid row(s): "
               f"{counts['historical']} historical, "
               f"{counts['preference']} preference, "
               f"{counts['new_value']} new value")
    if counts["outside_window"]:
        summary += (f" ({counts['outside_window']} row(s) with Payment Date "
                    "after the petition date — outside both periods)")
    preview_cols = ["Transfer Number", "Transfer Amount", "Invoice Number",
                    "Invoice Amount", "Payment Date", "Invoice Date", "Unpaid"]
    header = html.Thead(html.Tr([html.Th(c) for c in preview_cols]))
    body_rows = []
    for r in records[:5]:
        body_rows.append(html.Tr([html.Td(r.get(c)) for c in preview_cols]))
    table = dbc.Table([header, html.Tbody(body_rows)],
                      size="sm", striped=True, bordered=True,
                      className="mb-0 mt-2")
    note = ""
    if len(records) > 5:
        note = html.Div(f"Showing first 5 of {len(records)} rows.",
                        className="text-muted small mt-1")
    return html.Div([html.Div(summary), table, note])


@callback(
    Output("imp-pending", "data", allow_duplicate=True),
    Output("imp-preview", "children"),
    Output("imp-review-wrap", "style", allow_duplicate=True),
    Output("imp-status", "children", allow_duplicate=True),
    Input("imp-upload", "contents"),
    State("imp-upload", "filename"),
    State("imp-subcase", "value"),
    State("imp-mode", "value"),
    prevent_initial_call=True,
)
def imp_upload_parse(contents, filename, subcase_id, mode):
    import base64
    hidden = {"display": "none"}
    shown = {"display": "block"}
    user = auth.guard("admin", "firm_admin", "case_manager")
    if not contents:
        raise PreventUpdate
    if subcase_id is None:
        return None, dash.no_update, hidden, dbc.Alert(
            "Select a subcase to import into first.", color="warning")
    if not auth.can_edit_subcase(user, int(subcase_id)):
        return None, dash.no_update, hidden, _cm_error_alert(
            "You do not have permission to modify that subcase.")
    if mode not in data_import.IMPORT_MODES:
        return None, dash.no_update, hidden, dbc.Alert(
            "Select an import mode first.", color="warning")
    try:
        _, content_string = contents.split(",", 1)
    except ValueError:
        content_string = contents
    try:
        raw_bytes = base64.b64decode(content_string)
        _, raw_rows = data_import.parse_import_workbook(raw_bytes)
        records, errors = data_import.validate_import_rows(raw_rows)
    except ValueError as e:
        return None, dash.no_update, hidden, _cm_error_alert(str(e))
    if errors:
        shown_errors = errors[:20]
        extra = (f"... and {len(errors) - len(shown_errors)} more row(s). "
                 "Fix the file and re-upload; nothing was imported."
                 if len(errors) > len(shown_errors) else
                 "Fix the file and re-upload; nothing was imported.")
        return None, dash.no_update, hidden, dbc.Alert(
            [html.Strong(f"{filename or 'File'} rejected — "
                         f"{len(errors)} invalid row(s). "),
             html.Ul([html.Li(e) for e in shown_errors]),
             html.Div(extra, className="small")],
            color="danger")
    try:
        sub = store.get_subcase(subcase_id)
        petition = store.get_main_by_id(sub["main_case_id"])["petition_date"]
    except ValueError as e:
        return None, dash.no_update, hidden, _cm_error_alert(str(e))
    pending = {"subcase_id": int(subcase_id), "mode": mode, "records": records,
               "filename": filename or "upload.xlsx"}
    preview = dbc.Alert(
        [html.Strong(f"Loaded {filename}. "),
         _imp_preview_table(records, petition)],
        color="success")
    return pending, preview, shown, dash.no_update


def _imp_plan(pending):
    """Validate a pending import against the current user.

    Returns (plan, error_alert) mirroring _bk_restore_plan.
    """
    user = auth.guard("admin", "firm_admin", "case_manager")
    if not pending or not pending.get("records") or not pending.get("subcase_id"):
        return None, dbc.Alert("Upload a valid Excel file first.", color="warning")
    if pending.get("mode") not in data_import.IMPORT_MODES:
        return None, dbc.Alert("Select an import mode first.", color="warning")
    try:
        sub = store.get_subcase(pending["subcase_id"])
    except ValueError as e:
        return None, _cm_error_alert(str(e))
    scope = auth.firm_scope(user)
    if scope is not None and int(sub["firm_id"]) != int(scope):
        return None, _cm_error_alert("That subcase is outside your firm.")
    if not auth.can_edit_subcase(user, int(pending["subcase_id"])):
        return None, _cm_error_alert(
            "You do not have permission to modify that subcase.")
    existing = store.count_subcase_invoices(pending["subcase_id"])
    label = sub.get("display_number")
    name = sub.get("transferee_name") or "subcase"
    who = f"{name} ({label})" if label else name
    n = len(pending["records"])
    if pending["mode"] == "replace":
        detail = (f"This will delete ALL {existing} existing invoice row(s) in "
                  f"subcase '{who}' and import {n} row(s) from "
                  f"'{pending.get('filename')}'.")
    else:
        detail = (f"This will append {n} invoice row(s) from "
                  f"'{pending.get('filename')}' to subcase '{who}' "
                  f"(which has {existing} existing row(s)).")
    return {"subcase_id": int(pending["subcase_id"]), "mode": pending["mode"],
            "label": who, "detail": detail,
            "records": pending["records"]}, None


@callback(
    Output("imp-status", "children", allow_duplicate=True),
    Output("imp-confirm-modal", "is_open", allow_duplicate=True),
    Output("imp-confirm-body", "children"),
    Input("imp-review-btn", "n_clicks"),
    State("imp-pending", "data"),
    prevent_initial_call=True,
)
def imp_review_arm(n_clicks, pending):
    plan, error = _imp_plan(pending)
    if error is not None:
        return error, False, dash.no_update
    body = [html.P(plan["detail"]),
            html.P("This action cannot be undone.", className="text-muted")]
    return dash.no_update, True, body


@callback(
    Output("imp-confirm-modal", "is_open", allow_duplicate=True),
    Input("imp-confirm-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def imp_confirm_cancel(_):
    return False


@callback(
    Output("imp-status", "children", allow_duplicate=True),
    Output("imp-confirm-modal", "is_open", allow_duplicate=True),
    Output("imp-pending", "data", allow_duplicate=True),
    Output("imp-review-wrap", "style", allow_duplicate=True),
    Output("imp-target-info", "children", allow_duplicate=True),
    Input("imp-confirm-yes", "n_clicks"),
    State("imp-pending", "data"),
    prevent_initial_call=True,
)
def imp_confirm_import(n_clicks, pending):
    hidden = {"display": "none"}
    plan, error = _imp_plan(pending)
    if error is not None:
        return error, False, dash.no_update, dash.no_update, dash.no_update
    try:
        result = store.import_subcase_invoices(
            plan["subcase_id"], plan["records"], plan["mode"])
    except ValueError as e:
        return _cm_error_alert(str(e)), False, dash.no_update, dash.no_update, dash.no_update
    if plan["mode"] == "replace":
        msg = (f"Imported {result['imported']} invoice row(s) into "
               f"'{plan['label']}' (replaced {result['deleted']} existing row(s)).")
    else:
        msg = (f"Appended {result['imported']} invoice row(s) to "
               f"'{plan['label']}'.")
    status = dbc.Alert(msg, color="success")
    existing = store.count_subcase_invoices(plan["subcase_id"])
    info = f"Target: {plan['label']} — {existing} invoice row(s)."
    return status, False, None, hidden, info


@callback(
    Output("td-subcase", "options"),
    Output("td-subcase", "value"),
    Input("td-main", "value"),
)
def td_subcase_options(main_id):
    auth.guard("admin")
    if main_id is None:
        return [], None
    opts = store.list_subcase_options(main_id)
    return opts, opts[0]["value"] if opts else None


@callback(
    Output("td-target-info", "children"),
    Input("td-subcase", "value"),
    Input("td-main", "value"),
)
def td_target_info(subcase_id, main_id):
    auth.guard("admin")
    if subcase_id is None:
        return "Select a subcase to generate into."
    try:
        sub = store.get_subcase(subcase_id)
        petition = store.get_main_by_id(sub["main_case_id"])["petition_date"]
        hist_start, pref_start, petition_d = test_data.scenario_windows(petition)
    except ValueError as e:
        return str(e)
    existing = store.count_subcase_invoices(subcase_id)
    label = sub.get("display_number")
    name = sub.get("transferee_name") or ""
    who = f"{name} ({label})" if label else name
    return (f"Target: {who} — {existing} existing invoice row(s). "
            f"Historical {hist_start.isoformat()} to {pref_start.isoformat()}, "
            f"preference {pref_start.isoformat()} to {petition_d.isoformat()}.")


def _td_preview_table(records, petition_date):
    counts = test_data.summarize(records, petition_date)
    summary = (f"{counts['total']} generated row(s): "
               f"{counts['historical']} historical, "
               f"{counts['preference']} preference, "
               f"{counts['new_value']} new value")
    paid = [r for r in records if not r.get("Unpaid")]
    # Compute mean DSO per window from the records directly.
    try:
        hist_start, pref_start, _ = test_data.scenario_windows(petition_date)
        pref_start_s = pref_start.isoformat()
        hist_vals = [r["Invoice to Payment"] for r in paid if r["Payment Date"] < pref_start_s]
        pref_vals = [r["Invoice to Payment"] for r in paid if r["Payment Date"] >= pref_start_s]
        dso_line = (f"Mean DSO — historical: {sum(hist_vals)/len(hist_vals):.1f}d "
                    f"vs preference: {sum(pref_vals)/len(pref_vals):.1f}d."
                    if hist_vals and pref_vals else "")
    except Exception:
        dso_line = ""
    preview_cols = ["Transfer Number", "Transfer Amount", "Invoice Number",
                    "Invoice Amount", "Invoice Amount Paid", "Payment Date",
                    "Invoice Date", "Unpaid"]
    header = html.Thead(html.Tr([html.Th(c) for c in preview_cols]))
    body_rows = []
    for r in records[:5]:
        body_rows.append(html.Tr([html.Td(r.get(c)) for c in preview_cols]))
    table = dbc.Table([header, html.Tbody(body_rows)],
                      size="sm", striped=True, bordered=True,
                      className="mb-0 mt-2")
    note = ""
    if len(records) > 5:
        note = html.Div(f"Showing first 5 of {len(records)} rows.",
                        className="text-muted small mt-1")
    return html.Div([html.Div(summary), html.Div(dso_line, className="text-muted small"), table, note])


@callback(
    Output("td-pending", "data"),
    Output("td-preview", "children"),
    Output("td-review-wrap", "style"),
    Output("td-status", "children", allow_duplicate=True),
    Input("td-generate-btn", "n_clicks"),
    State("td-subcase", "value"),
    State("td-scenario", "value"),
    State("td-total", "value"),
    State("td-unpaid", "value"),
    State("td-seed", "value"),
    State("td-mode", "value"),
    prevent_initial_call=True,
)
def td_generate_preview(n_clicks, subcase_id, scenario, total, unpaid, seed_text, mode):
    import random
    hidden = {"display": "none"}
    shown = {"display": "block"}
    auth.guard("admin")
    if not n_clicks:
        raise PreventUpdate
    if subcase_id is None:
        return None, dash.no_update, hidden, dbc.Alert(
            "Select a subcase to generate into first.", color="warning")
    if scenario not in test_data.SCENARIOS:
        return None, dash.no_update, hidden, dbc.Alert(
            "Select a scenario first.", color="warning")
    if mode not in data_import.IMPORT_MODES:
        return None, dash.no_update, hidden, dbc.Alert(
            "Select a write mode first.", color="warning")
    try:
        total = int(total)
        unpaid = int(unpaid)
    except (TypeError, ValueError):
        return None, dash.no_update, hidden, dbc.Alert(
            "Invoice counts must be whole numbers.", color="warning")
    seed_text = (seed_text or "").strip()
    if seed_text:
        try:
            seed = int(seed_text)
        except ValueError:
            seed = seed_text
    else:
        seed = random.randrange(2 ** 31)
    try:
        sub = store.get_subcase(subcase_id)
        petition = store.get_main_by_id(sub["main_case_id"])["petition_date"]
        records = test_data.generate_test_invoices(
            petition, scenario, total_paid=total, unpaid_count=unpaid, seed=seed)
    except ValueError as e:
        return None, dash.no_update, hidden, dbc.Alert(str(e), color="danger")
    pending = {"subcase_id": int(subcase_id), "mode": mode, "records": records,
               "scenario": scenario, "seed": seed}
    preview = dbc.Alert(
        [html.Strong(f"Scenario '{scenario}' (seed {seed}). "),
         _td_preview_table(records, petition)],
        color="success")
    return pending, preview, shown, dash.no_update


def _td_plan(pending):
    """Validate a pending generation against the current admin user."""
    auth.guard("admin")
    if not pending or not pending.get("records") or not pending.get("subcase_id"):
        return None, dbc.Alert("Generate a preview first.", color="warning")
    if pending.get("mode") not in data_import.IMPORT_MODES:
        return None, dbc.Alert("Select a write mode first.", color="warning")
    try:
        sub = store.get_subcase(pending["subcase_id"])
    except ValueError as e:
        return None, dbc.Alert(str(e), color="danger")
    existing = store.count_subcase_invoices(pending["subcase_id"])
    label = sub.get("display_number")
    name = sub.get("transferee_name") or "subcase"
    who = f"{name} ({label})" if label else name
    n = len(pending["records"])
    scenario = pending.get("scenario") or "ordinary"
    if pending["mode"] == "replace":
        detail = (f"This will delete ALL {existing} existing invoice row(s) in "
                  f"subcase '{who}' and generate {n} '{scenario}' test row(s) "
                  f"(seed {pending.get('seed')}).")
    else:
        detail = (f"This will append {n} '{scenario}' test row(s) "
                  f"(seed {pending.get('seed')}) to subcase '{who}' "
                  f"(which has {existing} existing row(s)).")
    return {"subcase_id": int(pending["subcase_id"]), "mode": pending["mode"],
            "label": who, "detail": detail,
            "records": pending["records"]}, None


@callback(
    Output("td-status", "children", allow_duplicate=True),
    Output("td-confirm-modal", "is_open"),
    Output("td-confirm-body", "children"),
    Input("td-review-btn", "n_clicks"),
    State("td-pending", "data"),
    prevent_initial_call=True,
)
def td_review_arm(n_clicks, pending):
    plan, error = _td_plan(pending)
    if error is not None:
        return error, False, dash.no_update
    body = [html.P(plan["detail"]),
            html.P("This action cannot be undone. Reselect the subcase in "
                   "Analysis afterwards to reload its data.",
                   className="text-muted")]
    return dash.no_update, True, body


@callback(
    Output("td-confirm-modal", "is_open", allow_duplicate=True),
    Input("td-confirm-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def td_confirm_cancel(_):
    return False


@callback(
    Output("td-status", "children", allow_duplicate=True),
    Output("td-confirm-modal", "is_open", allow_duplicate=True),
    Output("td-pending", "data", allow_duplicate=True),
    Output("td-review-wrap", "style", allow_duplicate=True),
    Output("td-target-info", "children", allow_duplicate=True),
    Input("td-confirm-yes", "n_clicks"),
    State("td-pending", "data"),
    prevent_initial_call=True,
)
def td_confirm_generate(n_clicks, pending):
    hidden = {"display": "none"}
    plan, error = _td_plan(pending)
    if error is not None:
        return error, False, dash.no_update, dash.no_update, dash.no_update
    try:
        result = store.import_subcase_invoices(
            plan["subcase_id"], plan["records"], plan["mode"])
    except ValueError as e:
        return dbc.Alert(str(e), color="danger"), False, dash.no_update, dash.no_update, dash.no_update
    if plan["mode"] == "replace":
        msg = (f"Generated {result['imported']} test invoice row(s) in "
               f"'{plan['label']}' (replaced {result['deleted']} existing row(s)).")
    else:
        msg = (f"Appended {result['imported']} test invoice row(s) to "
               f"'{plan['label']}'.")
    status = dbc.Alert(msg + " Reselect the subcase in Analysis to view it.",
                       color="success")
    existing = store.count_subcase_invoices(plan["subcase_id"])
    info = f"Target: {plan['label']} — {existing} invoice row(s)."
    return status, False, None, hidden, info


@callback(
    Output("main-info", "children", allow_duplicate=True),
    Output("main-selector", "value", allow_duplicate=True),
    Output("subcase-selector", "options", allow_duplicate=True),
    Output("subcase-selector", "value", allow_duplicate=True),
    Output("historical", "rowData", allow_duplicate=True),
    Output("preference", "rowData", allow_duplicate=True),
    Output("new_value", "rowData", allow_duplicate=True),
    Output("ocb_grid", "rowData", allow_duplicate=True),
    Output("ocb-range", "data", allow_duplicate=True),
    Output("ocb-total-range-flag", "data", allow_duplicate=True),
    Output("ocb-ordinary-invoices", "data", allow_duplicate=True),
    Output("ocb-start", "value", allow_duplicate=True),
    Output("ocb-end", "value", allow_duplicate=True),
    Output("ocb-step", "value", allow_duplicate=True),
    Output("ocb-restore", "data", allow_duplicate=True),
    Output("ocb-metric", "value", allow_duplicate=True),
    Input("auth-boot", "data"),
    prevent_initial_call='initial_duplicate'
)
def session_boot(_):
    st = session.get_state()
    info = st.meta
    if info is None:
        return (dash.no_update,) * 16
    scope = auth.firm_scope(auth.current_user)
    main_info = _main_info_lines(info, info['subcase_id'])
    return (
        main_info,
        info['main_id'],
        store.list_subcase_options(info['main_id'], firm_id=scope),
        info['subcase_id'],
        st.df_historical.to_dict('records'),
        st.df_preference.to_dict('records'),
        st.df_snv.to_dict('records'),
        st.df_ocb.to_dict('records'),
        info['ocb_range'],
        info['ocb_total_flag'],
        info['ordinary_inv'],
        info['ocb_start'],
        info['ocb_end'],
        info['ocb_step'],
        {'range': info['ocb_range'], 'start': info['ocb_start'], 'end': info['ocb_end'],
         'step': info['ocb_step'], 'total': info['ocb_total_flag']},
        st.ocb_metric,
    )


@callback(
    Output("welcome-modal", "is_open"),
    Input("auth-boot", "data"),
)
def welcome_modal(_):
    return not store.list_main_options(firm_id=auth.firm_scope(auth.current_user))


@callback(
    Output("main-selector", "options"),
    Input("auth-boot", "data"),
)
def analysis_main_options_boot(_):
    return store.list_main_options(firm_id=auth.firm_scope(auth.current_user))


# Apply login protection to every route (including Dash callbacks)
auth.init_login(app.server)


# Run the app
if __name__ == '__main__':
    app.run(debug=os.environ.get('PAT_DEBUG') == '1')