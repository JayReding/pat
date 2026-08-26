# Import packages
from dash import Dash, html, dcc, callback, Output, Input
import dash_ag_grid as dag
import pandas as pd
import plotly.express as px

# Incorporate data
df = pd.read_csv('invoices.csv')
# Initialize the app
app = Dash()
app.title  = "Preference Analysis Tool"

app.layout = [
    html.Div(children='Preference Analysis Tool'),
    html.Hr(),
     dag.AgGrid(
        id="invoices",
        rowData=df.to_dict('records'),
        columnDefs=[{"field": i} for i in df.columns]
    ),
     html.H3(id="total-output"),
     html.H3(id="average_dso"),
     html.H3(id="average_dpd")
]

@callback(
    Output("total-output", "children"), 
    Input("invoices", "rowData")
)
def update_total_invoice_amount(rowData):
    """
    Callback function to update the total invoice amount based on the current rowData from the AG Grid.
    
    Parameters:
    rowData (list of dict): The current data displayed in the AG Grid.

    Returns:
    str: A formatted string displaying the total invoice amount.
    """
    if rowData is None:
        return "Total Invoice Amount: $0.00"
    
    # Convert rowData back to a DataFrame for processing
    df_current = pd.DataFrame(rowData)
    
    # Calculate the total invoice amount
    total_amount = calculate_invoice_amount(df_current)
    
    return f"Total Invoice Amount: ${total_amount:.2f}"

def calculate_invoice_amount(dataframe: pd.DataFrame) -> float:   
    """
    Calculate the total invoice amount from the given DataFrame.

    Parameters:
    dataframe (pd.DataFrame): The DataFrame containing invoice data.

    Returns:
    float: The total invoice amount.
    """
    return dataframe["Check Amount"].sum()

@callback(
    Output("average_dso", "children"),
    Input("invoices", "rowData")
)
def update_average_dso(rowData):
    if rowData is None:
        return "Average DSO: 0.00"
    
    df_current = pd.DataFrame(rowData)
    filtered_df = df[df_current['Invoice Status'] == "Paid"]
    average_dso = filtered_df["Invoice to Payment"].mean()
    return f"Average DSO: {average_dso:.2f}"

@callback(
    Output("average_dpd", "children"),
    Input("invoices", "rowData")
)
def update_average_dpd(rowData):
    if rowData is None:
        return "Average DPD: 0.00"
    
    df_current = pd.DataFrame(rowData)
    filtered_df = df[df_current['Invoice Status'] == "Paid"]
    average_dpd = filtered_df["Days Past Due"].mean()
    return f"Average DPD: {average_dpd:.2f}"

# Run the app
if __name__ == '__main__':
    app.run(debug=True)