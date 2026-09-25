"""Sanity check + missing-data report."""
from __future__ import annotations

import pandas as pd


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
