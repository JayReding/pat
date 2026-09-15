# Import packages
import os
from dash import Dash, html, dcc, callback, Output, Input, State
from dash.exceptions import PreventUpdate
import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta
from plotly import graph_objects as go

import analysis
import export_excel
import export_pdf
import store
import auth
import session

# Incorporate data
store.init_cases_db()
store.init_state_db()
store.init_users_db()


def load_case(st, subcase_id):
    main = store.get_main_by_subcase(subcase_id)
    petition_date = pd.Timestamp(main['petition_date'])
    pref_start = petition_date - pd.Timedelta(days=90)
    s = pref_start.strftime('%Y-%m-%d')
    p = petition_date.strftime('%Y-%m-%d')

    frames = store.load_case_frames(subcase_id, s, p)
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
        'hist_title': f'Historical Period: {hist_dates.min().strftime("%m/%d/%Y")} through {hist_dates.max().strftime("%m/%d/%Y")}',
        'pref_title': f'Preference Period: {s} through {p}',
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
    load_case(st, store.active_subcase_id())


session.set_loader(_session_loader)


# Initialize the app
app = Dash(use_pages=True, pages_folder="", external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.FONT_AWESOME])
app.title = "Preference Analysis Tool"


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


def _analysis_page():
    return html.Div(style={"padding": "20px"}, children=[
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
                    options=store.list_main_options(),
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
    ]),
    html.Div(children=[
            dcc.Tabs([
         dcc.Tab(id="summary", label='Case Summary', children=[
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
            dbc.ListGroup([
                dbc.ListGroupItem([html.Strong('Total Transfers: '), html.Span(id="summary-total-transfers")]),
                dbc.ListGroupItem([html.Strong('Total New Value: '), html.Span(id="summary-total-new-value")]),
                dbc.ListGroupItem([html.Strong('Net of New Value: '), html.Span(id="summary-net-new-value")]),
                dbc.ListGroupItem([html.Strong('Net Preference (Defenses Applied): '), html.Span(id="summary-net-pref-defenses")]),
                dbc.ListGroupItem([html.Strong('Historical Weighted Average DSO: '), html.Span(id="summary-hist-wavg")]),
                dbc.ListGroupItem([html.Strong('Preference Period Weighted DSO: '), html.Span(id="summary-pref-wavg")]),
                dbc.ListGroupItem([html.Strong('Weighted DSO Difference: '), html.Span(id="summary-dso-diff")]),
            ]),
        ]),
dcc.Tab(label='Historical Period', children=[
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
                    {"field": "Invoice Date"},
                    {"field": "Invoice Due"},
                    {"field": "Invoice to Payment"},
                    {"field": "Days Past Due"},
                ]
            ),
            ])
    ]),

         dcc.Tab(label='Preference Period', children=[
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
                {"field": "Invoice Date"},
                {"field": "Invoice Due"},
                {"field": "Invoice to Payment"},
                {"field": "Days Past Due"},
            ]
        ),
        ])
    ]),
         dcc.Tab(label='New Value', children=[
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
         dcc.Tab(id="ocb", label='Ordinary Course', children=[
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
])
    ])
])


def _cm_field(label, cid, ftype="text"):
    return html.Div(className="mb-2", style={"maxWidth": "420px"}, children=[
        html.Label(label, htmlFor=cid),
        dcc.Input(id=cid, type=ftype, className="form-control"),
    ])


def _manage_header():
    return html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"}, children=[
        dcc.Link("← Back to Analysis", href="/", style={"textDecoration": "none"}),
        html.H3(children="Management Console", style={"margin": 0}),
        html.Span(),
    ])


def _manage_sidebar(active):
    return dbc.Col(width=2, style={"borderRight": "1px solid var(--bs-border-color)", "padding": "16px"}, children=[
        dbc.Nav([
            html.Div("Case Management", className="sidebar-heading mb-1 mt-1 px-3 text-uppercase small fw-bold text-muted"),
            dbc.NavLink("Main Case Management", href="/manage", active=(active == "main"), className="mb-2"),
            dbc.NavLink("Subcase Management", href="/manage/subcases", active=(active == "subcase"), className="mb-2"),
            html.Div("Administration", className="sidebar-heading mb-1 mt-3 px-3 text-uppercase small fw-bold text-muted"),
            dbc.NavLink("User Management", id="admin-nav-btn", href="/manage/users", active=(active == "users")),
        ], pills=True, vertical=True),
    ])


def _maincase_page():
    return html.Div(style={"padding": "20px"}, children=[
        _manage_header(),
        dcc.Store(id="manage-boot", data=True),
        html.Hr(),
        dbc.Row([
            _manage_sidebar("main"),
            dbc.Col(width=10, children=[
                html.H3(children='Main Case Management'),
                dcc.Dropdown(id="cm-main", options=store.list_main_options(), clearable=False, style={"maxWidth": "500px", "marginBottom": "16px"}),
                _cm_field('Case Name', "cm-case-name"),
                _cm_field('Case Number', "cm-case-number"),
                _cm_field('Jurisdiction', "cm-jurisdiction"),
                _cm_field('Judge', "cm-judge"),
                _cm_field('Petition Date (YYYY-MM-DD)', "cm-petition-date", "date"),
                dbc.Button("Save Changes", id="cm-save", n_clicks=0, color="primary", className="mt-2"),
                html.Div(id="cm-status", className="mt-3"),
            ]),
        ]),
    ])


def _subcase_page():
    return html.Div(style={"padding": "20px"}, children=[
        _manage_header(),
        dcc.Store(id="manage-boot", data=True),
        html.Hr(),
        dbc.Row([
            _manage_sidebar("subcase"),
            dbc.Col(width=10, children=[
                html.H3(children='Subcase Management'),
                dcc.Dropdown(id="sc-main", options=store.list_main_options(), clearable=False, style={"maxWidth": "500px", "marginBottom": "16px"}),
                html.H5('Subcase Details', className="mt-3"),
                dcc.Dropdown(id="sc-subcase", options=[], clearable=False, style={"maxWidth": "420px", "marginBottom": "16px"}),
                _cm_field('File Number (blank = auto-assign)', "sc-file-number"),
                _cm_field('Filing Date (YYYY-MM-DD)', "sc-filing-date", "date"),
                html.H6('Contact', className="mt-3"),
                _cm_field('Contact Name', "sc-contact-name"),
                _cm_field('Contact Address', "sc-contact-address"),
                _cm_field('Contact Address 2', "sc-contact-address2"),
                html.Div(className="d-flex flex-wrap gap-2", children=[
                    _cm_field('City', "sc-contact-city"),
                    _cm_field('State', "sc-contact-state"),
                    _cm_field('ZIP', "sc-contact-zip"),
                ]),
                _cm_field('Contact Phone', "sc-contact-phone"),
                _cm_field('Contact Email', "sc-contact-email", "email"),
                html.H6('Attorney', className="mt-3"),
                _cm_field('Attorney Name', "sc-attorney-name"),
                _cm_field('Attorney Firm', "sc-attorney-firm"),
                _cm_field('Attorney Address', "sc-attorney-address"),
                _cm_field('Attorney Address 2', "sc-attorney-address2"),
                html.Div(className="d-flex flex-wrap gap-2", children=[
                    _cm_field('City', "sc-attorney-city"),
                    _cm_field('State', "sc-attorney-state"),
                    _cm_field('ZIP', "sc-attorney-zip"),
                ]),
                _cm_field('Attorney Phone', "sc-attorney-phone"),
                _cm_field('Attorney Email', "sc-attorney-email", "email"),
                dbc.Button("Save Subcase", id="sc-save-subcase", n_clicks=0, color="primary", className="mt-2"),
                html.Div(id="sc-subcase-status", className="mt-3"),
            ]),
        ]),
    ])


def _users_page():
    return html.Div(style={"padding": "20px"}, children=[
        _manage_header(),
        dcc.Store(id="manage-boot", data=True),
        dcc.Store(id="users-boot", data=True),
        html.Hr(),
        dbc.Row([
            _manage_sidebar("users"),
            dbc.Col(width=10, children=[
                html.H3(children='User Management'),
                html.Div(className="text-muted mb-3", id="admin-notice"),
                dag.AgGrid(
                    id="admin-users-grid",
                    dashGridOptions={"rowSelection": "single"},
                    columnDefs=[
                        {"field": "username", "headerName": "Username"},
                        {"field": "role", "headerName": "Role"},
                        {"field": "email", "headerName": "Email"},
                        {"field": "active", "headerName": "Active"},
                        {"field": "created_at", "headerName": "Created At"},
                    ],
                ),
                html.Hr(),
                html.H6('Create user'),
                html.Div(className="mt-2", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
                    html.Div(children=[html.Label('Username', htmlFor="new-user-username"), dcc.Input(id="new-user-username", type="text", className="form-control")]),
                    html.Div(children=[html.Label('Password', htmlFor="new-user-password"), dcc.Input(id="new-user-password", type="password", className="form-control")]),
                    html.Div(children=[html.Label('Email', htmlFor="new-user-email"), dcc.Input(id="new-user-email", type="email", className="form-control")]),
                    html.Div(children=[html.Label('Role', htmlFor="new-user-role"), dcc.Dropdown(id="new-user-role", options=[{"label": r, "value": r} for r in auth.ROLES], value="user", style={"minWidth": "140px"})]),
                    dbc.Button("Create User", id="admin-create-btn", color="primary"),
                ]),
                html.Hr(),
                html.H6('Actions on selected user'),
                html.Div(className="mt-2", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
                    html.Div(children=[html.Label('New role', htmlFor="admin-role-select"), dcc.Dropdown(id="admin-role-select", options=[{"label": r, "value": r} for r in auth.ROLES], style={"minWidth": "140px"})]),
                    html.Div(children=[html.Label('New password (reset)', htmlFor="admin-reset-pw"), dcc.Input(id="admin-reset-pw", type="password", className="form-control")]),
                    dbc.Button("Set Role", id="admin-set-role-btn", color="secondary"),
                    dbc.Button("Reset Password", id="admin-reset-btn", color="secondary"),
                    dbc.Button("Activate/Deactivate", id="admin-toggle-btn", color="secondary"),
                    dbc.Button("Delete User", id="admin-delete-btn", color="danger"),
                ]),
                html.Div(id="admin-status", className="mt-3"),
                html.Hr(),
                html.H6('Case Access (grants)'),
                html.P('Grant a user access to a main case (covers all its subcases) or to a single subcase. Roles: case_manager may edit granted cases; user may view.', className="text-muted small"),
                html.Div(className="mt-2", style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "alignItems": "flex-end"}, children=[
                    html.Div(children=[html.Label('User', htmlFor="grant-user"), dcc.Dropdown(id="grant-user", style={"minWidth": "160px"})]),
                    html.Div(children=[html.Label('Main case', htmlFor="grant-main"), dcc.Dropdown(id="grant-main", options=store.list_main_options(), style={"minWidth": "260px"})]),
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
            ]),
        ]),
    ])


def _shell():
    return html.Div(children=[
        dbc.Navbar(
            dbc.Container(
                [
                    dbc.NavbarBrand(
                        [html.I(className="fa-solid fa-chart-column me-2"), "Preference Analysis Tool"],
                        href="/", className="fw-semibold",
                    ),
                    html.Div(className="ms-auto d-flex align-items-center gap-3", children=[
                        dcc.Store(id="shell-boot", data=True),
                        html.Div(id="user-badge", className="navbar-text text-white-50"),
                        html.Form(dbc.Button("Log out", color="light", size="sm", className="px-3"),
                                  action="/logout", method="POST"),
                    ]),
                ],
                fluid=True,
            ),
            color="primary", dark=True, sticky="top", className="mb-3",
        ),
        html.Div(style={"padding": "20px"}, children=[dash.page_container]),
    ])


dash.register_page("maincase", path="/manage", layout=_maincase_page(), title="Main Case Management", name="Main Case Management")
dash.register_page("subcases", path="/manage/subcases", layout=_subcase_page(), title="Subcase Management", name="Subcase Management")
dash.register_page("users", path="/manage/users", layout=_users_page(), title="User Management", name="User Management")

app.layout = _shell()

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
    Output("summary-total-transfers", "children"),
    Output("summary-total-new-value", "children"),
    Output("summary-net-new-value", "children"),
    Output("summary-net-pref-defenses", "children"),
    Output("summary-hist-wavg", "children"),
    Output("summary-pref-wavg", "children"),
    Output("summary-dso-diff", "children"),
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
    diff = (pref_wavg - hist_wavg) / hist_wavg * 100
    hist_dpd = analysis.calc_weighted_dpd(st.df_historical)
    pref_dpd = analysis.calc_weighted_dpd(st.df_preference)
    dpd_diff = (pref_dpd - hist_dpd) / hist_dpd * 100

    tot_shares, ord_shares = _ocb_transfer_shares(st)
    net_pref_defenses, total_new_value = analysis.calc_net_pref_defenses(df_nv, tot_shares, ord_shares)

    tr_amt = st.df_transfers.groupby("Transfer Number")["Transfer Amount"].sum()
    ordinary_course_amount = (tr_amt * (ord_shares / tot_shares).reindex(tr_amt.index).fillna(0.0)).sum()
    if abs(total_transfers - total_new_value - net_pref_defenses - ordinary_course_amount) > 0.01:
        print(f"OCA identity off by ${abs(total_transfers - total_new_value - net_pref_defenses - ordinary_course_amount):,.2f}")

    return (f"${total_transfers:,.2f}", f"${total_new_value:,.2f}", f"${net_new_value:,.2f}",
                f"${net_pref_defenses:,.2f}",
                f"{hist_wavg:.2f}", f"{pref_wavg:.2f}", f"{diff:.2f}%",
                f"${net_pref_defenses:,.2f}", f"${net_pref_defenses:,.2f}", f"${total_transfers:,.2f}", f"${total_new_value:,.2f}",
                f"${ordinary_course_amount:,.2f}", f"{diff:.2f}%",
                "fa-solid fa-arrow-trend-up fa-3x" if diff >= 0 else "fa-solid fa-arrow-trend-down fa-3x",
                f"{dpd_diff:.2f}%",
                "fa-solid fa-arrow-trend-up fa-3x" if dpd_diff >= 0 else "fa-solid fa-arrow-trend-down fa-3x")

@callback(
    Output("ocb_grid", "rowData"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-metric", "value")
)
def update_ocb_grid(start, end, step, metric):
    st = session.get_state()
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

def _load_subcase_payload(st, subcase_id):
    info = load_case(st, subcase_id)
    store.save_app_state(subcase_id)
    st = session.get_state()
    main_info = [
        html.Div(f"Transferee: {info['transferee']}   |   {info['subcase_id_label']}: {info['subcase_display']}", style={"fontWeight": "bold", "fontSize": "1.25rem"}),
        html.Div(f"Main Case: {info['main_name']}   |   Preference Period: {info['pref_start']} - {info['petition_date']}"),
    ]
    return (
        main_info,
        st.df_historical.to_dict('records'),
        st.df_preference.to_dict('records'),
        st.df_snv.to_dict('records'),
        st.df_ocb.to_dict('records'),
        {'range': info['ocb_range'], 'start': info['ocb_start'], 'end': info['ocb_end'],
         'step': info['ocb_step'], 'total': info['ocb_total_flag']},
        st.ocb_metric,
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
    Input("main-selector", "value"),
    Input("subcase-selector", "value"),
    prevent_initial_call=True
)
def selection_changed(main_id, subcase_id):
    trig = dash.callback_context.triggered[0]["prop_id"]
    no_update = dash.no_update

    if trig == "main-selector.value":
        opts = store.list_subcase_options(main_id)
        st = session.get_state()
        sub_ids = {o["value"] for o in opts}
        if st.meta and st.meta.get("main_id") == main_id and st.active_subcase_id in sub_ids:
            return (no_update,) * 9
        if st.subcase_by_main is None:
            st.subcase_by_main = {}
        target = st.subcase_by_main.get(main_id)
        if target not in sub_ids:
            target = opts[0]["value"] if opts else None
        if target is None:
            return opts, None, no_update, no_update, no_update, \
                   no_update, no_update, no_update, no_update
        st.subcase_by_main[main_id] = target
        payload = _load_subcase_payload(st, target)
        return (opts, target) + payload

    if subcase_id is None:
        raise PreventUpdate

    st = session.get_state()
    if st.meta and st.meta.get("subcase_id") == subcase_id:
        return (no_update,) * 9
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
    status = f"OCB range: {s_label} to {e_label} — {int(mask.sum())} preference invoice(s) marked as Ordinary."
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
    fig.update_layout(
        title={"text": f"Invoice to Payment Distribution ({n} invoices)",
               "x": 0.0, "font": {"size": 13}},
        xaxis_title="Invoice to Payment (days)",
        yaxis_title="Number of Invoices",
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
        range=[0, cap],
    )
    fig.update_yaxes(showgrid=False, zeroline=False, showline=True, linewidth=1, linecolor="#adb5bd")
    return fig


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
    fig.update_layout(
        title={"text": f"Invoice to Payment Distribution ({n} invoices)",
               "x": 0.0, "font": {"size": 13}},
        xaxis_title="Invoice to Payment (days)",
        yaxis_title="Number of Invoices",
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
        range=[0, cap],
    )
    fig.update_yaxes(showgrid=False, zeroline=False, showline=True, linewidth=1, linecolor="#adb5bd")
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
    return f"Saved {datetime.now().strftime('%H:%M:%S')}"


@callback(
    Output("user-badge", "children"),
    Input("shell-boot", "data")
)
def update_user_badge(_):
    u = auth.current_user
    label = f"{u.role.title()} — {u.username}"
    if u.role in ("admin", "case_manager"):
        return dcc.Link(label, href="/manage",
                        className="text-white fw-semibold")
    return label


@callback(
    Output("admin-nav-btn", "style"),
    Input("manage-boot", "data"),
)
def manage_admin_gate(_):
    if auth.current_user.role == "admin":
        return dash.no_update
    return {"display": "none"}


@callback(
    Output("admin-users-grid", "rowData", allow_duplicate=True),
    Output("admin-notice", "children"),
    Output("grants-grid", "rowData", allow_duplicate=True),
    Output("grant-user", "options"),
    Input("users-boot", "data"),
    prevent_initial_call='initial_duplicate'
)
def admin_load(_):
    auth.guard("admin")
    user_map = [{"label": u["username"], "value": u["id"]} for u in store.list_users()]
    return store.list_users(), f"Managing users as {auth.current_user.username}.", store.list_all_grants(), user_map


@callback(
    Output("admin-users-grid", "rowData"),
    Output("admin-status", "children"),
    Input("admin-create-btn", "n_clicks"),
    Input("admin-set-role-btn", "n_clicks"),
    Input("admin-reset-btn", "n_clicks"),
    Input("admin-toggle-btn", "n_clicks"),
    Input("admin-delete-btn", "n_clicks"),
    State("new-user-username", "value"),
    State("new-user-password", "value"),
    State("new-user-email", "value"),
    State("new-user-role", "value"),
    State("admin-role-select", "value"),
    State("admin-reset-pw", "value"),
    State("admin-users-grid", "selectedRows"),
    prevent_initial_call=True
)
def manage_users(c_create, c_role, c_reset, c_toggle, c_delete,
                 new_name, new_pw, new_email, new_role, sel_role, reset_pw, selected):
    auth.guard("admin")
    from werkzeug.security import generate_password_hash
    trig = dash.callback_context.triggered_id
    uid = uname = None
    if selected:
        uid, uname = selected[0].get("id"), selected[0].get("username")
    status = ""

    if trig == "admin-create-btn" and new_name and new_pw:
        name = new_name.strip()
        if store.get_user_by_username(name):
            status = f"Username '{name}' already exists."
        else:
            store.create_user(name, generate_password_hash(new_pw), new_role or "user", (new_email or "").strip() or None)
            status = f"Created user '{name}' ({new_role or 'user'})."
    elif trig == "admin-create-btn":
        status = "Enter a username and password to create a user."
    elif trig == "admin-set-role-btn":
        if uid is not None and sel_role:
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
    elif trig == "admin-delete-btn":
        if uid is not None:
            if str(auth.current_user.id) == str(uid):
                status = "You cannot delete your own account."
            else:
                store.delete_user(uid)
                status = f"Deleted user '{uname}'."
        else:
            status = "Select a user."

    return store.list_users(), status


@callback(
    Output("cm-case-name", "value"),
    Output("cm-case-number", "value"),
    Output("cm-jurisdiction", "value"),
    Output("cm-judge", "value"),
    Output("cm-petition-date", "value"),
    Output("cm-case-name", "disabled"),
    Output("cm-case-number", "disabled"),
    Output("cm-jurisdiction", "disabled"),
    Output("cm-judge", "disabled"),
    Output("cm-petition-date", "disabled"),
    Output("cm-save", "disabled"),
    Input("cm-main", "value"),
)
def build_cm_details(main_id):
    if main_id is None:
        return (None,) * 5 + (True,) * 6
    m = store.get_main_by_id(main_id)
    can_edit = auth.can_edit_main(auth.current_user, main_id)
    return (
        m["case_name"], m["case_number"], m.get("jurisdiction") or "", m.get("judge") or "",
        m.get("petition_date") or "",
        not can_edit, not can_edit, not can_edit, not can_edit, not can_edit, not can_edit,
    )


_SC_SUBCASE_FIELD_DEFS = [
    ("sc-file-number", "file_number"),
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
    [Output(cid, "value") for cid, _ in _SC_SUBCASE_FIELD_DEFS] +
    [Output(cid, "disabled") for cid, _ in _SC_SUBCASE_FIELD_DEFS],
    Input("sc-subcase", "value"),
)
def populate_sc_subcase_fields(subcase_id):
    if subcase_id is None:
        return (None,) * len(_SC_SUBCASE_FIELD_DEFS) * 2
    can_edit = auth.can_edit_subcase(auth.current_user, subcase_id)
    sub = store.get_subcase(subcase_id)
    values = tuple(sub.get(key) or "" for _, key in _SC_SUBCASE_FIELD_DEFS)
    disabled = tuple(not can_edit for _ in _SC_SUBCASE_FIELD_DEFS)
    return values + disabled


@callback(
    Output("sc-subcase-status", "children"),
    Input("sc-save-subcase", "n_clicks"),
    State("sc-subcase", "value"),
    State("sc-file-number", "value"),
    State("sc-filing-date", "value"),
    State("sc-contact-name", "value"),
    State("sc-contact-address", "value"),
    State("sc-contact-address2", "value"),
    State("sc-contact-city", "value"),
    State("sc-contact-state", "value"),
    State("sc-contact-zip", "value"),
    State("sc-contact-phone", "value"),
    State("sc-contact-email", "value"),
    State("sc-attorney-name", "value"),
    State("sc-attorney-firm", "value"),
    State("sc-attorney-address", "value"),
    State("sc-attorney-address2", "value"),
    State("sc-attorney-city", "value"),
    State("sc-attorney-state", "value"),
    State("sc-attorney-zip", "value"),
    State("sc-attorney-phone", "value"),
    State("sc-attorney-email", "value"),
    prevent_initial_call=True,
)
def save_sc_subcase(n,
                    subcase_id, file_number, filing_date,
                    contact_name, contact_address, contact_address2, contact_city, contact_state,
                    contact_zip, contact_phone, contact_email,
                    attorney_name, attorney_firm, attorney_address, attorney_address2, attorney_city,
                    attorney_state, attorney_zip, attorney_phone, attorney_email):
    if subcase_id is None:
        return dbc.Alert("Select a subcase first.", color="warning")
    auth.guard_edit_subcase(subcase_id)
    try:
        fn = store.update_subcase_metadata(
            subcase_id,
            file_number=file_number, filing_date=filing_date,
            contact_name=contact_name, contact_address=contact_address,
            contact_address2=contact_address2, contact_city=contact_city,
            contact_state=contact_state, contact_zip=contact_zip,
            contact_phone=contact_phone, contact_email=contact_email,
            attorney_name=attorney_name, attorney_firm=attorney_firm,
            attorney_address=attorney_address, attorney_address2=attorney_address2,
            attorney_city=attorney_city, attorney_state=attorney_state,
            attorney_zip=attorney_zip, attorney_phone=attorney_phone,
            attorney_email=attorney_email,
        )
        return dbc.Alert(f"Saved. File number: {fn}", color="success")
    except ValueError as e:
        return dbc.Alert(str(e), color="danger")


@callback(
    Output("cm-status", "children"),
    Input("cm-save", "n_clicks"),
    State("cm-main", "value"),
    State("cm-case-name", "value"),
    State("cm-case-number", "value"),
    State("cm-jurisdiction", "value"),
    State("cm-judge", "value"),
    State("cm-petition-date", "value"),
    prevent_initial_call=True
)
def save_main(n_clicks, main_id, name, number, jurisdiction, judge, petition):
    if main_id is None:
        return dbc.Alert("Select a main case first.", color="warning")
    auth.guard_edit_main(main_id)
    try:
        store.update_main_case(main_id, name, number, jurisdiction, judge, petition)
        return dbc.Alert("Saved.", color="success")
    except ValueError as e:
        return dbc.Alert(str(e), color="danger")


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
    prevent_initial_call=True
)
def manage_grants(c_gm, c_rm, c_gs, c_rs, user_id, main_id, subcase_id):
    auth.guard("admin")
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
    return store.list_all_grants(), status


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
    main_info = [
        html.Div(f"Transferee: {info['transferee']}   |   {info['subcase_id_label']}: {info['subcase_display']}", style={"fontWeight": "bold", "fontSize": "1.25rem"}),
        html.Div(f"Main Case: {info['main_name']}   |   Preference Period: {info['pref_start']} - {info['petition_date']}"),
    ]
    return (
        main_info,
        info['main_id'],
        store.list_subcase_options(info['main_id']),
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


# Apply login protection to every route (including Dash callbacks)
auth.init_login(app.server)


# Run the app
if __name__ == '__main__':
    app.run(debug=os.environ.get('PAT_DEBUG') == '1')