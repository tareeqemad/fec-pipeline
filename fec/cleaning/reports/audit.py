"""Write cleaning changes and missing-data reports."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from fec.cleaning.reports.trail import AUDITED_FIELDS, UNTRACKED_STEP, AuditTrail, summarize
from fec.io import write_csv_atomic, write_json_atomic


CHANGE_COLUMNS = ['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason', 'source']


def write_audit(df_after, orig_map, out_dir, trail: AuditTrail):
    """Write every cleaning audit artifact."""
    after = df_after.drop_duplicates('sub_id', keep='first').set_index('sub_id')
    row_index = (
        orig_map.drop_duplicates('sub_id', keep='first')
        .set_index('sub_id')['row_index']
    )
    row_index.index = row_index.index.astype(str)

    net = trail.net_records()
    changes = pd.DataFrame.from_records(net, columns=CHANGE_COLUMNS)
    changes = changes[changes['field'].isin(AUDITED_FIELDS)]
    changes['row_index'] = row_index.reindex(changes['sub_id']).to_numpy()
    changes['row_index'] = changes['row_index'].astype('Int64')
    changes = changes[CHANGE_COLUMNS]
    write_csv_atomic(changes, os.path.join(out_dir, 'audit_changes.csv'), index=False)
    summary = {
        'changes': int(len(changes)),
        'changed_cells': int(changes[['sub_id', 'field']].drop_duplicates().shape[0]),
        'untracked_changes': sum(record['step'] == UNTRACKED_STEP for record in net),
        'steps': summarize([record for record in net if record['field'] in AUDITED_FIELDS]),
    }
    if 'employment_source' in df_after.columns:
        summary['inferred_employment_sub_ids'] = sorted(
            df_after.loc[df_after['employment_source'].eq('inferred'), 'sub_id']
            .astype(str).unique()
        )
    write_json_atomic(os.path.join(out_dir, 'audit_summary.json'), summary, indent=2, ensure_ascii=False)
    _write_amount_flags(after, row_index, out_dir)

    return summary


def _write_amount_flags(df, row_idx, out_dir):
    """Report negative and zero amounts without changing them."""
    path = os.path.join(out_dir, 'amount_flags.csv')
    flags = pd.DataFrame(columns=[
        'sub_id', 'row_index', 'contribution_receipt_amount', 'flag', 'evidence',
    ])
    if 'contribution_receipt_amount' in df.columns:
        amounts = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')
        mask = amounts.lt(0) | amounts.eq(0)
        if mask.any():
            selected = amounts[mask]
            negative = selected.lt(0)
            flags = pd.DataFrame({
                'sub_id': selected.index,
                'row_index': row_idx.reindex(selected.index).astype('Int64').values,
                'contribution_receipt_amount': selected.values,
                'flag': np.where(negative, 'refund_or_adjustment', 'zero_amount_void_or_refund'),
                'evidence': np.where(negative, 'negative_amount', 'zero_amount'),
            })
    write_csv_atomic(flags, path, index=False)


# check for unusual amounts and dates, returning warning strings
def _sanity_check(df: pd.DataFrame) -> list[str]:
    """Check for unusual amounts and dates; returns warning strings (empty = all OK)."""
    warnings = []
    amounts = df['contribution_receipt_amount']

    n_negative = int((amounts < 0).sum())
    n_zero = int((amounts == 0).sum())
    n_extreme = int((amounts.abs() > 100_000).sum())

    if n_negative:
        warnings.append(f"negative amounts: {n_negative:,}")
    if n_zero:
        warnings.append(f"zero amounts: {n_zero:,}")
    if n_extreme:
        warnings.append(f"amounts > $100K: {n_extreme:,}")

    today = pd.Timestamp.now()
    n_future = int((df['contribution_receipt_date'] > today + pd.Timedelta(days=30)).sum())
    if n_future:
        warnings.append(f"future dates (>30 days ahead): {n_future:,}")

    n_null_date = int(df['contribution_receipt_date'].isna().sum())
    if n_null_date:
        warnings.append(f"unparseable dates: {n_null_date:,}")

    return warnings


# record missing occupation/employer before later steps fill them
def _build_missing_report(df: pd.DataFrame) -> pd.DataFrame:
    """Report rows whose occupation/employer was missing in the ORIGINAL data; drops the temp _occ/_emp flags from df."""
    missing_mask = df['_occ_missing'] | df['_emp_missing']

    report = df.loc[
        missing_mask,
        ['sub_id', 'transaction_id', 'contributor_name', '_occ_missing', '_emp_missing'],
    ].copy()

    report.columns = [
        'sub_id', 'transaction_id', 'contributor_name',
        'occupation_was_missing', 'employer_was_missing',
    ]

    df.drop(columns=['_occ_missing', '_emp_missing'], inplace=True)

    return report
