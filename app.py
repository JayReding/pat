# Import packages
from dash import Dash, html, dcc, callback, Output, Input, State
from dash.exceptions import PreventUpdate
import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np

import analysis
import state
import store

# Incorporate data
store.init_cases_db()
store.init_state_db()

# Explicit per-case state (previously implicit module globals)
STATE = state.CaseState()


def load_case(subcase_id):
    master = store.get_master_by_subcase(subcase_id)
    petition_date = pd.Timestamp(master['petition_date'])
    pref_start = petition_date - pd.Timedelta(days=90)
    s = pref_start.strftime('%Y-%m-%d')
    p = petition_date.strftime('%Y-%m-%d')

    frames = store.load_case_frames(subcase_id, s, p)
    STATE.df_historical = frames['historical']
    STATE.df_preference = frames['preference']
    STATE.df_transfers = frames['transfers']
    STATE.df_newvalue = frames['newvalue']
    STATE.active_subcase_id = int(subcase_id)

    STATE.df_preference['Ordinary'] = 0

    settings = store.load_case_settings(subcase_id)
    ocb_range = settings.get('ocb_range')
    if ocb_range is not None and ocb_range.get('end') is None:
        ocb_range = None
    ocb_start = settings.get('ocb_start') if settings.get('ocb_start') is not None else 0
    ocb_end = settings.get('ocb_end') if settings.get('ocb_end') is not None else 100
    ocb_step = settings.get('ocb_step') if settings.get('ocb_step') is not None else 5
    ocb_total_flag = bool(settings.get('ocb_total_flag', False))

    ordinary_inv = []
    if ocb_range is not None:
        a, b = sorted((ocb_range['start'], ocb_range['end']))
        df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, ocb_start, ocb_end, ocb_step)
        s_label = df_cur['date_range'].iloc[a]
        e_label = df_cur['date_range'].iloc[b]
        lower, _ = analysis.ocb_label_bounds(s_label, ocb_start, ocb_end)
        _, upper = analysis.ocb_label_bounds(e_label, ocb_start, ocb_end)
        days = STATE.df_preference['Invoice to Payment']
        mask = pd.Series(True, index=STATE.df_preference.index)
        if lower is not None:
            mask &= days >= lower
        if upper is not None:
            mask &= days <= upper
        STATE.df_preference.loc[mask, 'Ordinary'] = 1
        ordinary_inv = STATE.df_preference.loc[mask, 'Invoice Number'].dropna().tolist()

    STATE.df_snv = analysis.calculate_new_value(pd.concat([STATE.df_transfers, STATE.df_newvalue], ignore_index=True))
    nv_settings = settings.get('nv_settings')
    if nv_settings:
        nv_lookup = {s['invoice_number']: s for s in nv_settings if s.get('invoice_number') is not None}
        STATE.df_snv['Remove'] = STATE.df_snv['Invoice Number'].map(
            lambda inv: nv_lookup.get(inv, {}).get('remove', False) if pd.notna(inv) else False
        )
        STATE.df_snv['Ordinary Exclusion'] = STATE.df_snv['Invoice Number'].map(
            lambda inv: (nv_lookup.get(inv, {}).get('reason') or '') if pd.notna(inv) else ''
        )
    STATE.df_snv = analysis.sync_new_value(STATE.df_snv, ordinary_inv)
    STATE.df_snv = analysis.finalize_new_value(STATE.df_snv)
    STATE.df_ocb = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical)

    hist_dates = pd.to_datetime(STATE.df_historical['Payment Date'])
    pref_dates = pd.to_datetime(STATE.df_preference['Payment Date'])
    return {
        'subcase_id': int(subcase_id),
        'master_id': master['master_id'],
        'master_name': master['case_name'],
        'master_number': master['case_number'],
        'jurisdiction': master['jurisdiction'],
        'judge': master['judge'],
        'petition_date': petition_date.strftime('%m/%d/%Y'),
        'pref_start': pref_start.strftime('%m/%d/%Y'),
        'transferee': master['transferee'],
        'adversary_number': master['adversary_number'] or '',
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


_init_case = load_case(store.active_subcase_id())


# Initialize the app
app = Dash(external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.FONT_AWESOME] )
app.title  = "Preference Analysis Tool"

app.layout = html.Div(style={"padding": "20px"}, children=[
    html.H1(children='Preference Analysis Tool'),
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
        html.Div(id="master-info", children=[
            html.Div(f"Transferee: {_init_case['transferee']}   |   Adversary Number: {_init_case['adversary_number']}", style={"fontWeight": "bold", "fontSize": "1.25rem"}),
            html.Div(f"Main Case: {_init_case['master_name']}   |   Preference Period: {_init_case['pref_start']} - {_init_case['petition_date']}"),
        ]),
        html.Div(style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap"}, children=[
            html.Div(children=[
                html.Label('Master Bankruptcy Case', htmlFor="master-selector"),
                dcc.Dropdown(
                    id="master-selector",
                    options=store.list_master_options(),
                    value=_init_case['master_id'],
                    clearable=False,
                    style={"width": "300px"},
                ),
            ]),
            html.Div(children=[
                html.Label('Subcase', htmlFor="subcase-selector"),
                dcc.Dropdown(
                    id="subcase-selector",
                    options=store.list_subcase_options(_init_case['master_id']),
                    value=_init_case['subcase_id'],
                    clearable=False,
                    style={"width": "300px"},
                ),
            ]),
        ]),
    ]),
    html.Div(children=[
            dcc.Tabs([
         dcc.Tab(id="summary", label='Case Summary', children=[
            html.H3(children='Case Summary'),
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
            html.H3(id="hist-period-title", children=_init_case['hist_title']),
            dag.AgGrid(
                id="historical",
                rowData=STATE.df_historical.to_dict('records'),
                getRowStyle=analysis.ocb_default_style(),
                style={"height": "600px"},
                columnDefs=[
                    {"field": "Transfer Number"},
                    {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Payment Date"},
                    {"field": "Invoice Number"},
                    {"field": "Invoice Date"},
                    {"field": "Invoice Due"},
                    {"field": "Invoice to Payment"},
                    {"field": "Days Past Due"},
                ]
            ),
            html.Div(id="hist-period-invoice-count", children=_init_case['hist_count']),
            html.Div(id="hist-total-output"),
            html.Div(id="hist-average_dso"),
            html.Div(id="hist-average_dpd"),
            html.Div(id="hist-weighted_dso"),
            html.Div(id="hist-weighted_dpd"),
            html.Div(id="hist-skew")
    ]),

        dcc.Tab(label='Preference Period', children=[
            html.H3(id="pref-period-title", children=_init_case['pref_title']),
            dag.AgGrid(
              id="preference",
              rowData=STATE.df_preference.to_dict('records'),
              getRowStyle=analysis.ocb_default_style(),
              style={"height": "600px"},
              columnDefs=[
                {"field": "Transfer Number"},
                {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                {"field": "Payment Date"},
                {"field": "Invoice Number"},
                {"field": "Invoice Date"},
                {"field": "Invoice Due"},
                {"field": "Invoice to Payment"},
                {"field": "Days Past Due"},
            ]
        ),
        html.Div(id="pref-period-invoice-count", children=_init_case['pref_count']),
        html.Div(id="pref-total-output"),
        html.Div(id="pref-average_dso"),
        html.Div(id="pref-average_dpd"),
        html.Div(id="pref-weighted_dso"),
        html.Div(id="pref-weighted_dpd"),
        html.Div(id="diff-wavg", style={"font-weight": "bold"})
    ]),
         dcc.Tab(label='New Value', children=[
            html.H3(children='New Value Tab'),
            html.P(children='This is the content for the New Value tab.'),
            dag.AgGrid(
                id="new_value",
                rowData=STATE.df_snv.to_dict('records'),
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
            html.Div(id="nv-net-preference-total"),
        ]),
         dcc.Tab(id="ocb", label='Ordinary Course', children=[
            html.H3(children='Ordinary Course'),
            dcc.Store(id="ocb-range", data=_init_case['ocb_range']),
            dcc.Store(id="ocb-total-range-flag", data=_init_case['ocb_total_flag']),
            dcc.Store(id="ocb-ordinary-invoices", data=_init_case['ordinary_inv']),
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
                        html.Label('Start Range', htmlFor="ocb-start"),
                        dcc.Dropdown(id="ocb-start", options=[{"label": str(i), "value": i} for i in range(-5, 21)], value=_init_case['ocb_start'], clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('End Range', htmlFor="ocb-end"),
                        dcc.Dropdown(id="ocb-end", options=[{"label": str(i), "value": i} for i in range(100, 301)], value=_init_case['ocb_end'], clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('Step Size', htmlFor="ocb-step"),
                        dbc.Select(id="ocb-step", options=[{"label": str(i), "value": i} for i in range(1, 11)], value=_init_case['ocb_step']),
                    ]),
                    dbc.Button("+/- 15 Days", id="ocb-plus15", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Total Range", id="ocb-total-range", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Clear OCB Range", id="ocb-clear", color="primary", className="mt-2 w-100"),
                    html.Div(id="autosave-status", className="mt-3 text-muted small"),
                ], width=3),
                dbc.Col([
                    dag.AgGrid(
                        id="ocb_grid",
                        rowData=STATE.df_ocb.to_dict('records'),
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
        ])
     ])
    ])
])

@callback(
    Output("new_value", "rowData"),
    Input("new_value", "cellValueChanged"),
    Input("ocb-ordinary-invoices", "data"),
    State("new_value", "rowData")
)
def update_new_value(cellChange, ordinary_invoices, rowData):
    df = pd.DataFrame(rowData)
    trig = dash.callback_context.triggered_id
    if trig in ("ocb-ordinary-invoices", None):
        df = analysis.sync_new_value(df, ordinary_invoices)
    df = analysis.finalize_new_value(df)
    return df.to_dict("records")

@callback(
    Output("nv-net-preference-total", "children"),
    Input("new_value", "rowData")
)
def update_nv_totals(rowData):
    df = pd.DataFrame(rowData)
    total = df["Net Preference"].iloc[-1]
    return f"Net Preference Total: ${total:,.2f}"

def _ocb_transfer_shares():
    tot = STATE.df_preference.groupby("Transfer Number")["Invoice Amount"].sum()
    ord_tot = STATE.df_preference[STATE.df_preference["Ordinary"] == 1].groupby("Transfer Number")["Invoice Amount"].sum()
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
    Input("new_value", "rowData"),
    Input("ocb-ordinary-invoices", "data"),
    Input("ocb-range", "data")
)
def update_summary(nv_rowData, ordinary_invoices, ocb_range):
    df_nv = pd.DataFrame(nv_rowData)
    total_transfers = STATE.df_transfers["Transfer Amount"].sum()
    total_new_value = df_nv["Allowed New Value"].sum()
    net_new_value = df_nv["Net Preference"].iloc[-1]
    hist_wavg = analysis.calc_weighted_dso(STATE.df_historical)
    pref_wavg = analysis.calc_weighted_dso(STATE.df_preference)
    diff = (pref_wavg - hist_wavg) / hist_wavg * 100

    tot_shares, ord_shares = _ocb_transfer_shares()
    net_pref_defenses = analysis.calc_net_pref_defenses(df_nv, tot_shares, ord_shares)

    return (f"${total_transfers:,.2f}", f"${total_new_value:,.2f}", f"${net_new_value:,.2f}",
            f"${net_pref_defenses:,.2f}",
            f"{hist_wavg:.2f}", f"{pref_wavg:.2f}", f"{diff:.2f}%",
            f"${net_pref_defenses:,.2f}")

@callback(
    Output("ocb_grid", "rowData"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value")
)
def update_ocb_grid(start, end, step):
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    df = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, start, end, step)
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
    prevent_initial_call=True
)
def manage_ocb_range(n_total, n_plus15, click, start, end, step, n_clicks, restore, sel, flag):
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    trig = dash.callback_context.triggered[0]["prop_id"]

    if trig == "ocb-restore.data":
        r = restore or {}
        return (r.get('range'), r.get('start'), r.get('end'),
                int(r.get('step') or step), bool(r.get('total')))

    if trig == "ocb-plus15.n_clicks":
        anchor = round(analysis.calc_weighted_dso(STATE.df_historical))
        min_days = anchor - 15
        max_days = anchor + 15
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, new_start, new_end, step)
        r0 = analysis.ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = analysis.ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, step, flag_out

    if trig == "ocb-total-range.n_clicks":
        min_days = int(STATE.df_historical["Invoice to Payment"].min())
        max_days = int(STATE.df_historical["Invoice to Payment"].max())
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, new_start, new_end, step)
        r0 = analysis.ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = analysis.ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, step, flag_out

    if trig == "ocb-clear.n_clicks":
        return None, start, end, step, False

    if trig != "ocb_grid.cellClicked":
        if flag:
            return dash.no_update, start, end, step, False
        return None, start, end, step, False

    idx = int(click["rowIndex"])
    if sel is None or sel.get("end") is not None:
        return {"start": idx, "end": None}, start, end, step, False
    return {"start": sel["start"], "end": idx}, start, end, step, False

@callback(
    Output("subcase-selector", "options"),
    Output("subcase-selector", "value"),
    Output("master-info", "children"),
    Output("historical", "rowData"),
    Output("preference", "rowData"),
    Output("new_value", "rowData", allow_duplicate=True),
    Output("ocb_grid", "rowData", allow_duplicate=True),
    Output("hist-period-title", "children"),
    Output("pref-period-title", "children"),
    Output("hist-period-invoice-count", "children"),
    Output("pref-period-invoice-count", "children"),
    Output("ocb-restore", "data"),
    Input("master-selector", "value"),
    Input("subcase-selector", "value"),
    prevent_initial_call=True
)
def selection_changed(master_id, subcase_id):
    trig = dash.callback_context.triggered[0]["prop_id"]
    no_update = dash.no_update

    if trig == "master-selector.value":
        opts = store.list_subcase_options(master_id)
        first_id = opts[0]["value"] if opts else None
        return (opts, first_id) + (no_update,) * 10

    if subcase_id is None:
        raise PreventUpdate

    info = load_case(subcase_id)
    store.save_app_state(subcase_id)
    master_info = [
        html.Div(f"Transferee: {info['transferee']}   |   Adversary Number: {info['adversary_number']}", style={"fontWeight": "bold", "fontSize": "1.25rem"}),
        html.Div(f"Main Case: {info['master_name']}   |   Preference Period: {info['pref_start']} - {info['petition_date']}"),
    ]
    return (
        no_update,
        no_update,
        master_info,
        STATE.df_historical.to_dict('records'),
        STATE.df_preference.to_dict('records'),
        STATE.df_snv.to_dict('records'),
        STATE.df_ocb.to_dict('records'),
        info['hist_title'],
        info['pref_title'],
        info['hist_count'],
        info['pref_count'],
        {'range': info['ocb_range'], 'start': info['ocb_start'], 'end': info['ocb_end'],
         'step': info['ocb_step'], 'total': info['ocb_total_flag']},
    )

@callback(
    Output("ocb_grid", "getRowStyle"),
    Output("ocb-range-status", "children"),
    Output("ocb-hist-coverage", "children"),
    Output("ocb-ordinary-invoices", "data"),
    Input("ocb-range", "data"),
    State("ocb-start", "value"),
    State("ocb-end", "value"),
    State("ocb-step", "value")
)
def apply_ocb_range(sel, start, end, step):
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    if sel is None:
        STATE.df_preference["Ordinary"] = 0
        return analysis.ocb_default_style(), "No OCB range selected.", "Historical invoices captured in range: —", []
    if sel.get("end") is None:
        STATE.df_preference["Ordinary"] = 0
        idx = int(sel["start"])
        df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, start, end, step)
        label = df_cur["date_range"].iloc[idx]
        status = f"Range start selected: {label} — click a second row to complete the range."
        return analysis.ocb_pending_style(idx), status, "Historical invoices captured in range: —", []
    a, b = sorted((int(sel["start"]), int(sel["end"])))
    df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, start, end, step)
    s_label = df_cur["date_range"].iloc[a]
    e_label = df_cur["date_range"].iloc[b]
    lower, _ = analysis.ocb_label_bounds(s_label, start, end)
    _, upper = analysis.ocb_label_bounds(e_label, start, end)
    days = STATE.df_preference["Invoice to Payment"]
    mask = pd.Series(True, index=STATE.df_preference.index)
    if lower is not None:
        mask &= days >= lower
    if upper is not None:
        mask &= days <= upper
    STATE.df_preference.loc[mask, "Ordinary"] = 1
    status = f"OCB range: {s_label} to {e_label} — {int(mask.sum())} preference invoice(s) marked as Ordinary."
    hist_days = STATE.df_historical["Invoice to Payment"]
    hist_mask = pd.Series(True, index=STATE.df_historical.index)
    if lower is not None:
        hist_mask &= hist_days >= lower
    if upper is not None:
        hist_mask &= hist_days <= upper
    coverage = f"Historical invoices captured in range: {int(hist_mask.sum())} of {len(STATE.df_historical)} ({hist_mask.mean() * 100:.2f}%)"
    ordinary_inv = STATE.df_preference.loc[mask, "Invoice Number"].dropna().tolist()
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
    df_cur = analysis.build_ocb_data(STATE.df_preference, STATE.df_historical, start, end, step)
    lower, _ = analysis.ocb_label_bounds(df_cur["date_range"].iloc[a], start, end)
    _, upper = analysis.ocb_label_bounds(df_cur["date_range"].iloc[b], start, end)
    if lower is not None and upper is not None:
        total_days = upper - lower
    else:
        total_days = float("inf")
    if total_days > 100:
        return html.Span("Total ranges broader than 100 days may be unordinary in many jurisdictions.", style={"color": "red", "fontWeight": "bold"})
    return ""
 
@callback(
    Output("hist-total-output", "children"),
    Output("hist-average_dso", "children"),
    Output("hist-average_dpd", "children"),
    Output("hist-weighted_dso", "children"),
    Output("hist-weighted_dpd", "children"),
    Output("hist-skew", "children"),
    Input("historical", "rowData")
)
 
def update_hist_totals(rowData):
    df = pd.DataFrame(rowData)
    total = df["Transfer Amount"].sum()
    average_dso = df["Invoice to Payment"].mean()
    average_dpd = df["Days Past Due"].mean()
    weighted_dso = analysis.calc_weighted_dso(df)
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    skew = df["Invoice to Payment"].skew()
    if skew > 1 or skew < -1:
        skew_warning = " (Warning: Skew is outside the range of -1 to 1, indicating a non-normal distribution)"
    else:
        skew_warning = ""
    
    return f"Historical Period Total Transfer Amount: ${total:,.2f}", f"Historical Period Average DSO: {average_dso:.2f}", f"Historical Period Average DPD: {average_dpd:.2f}", f"Historical Period Weighted DSO: {weighted_dso:.2f}", f"Historical Period Weighted DPD: {weighted_dpd:.2f}", f"Historical Period Skew: {skew:.2f}, {skew_warning}"

@callback(
    Output("pref-total-output", "children"),
    Output("pref-average_dso", "children"),
    Output("pref-average_dpd", "children"),
    Output("pref-weighted_dso", "children"),
    Output("pref-weighted_dpd", "children"),
    Output("diff-wavg", "children"),
    Input("preference", "rowData")
)

def update_pref_totals(rowData):
    df = pd.DataFrame(rowData)
    total = df["Transfer Amount"].sum()
    average_dso = df["Invoice to Payment"].mean()
    average_dpd = df["Days Past Due"].mean()
    weighted_dso = analysis.calc_weighted_dso(df)
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    diff = analysis.compare_hist_pref(STATE.df_historical, STATE.df_preference)
    return f"Preference Period Total Transfer Amount: ${total:,.2f}", f"Preference Period Average DSO: {average_dso:.2f}", f"Preference Period Average DPD: {average_dpd:.2f}", f"Preference Period Weighted DSO: {weighted_dso:.2f}", f"Preference Period Weighted DPD: {weighted_dpd:.2f}", f"Weighted DSO Difference (Historical vs Preference): {diff:.2f}%"

@callback(
    Output("autosave-status", "children"),
    Input("ocb-range", "data"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-total-range-flag", "data"),
    Input("new_value", "rowData"),
    prevent_initial_call=True
)
def autosave_settings(ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_rowData):
    from datetime import datetime
    nv_settings = []
    for row in nv_rowData:
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
    store.save_case_settings(STATE.active_subcase_id, ocb_range, ocb_start, ocb_end, ocb_step, ocb_total_flag, nv_settings)
    return f"Saved {datetime.now().strftime('%H:%M:%S')}"


# Run the app
if __name__ == '__main__':
    app.run(debug=True)