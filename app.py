# Import packages
from dash import Dash, html, dcc, callback, Output, Input
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
import sqlite3

# Incorporate data
#df = pd.read_csv('testdata.csv')
df_historical = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" < date('2023-07-17')", sqlite3.connect('pat_test.db'))
df_preference = pd.read_sql("SELECT * from test_data WHERE \"Payment Date\" >= date('2023-07-17') AND \"Payment Date\" <= date('2023-10-15')", sqlite3.connect('pat_test.db'))

# Initialize the app
app = Dash(external_stylesheets=[dbc.themes.BOOTSTRAP] )
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

app.layout = [
    html.H1(children='Preference Analysis Tool'),
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
     html.Div(id="pref-weighted_dpd")
]

# Here is where we update our statisticakl analysis for the historical period

@callback(
    Output("hist-total-output", "children"),
    Output("hist-average_dso", "children"),
    Output("hist-average_dpd", "children"),
    Output("hist-weighted_dso", "children"),
    Output("hist-weighted_dpd", "children"),
    Input("historical", "rowData")
)

def update_hist_totals(rowData):
    df = pd.DataFrame(rowData)
    total = df["Transfer Amount"].sum()
    average_dso = df["Invoice to Payment"].mean()
    average_dpd = df["Days Past Due"].mean()
    weighted_dso = df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    return f"Historical Period Total Transfer Amount: ${total:,.2f}", f"Historical Period Average DSO: {average_dso:.2f}", f"Historical Period Average DPD: {average_dpd:.2f}", f"Historical Period Weighted DSO: {weighted_dso:.2f}", f"Historical Period Weighted DPD: {weighted_dpd:.2f}"


# Here is where we update our statistical analysis for the preference period
@callback(
    Output("pref-total-output", "children"),
    Output("pref-average_dso", "children"),
    Output("pref-average_dpd", "children"),
    Output("pref-weighted_dso", "children"),
    Output("pref-weighted_dpd", "children"),
    Input("preference", "rowData")
)

def update_pref_totals(rowData):
    df = pd.DataFrame(rowData)
    total = df["Transfer Amount"].sum()
    average_dso = df["Invoice to Payment"].mean()
    average_dpd = df["Days Past Due"].mean()
    weighted_dso = df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    return f"Preference Period Total Transfer Amount: ${total:,.2f}", f"Preference Period Average DSO: {average_dso:.2f}", f"Preference Period Average DPD: {average_dpd:.2f}", f"Preference Period Weighted DSO: {weighted_dso:.2f}", f"Preference Period Weighted DPD: {weighted_dpd:.2f}"

# Run the app
if __name__ == '__main__':
    app.run(debug=True)