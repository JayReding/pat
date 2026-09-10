# Import packages
from dash import Dash, html, dcc, callback, Output, Input, State
import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
import plotly.express as px
import sqlite3
import json

# Incorporate data
#df = pd.read_csv('testdata.csv')
df_historical = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" < date('2023-07-17')", sqlite3.connect('pat_test.db'))
df_preference = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" >= date('2023-07-17') AND \"Payment Date\" <= date('2023-10-15')", sqlite3.connect('pat_test.db'))
df_transfers = pd.read_sql("SELECT DISTINCT \"Transfer Number\", \"Transfer Amount\", \"Payment Date\" FROM test_data WHERE \"Payment Date\" >= date('2023-07-17') AND \"Payment Date\" <= date('2023-10-15') ORDER BY \"Payment Date\";", sqlite3.connect('pat_test.db'))
df_newvalue = pd.read_sql("SELECT * from test_data WHERE \"Unpaid\" IS 1", sqlite3.connect('pat_test.db'))



def trim_leading_nulls(df: pd.DataFrame, column_name: str) -> pd.DataFrame:
    """
    Removes all rows from the beginning of the DataFrame until the first 
    non-null value is found in the specified column.

    Args:
        df: The pandas DataFrame to clean.
        column_name: The name of the column to check for leading nulls.

    Returns:
        A new DataFrame starting from the first row where 'column_name' is not null.
    """
    
    # 1. Identify the boolean mask: True where the column is NOT null
    is_not_null = df[column_name].notna()
    
    # 2. Find the index of the FIRST True value (i.e., the first non-null row)
    first_valid_index = df.index[is_not_null].min()
    
    # Handle the edge case where the entire column might be null
    if pd.isna(first_valid_index):
        print(f"Warning: The entire '{column_name}' column is null. Returning an empty DataFrame.")
        return pd.DataFrame(columns=df.columns)

    # 3. Slice the DataFrame starting from that index
    cleaned_df = df.loc[first_valid_index:]
    
    return cleaned_df


#Run our new value analysis

def calculate_new_value(df: pd.DataFrame) -> pdDataFrame:
    #Combine the relevant dates, ignoring any that are null to create a unified transaction date for mew value analysis
    df["Transaction Date"] = df[["Payment Date", "Invoice Date"]].apply(lambda row: ', '.join(row.dropna()), axis=1)

    # Sort the result by transaction date
    df.sort_values(by="Transaction Date", inplace=True)

    df = trim_leading_nulls(df, "Transfer Amount")  # Remove leading nulls in 'Transfer Amount'

    #df["Allowed New Value"] = np.where(df["Unpaid"] > 0, df["Invoice Amount"], np.nan)  # Set defaults for allowed new value based on invoice amount
    df["Allowed New Value"] = df["Invoice Amount"]  # Set defaults for allowed new value based on invoice amount

    # We need to create a cumulative sum by adding the transfers and subtracting any subsequent new value
    df["Net Change"] = df["Transfer Amount"].fillna(0) - df["Invoice Amount"].fillna(0)

    df["Net Preference"] = df["Net Change"].cumsum().clip(lower=0) # Ensure that the net preference does not go below zero

    df = df.drop(columns=["Net Change"])  # Drop the intermediate column

    return df

df_snv = calculate_new_value(pd.concat([df_transfers, df_newvalue], ignore_index=True))

def build_ocb_data(start=0, end=100, step=5):
    nrows = max(1, (end - start) // step)
    edges = [start] + [start + k*step + 1 for k in range(1, nrows)] + [end + 1]
    labels = [f"< {start}"] + [f"{edges[i]}-{edges[i+1]-1}" for i in range(nrows)] + [f"> {end}"]
    bins = [float("-inf")] + edges + [float("inf")]
    p = df_preference.copy()
    h = df_historical.copy()
    p["bin"] = pd.cut(p["Invoice to Payment"], bins=bins, labels=labels, right=False, include_lowest=True)
    h["bin"] = pd.cut(h["Invoice to Payment"], bins=bins, labels=labels, right=False, include_lowest=True)
    rows = []
    for lab in labels:
        pi = p.index[p["bin"] == lab]
        hi = h.index[h["bin"] == lab]
        pc, hc = len(pi), len(hi)
        p_pct = pc / len(p) * 100
        h_pct = hc / len(h) * 100
        pct_diff = (p_pct - h_pct) / h_pct * 100 if h_pct != 0 else 0.0
        rows.append({
            "date_range": lab,
            "pref_pct": p_pct,
            "hist_pct": h_pct,
            "pct_diff": pct_diff,
            "pref_count": pc,
            "hist_count": hc,
            "pref_amount": p.loc[pi, "Invoice Amount"].sum(),
            "hist_amount": h.loc[hi, "Invoice Amount"].sum(),
        })
    return pd.DataFrame(rows)


df_ocb = build_ocb_data()

CURRENT_CASE_ID = 1

def _init_state_db():
    conn = sqlite3.connect('pat_state.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS cases (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        source_file TEXT,
        meta TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS case_settings (
        case_id INTEGER PRIMARY KEY REFERENCES cases(id),
        ocb_start INTEGER,
        ocb_end INTEGER,
        ocb_step INTEGER,
        ocb_range TEXT,
        ocb_total_flag INTEGER,
        nv_settings TEXT
    )''')
    cur.execute("SELECT COUNT(*) FROM cases WHERE id=?", (CURRENT_CASE_ID,))
    if cur.fetchone()[0] == 0:
        from datetime import datetime, timezone
        cur.execute(
            "INSERT INTO cases (id, name, created_at, source_file, meta) VALUES (?, ?, ?, ?, ?)",
            (CURRENT_CASE_ID, 'Default', datetime.now(timezone.utc).isoformat(), 'pat_test.db', '{}'),
        )
    conn.commit()
    return conn

_init_state_db()

def _get_state_conn():
    return sqlite3.connect('pat_state.db')

def _load_case_settings(case_id):
    conn = _get_state_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings FROM case_settings WHERE case_id=?",
        (case_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {}
    return {
        'ocb_start': row[0], 'ocb_end': row[1], 'ocb_step': row[2],
        'ocb_range': json.loads(row[3]) if row[3] else None,
        'ocb_total_flag': bool(row[4]) if row[4] is not None else False,
        'nv_settings': json.loads(row[5]) if row[5] else None,
    }


def ocb_default_style():
    return {
        "styleConditions": [
            {"condition": "params.rowIndex % 2 === 1", "style": {"backgroundColor": "var(--bs-secondary-bg)"}},
        ],
        "defaultStyle": {"backgroundColor": "var(--bs-body-bg)"},
    }


def ocb_range_style(a, b):
    return {
        "styleConditions": [
            {"condition": f"params.rowIndex >= {a} && params.rowIndex <= {b}", "style": {"backgroundColor": "var(--bs-success-bg-subtle)"}},
            {"condition": "params.rowIndex % 2 === 1", "style": {"backgroundColor": "var(--bs-secondary-bg)"}},
        ],
        "defaultStyle": {"backgroundColor": "var(--bs-body-bg)"},
    }


def ocb_pending_style(idx):
    return {
        "styleConditions": [
            {"condition": f"params.rowIndex === {idx}", "style": {"backgroundColor": "var(--bs-warning-bg-subtle)"}},
            {"condition": "params.rowIndex % 2 === 1", "style": {"backgroundColor": "var(--bs-secondary-bg)"}},
        ],
        "defaultStyle": {"backgroundColor": "var(--bs-body-bg)"},
    }


def ocb_label_bounds(label, start, end):
    if label.startswith("< "):
        return None, start - 1
    if label.startswith("> "):
        return end + 1, None
    lo, hi = label.split("-")
    return int(lo), int(hi)


def ocb_row_index(value, df, start, end):
    for i, lab in enumerate(df["date_range"]):
        lo, hi = ocb_label_bounds(lab, start, end)
        if lo is None:
            lo = float("-inf")
        if hi is None:
            hi = float("inf")
        if lo <= value <= hi:
            return i
    return 0

_saved = _load_case_settings(CURRENT_CASE_ID)

_restored_ocb_range = _saved.get('ocb_range')
if _restored_ocb_range is not None and _restored_ocb_range.get('end') is None:
    _restored_ocb_range = None
_restored_ocb_start = _saved.get('ocb_start') if _saved.get('ocb_start') is not None else 0
_restored_ocb_end = _saved.get('ocb_end') if _saved.get('ocb_end') is not None else 100
_restored_ocb_step = _saved.get('ocb_step') if _saved.get('ocb_step') is not None else 5
_restored_ocb_total_flag = _saved.get('ocb_total_flag', False)

_restored_ordinary_inv = []
if _restored_ocb_range is not None:
    _a, _b = sorted((_restored_ocb_range['start'], _restored_ocb_range['end']))
    _df_cur = build_ocb_data(_restored_ocb_start, _restored_ocb_end, _restored_ocb_step)
    _s_label = _df_cur["date_range"].iloc[_a]
    _e_label = _df_cur["date_range"].iloc[_b]
    _lower, _ = ocb_label_bounds(_s_label, _restored_ocb_start, _restored_ocb_end)
    _, _upper = ocb_label_bounds(_e_label, _restored_ocb_start, _restored_ocb_end)
    _days = df_preference["Invoice to Payment"]
    _mask = pd.Series(True, index=df_preference.index)
    if _lower is not None:
        _mask &= _days >= _lower
    if _upper is not None:
        _mask &= _days <= _upper
    _restored_ordinary_inv = df_preference.loc[_mask, "Invoice Number"].dropna().tolist()
    df_preference.loc[_mask, "Ordinary"] = 1

if _saved.get('nv_settings'):
    _nv_lookup = {s['invoice_number']: s for s in _saved['nv_settings'] if s.get('invoice_number') is not None}
    df_snv['Remove'] = df_snv['Invoice Number'].map(
        lambda inv: _nv_lookup.get(inv, {}).get('remove', False) if pd.notna(inv) else False
    )
    df_snv['Ordinary Exclusion'] = df_snv['Invoice Number'].map(
        lambda inv: (_nv_lookup.get(inv, {}).get('reason') or '') if pd.notna(inv) else ''
    )


# Initialize the app
app = Dash(external_stylesheets=[dbc.themes.ZEPHYR, dbc.icons.FONT_AWESOME] )
app.title  = "Preference Analysis Tool"

# Get historical range data
df_historical_dates = pd.to_datetime(df_historical["Payment Date"])
history_start = df_historical_dates.min()
history_end = df_historical_dates.max()
history_count = df_historical_dates.shape[0]

# Get prefernence period range data
df_preference_dates = pd.to_datetime(df_preference["Payment Date"])
preference_count = df_preference_dates.shape[0]


date_obj = "d3.timeParse('%Y-%m-%d %H:%M:%S')(params.data.date)"

app.layout = html.Div(style={"padding": "20px"}, children=[
    html.H1(children='Preference Analysis Tool'),
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
            html.H3(children=f'Historical Period: {history_start.strftime("%m/%d/%Y")} through {history_end.strftime("%m/%d/%Y")}'),
            dag.AgGrid(
                id="historical",
                rowData=df_historical.to_dict('records'),
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
            html.Div(children=f'Historical Period Invoice Count: {history_count}'),
            html.Div(id="hist-total-output"),
            html.Div(id="hist-average_dso"),
            html.Div(id="hist-average_dpd"),
            html.Div(id="hist-weighted_dso"),
            html.Div(id="hist-weighted_dpd"),
            html.Div(id="hist-skew")
    ]),

        dcc.Tab(label='Preference Period', children=[
            html.H3(children='Preference Period: 07/17/2023 through 10/15/2023'),
            dag.AgGrid(
              id="preference",
              rowData=df_preference.to_dict('records'),
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
        html.Div(children=f'Preference Period Invoice Count: {preference_count}'),
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
                rowData=df_snv.to_dict('records'),
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
            dcc.Store(id="ocb-range", data=_restored_ocb_range),
            dcc.Store(id="ocb-total-range-flag", data=_restored_ocb_total_flag),
            dcc.Store(id="ocb-ordinary-invoices", data=_restored_ordinary_inv),
            dbc.Row([
                dbc.Col([
                    html.Div(id="ocb-range-status", className="mb-3"),
                    html.Div(id="ocb-hist-coverage", className="mb-3"),
                    html.Div(id="ocb-range-warning", className="mb-3"),
                    html.Div(className="mb-3", children=[
                        html.Label('Start Range', htmlFor="ocb-start"),
                        dcc.Dropdown(id="ocb-start", options=[{"label": str(i), "value": i} for i in range(-5, 21)], value=_restored_ocb_start, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('End Range', htmlFor="ocb-end"),
                        dcc.Dropdown(id="ocb-end", options=[{"label": str(i), "value": i} for i in range(100, 301)], value=_restored_ocb_end, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('Step Size', htmlFor="ocb-step"),
                        dbc.Select(id="ocb-step", options=[{"label": str(i), "value": i} for i in range(1, 11)], value=_restored_ocb_step),
                    ]),
                    dbc.Button("+/- 15 Days", id="ocb-plus15", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Total Range", id="ocb-total-range", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Clear OCB Range", id="ocb-clear", color="primary", className="mt-2 w-100"),
                    html.Div(id="autosave-status", className="mt-3 text-muted small"),
                ], width=3),
                dbc.Col([
                    dag.AgGrid(
                        id="ocb_grid",
                        rowData=df_ocb.to_dict('records'),
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
        ordinary_set = set(ordinary_invoices) if ordinary_invoices else set()
        for idx, row in df.iterrows():
            inv = row.get("Invoice Number")
            if pd.notna(inv) and inv in ordinary_set:
                df.at[idx, "Remove"] = True
                df.at[idx, "Ordinary Exclusion"] = "Paid by unavoidable transfer - 547(c)(4)(A)"
            elif pd.notna(inv) and row.get("Ordinary Exclusion") == "Paid by unavoidable transfer - 547(c)(4)(A)":
                df.at[idx, "Remove"] = False
                df.at[idx, "Ordinary Exclusion"] = ""

    if "Remove" not in df.columns:
        df["Remove"] = False
    else:
        df["Remove"] = df["Remove"].fillna(False)

    df["Allowed New Value"] = df.apply(
        lambda row: 0 if row.get("Remove") else (
            row["Invoice Amount"] if pd.notna(row.get("Invoice Amount")) else 0
        ),
        axis=1
    )

    df["Net Preference"] = (
        df["Transfer Amount"].fillna(0) - df["Allowed New Value"].fillna(0)
    ).cumsum().clip(lower=0)

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
    tot = df_preference.groupby("Transfer Number")["Invoice Amount"].sum()
    ord_tot = df_preference[df_preference["Ordinary"] == 1].groupby("Transfer Number")["Invoice Amount"].sum()
    return tot, ord_tot

@callback(
    Output("summary-total-transfers", "children"),
    Output("summary-total-new-value", "children"),
    Output("summary-net-new-value", "children"),
    Output("summary-net-pref-defenses", "children"),
    Output("summary-hist-wavg", "children"),
    Output("summary-pref-wavg", "children"),
    Output("summary-dso-diff", "children"),
    Input("new_value", "rowData")
)
def update_summary(nv_rowData):
    df_nv = pd.DataFrame(nv_rowData)
    total_transfers = df_transfers["Transfer Amount"].sum()
    total_new_value = df_nv["Allowed New Value"].sum()
    net_new_value = df_nv["Net Preference"].iloc[-1]
    hist_wavg = calc_weighted_dso(df_historical)
    pref_wavg = calc_weighted_dso(df_preference)
    diff = (pref_wavg - hist_wavg) / hist_wavg * 100

    tot_shares, ord_shares = _ocb_transfer_shares()
    running = 0.0
    for row in df_nv.to_dict("records"):
        t_amt = row.get("Transfer Amount")
        if pd.notna(t_amt):
            tr = row.get("Transfer Number")
            t = tot_shares.get(tr, 0.0)
            o = ord_shares.get(tr, 0.0)
            running = max(0.0, running + t_amt * (1 - (o / t if t else 0.0)))
        else:
            running = max(0.0, running - (row.get("Allowed New Value") or 0))
    net_pref_defenses = running

    return (f"${total_transfers:,.2f}", f"${total_new_value:,.2f}", f"${net_new_value:,.2f}",
            f"${net_pref_defenses:,.2f}",
            f"{hist_wavg:.2f}", f"{pref_wavg:.2f}", f"{diff:.2f}%")

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
    df = build_ocb_data(start, end, step)
    return df.to_dict("records")

@callback(
    Output("ocb-range", "data"),
    Output("ocb-start", "value"),
    Output("ocb-end", "value"),
    Output("ocb-total-range-flag", "data"),
    Input("ocb-total-range", "n_clicks"),
    Input("ocb-plus15", "n_clicks"),
    Input("ocb_grid", "cellClicked"),
    Input("ocb-start", "value"),
    Input("ocb-end", "value"),
    Input("ocb-step", "value"),
    Input("ocb-clear", "n_clicks"),
    State("ocb-range", "data"),
    State("ocb-total-range-flag", "data"),
    prevent_initial_call=True
)
def manage_ocb_range(n_total, n_plus15, click, start, end, step, n_clicks, sel, flag):
    start = int(start) if start is not None else 0
    end = int(end) if end is not None else 100
    step = int(step) if step is not None else 5
    trig = dash.callback_context.triggered[0]["prop_id"]

    if trig == "ocb-plus15.n_clicks":
        anchor = round(calc_weighted_dso(df_historical))
        min_days = anchor - 15
        max_days = anchor + 15
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = build_ocb_data(new_start, new_end, step)
        r0 = ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, flag_out

    if trig == "ocb-total-range.n_clicks":
        min_days = int(df_historical["Invoice to Payment"].min())
        max_days = int(df_historical["Invoice to Payment"].max())
        new_start = max(-5, min(min(start, min_days), 20))
        new_end = max(100, min(max(end, max_days), 300))
        df_cur = build_ocb_data(new_start, new_end, step)
        r0 = ocb_row_index(min_days, df_cur, new_start, new_end)
        r1 = ocb_row_index(max_days, df_cur, new_start, new_end)
        flag_out = (new_start != start) or (new_end != end)
        return {"start": r0, "end": r1}, new_start, new_end, flag_out

    if trig == "ocb-clear.n_clicks":
        return None, start, end, False

    if trig != "ocb_grid.cellClicked":
        if flag:
            return dash.no_update, start, end, False
        return None, start, end, False

    idx = int(click["rowIndex"])
    if sel is None or sel.get("end") is not None:
        return {"start": idx, "end": None}, start, end, False
    return {"start": sel["start"], "end": idx}, start, end, False

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
        df_preference["Ordinary"] = 0
        return ocb_default_style(), "No OCB range selected.", "Historical invoices captured in range: —", []
    if sel.get("end") is None:
        df_preference["Ordinary"] = 0
        idx = int(sel["start"])
        df_cur = build_ocb_data(start, end, step)
        label = df_cur["date_range"].iloc[idx]
        status = f"Range start selected: {label} — click a second row to complete the range."
        return ocb_pending_style(idx), status, "Historical invoices captured in range: —", []
    a, b = sorted((int(sel["start"]), int(sel["end"])))
    df_cur = build_ocb_data(start, end, step)
    s_label = df_cur["date_range"].iloc[a]
    e_label = df_cur["date_range"].iloc[b]
    lower, _ = ocb_label_bounds(s_label, start, end)
    _, upper = ocb_label_bounds(e_label, start, end)
    days = df_preference["Invoice to Payment"]
    mask = pd.Series(True, index=df_preference.index)
    if lower is not None:
        mask &= days >= lower
    if upper is not None:
        mask &= days <= upper
    df_preference.loc[mask, "Ordinary"] = 1
    status = f"OCB range: {s_label} to {e_label} — {int(mask.sum())} preference invoice(s) marked as Ordinary."
    hist_days = df_historical["Invoice to Payment"]
    hist_mask = pd.Series(True, index=df_historical.index)
    if lower is not None:
        hist_mask &= hist_days >= lower
    if upper is not None:
        hist_mask &= hist_days <= upper
    coverage = f"Historical invoices captured in range: {int(hist_mask.sum())} of {len(df_historical)} ({hist_mask.mean() * 100:.2f}%)"
    ordinary_inv = df_preference.loc[mask, "Invoice Number"].dropna().tolist()
    return ocb_range_style(a, b), status, coverage, ordinary_inv

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
    df_cur = build_ocb_data(start, end, step)
    lower, _ = ocb_label_bounds(df_cur["date_range"].iloc[a], start, end)
    _, upper = ocb_label_bounds(df_cur["date_range"].iloc[b], start, end)
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
    weighted_dso = calc_weighted_dso(df)
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
    weighted_dso = calc_weighted_dso(df)
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    diff = compare_hist_pref()
    return f"Preference Period Total Transfer Amount: ${total:,.2f}", f"Preference Period Average DSO: {average_dso:.2f}", f"Preference Period Average DPD: {average_dpd:.2f}", f"Preference Period Weighted DSO: {weighted_dso:.2f}", f"Preference Period Weighted DPD: {weighted_dpd:.2f}", f"Weighted DSO Difference (Historical vs Preference): {diff:.2f}%"

def calc_weighted_dso(df):
    return df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()

def compare_hist_pref():
    hist_weighted_dso = calc_weighted_dso(df_historical)
    pref_weighted_dso = calc_weighted_dso(df_preference)
    diff = (pref_weighted_dso - hist_weighted_dso) / hist_weighted_dso * 100
    return(diff) 

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
    ocb_range_json = json.dumps(ocb_range) if ocb_range is not None else None
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
    nv_json = json.dumps(nv_settings) if nv_settings else None
    conn = _get_state_conn()
    conn.execute(
        """INSERT INTO case_settings (case_id, ocb_start, ocb_end, ocb_step, ocb_range, ocb_total_flag, nv_settings)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(case_id) DO UPDATE SET
               ocb_start=excluded.ocb_start, ocb_end=excluded.ocb_end,
               ocb_step=excluded.ocb_step, ocb_range=excluded.ocb_range,
               ocb_total_flag=excluded.ocb_total_flag, nv_settings=excluded.nv_settings""",
        (CURRENT_CASE_ID, int(ocb_start or 0), int(ocb_end or 100), int(ocb_step or 5),
         ocb_range_json, int(bool(ocb_total_flag)), nv_json),
    )
    conn.commit()
    conn.close()
    return f"Saved {datetime.now().strftime('%H:%M:%S')}"

def get_case_meta(case_id=CURRENT_CASE_ID):
    conn = _get_state_conn()
    cur = conn.cursor()
    cur.execute("SELECT meta FROM cases WHERE id=?", (case_id,))
    row = cur.fetchone()
    conn.close()
    return json.loads(row[0]) if row and row[0] else {}

def set_case_meta(key, value, case_id=CURRENT_CASE_ID):
    meta = get_case_meta(case_id)
    meta[key] = value
    conn = _get_state_conn()
    conn.execute("UPDATE cases SET meta=? WHERE id=?", (json.dumps(meta), case_id))
    conn.commit()
    conn.close()



# Run the app
if __name__ == '__main__':
    app.run(debug=True)