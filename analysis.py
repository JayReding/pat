import numpy as np
import pandas as pd
from collections import Counter


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


def build_ocb_data(pref_df, hist_df, start=0, end=100, step=5, metric="Invoice to Payment"):
    nrows = max(1, (end - start) // step)
    edges = [start] + [start + k*step + 1 for k in range(1, nrows)] + [end + 1]
    labels = [f"< {start}"] + [f"{edges[i]}-{edges[i+1]-1}" for i in range(nrows)] + [f"> {end}"]
    bins = [float("-inf")] + edges + [float("inf")]
    p = pref_df.copy()
    h = hist_df.copy()
    p["bin"] = pd.cut(p[metric], bins=bins, labels=labels, right=False, include_lowest=True)
    h["bin"] = pd.cut(h[metric], bins=bins, labels=labels, right=False, include_lowest=True)
    rows = []
    for lab in labels:
        pi = p.index[p["bin"] == lab]
        hi = h.index[h["bin"] == lab]
        pc, hc = len(pi), len(hi)
        p_pct = pc / len(p) * 100 if len(p) else 0.0
        h_pct = hc / len(h) * 100 if len(h) else 0.0
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


def calc_weighted(df, metric):
    if df.empty:
        return 0.0
    return df[metric].mul(df["Transfer Amount"]).sum() / df["Transfer Amount"].sum()


def calc_weighted_dso(df):
    return calc_weighted(df, "Invoice to Payment")


def calc_weighted_dpd(df):
    return calc_weighted(df, "Days Past Due")


def compare_hist_pref(hist_df, pref_df, metric="Invoice to Payment"):
    hist_wavg = calc_weighted(hist_df, metric)
    pref_wavg = calc_weighted(pref_df, metric)
    if not hist_wavg:
        return 0.0
    diff = (pref_wavg - hist_wavg) / hist_wavg * 100
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


def pct_diff(hist_val, pref_val):
    """Signed percent difference of pref vs hist; None when hist is zero."""
    try:
        hist_val = float(hist_val)
        pref_val = float(pref_val)
    except (TypeError, ValueError):
        return None
    if hist_val == 0:
        return None
    return (pref_val - hist_val) / abs(hist_val) * 100


def diff_flag(diff):
    """'danger' if |diff| >= 20, 'warning' if |diff| > 10, else None."""
    if diff is None:
        return None
    ad = abs(diff)
    if ad >= 20:
        return "danger"
    if ad > 10:
        return "warning"
    return None


def daily_transaction_rate(df):
    """Unique transfers per day over the Payment Date span; None if empty."""
    if df.empty or "Transfer Number" not in df.columns or "Payment Date" not in df.columns:
        return None
    dates = pd.to_datetime(df["Payment Date"], errors="coerce").dropna()
    if dates.empty:
        return None
    days = max((dates.max() - dates.min()).days + 1, 1)
    n = df["Transfer Number"].nunique(dropna=True) or len(df)
    return n / days


_TERMS_MIN_COUNT = 2
_TERMS_MIN_SHARE = 0.05
_TERMS_DAY_GAP = 10


def _norm_term(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def _cluster_terms(values, gap=_TERMS_DAY_GAP):
    """Group implied-terms values into clusters a `gap`-day shift apart.

    Consecutive values more than `gap` days apart start a new cluster, which
    suggests an actual change in terms; smaller wiggles (weekends, holidays)
    stay together. Returns a list of clusters (each a sorted value list).
    """
    clusters = []
    for v in sorted(set(values)):
        if clusters and v - clusters[-1][-1] <= gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return clusters


def _cluster_rep(cluster):
    mid = (cluster[(len(cluster) - 1) // 2] + cluster[len(cluster) // 2]) / 2
    return int(round(mid))


def terms_summary(df):
    """Detect payment-terms changes within one period's invoices.

    Two methods: distinct ``Terms Days`` values, and distinct implied terms
    (``Invoice Due`` minus ``Invoice Date``). Implied terms within
    ``_TERMS_DAY_GAP`` days of each other collapse into one cluster, so only
    a shift greater than that suggests an actual change in terms. A terms
    value only counts when seen on at least ``_TERMS_MIN_COUNT`` invoices
    and at least ``_TERMS_MIN_SHARE`` of the invoices usable by that method,
    so a handful of aberrant rows cannot flag the period.

    Returns {changed, display}.
    """
    frames = []
    if "Terms Days" in df.columns:
        vals = pd.to_numeric(df["Terms Days"], errors="coerce").dropna()
        frames.append([(i, _norm_term(v)) for i, v in vals.items()])
    if {"Invoice Date", "Invoice Due"} <= set(df.columns):
        dates = pd.to_datetime(df["Invoice Date"], errors="coerce")
        dues = pd.to_datetime(df["Invoice Due"], errors="coerce")
        ok = dates.notna() & dues.notna()
        implied = (dues[ok] - dates[ok]).dt.days
        rep_of = {}
        for cluster in _cluster_terms(implied.tolist()):
            rep = _cluster_rep(cluster)
            for v in cluster:
                rep_of[v] = rep
        frames.append([(i, rep_of[v]) for i, v in implied.items()])
    frames = [f for f in frames if f]
    if not frames:
        return {"changed": False, "display": "Insufficient data"}
    keep_vals = set()
    for entries in frames:
        vals = [v for _, v in entries]
        counts = Counter(vals)
        total = len(vals)
        keep_vals.update(v for v, n in counts.items()
                         if n >= _TERMS_MIN_COUNT and n / total >= _TERMS_MIN_SHARE)
    qualifying = sorted(keep_vals, key=float)
    rows_with_data = {i for entries in frames for i, _ in entries}
    rows_kept = {i for entries in frames for i, v in entries if v in keep_vals}
    ignored = len(rows_with_data - rows_kept)
    note = (f"; +{ignored} aberrant invoice{'s' if ignored != 1 else ''} below threshold"
            if ignored else "")
    if len(qualifying) > 1:
        terms = ", ".join(f"{v:g}" for v in qualifying)
        return {"changed": True, "display": f"Changed ({terms} days{note})"}
    label = f"{qualifying[0]:g} days" if qualifying else "no qualifying terms observed"
    return {"changed": False, "display": f"No change ({label}{note})"}


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _skew_note(skew):
    if skew is None:
        return ""
    if skew > 1 or skew < -1:
        return " (Warning: Skew is outside the range of -1 to 1, indicating a non-normal distribution)"
    return ""


def build_insights(hist_df, pref_df):
    """Row dicts comparing historical vs preference periods.

    Each row: {metric, hist, pref, diff, flag} with display strings;
    ``diff`` is a signed percent (or "n/a"), ``flag`` is "warning",
    "danger", or None per the 10%/20% thresholds.
    """
    if hist_df.empty or pref_df.empty:
        return []
    rows = []

    def add(metric, hist_val, pref_val, fmt):
        h, p = _finite(hist_val), _finite(pref_val)
        d = pct_diff(h, p) if h is not None and p is not None else None
        rows.append({
            "metric": metric,
            "hist": fmt(h),
            "pref": fmt(p),
            "diff": f"{d:+.2f}%" if d is not None else "n/a",
            "flag": diff_flag(d),
        })

    days = lambda v: f"{v:.2f}" if v is not None else "n/a"
    money = lambda v: f"${v:,.2f}" if v is not None else "n/a"
    add("Weighted Average Days Outstanding",
        calc_weighted_dso(hist_df), calc_weighted_dso(pref_df), days)
    add("Weighted Average Days Past Due",
        calc_weighted_dpd(hist_df), calc_weighted_dpd(pref_df), days)
    h_terms = terms_summary(hist_df)
    p_terms = terms_summary(pref_df)
    if h_terms["changed"] or p_terms["changed"]:
        terms_diff, terms_flag = "Changed", "warning"
    else:
        terms_diff, terms_flag = "No change", None
    rows.append({"metric": "Changes in Terms", "hist": h_terms["display"],
                 "pref": p_terms["display"], "diff": terms_diff, "flag": terms_flag})
    add("Average Number of Daily Transactions",
        daily_transaction_rate(hist_df), daily_transaction_rate(pref_df), days)
    add("Average Amount of Invoices",
        hist_df["Invoice Amount"].mean(), pref_df["Invoice Amount"].mean(), money)
    add("Average Amount of Transfers",
        hist_df["Transfer Amount"].mean(), pref_df["Transfer Amount"].mean(), money)
    add("Average Number of Invoices Paid per Transfer",
        hist_df.groupby("Transfer Number").size().mean(),
        pref_df.groupby("Transfer Number").size().mean(), days)
    timing = lambda df: df["Invoice to Payment"] if "Invoice to Payment" in df.columns else pd.Series(dtype=float)
    h_skew = _finite(timing(hist_df).skew())
    p_skew = _finite(timing(pref_df).skew())
    if (h_skew is not None and p_skew is not None
            and abs(h_skew) < 1 and abs(p_skew) < 1):
        add("Standard Deviation of Payment Timing",
            timing(hist_df).std(ddof=1), timing(pref_df).std(ddof=1), days)

    rows.append({
        "metric": "Skew of Payment Timing",
        "hist": f"{h_skew:.2f}{_skew_note(h_skew)}" if h_skew is not None else "n/a",
        "pref": f"{p_skew:.2f}{_skew_note(p_skew)}" if p_skew is not None else "n/a",
        "diff": "\u2014",
        "flag": None,
    })
    return rows