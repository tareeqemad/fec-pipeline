"""Quality gates, outlier reports, and report saving."""
from pathlib import Path

import pandas as pd

from fec.cleaning.quality_employment import (
    _gate_not_employed_status,
    _gate_occupation_category_consistency,
    _gate_previous_employer_scope,
    _gate_retired_active_sync,
    _gate_retired_employer_marker,
    _gate_self_employed_status,
    _gate_slash_previous_employer,
    _gate_valid_categories,
)

_ZIP_PREFIX_STATES = {
    '0': {'CT', 'MA', 'ME', 'NH', 'NJ', 'PR', 'RI', 'VT', 'VI', 'AE', 'AA'},
    '1': {'DE', 'NY', 'PA'},
    '2': {'DC', 'MD', 'NC', 'SC', 'VA', 'WV'},
    '3': {'AL', 'FL', 'GA', 'MS', 'TN', 'AA', 'AP'},
    '4': {'IN', 'KY', 'MI', 'OH'},
    '5': {'IA', 'MN', 'MT', 'ND', 'NE', 'SD', 'WI'},
    '6': {'CO', 'IL', 'KS', 'MO', 'NE', 'NM', 'OK', 'TX'},
    '7': {'AR', 'LA', 'OK', 'TX'},
    '8': {'AZ', 'CO', 'ID', 'MT', 'NM', 'NV', 'UT', 'WY'},
    '9': {'AK', 'CA', 'HI', 'OR', 'WA', 'GU', 'AS', 'MP', 'AP'},
}


def _gate_nan_strings(df):
    nan_count = 0
    for col in ['contributor_occupation', 'contributor_employer', 'contributor_name',
                'contributor_city', 'contributor_state', 'contributor_street_1']:
        if col in df.columns:
            nan_count += int((df[col].astype('string').str.upper() == 'NAN').sum())
    issue = f"Found {nan_count} literal 'NAN' in text columns" if nan_count else None
    return [('no_nan_strings', {'passed': nan_count == 0, 'count': nan_count}, issue)]


def _gate_amounts_readable(df):
    # every filing keeps a real dollar amount; blank or text is never $0
    if 'contribution_receipt_amount' not in df.columns:
        return []
    amounts = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')
    bad = int(amounts.isna().sum())
    issue = f"{bad} rows with a missing or unreadable contribution_receipt_amount" if bad else None
    return [('amounts_readable', {'passed': bad == 0, 'count': bad}, issue)]


def _gate_zip_coverage(df):
    if 'contributor_zip' not in df.columns:
        return []
    pct = float((df['contributor_zip'].isna() | (df['contributor_zip'].astype(str).str.strip() == '')).mean() * 100)
    issue = f"ZIP empty: {pct:.1f}%" if pct >= 50 else None
    return [('zip_coverage', {'passed': pct < 50, 'pct_empty': round(pct, 2)}, issue)]


def _dup_id_check(df, col, key):
    if col not in df.columns:
        return []
    n_dup = int(df[col].duplicated().sum())
    issue = f"Duplicate {col}: {n_dup}" if n_dup else None
    return [(key, {'passed': n_dup == 0, 'count': n_dup}, issue)]


def _gate_dup_sub_id(df):
    return _dup_id_check(df, 'sub_id', 'no_duplicate_sub_id')


def _gate_dup_transaction_id(df):
    return _dup_id_check(df, 'transaction_id', 'no_duplicate_transaction_id')


def _gate_future_dates(df):
    if 'contribution_receipt_date' not in df.columns:
        return []
    dates = pd.to_datetime(df['contribution_receipt_date'], errors='coerce')
    today = pd.Timestamp.now()
    n_future = int((dates > today + pd.Timedelta(days=60)).sum())
    issue = f"Future dates (>60 days ahead): {n_future}" if n_future else None
    return [('no_future_dates', {'passed': n_future == 0, 'count': n_future}, issue)]


def _gate_row_count(df):
    return [('row_count', int(len(df)), None)]


def _gate_zip_state(df):
    if not ('contributor_zip' in df.columns and 'contributor_state' in df.columns):
        return []
    # vectorized: 10 prefix masks instead of a per-row loop
    zip_str = df['contributor_zip'].astype(str)
    valid_rows = df['contributor_zip'].notna() & (zip_str.str.len() == 5)
    zip_first = zip_str.str[0]
    state = df['contributor_state'].astype(str)

    n_zip_mismatch = 0
    for prefix, allowed in _ZIP_PREFIX_STATES.items():
        in_prefix = valid_rows & (zip_first == prefix)
        n_zip_mismatch += int((in_prefix & ~state.isin(allowed)).sum())
    issue = f"ZIP-State mismatches: {n_zip_mismatch}" if n_zip_mismatch >= 100 else None
    return [('zip_state_match', {'passed': n_zip_mismatch < 100, 'count': n_zip_mismatch}, issue)]


def _gate_email_as_address(df):
    if 'contributor_street_1' not in df.columns:
        return []
    n_email_addr = int(df['contributor_street_1'].fillna('').str.contains('@', regex=False).sum())
    issue = f"Email addresses in street field: {n_email_addr}" if n_email_addr else None
    return [('no_email_as_address', {'passed': n_email_addr == 0, 'count': n_email_addr}, issue)]


def _gate_special_chars_names(df):
    n_special_chars = 0
    for col in ['contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            n_special_chars += int(df[col].fillna('').str.contains(r'[`;\[\]@]', regex=True).sum())
    issue = f"Special characters in names: {n_special_chars}" if n_special_chars else None
    return [('no_special_chars_in_names', {'passed': n_special_chars == 0, 'count': n_special_chars}, issue)]


def _gate_null_surname(df):
    # "NULL" is a real surname; pandas' default read coerces it to NaN, so if
    # contributor_name starts with "NULL," the surname must be the literal string
    if not {'contributor_name', 'contributor_last_name'}.issubset(df.columns):
        return []
    name_null_prefix = df['contributor_name'].astype('string').str.startswith('NULL,', na=False)
    last_names = df['contributor_last_name'].astype('string')
    lost = name_null_prefix & (last_names.isna() | last_names.eq(''))
    n_lost = int(lost.sum())
    issue = (
        f"NULL surname erased on {n_lost} row(s) — pandas coerced literal "
        "'NULL' to NaN. Ensure all readers use "
        "fec.io.read_pipeline_csv()"
    ) if n_lost else None
    return [('null_surname_preserved', {'passed': n_lost == 0, 'count': n_lost}, issue)]


# order matters: this is the checks-dict order and the order issues are reported
_QUALITY_GATES = [
    _gate_nan_strings,
    _gate_amounts_readable,
    _gate_valid_categories,
    _gate_occupation_category_consistency,
    _gate_self_employed_status,
    _gate_not_employed_status,
    _gate_previous_employer_scope,
    _gate_zip_coverage,
    _gate_dup_sub_id,
    _gate_dup_transaction_id,
    _gate_future_dates,
    _gate_row_count,
    _gate_zip_state,
    _gate_email_as_address,
    _gate_special_chars_names,
    _gate_retired_active_sync,
    _gate_retired_employer_marker,
    _gate_slash_previous_employer,
    _gate_null_surname,
]


def run_quality_gates(df: pd.DataFrame) -> dict:
    """Run quality checks. Returns {passed, checks, issues}."""
    checks, issues = {}, []
    for gate in _QUALITY_GATES:
        for key, check, issue in gate(df):
            checks[key] = check
            if issue:
                issues.append(issue)
    results = [
        check['passed']
        for check in checks.values()
        if isinstance(check, dict) and check.get('passed') is not None
    ]
    passed = all(results)
    return {'passed': passed, 'checks': checks, 'issues': issues}


def save_report(df: pd.DataFrame, dir_path: str, name: str) -> None:
    """Save a DataFrame as CSV."""
    if df is None or df.empty:
        return
    base = Path(dir_path) / name
    base.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(base.with_suffix('.csv'), index=False)
