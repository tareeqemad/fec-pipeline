"""
cleaning/enhancements.py — Post-cleaning data quality enhancements.

Orchestrates all enhancement sub-modules:
  - entity_classification: reclassify mistyped entities, name corrections
  - employer_synonyms: employer normalization, synonyms, occ-as-employer fixes
  - safety_nets: final consistency fixes

Also contains:
  - Occupation/employer swap detection
  - Occupation canonical normalization (OCCUPATION_CANONICAL dict)
  - Remaining junk cleanup

Each function returns counts of changes made for reporting.
"""
import re
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx, _set_missing
from fec.cleaning.entity_classification import (
    fix_remaining_misclassified,
    fix_misclassified_business_entities,
    normalize_business_names,
    apply_name_corrections,
    fix_double_apostrophes,
    fix_null_last_name,
    normalize_name_periods,
    fix_credential_in_name,
    fix_fullname_in_both_fields,
    fix_employer_equals_occupation,
    fix_is_individual_column,
)
from fec.cleaning.employer_synonyms import (
    normalize_employer_canonical,
    apply_employer_synonyms,
    expand_employer_abbreviations,
    expand_employer_associates,
    fix_occupation_as_employer,
    fix_normalized_mid_suffix,
    _recanonicalize_employers,
    restore_display_suffixes,
    merge_typo_variants,
)
from fec.cleaning.safety_nets import apply_safety_nets
from fec.cleaning.occupations import _categorize
from fec.config.occupation_rules import OCCUPATION_CANONICAL
from fec.config.constants import (
    OCCUPATION_AS_EMPLOYER, JUNK_EMPLOYER_RE,
    REFUSAL_EMPLOYERS,
)
from fec.log import get_logger

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_CORP_IN_OCC_RE = re.compile(
    # Words that mark the occupation field as actually holding a company name
    # (filer swapped emp and occ — e.g. emp='CLAIMS' occ='ARCH INSURANCE').
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bLTD\b'
    r'|\bCOMPANY\b|\bCORPORATION\b|\bHOLDINGS\b|\bGROUP\b'
    r'|\bPARTNERS\b|\bVENTURES\b|\bCAPITAL\b|\bFUND\b'
    r'|\bASSOCIATES\b|\bENTERPRISES\b|\bPROPERTIES\b|\bREALTY\b'
    r'|\bADVISORS\b|\bINSURANCE\b|\bINDUSTRIES\b|\bBROTHERS\b'
    r'|\bBANK\b|\bFINANCIAL\b|\bMEDIA\b|\bSYSTEMS\b'
    r'|\bTECHNOLOGIES\b|\bSOLUTIONS\b|\bSERVICES\b|\bMANAGEMENT\b'
    r'|\bTRUST\b|\bINTERNATIONAL\b|\bGLOBAL\b|\bPARTNERS\b\s+LLP'
    # Two-word "X & Y" patterns (BROWN & BROWN, BAKER & MCKENZIE, KIRKLAND & ELLIS):
    r'|\b\w+\s*&\s*\w+\b'
    r'|& (?:PARTNERS|ASSOCIATES|CRUTCHER|DE LLANO|BUTLER)',
    re.IGNORECASE,
)

_ORG_IN_OCC_RE = re.compile(
    # Institutional words that signal occupation field actually contains
    # an organization name (swap candidate when employer is a job word).
    r'\bHOSPITAL\b|\bUNIVERSITY\b|\bINSTITUTE\b'
    r'|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bBUREAU\b'
    r'|\bDEPARTMENT\b|\bMINISTRY\b'
    # The tracked committees themselves — a filer who typed "AIPAC" in the
    # occupation field put the ORGANISATION there, so it's a swap candidate.
    r'|\bAIPAC\b|\bDMFI\b',
    re.IGNORECASE,
)

_SCHOOL_EMP_RE = re.compile(
    r'SCHOOL|ACADEMY|COLLEGE|UNIVERSITY|EDUCATION|MDCPS|ISD\b|UNIFIED|DISTRICT',
    re.IGNORECASE,
)

_OCC_KEYWORDS = frozenset({
    'ATTORNEY', 'LAWYER', 'PARTNER', 'MANAGER', 'ADMINISTRATOR',
    'PRESIDENT', 'DIRECTOR', 'EXECUTIVE', 'PHYSICIAN', 'ACCOUNTANT',
    'CONSULTANT', 'ENGINEER', 'OFFICER', 'ANALYST', 'ADVISOR',
    'COUNSEL', 'DENTIST', 'SURGEON', 'BROKER', 'AGENT',
    # Job roles / industries filers mistakenly enter as the employer —
    # when this happens the real company is hiding in the occupation field.
    'CLAIMS', 'CPA', 'OWNER', 'MANAGING PARTNER', 'MANAGING DIRECTOR',
    'PAC MANAGER',
    'INSURANCE BROKER', 'INSURANCE AGENT', 'REAL ESTATE',
    'INVESTMENT MANAGEMENT', 'FINANCE', 'MARKETING', 'MANAGEMENT',
    'SALES', 'CASHIER', 'DESIGNER', 'CATER', 'CATERER', 'WRITER',
    'ARTIST', 'PSYCHOLOGIST', 'THERAPIST', 'PROFESSOR',
}) | OCCUPATION_AS_EMPLOYER  # constants.py set adds the formal job titles

_KNOWN_SHORT_OCC = frozenset({
    'MD', 'VP', 'PA', 'RN', 'IT', 'PR', 'HR', 'AI', 'DJ', 'DO', 'GP', 'GM',
})

_EMP_JUNK = frozenset({
    'VARIOUS', 'JOB', 'A', 'EMP', 'N', 'MULTIPLE', 'OTHER',
})

# Every self-employed spelling → canonical SELF-EMPLOYED. Full-match
# anchored: SELF, SELF EMPLOYED, SELFEMPLOYED, SELF-EMP, SELF/EMPLOYED,
# SELF EMPLOYEED, SELF EMPLOYMENT all match; SELFRIDGES, SELFHELP and
# 'SELF EMPLOYED CONSULTANT' do NOT (trailing text fails the `$`).
_SELF_EMP_RE = r'^SELF(?:[\s\-/]*EMP(?:LOY\w*)?)?$'

# OCCUPATION_CANONICAL (variant → canonical job title) now lives with the other
# occupation lookups in fec/config/occupation_rules.py — imported above.


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Swap detection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _swap_occ_emp(df: pd.DataFrame, idx: pd.Index) -> None:
    """Swap occupation ↔ employer for the given index and re-categorize."""
    old_occ = df.loc[idx, 'contributor_occupation'].copy()
    old_emp = df.loc[idx, 'contributor_employer'].copy()
    df.loc[idx, 'contributor_occupation'] = old_emp
    df.loc[idx, 'contributor_employer'] = old_occ
    df.loc[idx, 'occupation_category'] = _categorize(df.loc[idx, 'contributor_occupation'])


def fix_remaining_swapped_occ_emp(df: pd.DataFrame) -> Tuple[pd.DataFrame, int, int]:
    """
    Fix occupation/employer swaps the core pipeline missed:
      A) occ has corp pattern + emp is a job word → swap
      B) occ == emp and both are job words → emp = SELF-EMPLOYED
      C) occ is an institution + emp is a job word → swap

    Returns: (df, n_swapped, n_same_fixed)
    """
    ii = _indiv_idx(df)
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    emp = _norm(df.loc[ii, 'contributor_employer'])

    # A) occ looks like company, emp looks like job title
    occ_is_corp = occ.str.contains(_CORP_IN_OCC_RE.pattern, na=False, regex=True)
    emp_is_job = emp.isin(_OCC_KEYWORDS)
    occ_starts_corp = occ.str.startswith('CORP ', na=False)
    swap_a = ii[occ_is_corp & emp_is_job & ~occ_starts_corp]
    if len(swap_a):
        _swap_occ_emp(df, swap_a)

    # B) occ == emp, both are job words → emp = SELF-EMPLOYED
    same_idx = ii[(occ == emp) & emp.isin(_OCC_KEYWORDS)]
    n_same = len(same_idx)
    if n_same:
        df.loc[same_idx, 'contributor_employer'] = 'SELF-EMPLOYED'

    # C) occ is an institution, emp is a job → swap
    occ_is_org = occ.str.contains(_ORG_IN_OCC_RE.pattern, na=False, regex=True)
    swap_c = ii[occ_is_org & emp_is_job]
    if len(swap_c):
        _swap_occ_emp(df, swap_c)

    n_swapped = len(swap_a) + len(swap_c)
    return df, n_swapped, n_same


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Occupation normalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def normalize_occupation_canonical(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Map occupation variants to canonical short forms.
    Only safe, unambiguous mappings.
    Returns: (df, n_changed)
    """
    ii = _indiv_idx(df)
    occ = df.loc[ii, 'contributor_occupation']
    hits = ii[occ.isin(OCCUPATION_CANONICAL)]
    n_changed = len(hits)
    if n_changed:
        df.loc[hits, 'contributor_occupation'] = occ[hits].map(OCCUPATION_CANONICAL)
    return df, n_changed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Remaining junk cleanup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def clean_remaining_junk(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Final pass for junk that slipped through.
    Delegates to sub-functions for each fix category.
    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    n = 0
    n += _clean_junk_employer_xxx(df, ii)
    n += _clean_junk_employer_patterns(df, ii)
    n += _clean_junk_short_occ(df, ii)
    n += _clean_junk_occ_patterns(df, ii)
    n += _clean_junk_number_tail(df, ii)
    n += _clean_junk_llp_standalone(df, ii)
    n += _clean_junk_not_disclosed(df, ii)
    n += _clean_junk_employer_fixes(df, ii)
    n += _clean_junk_nist(df, ii)
    return df, n


# Refusal / "not a real employer" words. These must be NULLED out of the
# employer field — unlike RETIRED / SELF-EMPLOYED / NOT EMPLOYED, which the
# schema intentionally keeps in the employer column as a status. We only
# null the junk-y subset: refusals plus N/A-style placeholders and their
# typos. Built from the single-source sets so it never drifts.
_NULL_EMPLOYER_WORDS = (
    REFUSAL_EMPLOYERS
    | {'N/A', 'NA', 'N A', 'NONE', 'NOT APPLICABLE', 'NOT APPLICAABLE',
       'NOT DISCLOSED', 'INFORMATION REQUESTED',
       'INFORMATION REQUESTED PER BEST EFFORTS', 'PHYSICAN', 'SELP EMPLOYED'}
)


def _clean_junk_status_word_employer(df: pd.DataFrame) -> int:
    """Final sweep: null employers that are really refusals / placeholders
    (N/A, NONE, PRIVATE, PHYSICAN typo, …). Normalization can turn a raw
    value into one of these AFTER the upstream skip-filter ran (e.g. "N A" →
    "N/A"), so this runs at the very end. Collapses internal whitespace so
    "N A" and "N/A" are both caught. Status words that legitimately live in
    the employer column (RETIRED, SELF-EMPLOYED, NOT EMPLOYED) are left."""
    ii = _indiv_idx(df)
    emp = _norm(df.loc[ii, 'contributor_employer'])
    collapsed = emp.str.replace(r'\s+', ' ', regex=True)
    hit = ii[(emp != '') & collapsed.isin(_NULL_EMPLOYER_WORDS)]
    if len(hit):
        df.loc[hit, 'contributor_employer'] = np.nan
        return len(hit)
    return 0


def _clean_junk_employer_xxx(df: pd.DataFrame, ii: pd.Index) -> int:
    emp = _norm(df.loc[ii, 'contributor_employer'])
    xxx = ii[emp.isin({'XXX', 'XX', 'XXXX', 'X'})]
    if len(xxx):
        df.loc[xxx, 'contributor_employer'] = np.nan
        return len(xxx)
    return 0


def _clean_junk_employer_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    """Blank employers that are structurally not a company name — dates,
    all-numeric strings, masked digits (XXX-XX-XXXX), pure punctuation.
    Pattern-based, so new junk is caught without extending a denylist."""
    emp = _norm(df.loc[ii, 'contributor_employer'])
    junk = ii[(emp != '') & emp.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        df.loc[junk, 'contributor_employer'] = np.nan
        return len(junk)
    return 0


def _clean_self_employed_variants(df: pd.DataFrame) -> int:
    """Unify every self-employed spelling — SELF, SELF EMPLOYED,
    SELFEMPLOYED, SELF-EMP, SELF/EMPLOYED, SELF EMPLOYEED ... — to the
    canonical SELF-EMPLOYED, in both the employer and occupation columns.

    Runs as a final pass: restore_display_suffixes would otherwise rewrite
    SELF-EMPLOYED back to the most-common raw form (SELF EMPLOYED)."""
    ii = _indiv_idx(df)
    n = 0
    for col in ('contributor_employer', 'contributor_occupation'):
        if col not in df.columns:
            continue
        v = _norm(df.loc[ii, col])
        hit = ii[(v != 'SELF-EMPLOYED') & v.str.match(_SELF_EMP_RE, na=False)]
        if len(hit):
            df.loc[hit, col] = 'SELF-EMPLOYED'
            if col == 'contributor_occupation':
                df.loc[hit, 'occupation_category'] = 'SELF-EMPLOYED'
            n += len(hit)
    return n


def _clean_junk_short_occ(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    short = ii[(occ.str.len() <= 2) & (occ != '') & ~occ.isin(_KNOWN_SHORT_OCC)]
    if len(short):
        _set_missing(df, short)
        return len(short)
    return 0


def _clean_junk_occ_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    """Blank occupations that are structural junk — phone numbers, dates,
    all-numeric strings, masked digits. Same pattern as the employer junk
    cleaner; set MISSING so a later same-donor pass can refill."""
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    junk = ii[(occ != '') & occ.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        _set_missing(df, junk)
        return len(junk)
    return 0


def _clean_junk_number_tail(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    num_tail = ii[occ.str.contains(r'^[A-Z ]+\s+\d+$', regex=True, na=False)]
    if len(num_tail):
        df.loc[num_tail, 'contributor_occupation'] = occ[num_tail].str.replace(r'\s+\d+$', '', regex=True)
        return len(num_tail)
    return 0


def _clean_junk_llp_standalone(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    llp = ii[occ == 'LLP']
    if len(llp):
        df.loc[llp, 'contributor_occupation'] = 'ATTORNEY'
        df.loc[llp, 'occupation_category'] = 'LEGAL'
        df.loc[llp, 'occupation_status'] = 'DISCLOSED'
        return len(llp)
    return 0


def _clean_junk_not_disclosed(df: pd.DataFrame, ii: pd.Index) -> int:
    nd_text = ii[
        (_norm(df.loc[ii, 'contributor_occupation']) == 'NOT DISCLOSED')
        & (df.loc[ii, 'occupation_status'] != 'NOT_DISCLOSED')
    ]
    if len(nd_text):
        _set_missing(df, nd_text)
        return len(nd_text)
    return 0


def _clean_junk_employer_fixes(df: pd.DataFrame, ii: pd.Index) -> int:
    n = 0
    emp = _norm(df.loc[ii, 'contributor_employer'])
    occ = _norm(df.loc[ii, 'contributor_occupation'])

    # employer = DOCTOR/PHYSICIAN when occ is medical → SELF-EMPLOYED
    doc_emp = ii[emp.isin({'DOCTOR', 'PHYSICIAN'}) & occ.isin({'PHYSICIAN', 'DOCTOR', 'MEDICAL DOCTOR'})]
    if len(doc_emp):
        df.loc[doc_emp, 'contributor_employer'] = 'SELF-EMPLOYED'
        n += len(doc_emp)

    # employer = occupation word + occ = SELF-EMPLOYED → swap
    swap_se = ii[emp.isin(_OCC_KEYWORDS) & (occ == 'SELF-EMPLOYED')]
    if len(swap_se):
        real_occ = df.loc[swap_se, 'contributor_employer'].copy()
        df.loc[swap_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[swap_se, 'contributor_occupation'] = real_occ
        df.loc[swap_se, 'occupation_category'] = _categorize(df.loc[swap_se, 'contributor_occupation'])
        df.loc[swap_se, 'occupation_status'] = 'DISCLOSED'
        n += len(swap_se)

    # Employer junk → NaN
    emp_junk = ii[emp.isin(_EMP_JUNK)]
    if len(emp_junk):
        df.loc[emp_junk, 'contributor_employer'] = np.nan
        n += len(emp_junk)

    # SELF variants → SELF-EMPLOYED
    emp_raw = df.loc[ii, 'contributor_employer'].fillna('')
    self_pattern = ii[
        emp_raw.str.match(r'^SELF[\s,/\-]+', case=False, na=False)
        & (emp_raw != 'SELF-EMPLOYED')
        & ~emp_raw.str.startswith('SELF-EMPLOYED', na=False)
    ]
    if len(self_pattern):
        extracted = emp_raw[self_pattern].str.replace(
            r'^SELF[\s,/\-]+(?:EMPLOYED[\s,/\-]*)?', '', regex=True
        ).str.strip().str.lstrip(',').str.lstrip('-').str.lstrip('/').str.strip()
        good = self_pattern[extracted.str.len() > 2]
        if len(good):
            df.loc[good, 'contributor_employer'] = 'SELF-EMPLOYED'
            n += len(good)

    return n


def _clean_junk_nist(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    nist = ii[occ == 'NIST']
    n = 0
    if len(nist):
        emp_nist = _norm(df.loc[nist, 'contributor_employer'])
        is_school = nist[emp_nist.str.contains(_SCHOOL_EMP_RE.pattern, na=False, regex=True)]
        not_school = nist.difference(is_school)
        if len(is_school):
            df.loc[is_school, 'contributor_occupation'] = 'TEACHER'
            df.loc[is_school, 'occupation_category'] = 'EDUCATION'
            df.loc[is_school, 'occupation_status'] = 'DISCLOSED'
            n += len(is_school)
        if len(not_school):
            _set_missing(df, not_school)
            n += len(not_school)
    return n


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Audit helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _snap(df: pd.DataFrame, cols: List[str]) -> Dict[str, pd.Series]:
    if 'sub_id' not in df.columns:
        return {}
    return {col: df[col].copy() for col in cols if col in df.columns}


def _diff(df: pd.DataFrame, snap: Dict[str, pd.Series],
          audit_records: List[dict], step: str, reason: str) -> None:
    if 'sub_id' not in df.columns:
        return
    for col, before_series in snap.items():
        after_series = df[col]
        b = before_series.fillna('').astype(str)
        a = after_series.fillna('').astype(str)
        changed = b != a
        for idx in df.index[changed]:
            audit_records.append({
                'sub_id': str(df.at[idx, 'sub_id']),
                'field': col,
                'before': b.at[idx],
                'after': a.at[idx],
                'step': step,
                'reason': reason,
            })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Master function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def run_enhancements(
    df: pd.DataFrame, verbose: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, int], List[dict]]:
    """Run all post-cleaning enhancements. Returns: (df, report_dict, audit_records)"""
    log = logger.info if verbose else lambda msg: None
    report: Dict[str, int] = {}
    audit_records: List[dict] = []

    if 'is_individual' not in df.columns:
        if 'entity_type' in df.columns:
            df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
        else:
            df['is_individual'] = True

    # 1–2. Entity reclassification
    s = _snap(df, ['entity_type', 'is_individual', 'contributor_first_name',
                    'contributor_last_name', 'occupation_category', 'occupation_status',
                    'contributor_occupation', 'committee_type'])
    df, n = fix_remaining_misclassified(df)
    _diff(df, s, audit_records, 'enh_reclassify_committee', 'committee_name_pattern')
    report['reclassified_to_committee'] = n
    log(f"Reclassified {n:,} remaining individuals → COMMITTEE/PAC")

    s = _snap(df, ['entity_type', 'is_individual', 'contributor_first_name',
                    'contributor_last_name', 'occupation_category', 'occupation_status',
                    'contributor_occupation'])
    df, n2 = fix_misclassified_business_entities(df)
    _diff(df, s, audit_records, 'enh_reclassify_business', 'business_suffix_or_org_pattern')
    report['reclassified_business_entities'] = n2
    log(f"Reclassified {n2:,} business entities → COMMITTEE/PAC")

    # 3. Normalize business names
    s = _snap(df, ['contributor_name'])
    df, n3 = normalize_business_names(df)
    _diff(df, s, audit_records, 'enh_normalize_business_names', 'strip_legal_suffix')
    report['normalized_business_names'] = n3
    log(f"Normalized {n3:,} business names (stripped LLC/LLP/INC etc.)")

    # 4–5. Name fixes
    s = _snap(df, ['contributor_name'])
    df, n4 = normalize_name_periods(df)
    _diff(df, s, audit_records, 'enh_normalize_name_periods', 'remove_periods_from_initials')
    report['normalized_name_periods'] = n4
    log(f"Normalized {n4:,} name periods (I.R. → I R, MR. → MR)")

    s = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, n4b = fix_credential_in_name(df)
    _diff(df, s, audit_records, 'enh_fix_credential_in_name', 'strip_credential_between_last_first')
    report['credential_in_name_fixed'] = n4b
    log(f"Stripped credentials from {n4b:,} names (M.D./Ph.D./J.D. wedged in LAST, X, FIRST)")

    s = _snap(df, ['contributor_name'])
    df, n5 = apply_name_corrections(df)
    _diff(df, s, audit_records, 'enh_name_corrections', 'manual_correction')
    report['name_corrections'] = n5
    log(f"Applied {n5:,} manual name corrections")

    s = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, _ = fix_double_apostrophes(df)
    _diff(df, s, audit_records, 'enh_fix_apostrophes', 'double_apostrophe_cleanup')

    s = _snap(df, ['contributor_name', 'contributor_last_name'])
    df, n5c = fix_null_last_name(df)
    _diff(df, s, audit_records, 'enh_fix_null_lastname', 'literal_null_in_lastname')
    report['null_lastname_fixed'] = n5c
    log(f"Fixed {n5c:,} records with literal NULL as last name")

    # 6. Swapped occ/emp
    s = _snap(df, ['contributor_occupation', 'contributor_employer',
                    'occupation_category', 'occupation_status'])
    df, n_swap, n_same = fix_remaining_swapped_occ_emp(df)
    _diff(df, s, audit_records, 'enh_swap_occ_emp', 'occ_emp_swap_or_same_value')
    report['occ_emp_swapped'] = n_swap
    report['occ_emp_same_fixed'] = n_same
    log(f"Swapped {n_swap:,} occ↔emp, fixed {n_same:,} same-value pairs")

    # 7. Occupation canonical
    s = _snap(df, ['contributor_occupation'])
    df, n = normalize_occupation_canonical(df)
    _diff(df, s, audit_records, 'enh_normalize_occupation', 'canonical_variant')
    report['occupation_normalized'] = n
    log(f"Normalized {n:,} occupation variants → canonical")

    # 8. Remaining junk
    s = _snap(df, ['contributor_occupation', 'contributor_employer',
                    'occupation_category', 'occupation_status'])
    df, n = clean_remaining_junk(df)
    _diff(df, s, audit_records, 'enh_clean_junk', 'junk_cleanup')
    report['junk_cleaned'] = n
    log(f"Cleaned {n:,} remaining junk values")

    # 9. Employer canonical
    s = _snap(df, ['employer_name_normalized'])
    df, n = normalize_employer_canonical(df)
    _diff(df, s, audit_records, 'enh_normalize_employer', 'strip_suffix_normalize_and')
    report['employer_normalized'] = n
    log(f"Normalized {n:,} employer name variants")

    # 10–12. Field consistency fixes
    s = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, n = fix_fullname_in_both_fields(df)
    _diff(df, s, audit_records, 'enh_fix_fullname_both', 'fullname_copied_to_both_fields')
    report['fullname_both_fixed'] = n
    log(f"Fixed {n:,} records with full name in both first & last")

    s = _snap(df, ['contributor_occupation', 'occupation_category', 'occupation_status'])
    df, n = fix_employer_equals_occupation(df)
    _diff(df, s, audit_records, 'enh_fix_emp_eq_occ', 'employer_equals_occupation')
    report['emp_eq_occ_fixed'] = n
    log(f"Fixed {n:,} records where employer = occupation")

    s = _snap(df, ['is_individual'])
    df, n = fix_is_individual_column(df)
    _diff(df, s, audit_records, 'enh_fix_is_individual', 'set_true_false_from_entity_type')
    report['is_individual_fixed'] = n
    log(f"Fixed {n:,} is_individual values (True/False from entity_type)")

    # 13–15. Employer fixes
    s = _snap(df, ['employer_name_normalized'])
    df, n = fix_normalized_mid_suffix(df)
    _diff(df, s, audit_records, 'enh_fix_norm_mid_suffix', 'strip_mid_string_llc_inc')
    report['norm_mid_suffix_fixed'] = n
    log(f"Fixed {n:,} normalized employers with mid-string LLC/INC")

    s = _snap(df, ['contributor_employer'])
    df, n14 = apply_employer_synonyms(df)
    _diff(df, s, audit_records, 'enh_employer_synonyms', 'verified_same_company')
    report['employer_synonyms_merged'] = n14
    log(f"Merged {n14:,} employer name variants → canonical")

    s = _snap(df, ['contributor_employer', 'contributor_occupation',
                    'occupation_category', 'occupation_status'])
    df, n15 = fix_occupation_as_employer(df)
    _diff(df, s, audit_records, 'enh_fix_occ_as_employer', 'occupation_word_in_employer')
    report['occ_as_employer_fixed'] = n15
    log(f"Fixed {n15:,} records where employer was an occupation word")

    # 16. Safety net
    apply_safety_nets(df, verbose=verbose)

    # Final employer re-canonicalization
    n_recanon = _recanonicalize_employers(df)
    if n_recanon:
        report['employer_recanonicalized'] = n_recanon
        log(f"Re-canonicalized {n_recanon:,} employer variants (post-enhancement)")

    # Restore display suffixes (HOUSING -> HOUSING INC etc.) from raw FEC data
    from fec.env import RAW_CSV
    n_restored = restore_display_suffixes(df, RAW_CSV)
    if n_restored:
        report['display_suffixes_restored'] = n_restored
        log(f"Restored display suffixes for {n_restored:,} rows (HOUSING → HOUSING INC etc.)")

    # Fuzzy typo merging runs LAST so it catches typos that the other passes
    # leave behind (e.g. raw forms restored by restore_display_suffixes).
    # Operates on BOTH contributor_employer and previous_employer.
    n_typo = merge_typo_variants(df)
    if n_typo:
        report['typo_variants_merged'] = n_typo
        log(f"Merged {n_typo:,} typo-variant rows (fuzzy match ≥ 92%)")

    # Re-assert curated synonym merges: restore_display_suffixes and
    # merge_typo_variants can revert a synonym to a raw form sharing its
    # canonical_key. The curated dict gets the final word on employer names.
    s = _snap(df, ['contributor_employer'])
    df, n14b = apply_employer_synonyms(df)
    _diff(df, s, audit_records, 'enh_employer_synonyms_final', 'verified_same_company')
    if n14b:
        report['employer_synonyms_merged'] = report.get('employer_synonyms_merged', 0) + n14b
        log(f"Re-applied {n14b:,} employer synonyms (final pass)")

    # Expand unambiguous abbreviations (MGMT → MANAGEMENT, …) AFTER synonyms,
    # so abbreviations a synonym/override target reintroduces also collapse.
    df, n_abbr = expand_employer_abbreviations(df)
    if n_abbr:
        report['employer_abbreviations_expanded'] = n_abbr
        log(f"Expanded {n_abbr:,} employer abbreviations (MGMT→MANAGEMENT, …)")

    # ASSOC is contextual: real associations → ASSOCIATION, the rest → ASSOCIATES.
    df, n_assoc = expand_employer_associates(df)
    if n_assoc:
        report['employer_associates_normalized'] = n_assoc
        log(f"Normalized {n_assoc:,} ASSOC employers (→ ASSOCIATES / ASSOCIATION)")

    n_self = _clean_self_employed_variants(df)
    if n_self:
        report['self_employed_unified'] = n_self
        log(f"Unified {n_self:,} self-employed variants → SELF-EMPLOYED")

    # Final sweep — null refusals / N/A-style placeholders that only became a
    # skip-word after all the normalization above (e.g. "N A" → "N/A").
    n_null_emp = _clean_junk_status_word_employer(df)
    if n_null_emp:
        report['status_word_employer_nulled'] = n_null_emp
        log(f"Nulled {n_null_emp:,} refusal/placeholder employers (N/A, PRIVATE, etc.)")

    # Drop employer_name_normalized
    if 'employer_name_normalized' in df.columns:
        df.drop(columns=['employer_name_normalized'], inplace=True)
        log("  → Dropped employer_name_normalized (merged into contributor_employer)")

    log(f"  → Enhancement audit: {len(audit_records):,} field changes tracked")
    return df, report, audit_records
