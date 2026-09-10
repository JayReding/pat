# Import packages
from dash import Dash, html, dcc, callback, Output, Input, State
import dash
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
import plotly.express as px
import sqlite3

# Incorporate data
#df = pd.read_csv('testdata.csv')
df_historical = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" < date('2023-07-17')", sqlite3.connect('pat_test.db'))
df_preference = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" >= date('2023-07-17') AND \"Payment Date\" <= date('2023-10-15')", sqlite3.connect('pat_test.db'))
df_transfers = pd.read_sql("SELECT DISTINCT \"Transfer Number\", \"Transfer Amount\", \"Payment Date\" FROM test_data WHERE \"Payment Date\" >= date('2023-07-14') AND \"Payment Date\" <= date('2023-10-15') ORDER BY \"Payment Date\";", sqlite3.connect('pat_test.db'))
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
                    {"headerName": "Exclude New Value", "field": "Remove", "editable": {"function": "params.data['Invoice Amount'] != null"}, "cellEditor": "agCheckboxCellEditor", "cellEditorParams": {"values": [True, False]}, "valueSetter": {"function": "params.data['Remove'] = params.newValue; return true;"}}
                ]
            ),
            html.Div(id="nv-net-preference-total"),
        ]),
         dcc.Tab(id="ocb", label='Ordinary Course', children=[
            html.H3(children='Ordinary Course'),
            dcc.Store(id="ocb-range", data=None),
            dcc.Store(id="ocb-total-range-flag", data=False),
            dbc.Row([
                dbc.Col([
                    html.Div(id="ocb-range-status", className="mb-3"),
                    html.Div(id="ocb-hist-coverage", className="mb-3"),
                    html.Div(id="ocb-range-warning", className="mb-3"),
                    html.Div(className="mb-3", children=[
                        html.Label('Start Range', htmlFor="ocb-start"),
                        dcc.Dropdown(id="ocb-start", options=[{"label": str(i), "value": i} for i in range(-5, 21)], value=0, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('End Range', htmlFor="ocb-end"),
                        dcc.Dropdown(id="ocb-end", options=[{"label": str(i), "value": i} for i in range(100, 301)], value=100, clearable=False, searchable=False),
                    ]),
                    html.Div(className="mb-3", children=[
                        html.Label('Step Size', htmlFor="ocb-step"),
                        dbc.Select(id="ocb-step", options=[{"label": str(i), "value": i} for i in range(1, 11)], value=5),
                    ]),
                    dbc.Button("+/- 15 Days", id="ocb-plus15", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Total Range", id="ocb-total-range", color="secondary", className="mt-2 w-100"),
                    dbc.Button("Clear OCB Range", id="ocb-clear", color="primary", className="mt-2 w-100"),
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
    State("new_value", "rowData")
)
def update_new_value(cellChange, rowData):
    df = pd.DataFrame(rowData)

    if "Remove" not in df.columns:
        df["Remove"] = False

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

@callback(
    Output("summary-total-transfers", "children"),
    Output("summary-total-new-value", "children"),
    Output("summary-net-new-value", "children"),
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
    return (f"${total_transfers:,.2f}", f"${total_new_value:,.2f}", f"${net_new_value:,.2f}",
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
        return ocb_default_style(), "No OCB range selected.", "Historical invoices captured in range: —"
    if sel.get("end") is None:
        df_preference["Ordinary"] = 0
        idx = int(sel["start"])
        df_cur = build_ocb_data(start, end, step)
        label = df_cur["date_range"].iloc[idx]
        status = f"Range start selected: {label} — click a second row to complete the range."
        return ocb_pending_style(idx), status, "Historical invoices captured in range: —"
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
    return ocb_range_style(a, b), status, coverage

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



# Run the app
if __name__ == '__main__':
    app.run(debug=True)