"""Portfolio view: per-subcase money rollups for a chosen main case.

Mirrors the figures shown on the Case Summary (Total Transfers, Total
New Value, Ordinary Course, Net Preference with defenses applied) for a
single subcase, so the portfolio numbers always match the detail view.
"""

import pandas as pd

import analysis
import store


def subcase_rollup(subcase_id, firm_id=None):
    """Return {total_transfers, total_new_value, ordinary_course,
    net_preference} for one subcase, or None when the subcase is outside
    the firm scope.

    Replicates load_case() + update_summary() math: the 90-day preference
    window, the saved OCB range driving the Ordinary flag, and the saved
    new-value settings.
    """
    main = store.get_main_by_subcase(subcase_id)
    if firm_id is not None and main['firm_id'] != firm_id:
        return None
    petition = pd.Timestamp(main['petition_date'])
    pref_start = petition - pd.Timedelta(days=90)
    s = pref_start.strftime('%Y-%m-%d')
    p = petition.strftime('%Y-%m-%d')

    frames = store.load_case_frames(subcase_id, s, p, firm_id=firm_id)
    df_pref = frames['preference'].copy()
    df_hist = frames['historical']
    df_transfers = frames['transfers']
    df_newvalue = frames['newvalue']

    df_pref['Ordinary'] = 0

    settings = store.load_case_settings(subcase_id)
    ocb_range = settings.get('ocb_range')
    if ocb_range is not None and ocb_range.get('end') is None:
        ocb_range = None
    ocb_start = settings.get('ocb_start') if settings.get('ocb_start') is not None else 0
    ocb_end = settings.get('ocb_end') if settings.get('ocb_end') is not None else 100
    ocb_step = settings.get('ocb_step') if settings.get('ocb_step') is not None else 5
    metric = settings.get('ocb_metric') or 'Invoice to Payment'

    ordinary_inv = []
    if ocb_range is not None:
        a, b = sorted((ocb_range['start'], ocb_range['end']))
        df_cur = analysis.build_ocb_data(df_pref, df_hist, ocb_start, ocb_end, ocb_step, metric)
        if not df_cur.empty and 0 <= a < len(df_cur) and 0 <= b < len(df_cur):
            s_label = df_cur['date_range'].iloc[a]
            e_label = df_cur['date_range'].iloc[b]
            lower, _ = analysis.ocb_label_bounds(s_label, ocb_start, ocb_end)
            _, upper = analysis.ocb_label_bounds(e_label, ocb_start, ocb_end)
            days = df_pref[metric]
            mask = pd.Series(True, index=df_pref.index)
            if lower is not None:
                mask &= days >= lower
            if upper is not None:
                mask &= days <= upper
            df_pref.loc[mask, 'Ordinary'] = 1
            ordinary_inv = df_pref.loc[mask, 'Invoice Number'].dropna().tolist()

    df_snv = analysis.calculate_new_value(pd.concat([df_transfers, df_newvalue], ignore_index=True))
    nv_settings = settings.get('nv_settings')
    if nv_settings:
        nv_lookup = {rec['invoice_number']: rec for rec in nv_settings if rec.get('invoice_number') is not None}
        df_snv['Remove'] = df_snv['Invoice Number'].map(
            lambda inv: nv_lookup.get(inv, {}).get('remove', False) if pd.notna(inv) else False
        )
        df_snv['Ordinary Exclusion'] = df_snv['Invoice Number'].map(
            lambda inv: (nv_lookup.get(inv, {}).get('reason') or '') if pd.notna(inv) else ''
        )
    df_snv = analysis.sync_new_value(df_snv, ordinary_inv)
    df_snv = analysis.finalize_new_value(df_snv)

    tot_shares = df_pref.groupby('Transfer Number')['Invoice Amount'].sum()
    ord_shares = df_pref[df_pref['Ordinary'] == 1].groupby('Transfer Number')['Invoice Amount'].sum()
    net_pref_defenses, total_new_value = analysis.calc_net_pref_defenses(df_snv, tot_shares, ord_shares)

    tr_amt = df_transfers.groupby('Transfer Number')['Transfer Amount'].sum()
    ordinary_course = (tr_amt * (ord_shares / tot_shares).reindex(tr_amt.index).fillna(0.0)).sum()
    total_transfers = df_transfers['Transfer Amount'].sum()

    return {
        'total_transfers': float(total_transfers),
        'total_new_value': float(total_new_value),
        'ordinary_course': float(ordinary_course),
        'net_preference': float(net_pref_defenses),
    }


def main_case_rollup(main_id, firm_id=None):
    """Return one row per subcase of the main case: case name + figures."""
    rows = []
    for opt in store.list_subcase_options(main_id=main_id, firm_id=firm_id):
        fig = subcase_rollup(opt['value'], firm_id=firm_id)
        if fig is None:
            continue
        rows.append({'id': opt['value'], 'case_name': opt['label'], **fig})
    return rows