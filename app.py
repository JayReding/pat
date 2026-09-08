# Import packages
from dash import Dash, html, dcc, callback, Output, Input
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

    # We need to create a cumulative sum by adding the transfers and subtracting any subsequent new value
    df["Net Change"] = df["Transfer Amount"].fillna(0) - df["Invoice Amount"].fillna(0)

    df["Net Preference"] = df["Net Change"].cumsum().clip(lower=0) # Ensure that the net preference does not go below zero
    

    #nitial_snv = df["Net Change"].clip(lower=0)  # Ensure that the net preference does not go below zero

    # The net preference cannot go below zero, so we use np.maximum to ensure that any negative values are set to zero
    # df["Net Preference"] = df["Net Change 1"] - df["Net Change 2"]


    df = df.drop(columns=["Net Change"])  # Drop the intermediate column

    return df

df_snv = calculate_new_value(pd.concat([df_transfers, df_newvalue], ignore_index=True))



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
                id="transfers",
                rowData=df_snv.to_dict('records'),
                columnDefs=[
                    {"field": "Transaction Date"},
                    {"field": "Transfer Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Payment Date"},
                    {"field": "Invoice Number"},
                    {"field": "Invoice Amount", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}},
                    {"field": "Invoice Date"},
                    {"field": "Net Preference", "valueFormatter": {"function": "d3.format('($,.2f')(params.value)"}}\
                ]
            )
        ])
     ])

])
 
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
    weighted_dso = df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
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
    weighted_dso = df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    weighted_dpd = df["Days Past Due"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()
    diff = compare_hist_pref()
    return f"Preference Period Total Transfer Amount: ${total:,.2f}", f"Preference Period Average DSO: {average_dso:.2f}", f"Preference Period Average DPD: {average_dpd:.2f}", f"Preference Period Weighted DSO: {weighted_dso:.2f}", f"Preference Period Weighted DPD: {weighted_dpd:.2f}", f"Weighted DSO Difference (Historical vs Preference): {diff:.2f}%"

def compare_hist_pref():
    hist_weighted_dso = df_historical["Invoice to Payment"].mul(df_historical["Transfer Amount"]).sum() / df_historical["Transfer Amount"].sum()
    pref_weighted_dso = df_preference["Invoice to Payment"].mul(df_preference["Transfer Amount"]).sum() / df_preference["Transfer Amount"].sum()
    diff = (pref_weighted_dso - hist_weighted_dso) / hist_weighted_dso * 100
    return(diff) 



# Run the app
if __name__ == '__main__':
    app.run(debug=True)