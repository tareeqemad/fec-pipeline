"""Quality gates, outlier reports, and report saving."""

import json
import pandas as pd
from pathlib import Path

from fec.config import VALID_CATEGORIES


# ── Quality gates ──────────────────────────────────────────────────────────
# Each gate is a pure check: it reads `df` (never mutates it) and returns a list
# of (key, check, issue) tuples — `check` is stored under `checks[key]`, and
# `issue` (a string, or None) is appended to the issues list when present. A
# gate returns [] to skip itself when its required columns are absent. Gates run
# in the order listed in _QUALITY_GATES; that order IS the checks-dict order.


def _gate_nan_strings(df):
    # 1. Literal "NAN" strings in text columns
    nan_count = 0
    for col in ['contributor_occupation', 'contributor_employer', 'contributor_name',
                'contributor_city', 'contributor_state', 'contributor_street_1']:
        if col in df.columns:
            nan_count += int((df[col].astype('string').str.upper() == 'NAN').sum())
    issue = f"Found {nan_count} literal 'NAN' in text columns" if nan_count else None
    return [('no_nan_strings', {'passed': nan_count == 0, 'count': nan_count}, issue)]


def _gate_valid_categories(df):
    # 2. Occupation categories all valid
    if 'occupation_category' not in df.columns:
        return []
    invalid = [str(v) for v in df['occupation_category'].dropna().unique() if v not in VALID_CATEGORIES]
    issue = f"Invalid categories: {invalid[:10]}" if invalid else None
    return [('valid_categories', {'passed': len(invalid) == 0, 'invalid': invalid[:10]}, issue)]


def _gate_zip_coverage(df):
    # 3. ZIP coverage
    if 'contributor_zip' not in df.columns:
        return []
    pct = float((df['contributor_zip'].isna() | (df['contributor_zip'].astype(str).str.strip() == '')).mean() * 100)
    issue = f"ZIP empty: {pct:.1f}%" if pct >= 50 else None
    return [('zip_coverage', {'passed': pct < 50, 'pct_empty': round(pct, 2)}, issue)]


def _dup_id_check(df, col, key):
    # 4. Duplicate id (sub_id / transaction_id)
    if col not in df.columns:
        return []
    n_dup = int(df[col].duplicated().sum())
    issue = f"Duplicate {col}: {n_dup}" if n_dup else None
    return [(key, {'passed': n_dup == 0, 'count': n_dup}, issue)]


def _gate_dup_sub_id(df):
    return _dup_id_check(df, 'sub_id', 'no_duplicate_sub_id')


def _gate_dup_transaction_id(df):
    return _dup_id_check(df, 'transaction_id', 'no_duplicate_transaction_id')


def _gate_extreme_amounts(df):
    # 5. Sanity: amounts
    if 'contribution_receipt_amount' not in df.columns:
        return []
    n_extreme = int((df['contribution_receipt_amount'].abs() > 1_000_000).sum())
    issue = f"Extreme amounts (>$1M): {n_extreme}" if n_extreme else None
    return [('no_extreme_amounts', {'passed': n_extreme == 0, 'count': n_extreme, 'threshold': '$1M'}, issue)]


def _gate_future_dates(df):
    # 6. Sanity: future dates
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
    # 7. ZIP-State mismatch
    if not ('contributor_zip' in df.columns and 'contributor_state' in df.columns):
        return []
    zip_prefix_to_states = {
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
    # Vectorized: for each of the 10 ZIP prefixes, mask rows where the
    # state doesn't belong to that prefix's allowed set. ~1M-row loop
    # becomes 10 boolean ops.
    z5_str = df['contributor_zip'].astype(str)
    valid_rows = df['contributor_zip'].notna() & (z5_str.str.len() == 5)
    zip_first = z5_str.str[0]
    state = df['contributor_state'].astype(str)

    n_zip_mismatch = 0
    for prefix, allowed in zip_prefix_to_states.items():
        in_prefix = valid_rows & (zip_first == prefix)
        n_zip_mismatch += int((in_prefix & ~state.isin(allowed)).sum())
    issue = f"ZIP-State mismatches: {n_zip_mismatch}" if n_zip_mismatch >= 100 else None
    return [('zip_state_match', {'passed': n_zip_mismatch < 100, 'count': n_zip_mismatch}, issue)]


def _gate_email_as_address(df):
    # 8. Email addresses used as street addresses
    if 'contributor_street_1' not in df.columns:
        return []
    n_email_addr = int(df['contributor_street_1'].fillna('').str.contains('@', regex=False).sum())
    issue = f"Email addresses in street field: {n_email_addr}" if n_email_addr else None
    return [('no_email_as_address', {'passed': n_email_addr == 0, 'count': n_email_addr}, issue)]


def _gate_special_chars_names(df):
    # 9. Backticks/semicolons remaining in names
    n_special_chars = 0
    for col in ['contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            n_special_chars += int(df[col].fillna('').str.contains(r'[`;\[\]@]', regex=True).sum())
    issue = f"Special characters in names: {n_special_chars}" if n_special_chars else None
    return [('no_special_chars_in_names', {'passed': n_special_chars == 0, 'count': n_special_chars}, issue)]


def _gate_individual_not_applicable(df):
    # 10a. No individual with occupation_status='NOT_APPLICABLE'.
    # NOT_APPLICABLE is reserved for committees (schema CHECK constraint).
    # If an individual has this status, a reclassification step earlier
    # in the pipeline forgot to re-derive the status — see the sweep
    # in fec/database/post_merge_fixes.py (_not_applicable_individual_sweep).
    if not {'entity_type', 'occupation_status'}.issubset(df.columns):
        return []
    na_indiv = (df['entity_type'] == 'INDIVIDUAL') & (df['occupation_status'] == 'NOT_APPLICABLE')
    n_na = int(na_indiv.sum())
    issue = (
        f"Individuals with NOT_APPLICABLE status: {n_na} — "
        "should be DISCLOSED / MISSING / NOT_DISCLOSED"
    ) if n_na else None
    return [('no_individual_not_applicable', {'passed': n_na == 0, 'count': n_na}, issue)]


def _gate_committee_shared_ai_addr(df):
    # 10c. AI-hallucinated committee addresses.
    # A real committee HQ is unique to that committee. If the same
    # employer_address appears for 2+ different contributor_names
    # (committees), the AI invented it as a default placeholder.
    if not {'entity_type', 'contributor_name', 'employer_address', 'resolve_method'}.issubset(df.columns):
        return []
    ai_comm = (
        df['entity_type'].eq('COMMITTEE/PAC')
        & df['resolve_method'].fillna('').astype(str).str.contains('ai_openai', na=False)
        & df['employer_address'].notna()
        & (df['employer_address'].astype(str).str.strip() != '')
    )
    if ai_comm.any():
        shared = df.loc[ai_comm].groupby('employer_address')['contributor_name'].nunique()
        bad_addrs = set(shared[shared >= 2].index)
        bad_addrs.discard('')
        n_bad = int((ai_comm & df['employer_address'].isin(bad_addrs)).sum())
    else:
        n_bad = 0
    issue = (
        f"AI-hallucinated committee addresses: {n_bad} rows "
        "— run _clear_ai_hallucinated_addresses in resolve.py"
    ) if n_bad else None
    return [('no_committee_shared_ai_addr', {'passed': n_bad == 0, 'count': n_bad}, issue)]


def _gate_retired_donor_consistency(df):
    # 10b. Per-donor employer consistency.
    # A donor who ever listed RETIRED and never a real employer should
    # not also have NOT EMPLOYED / SELF-EMPLOYED filings — those are
    # collapsed by the once-retired sweep in post_merge_fixes. If the
    # gate fails, the sweep was skipped or regressed.
    if not {'entity_type', 'donor_key', 'contributor_employer'}.issubset(df.columns):
        return []
    emp_u = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    is_i = df['entity_type'] == 'INDIVIDUAL'
    status_nonret = {'NOT EMPLOYED', 'UNEMPLOYED', 'SELF-EMPLOYED', 'SELF EMPLOYED'}
    nonret_statuses = {'NOT DISCLOSED', '', 'HOMEMAKER', 'STUDENT',
                       'CAMPAIGN/COMMITTEE', 'N/A', 'NA', 'NAN', 'NONE',
                       'RETIRED'} | status_nonret
    flags = pd.DataFrame({
        'donor_key': df.loc[is_i, 'donor_key'],
        'retired':  emp_u[is_i].eq('RETIRED'),
        'nonret':   emp_u[is_i].isin(status_nonret),
        'real':     ~emp_u[is_i].isin(nonret_statuses) & (emp_u[is_i] != ''),
    }).groupby('donor_key').agg('any')
    bad_donors = flags.index[flags['retired'] & ~flags['real'] & flags['nonret']]
    n_bad_rows = int((is_i & df['donor_key'].isin(bad_donors) &
                      emp_u.isin(status_nonret)).sum())
    issue = (
        f"Retired donors with NOT EMPLOYED/SELF-EMPLOYED: {n_bad_rows} rows "
        "— run _once_retired_always_retired sweep"
    ) if n_bad_rows else None
    return [('retired_donor_consistency', {'passed': n_bad_rows == 0, 'count': n_bad_rows}, issue)]


def _gate_retired_active_sync(df):
    # 10a. retired + active — logical contradiction.
    # If a donor's occupation_category is RETIRED the employer_status
    # must also be retired. The post_merge _retired_active_sync sweep
    # collapses the two; a non-zero count here means that step was
    # skipped or a new cohort slipped through.
    if not {'entity_type', 'occupation_category', 'employer_status'}.issubset(df.columns):
        return []
    bad_sync = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['occupation_category'] == 'RETIRED')
        & (df['employer_status'] == 'active')
    )
    n_bad_sync = int(bad_sync.sum())
    issue = (
        f"retired+active contradiction: {n_bad_sync} rows "
        "-- run _retired_active_sync in post_merge_fixes"
    ) if n_bad_sync else None
    return [('retired_active_sync', {'passed': n_bad_sync == 0, 'count': n_bad_sync}, issue)]


def _gate_slash_previous_employer(df):
    # 10b. previous_employer must not contain slash composite strings.
    # 'COMPANY/TITLE' and 'STATUS/COMPANY' formats from legacy FEC
    # filings are resolved by _resolve_slash_previous_employer into
    # clean company names. Real slash-brands (BRIDGESTONE/FIRESTONE)
    # are whitelisted.
    if 'previous_employer' not in df.columns:
        return []
    _PREV_EMP_SLASH_OK = {'BRIDGESTONE/FIRESTONE'}
    pe = df['previous_employer'].fillna('').astype(str)
    slashy = pe.str.contains('/') & ~pe.str.upper().isin(_PREV_EMP_SLASH_OK)
    n_slash = int(slashy.sum())
    issue = (
        f"previous_employer slash leaks: {n_slash} rows "
        "-- run _normalize_previous_employer_column in apply.py"
    ) if n_slash else None
    return [('no_slash_in_previous_employer', {'passed': n_slash == 0, 'count': n_slash}, issue)]


def _gate_null_surname(df):
    # 10c. Literal-NULL surname preservation.
    # Real donors exist with surname "Null" (e.g. "NULL, JAMES"). Pandas'
    # default read coerces "NULL" → NaN, which then writes as empty and
    # destroys the distinction between "real Null surname" and "missing
    # surname". If contributor_name starts with "NULL," the surname must
    # be the literal string "NULL" — not NaN, not empty.
    if not {'contributor_name', 'contributor_last_name'}.issubset(df.columns):
        return []
    name_null_prefix = df['contributor_name'].astype('string').str.startswith('NULL,', na=False)
    ln = df['contributor_last_name'].astype('string')
    lost = name_null_prefix & (ln.isna() | ln.eq(''))
    n_lost = int(lost.sum())
    issue = (
        f"NULL surname erased on {n_lost} row(s) — pandas coerced literal "
        "'NULL' to NaN. Ensure all readers use "
        "fec.io.read_pipeline_csv()"
    ) if n_lost else None
    return [('null_surname_preserved', {'passed': n_lost == 0, 'count': n_lost}, issue)]


# Order matters: this is the order checks appear in the result dict and the
# order issues are reported (unchanged from the original inline sequence).
_QUALITY_GATES = [
    _gate_nan_strings,
    _gate_valid_categories,
    _gate_zip_coverage,
    _gate_dup_sub_id,
    _gate_dup_transaction_id,
    _gate_extreme_amounts,
    _gate_future_dates,
    _gate_row_count,
    _gate_zip_state,
    _gate_email_as_address,
    _gate_special_chars_names,
    _gate_individual_not_applicable,
    _gate_committee_shared_ai_addr,
    _gate_retired_donor_consistency,
    _gate_retired_active_sync,
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
    passed = all(c.get('passed', True) for c in checks.values() if isinstance(c, dict) and 'passed' in c)
    return {'passed': passed, 'checks': checks, 'issues': issues}


def build_outlier_report(df: pd.DataFrame) -> dict:
    """Top-50 values for occupation and employer."""
    report = {}
    for col, key in [('contributor_occupation', 'top50_occupation'), ('contributor_employer', 'top50_employer')]:
        if col in df.columns:
            vc = df[col].dropna().value_counts().head(50)
            report[key] = [{'value': str(v), 'count': int(c)} for v, c in vc.items()]
    if 'entity_type' in df.columns and 'contributor_occupation' in df.columns:
        report['committee_default_count'] = int(
            ((df['entity_type'] == 'COMMITTEE/PAC') & (df['contributor_occupation'] == 'POLITICAL COMMITTEE')).sum()
        )
    return report


def save_report(df: pd.DataFrame, dir_path: str, name: str) -> None:
    """Save a DataFrame as both CSV and JSON."""
    if df is None or df.empty:
        return
    base = Path(dir_path) / name
    base.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(base.with_suffix('.csv'), index=False)
    with open(base.with_suffix('.json'), 'w', encoding='utf-8') as f:
        json.dump(df.to_dict(orient='records'), f, indent=2, ensure_ascii=False, default=str)
