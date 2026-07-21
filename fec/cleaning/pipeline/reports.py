"""cleaning/pipeline/reports.py — sanity check + missing-data report.

Pipeline reporting steps (no mutation of the data beyond dropping the temporary
`_occ_missing` / `_emp_missing` flags after the report is built).
"""
from __future__ import annotations

from typing import List

import pandas as pd


def _sanity_check(df: pd.DataFrame) -> List[str]:
    """
    Check for unusual amounts and dates.
    Returns a list of warning strings (empty = all OK).
    """
    warnings = []
    amt = df['contribution_receipt_amount']

    n_negative = int((amt < 0).sum())
    n_zero = int((amt == 0).sum())
    n_extreme = int((amt.abs() > 100_000).sum())

    if n_negative:
        warnings.append(f"negative amounts: {n_negative:,}")
    if n_zero:
        warnings.append(f"zero amounts: {n_zero:,}")
    if n_extreme:
        warnings.append(f"amounts > $100K: {n_extreme:,}")

    # Future dates (more than 30 days from now)
    today = pd.Timestamp.now()
    n_future = int((df['contribution_receipt_date'] > today + pd.Timedelta(days=30)).sum())
    if n_future:
        warnings.append(f"future dates (>30 days ahead): {n_future:,}")

    n_null_date = int(df['contribution_receipt_date'].isna().sum())
    if n_null_date:
        warnings.append(f"unparseable dates: {n_null_date:,}")

    return warnings


def _build_missing_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a report of records that had missing occupation or employer
    in the ORIGINAL data (before we filled them in).
    """
    missing_mask = df['_occ_missing'] | df['_emp_missing']

    report = df.loc[
        missing_mask,
        ['sub_id', 'transaction_id', 'contributor_name', '_occ_missing', '_emp_missing'],
    ].copy()

    report.columns = [
        'sub_id', 'transaction_id', 'contributor_name',
        'occupation_was_missing', 'employer_was_missing',
    ]

    # Clean up temp columns
    df.drop(columns=['_occ_missing', '_emp_missing'], errors='ignore', inplace=True)

    return report
