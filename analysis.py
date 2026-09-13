import numpy as np
import pandas as pd


def trim_leading_nulls(df, column_name):
    """
    Removes all rows from the beginning of the DataFrame until the first
    non-null value is found in the specified column.

    Args:
        df: The pandas DataFrame to clean.
        column_name: The name of the column to check for leading nulls.

    Returns:
        A new DataFrame starting from the first row where 'column_name' is not null.
    """

    is_not_null = df[column_name].notna()

    first_valid_index = df.index[is_not_null].min()

    if pd.isna(first_valid_index):
        print(f"Warning: The entire '{column_name}' column is null. Returning an empty DataFrame.")
        return pd.DataFrame(columns=df.columns)

    cleaned_df = df.loc[first_valid_index:]

    return cleaned_df


def calculate_new_value(df):
    df["Transaction Date"] = df[["Payment Date", "Invoice Date"]].apply(lambda row: ', '.join(row.dropna()), axis=1)

    df.sort_values(by="Transaction Date", inplace=True)

    df = trim_leading_nulls(df, "Transfer Amount")

    df["Allowed New Value"] = df["Invoice Amount"]

    df["Net Change"] = df["Transfer Amount"].fillna(0) - df["Invoice Amount"].fillna(0)

    df["Net Preference"] = df["Net Change"].cumsum().clip(lower=0)

    df = df.drop(columns=["Net Change"])

    return df


def sync_new_value(df, ordinary_invoices):
    ordinary_set = set(ordinary_invoices or [])
    for idx, row in df.iterrows():
        inv = row.get("Invoice Number")
        if pd.notna(inv) and inv in ordinary_set:
            df.at[idx, "Remove"] = True
            df.at[idx, "Ordinary Exclusion"] = "Paid by unavoidable transfer - 547(c)(4)(A)"
        elif pd.notna(inv) and row.get("Ordinary Exclusion") == "Paid by unavoidable transfer - 547(c)(4)(A)":
            df.at[idx, "Remove"] = False
            df.at[idx, "Ordinary Exclusion"] = ""
    return df


def finalize_new_value(df):
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
    return df


def build_ocb_data(pref_df, hist_df, start=0, end=100, step=5):
    nrows = max(1, (end - start) // step)
    edges = [start] + [start + k*step + 1 for k in range(1, nrows)] + [end + 1]
    labels = [f"< {start}"] + [f"{edges[i]}-{edges[i+1]-1}" for i in range(nrows)] + [f"> {end}"]
    bins = [float("-inf")] + edges + [float("inf")]
    p = pref_df.copy()
    h = hist_df.copy()
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


def calc_weighted_dso(df):
    return df["Invoice to Payment"].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()


def compare_hist_pref(hist_df, pref_df):
    hist_weighted_dso = calc_weighted_dso(hist_df)
    pref_weighted_dso = calc_weighted_dso(pref_df)
    diff = (pref_weighted_dso - hist_weighted_dso) / hist_weighted_dso * 100
    return diff


def gaussian_kde(x, grid_points=200):
    """
    Gaussian kernel density estimate over a grid covering the data range.

    Uses the Silverman rule-of-thumb bandwidth. Returns a tuple of
    (grid, density) arrays suitable for a smooth fitted curve.

    Args:
        x: 1D array-like of sample values.
        grid_points: number of grid points spanning min(x)..max(x).
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return np.array([]), np.array([])
    n = x.size
    std = x.std(ddof=1) if n > 1 else 0.0
    q75, q25 = np.percentile(x, [75, 25])
    iqr = q75 - q25
    bw = 0.9 * min(std, iqr / 1.34) * n ** (-0.2)
    if bw == 0 or not np.isfinite(bw):
        bw = std if std > 0 else (x.max() - x.min()) / (grid_points - 1)
    if bw == 0:
        bw = 1.0
    grid = np.linspace(x.min(), x.max(), grid_points)
    z = (grid[:, None] - x[None, :]) / bw
    density = (np.exp(-0.5 * z ** 2).sum(axis=1) / (n * bw * np.sqrt(2 * np.pi)))
    return grid, density


def calc_net_pref_defenses(df_nv, tot_shares, ord_shares):
    """
    Computes the net preference after defenses and the amount of new value
    actually applied, in one date-ordered running pass.

    Returns:
        (net_pref_defenses, applied_new_value): the final running balance and
        the total of new-value subtractions that were not floored away.
    """
    running = 0.0
    applied_new_value = 0.0
    for row in df_nv.to_dict("records"):
        t_amt = row.get("Transfer Amount")
        if pd.notna(t_amt):
            tr = row.get("Transfer Number")
            t = tot_shares.get(tr, 0.0)
            o = ord_shares.get(tr, 0.0)
            running = max(0.0, running + t_amt * (1 - (o / t if t else 0.0)))
        else:
            av = row.get("Allowed New Value") or 0
            if av > 0:
                before = running
                running = max(0.0, running - av)
                applied_new_value += before - running
    return running, applied_new_value