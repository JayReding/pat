import pandas as pd
import sqlite3

# 1. Load the CSV into a DataFrame
df = pd.read_csv('testdata2.csv')

# 2. Connect to (or create) the SQLite database
conn = sqlite3.connect('pat_test.db')

# 3. Write the data to a SQL table
df.to_sql('test_data', conn, if_exists='replace', index=False)

# 4. Close the connection
conn.close()