"""Quality gates for employment status, categories and previous employers."""
from fec.cleaning.employer_status import classify_employer_statuses
from fec.cleaning.occupations import _categorize_final
from fec.config.constants import SLASH_BRAND_EMPLOYERS
from fec.config.occupation_rules.categories import VALID_CATEGORIES


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


def _gate_previous_employer_scope(df):
    """Previous employment belongs only to retired rows."""
    required = {'employer_status', 'previous_employer'}
    not_run = _resolve_only_gate('previous_employer_scope', df, required)
    if not_run:
        return not_run

    previous = df['previous_employer'].fillna('').astype(str).str.strip()
    bad = int((previous.ne('') & ~df['employer_status'].eq('retired')).sum())
    issue = f"Previous employer on non-retired rows: {bad}" if bad else None
    return [('previous_employer_scope', {
        'passed': bad == 0,
        'count': bad,
    }, issue)]


def _gate_retired_active_sync(df):
    # RETIRED category with employer_status=active contradicts classify_employer_status
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
        "-- check classify_employer_status in fec/cleaning/previous_employer.py"
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
    # 'COMPANY/TITLE' composites are resolved by resolve.py (_normalize_previous_employer_column);
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
        "-- run resolve.py (fec/resolve/pipeline/quality_fixes.py normalizes the column)"
    ) if n_slash else None
    return [('no_slash_in_previous_employer', {'passed': n_slash == 0, 'count': n_slash}, issue)]
