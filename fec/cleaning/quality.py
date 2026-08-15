"""Quality gates, outlier reports, and report saving."""
from pathlib import Path

import pandas as pd

from fec.cleaning.occupations import _categorize_final
from fec.cleaning.previous_employer import classify_employer_statuses
from fec.config.constants import (
    EMPLOYER_STATUS_VALUES,
    NOT_EMPLOYED_VARIANTS,
    SELF_EMPLOYED_VARIANTS,
    SLASH_BRAND_EMPLOYERS,
)
from fec.config.occupation_rules.categories import VALID_CATEGORIES

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

# Each gate reads df without mutating.
def _resolve_only_gate(key, df, required):
    missing = sorted(required - set(df.columns))
    if not missing:
        return None
    return [(key, {
        'passed': None,
        'count': None,
        'not_run': True,
        'missing_columns': missing,
        'reason': 'run after resolve --apply',
    }, None)]


def _gate_nan_strings(df):
    nan_count = 0
    for col in ['contributor_occupation', 'contributor_employer', 'contributor_name',
                'contributor_city', 'contributor_state', 'contributor_street_1']:
        if col in df.columns:
            nan_count += int((df[col].astype('string').str.upper() == 'NAN').sum())
    issue = f"Found {nan_count} literal 'NAN' in text columns" if nan_count else None
    return [('no_nan_strings', {'passed': nan_count == 0, 'count': nan_count}, issue)]


def _gate_valid_categories(df):
    if 'occupation_category' not in df.columns:
        return []
    values = df['occupation_category'].astype('string').fillna('').str.strip()
    invalid = [str(value) for value in values.unique() if value and value not in VALID_CATEGORIES]
    blank = int(values.eq('').sum())
    issue = None
    if invalid or blank:
        issue = f"Invalid categories: {invalid[:10]}; blank categories: {blank}"
    return [('valid_categories', {
        'passed': not invalid and blank == 0,
        'invalid': invalid[:10],
        'blank': blank,
    }, issue)]


def _gate_occupation_category_consistency(df):
    required = {'entity_type', 'contributor_occupation', 'occupation_category'}
    if not required.issubset(df.columns):
        return []

    is_indiv = df['entity_type'].eq('INDIVIDUAL')
    expected = _categorize_final(df.loc[is_indiv, 'contributor_occupation'])
    actual = df.loc[is_indiv, 'occupation_category'].fillna('')
    mismatched = actual.ne(expected)
    count = int(mismatched.sum())
    issue = f"Occupation/category mismatches: {count}" if count else None
    return [('occupation_category_consistency', {
        'passed': count == 0,
        'count': count,
    }, issue)]


def _gate_self_employed_status(df):
    """Self-employment fields and status must agree."""
    required = {
        'entity_type', 'contributor_employer', 'contributor_occupation',
        'occupation_category', 'employer_status',
    }
    not_run = _resolve_only_gate('self_employed_status_consistency', df, required)
    if not_run:
        return not_run

    individuals = df['entity_type'].eq('INDIVIDUAL')
    status = df['employer_status'].fillna('').str.strip().str.lower()
    expected = classify_employer_statuses(df)
    marker = individuals & expected.eq('self_employed')
    self_status = individuals & status.eq('self_employed')

    marker_without_status = int((marker & ~self_status).sum())
    status_without_marker = int((self_status & ~marker).sum())
    count = marker_without_status + status_without_marker
    issue = f"SELF-EMPLOYED/status mismatches: {count}" if count else None
    return [('self_employed_status_consistency', {
        'passed': count == 0,
        'count': count,
        'marker_without_status': marker_without_status,
        'status_without_marker': status_without_marker,
    }, issue)]


def _gate_not_employed_status(df):
    """Non-working occupations and status must agree."""
    required = {
        'entity_type', 'contributor_employer', 'contributor_occupation',
        'occupation_category', 'employer_status',
    }
    not_run = _resolve_only_gate('not_employed_status_consistency', df, required)
    if not_run:
        return not_run

    individuals = df['entity_type'].eq('INDIVIDUAL')
    status = df['employer_status'].fillna('').str.strip().str.lower()
    expected = classify_employer_statuses(df)
    marker = individuals & expected.eq('not_employed')
    not_status = individuals & status.eq('not_employed')

    marker_without_status = int((marker & ~not_status).sum())
    status_without_marker = int((not_status & ~marker).sum())
    count = marker_without_status + status_without_marker
    issue = f"NOT EMPLOYED/status mismatches: {count}" if count else None
    return [('not_employed_status_consistency', {
        'passed': count == 0,
        'count': count,
        'marker_without_status': marker_without_status,
        'status_without_marker': status_without_marker,
    }, issue)]


def _gate_previous_employer_consistency(df):
    """One retired donor must resolve to one previous employer."""
    required = {'entity_type', 'donor_key', 'employer_status', 'previous_employer'}
    not_run = _resolve_only_gate('previous_employer_consistency', df, required)
    if not_run:
        return not_run

    retired = df[
        df['entity_type'].eq('INDIVIDUAL')
        & df['employer_status'].eq('retired')
    ]
    previous = retired['previous_employer'].fillna('').astype(str).str.strip()
    counts = (
        retired.assign(_previous=previous.mask(previous.eq('')))
        .groupby('donor_key')['_previous']
        .nunique(dropna=True)
    )
    bad = int(counts.gt(1).sum())
    issue = f"Retired donors with multiple previous employers: {bad}" if bad else None
    return [('previous_employer_consistency', {
        'passed': bad == 0,
        'count': bad,
    }, issue)]


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


def _gate_retired_donor_consistency(df):
    # a once-retired donor with no real employer should not carry NOT EMPLOYED / SELF-EMPLOYED
    # Donor consistency settles these filings.
    if not {'entity_type', 'donor_key', 'contributor_employer'}.issubset(df.columns):
        return []
    emp_upper = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    status_nonret = NOT_EMPLOYED_VARIANTS | SELF_EMPLOYED_VARIANTS
    flags = pd.DataFrame({
        'donor_key': df.loc[is_indiv, 'donor_key'],
        'retired':  emp_upper[is_indiv].eq('RETIRED'),
        'nonret':   emp_upper[is_indiv].isin(status_nonret),
        'real':     ~emp_upper[is_indiv].isin(EMPLOYER_STATUS_VALUES),
    }).groupby('donor_key').agg('any')
    bad_donors = flags.index[flags['retired'] & ~flags['real'] & flags['nonret']]
    n_bad_rows = int((is_indiv & df['donor_key'].isin(bad_donors) &
                      emp_upper.isin(status_nonret)).sum())
    issue = (
        f"Retired donors with NOT EMPLOYED/SELF-EMPLOYED: {n_bad_rows} rows "
        "— run _once_retired_always_retired sweep"
    ) if n_bad_rows else None
    return [('retired_donor_consistency', {'passed': n_bad_rows == 0, 'count': n_bad_rows}, issue)]


def _gate_retired_active_sync(df):
    # RETIRED category with employer_status=active contradicts the _retired_active_sync sweep
    required = {'entity_type', 'occupation_category', 'employer_status'}
    not_run = _resolve_only_gate('retired_active_sync', df, required)
    if not_run:
        return not_run
    bad_sync = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['occupation_category'] == 'RETIRED')
        & (df['employer_status'] == 'active')
    )
    n_bad_sync = int(bad_sync.sum())
    issue = (
        f"retired+active contradiction: {n_bad_sync} rows "
        "-- run _retired_active_sync in donor_consistency"
    ) if n_bad_sync else None
    return [('retired_active_sync', {'passed': n_bad_sync == 0, 'count': n_bad_sync}, issue)]


def _gate_retired_employer_marker(df):
    """A retired occupation must finish with the canonical RETIRED employer marker."""
    required = {'entity_type', 'occupation_category', 'contributor_employer'}
    if not required.issubset(df.columns):
        return []
    employer = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    bad = (
        df['entity_type'].eq('INDIVIDUAL')
        & df['occupation_category'].eq('RETIRED')
        & employer.ne('RETIRED')
    )
    count = int(bad.sum())
    issue = f"Retired rows without RETIRED employer marker: {count}" if count else None
    return [('retired_employer_marker', {'passed': count == 0, 'count': count}, issue)]


def _gate_slash_previous_employer(df):
    # 'COMPANY/TITLE' composites are resolved by _resolve_slash_previous_employer;
    # real slash brands (BRIDGESTONE/FIRESTONE) are whitelisted
    if 'previous_employer' not in df.columns:
        return []
    prev_emp = df['previous_employer'].fillna('').astype(str)
    slashy = (
        prev_emp.str.contains('/', regex=False)
        & ~prev_emp.str.upper().isin(SLASH_BRAND_EMPLOYERS)
    )
    n_slash = int(slashy.sum())
    issue = (
        f"previous_employer slash leaks: {n_slash} rows "
        "-- run _normalize_previous_employer_column in apply.py"
    ) if n_slash else None
    return [('no_slash_in_previous_employer', {'passed': n_slash == 0, 'count': n_slash}, issue)]


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
    _gate_valid_categories,
    _gate_occupation_category_consistency,
    _gate_self_employed_status,
    _gate_not_employed_status,
    _gate_previous_employer_consistency,
    _gate_zip_coverage,
    _gate_dup_sub_id,
    _gate_dup_transaction_id,
    _gate_future_dates,
    _gate_row_count,
    _gate_zip_state,
    _gate_email_as_address,
    _gate_special_chars_names,
    _gate_retired_donor_consistency,
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
