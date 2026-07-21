"""
cleaning/occupations.py — Employer & occupation cleaning + categorization.

Pipeline:
  1. Normalize text (strip, uppercase, replace FEC junk, apply known mappings)
  2. Categorize occupations by regex rules (first match wins)
  3. Apply abbreviation/typo fixes
  4. Fill missing occupation for committees (from committee name patterns)
  5. Cross-fill occupation ↔ employer when one is obvious
  6. Final fallback: NOT DISCLOSED for anything still missing
"""
import re
from collections import defaultdict
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from fec.config import (
    MISSING_VALUES, EMPLOYER_NORMALIZE, OCCUPATION_NORMALIZE,
    OCCUPATION_FIXES, CATEGORY_PATTERNS, CATEGORY_OVERRIDES,
    COMM_PATTERNS, RETIRE_RE,
)
from fec.config.constants import OCCUPATION_AS_EMPLOYER

from fec.log import get_logger

logger = get_logger(__name__)

# Pre-build junk-value → NaN replacement dict (one batch replace, not a loop)
_JUNK_TO_NAN = {val: np.nan for val in MISSING_VALUES}

# Cyrillic lookalikes → ASCII (common in FEC data entry)
_CYRILLIC_TO_ASCII = str.maketrans({
    '\u0410': 'A', '\u0412': 'B', '\u0421': 'C', '\u0415': 'E',
    '\u041d': 'H', '\u041a': 'K', '\u041c': 'M', '\u041e': 'O',
    '\u0420': 'P', '\u0422': 'T', '\u0423': 'Y', '\u0425': 'X',
})


def clean_employer_occupation(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Full employer/occupation pipeline.

    Returns:
        (df, counts_dict) — df is modified in-place, counts track changes.
    """
    counts = {
        'normalized': 0,
        'occ_fixed': 0,
        'comm_filled': 0,
    }

    # ── Save raw employer before any changes (for employer_change_type) ──
    _raw_emp = df['contributor_employer'].copy()

    # ── 1. Normalize text ──

    df['contributor_employer'], n_emp = _normalize_text(
        df['contributor_employer'], EMPLOYER_NORMALIZE
    )
    df['contributor_occupation'], n_occ = _normalize_text(
        df['contributor_occupation'], OCCUPATION_NORMALIZE, collapse_retire=True
    )
    counts['normalized'] = n_emp + n_occ

    # ── 1b. Employer-specific deep cleaning ──

    n_emp_deep = _deep_clean_employer(df)
    counts['normalized'] += n_emp_deep

    # ── 1c. Canonicalize employer name variants ──
    # Unifies INC/LLC/LLP/CORP/PC/PA suffix variations, punctuation,
    # and spacing differences. Picks the most frequent spelling as canonical.

    n_canon = _canonicalize_employers(df)
    counts['normalized'] += n_canon

    # ── 1e. Detect and fix swapped occupation/employer fields ──
    # Must run BEFORE committee cleanup (step 4) — committee records get their
    # occupation cleared, which destroys the swap signal. Records that are
    # later reclassified as INDIVIDUAL in step 8 would lose the fix.
    _fix_swapped_occ_emp(df)

    # ── 1d. Initialize occupation_status ──
    # At this point, NaN = raw was NULL/empty/junk (normalized away in step 1)
    # DISCLOSED = both employer AND occupation present
    # EMPLOYER_MISSING = occupation present but employer missing (not a refusal)
    # MISSING = occupation missing
    has_occ = df['contributor_occupation'].notna()
    has_emp = df['contributor_employer'].notna()
    df['occupation_status'] = 'MISSING'
    df.loc[has_occ & has_emp, 'occupation_status'] = 'DISCLOSED'
    df.loc[has_occ & ~has_emp, 'occupation_status'] = 'EMPLOYER_MISSING'

    # ── 2. Categorize by regex rules ──

    df['occupation_category'] = _categorize(df['contributor_occupation'])

    # ── 3. Apply known typo → (occupation, category) fixes ──

    needs_fix = df['contributor_occupation'].isin(OCCUPATION_FIXES)
    if needs_fix.any():
        counts['occ_fixed'] = int(needs_fix.sum())
        orig = df.loc[needs_fix, 'contributor_occupation']
        df.loc[needs_fix, 'contributor_occupation'] = orig.map(
            {k: v[0] for k, v in OCCUPATION_FIXES.items()}
        )
        df.loc[needs_fix, 'occupation_category'] = orig.map(
            {k: v[1] for k, v in OCCUPATION_FIXES.items()}
        )

        # Set occupation_status for entries fixed to "NOT DISCLOSED"
        fixed_to_nd = needs_fix & (df['contributor_occupation'] == 'NOT DISCLOSED')
        # Explicit refusals → keep "NOT DISCLOSED" text with NOT_DISCLOSED status
        _REFUSAL_INPUTS = {'PRIVATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
                           'DECLINED TO ANSWER', 'MYOB', 'TMI'}
        is_refusal = fixed_to_nd & orig.isin(_REFUSAL_INPUTS)
        is_junk = fixed_to_nd & ~orig.isin(_REFUSAL_INPUTS)
        df.loc[is_refusal, 'occupation_status'] = 'NOT_DISCLOSED'
        # Junk → NaN (not "NOT DISCLOSED" text)
        df.loc[is_junk, 'contributor_occupation'] = np.nan
        df.loc[is_junk, 'occupation_category'] = pd.NA
        df.loc[is_junk, 'occupation_status'] = 'MISSING'

    # ── 4. Classify committees into committee_type (separate from occupation) ──

    is_committee = ~df['is_individual']
    df['committee_type'] = pd.Series(dtype='object', index=df.index)

    if is_committee.any():
        # Classify committee type from the committee name
        df.loc[is_committee, 'committee_type'] = _classify_committee_names(
            df.loc[is_committee, 'contributor_name']
        )
        counts['comm_filled'] = int(is_committee.sum())

        # Ensure committees have category = POLITICAL COMMITTEE (not a job category)
        df.loc[is_committee, 'occupation_category'] = 'POLITICAL COMMITTEE'

        # Committees don't have occupations — status is NOT_APPLICABLE, not MISSING
        # Note: can't use 'N/A' because pandas treats it as NaN in CSV I/O
        df.loc[is_committee, 'occupation_status'] = 'NOT_APPLICABLE'

        # Clear occupation for committees — it's not a real job title
        df.loc[is_committee, 'contributor_occupation'] = np.nan

        # Also fill employer if missing
        comm_no_emp = is_committee & df['contributor_employer'].isna()
        df.loc[comm_no_emp, 'contributor_employer'] = 'CAMPAIGN/COMMITTEE'

    # ── 5. Cross-fill occupation ↔ employer ──

    _cross_fill(df)

    # ── 6. Final fallback for individuals still missing ──
    # At this point, any remaining NaN occupation for individuals
    # is truly unknown — leave as NaN (not "NOT DISCLOSED").
    # Only explicit refusals (PRIVATE, CONFIDENTIAL, etc.) get "NOT DISCLOSED".

    still_missing_occ = df['is_individual'] & df['contributor_occupation'].isna()

    # Leave occupation as NaN — don't fill with "NOT DISCLOSED"
    df.loc[still_missing_occ, 'occupation_category'] = pd.NA
    df.loc[still_missing_occ, 'occupation_status'] = 'MISSING'

    # ── 7. Derive employer_change_type ──

    _clean_emp = df['contributor_employer'].copy()
    raw_upper  = _raw_emp.fillna('').astype(str).str.strip().str.upper()
    clean_upper = _clean_emp.fillna('').astype(str).str.strip().str.upper()

    change_type = pd.Series('unchanged', index=df.index)

    # Filled: raw was empty/junk → now has a value
    raw_empty = raw_upper.isin({'', 'NAN'}) | _raw_emp.isna()
    change_type[raw_empty & ~_clean_emp.isna()] = 'filled'

    # Normalized: value changed but not from empty
    changed = ~raw_empty & (raw_upper != clean_upper)
    change_type[changed] = 'normalized'

    # Swapped: detected by _fix_swapped_occ_emp (tracked via internal column)
    if '_occ_emp_swapped' in df.columns:
        change_type[df['_occ_emp_swapped'].fillna(False).astype(bool)] = 'swapped'

    df['employer_change_type'] = change_type

    return df, counts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Internal helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _normalize_text(series: pd.Series, normalize_map: dict, collapse_retire: bool = False) -> Tuple[pd.Series, int]:
    """
    Clean a text column:
      - Strip whitespace, uppercase
      - Fix HTML entities and special characters
      - Replace FEC junk values with NaN
      - Optionally collapse all RETIRE* variants → "RETIRED"
      - Apply normalize_map (known synonyms → canonical form)

    Returns: (cleaned_series, n_changed)
    """
    before = series.copy()

    s = series.astype('string').str.strip().str.upper()

    # Clean Cyrillic lookalikes (e.g. ATTORМEY → ATTORNEY)
    s = s.str.translate(_CYRILLIC_TO_ASCII)

    # Fix HTML entities (e.g. MARY&RSQUO;S → MARY'S)
    s = s.str.replace('&RSQUO;', "'", regex=False)
    s = s.str.replace('&LSQUO;', "'", regex=False)
    s = s.str.replace('&AMP;', '&', regex=False)
    s = s.str.replace('&RDQUO;', '"', regex=False)
    s = s.str.replace('&LDQUO;', '"', regex=False)
    s = s.str.replace(r'&[A-Z]+;', '', regex=True)

    # Remove curly braces (e.g. MARCUM LLP{ → MARCUM LLP)
    s = s.str.replace(r'[{}]', '', regex=True)

    # Remove trailing digits stuck to words (e.g. OWNER3 → OWNER)
    s = s.str.replace(r'(\b[A-Z]{3,})\d+\b', r'\1', regex=True)

    # Strip trailing punctuation (e.g. "ENGINEER." → "ENGINEER", "CO/" → "CO")
    s = s.str.replace(r'[\.\,\;\:\/\\]+\s*$', '', regex=True).str.strip()

    # Collapse double+ spaces → single space
    s = s.str.replace(r'\s{2,}', ' ', regex=True).str.strip()

    s = s.replace(_JUNK_TO_NAN)

    if collapse_retire:
        s = s.copy()
        s.loc[s.str.contains(RETIRE_RE.pattern, na=False, regex=True)] = 'RETIRED'

    s = s.replace(normalize_map)
    result = s.astype(object)

    # Count how many values actually changed
    n_changed = int(
        (before.fillna('__NULL__').astype(str) != result.fillna('__NULL__').astype(str)).sum()
    )
    return result, n_changed


def _categorize(occupation_series: pd.Series) -> pd.Series:
    """
    Assign an occupation category by regex pattern match.
    First matching rule wins (see CATEGORY_RULES in config.py).
    Explicit overrides applied first for known misclassifications.
    """
    result = pd.Series('OTHER', index=occupation_series.index)
    result[occupation_series.isna()] = pd.NA

    # Apply explicit overrides BEFORE regex (these are known misclassifications)
    override_mask = occupation_series.isin(CATEGORY_OVERRIDES)
    if override_mask.any():
        result[override_mask] = occupation_series[override_mask].map(CATEGORY_OVERRIDES)

    filled = occupation_series.fillna('').astype(str)
    for category, pattern in CATEGORY_PATTERNS:
        # Use pattern.pattern (string) instead of compiled regex object
        # for compatibility with Python 3.14 + pandas
        matches = filled.str.contains(pattern.pattern, na=False, regex=True) & (result == 'OTHER')
        result[matches] = category

    return result


def _classify_committee_names(names: pd.Series) -> pd.Series:
    """
    Classify committee names into sub-types by keyword matching.
    E.g. "SMITH FOR CONGRESS" → "CONGRESSIONAL CAMPAIGN"
    """
    result = pd.Series('POLITICAL COMMITTEE', index=names.index)
    filled = names.fillna('').astype(str)

    for label, pattern in COMM_PATTERNS:
        matches = filled.str.contains(pattern.pattern, na=False, regex=True) & (result == 'POLITICAL COMMITTEE')
        result[matches] = label

    return result


def _cross_fill(df: pd.DataFrame) -> None:
    """
    Fill occupation from employer (and vice versa) when the answer is obvious.

    Examples:
      - employer="RETIRED", occupation=NaN  →  occupation="RETIRED"
      - occupation="HOMEMAKER", employer=NaN  →  employer="NOT EMPLOYED"
      - occupation="STUDENT", employer=NaN   →  employer="STUDENT"
    """
    # Occupation from employer — when employer makes it obvious
    fill_occ_from_emp = {
        'RETIRED':       ('RETIRED',       'RETIRED'),
        'SELF-EMPLOYED': ('SELF-EMPLOYED', 'SELF-EMPLOYED'),
        'NOT EMPLOYED':  ('NOT EMPLOYED',  'NOT EMPLOYED'),
    }
    for employer_val, (occ_val, cat_val) in fill_occ_from_emp.items():
        mask = (
            df['is_individual']
            & df['contributor_occupation'].isna()
            & (df['contributor_employer'] == employer_val)
        )
        df.loc[mask, 'contributor_occupation'] = occ_val
        df.loc[mask, 'occupation_category'] = cat_val
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'

    # Employer from occupation — fill only when the employer slot is empty
    # and the occupation implies a specific employer type
    fill_emp_from_occ = {
        'RETIRED':      'RETIRED',
        'NOT EMPLOYED': 'NOT EMPLOYED',
        'HOMEMAKER':    'NOT EMPLOYED',
        'SELF-EMPLOYED':'SELF-EMPLOYED',
        'STUDENT':      'STUDENT',
    }

    has_occ_no_emp = df['contributor_occupation'].notna() & df['contributor_employer'].isna()
    if has_occ_no_emp.any():
        df.loc[has_occ_no_emp, 'contributor_employer'] = (
            df.loc[has_occ_no_emp, 'contributor_occupation']
            .map(fill_emp_from_occ)
            # For everything else: leave as NaN — the AI classifier may use
            # the employer field for context, so don't pollute it with "NOT DISCLOSED"
            # prematurely. The final fallback in clean_employer_occupation handles this.
        )


def _fix_swapped_occ_emp(df: pd.DataFrame) -> None:
    """
    Detect and fix cases where occupation and employer are swapped.

    Pattern: occupation contains LLC/INC/LLP (looks like a company),
    AND employer contains a known occupation keyword (ATTORNEY, MANAGER, etc.)

    Example:
      occ="MARC BERN & PARTNERS LLP", emp="ATTORNEY"
      → swap to occ="ATTORNEY", emp="MARC BERN & PARTNERS LLP"
    """

    corp_pattern = re.compile(
        r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bP\.?A\.?\s*$'
        r'|\bPARTNERS\b|\bGROUP\b|\bASSOCIATES\b|\bVENTURES\b'
        r'|\bHOLDINGS\b|\bSERVICES\b|\bENTERPRISES\b'
        # Institutional words — picks up "GEORGE WASHINGTON UNIVERSITY",
        # "MASS GENERAL HOSPITAL", etc. when they end up in the occ field.
        r'|\bUNIVERSITY\b|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
        r'|\bHOSPITAL\b|\bINSTITUTE\b'
        r'|\bFOUNDATION\b|\bAGENCY\b|\bDEPARTMENT\b|\bBUREAU\b',
        re.I,
    )
    # Job-title words that should never be the employer field.
    # OCCUPATION_AS_EMPLOYER is the single source of truth (PROFESSOR,
    # TEACHER, NURSE, PHYSICIAN, ATTORNEY, ...) — keep this list aligned
    # by import, not by copying.
    occ_keywords = OCCUPATION_AS_EMPLOYER | {
        'MANAGER', 'PARTNER', 'ADMINISTRATOR',
        'PRESIDENT', 'VICE PRESIDENT', 'DIRECTOR', 'EXECUTIVE',
        'OFFICER', 'CEO', 'CFO', 'COO', 'CTO', 'CMO',
    }

    has_both = df['contributor_occupation'].notna() & df['contributor_employer'].notna()

    occ_is_corp = df['contributor_occupation'].str.contains(corp_pattern, na=False)
    emp_is_job = df['contributor_employer'].isin(occ_keywords)

    swap_mask = has_both & occ_is_corp & emp_is_job
    if swap_mask.any():
        # Track the swap for employer_change_type
        df['_occ_emp_swapped'] = swap_mask

        old_occ = df.loc[swap_mask, 'contributor_occupation'].copy()
        old_emp = df.loc[swap_mask, 'contributor_employer'].copy()
        df.loc[swap_mask, 'contributor_occupation'] = old_emp
        df.loc[swap_mask, 'contributor_employer'] = old_occ

        # Re-categorize the swapped occupation
        df.loc[swap_mask, 'occupation_category'] = _categorize(
            df.loc[swap_mask, 'contributor_occupation']
        )


def _deep_clean_employer(df: pd.DataFrame) -> int:
    """
    Employer-specific cleaning beyond text normalization.
    Delegates to focused sub-functions for each fix category.
    Returns: number of values changed.
    """
    n = 0
    n += _deep_clean_emp_numeric_email(df)
    n += _deep_clean_emp_short_junk(df)
    n += _deep_clean_emp_semicolons(df)
    n += _deep_clean_emp_title_prefix(df)
    n += _deep_clean_emp_retired_variants(df)
    n += _deep_clean_emp_self_employed_typos(df)
    n += _deep_clean_emp_homemaker_sync(df)
    n += _deep_clean_emp_typo_patterns(df)
    n += _deep_clean_emp_truncated(df)
    return n


def _deep_clean_emp_numeric_email(df: pd.DataFrame) -> int:
    """Numeric-only → NaN, email addresses → NaN (except DUN & BRADSTREET)."""
    emp = df['contributor_employer']
    n = 0

    numeric = emp.str.match(r'^\d+$', na=False)
    if numeric.any():
        n += int(numeric.sum())
        df.loc[numeric, 'contributor_employer'] = np.nan

    email = emp.str.contains('@', na=False)
    if email.any():
        dun = email & emp.str.contains('DUN.*BRADSTREET', na=False, case=False)
        pure_email = email & ~dun
        if dun.any():
            df.loc[dun, 'contributor_employer'] = 'DUN & BRADSTREET'
        if pure_email.any():
            df.loc[pure_email, 'contributor_employer'] = np.nan
        n += int(email.sum())

    return n


def _deep_clean_emp_short_junk(df: pd.DataFrame) -> int:
    """Single-char junk → NaN, two-char placeholders (XX, ME=ME, ND=ND, RD/RE+RETIRED)."""
    emp = df['contributor_employer']
    n = 0

    single = emp.str.len().eq(1) & emp.notna()
    if single.any():
        n += int(single.sum())
        df.loc[single, 'contributor_employer'] = np.nan

    xx = emp.eq('XX')
    if xx.any():
        n += int(xx.sum())
        df.loc[xx, 'contributor_employer'] = np.nan

    me = emp.eq('ME') & df['contributor_occupation'].eq('ME')
    if me.any():
        n += int(me.sum())
        df.loc[me, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[me, 'contributor_occupation'] = 'SELF-EMPLOYED'

    nd = emp.eq('ND') & df['contributor_occupation'].eq('ND')
    if nd.any():
        n += int(nd.sum())
        df.loc[nd, 'contributor_employer'] = np.nan
        df.loc[nd, 'contributor_occupation'] = np.nan

    rd_ret = emp.isin(['RD', 'RE']) & df['contributor_occupation'].eq('RETIRED')
    if rd_ret.any():
        n += int(rd_ret.sum())
        df.loc[rd_ret, 'contributor_employer'] = 'RETIRED'

    return n


def _deep_clean_emp_semicolons(df: pd.DataFrame) -> int:
    """Semicolons separating multiple employers → keep first."""
    emp = df['contributor_employer']
    semi = emp.str.contains(r'\s;\s', na=False, regex=True)
    n = int(semi.sum())
    if n:
        df.loc[semi, 'contributor_employer'] = (
            emp.loc[semi].str.split(r'\s*;\s*', regex=True).str[0].str.strip()
        )
    return n


_TITLE_PREFIX_RE = re.compile(
    r'^(?:CEO|CFO|COO|CTO|CIO|CMO|PRESIDENT|VICE PRESIDENT|VP|EVP|SVP|'
    r'CHAIRMAN|CHAIRPERSON|CHAIR|FOUNDER|CO-FOUNDER|PARTNER|PRINCIPAL|OWNER|'
    r'MANAGING DIRECTOR|EXECUTIVE DIRECTOR|DIRECTOR|MANAGER)\s*,\s*(?=\S)',
    re.I,
)


def _deep_clean_emp_title_prefix(df: pd.DataFrame) -> int:
    """'COO, SANDY SPRING BUILDERS' → 'SANDY SPRING BUILDERS'.

    A filer who typed their TITLE ahead of the firm leaves it glued to the
    employer. Only the known-title list above is stripped, so law firms that
    genuinely lead with a surname ("PACHULSKI, STANG...", "WEIL, GOTSHAL...")
    are untouched — a surname is not a title. Never blanks the field: the
    lookahead requires a firm name to survive the strip.
    """
    emp = df['contributor_employer'].fillna('')
    stripped = emp.str.replace(_TITLE_PREFIX_RE, '', regex=True).str.strip()
    mask = (stripped != emp) & (stripped != '')
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = stripped[mask]
    return n


def _deep_clean_emp_retired_variants(df: pd.DataFrame) -> int:
    """'RETIRED FROM ...', 'SEMI RETIRED', 'SELF RETIRED', 'CONSULTANT (SELF-EMPLOYED)' → normalize."""
    emp = df['contributor_employer']
    n = 0

    ret_prefix = emp.str.match(
        r'^RETIRED\s+(FROM|TEACHER|LAWYER|PHYSICIAN|MILITARY|'
        r'EXECUTIVE|DOCTOR|PROFESSOR|NURSE)',
        na=False,
    )
    if ret_prefix.any():
        n += int(ret_prefix.sum())
        df.loc[ret_prefix, 'contributor_employer'] = 'RETIRED'

    # Exact-string typos of RETIRED that RETIRE_RE (matches "RETIRE") misses
    # because the misspelling breaks the substring — "RETIEED" (no R after TI),
    # "RETIREE" (noun form). Surfaced by build_employers flagging them as
    # employers with no HQ.
    ret_typo = emp.isin(['RETIEED', 'RETIREE', 'RETIERD', 'RETIREED'])
    if ret_typo.any():
        n += int(ret_typo.sum())
        df.loc[ret_typo, 'contributor_employer'] = 'RETIRED'

    semi_ret = emp.str.match(r'^SEMI\s*RETIRED', na=False)
    if semi_ret.any():
        n += int(semi_ret.sum())
        df.loc[semi_ret, 'contributor_employer'] = 'RETIRED'

    self_ret = emp.eq('SELF RETIRED')
    if self_ret.any():
        n += int(self_ret.sum())
        df.loc[self_ret, 'contributor_employer'] = 'RETIRED'

    consult_self = emp.eq('CONSULTANT (SELF-EMPLOYED)')
    if consult_self.any():
        n += int(consult_self.sum())
        df.loc[consult_self, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n


def _deep_clean_emp_self_employed_typos(df: pd.DataFrame) -> int:
    """Normalize common SELF-EMPLOYED typos and variants.

    Catches misspellings that slip through text normalization:
      SLEF, SLEF-EMPLOYED, SELF EMPLOYE, SELF-EMPOLYED, etc.
    Must run BEFORE _cross_fill and BEFORE safety-net steps like
    _fix_retired_while_active which would treat these as real employers.
    """
    _SE_TYPOS = {
        'SLEF', 'SLEF-EMPLOYED', 'SLEF EMPLOYED',
        'SELF-EMPOLYED', 'SELF EMPOLYED', 'SELF-EMPLOYE', 'SELF EMPLOYE',
        'SELF-EMPLYED', 'SELF EMPLYED', 'SELF-EMPLO', 'SELF EMPLO',
        'SELFEMPLOYED', 'SELF-EMPLYOED', 'SELF EMPLYOED',
        'SEL-EMPLOYED', 'SEL EMPLOYED', 'SELD-EMPLOYED', 'SELD EMPLOYED',
        'SELP', 'SELP EMPLOYED', 'SELP-EMPLOYED',   # P/F keyboard slip
        'SWLF', 'SWLF EMPLOYED', 'SWLF-EMPLOYED',   # W/E keyboard slip
    }
    emp = df['contributor_employer'].fillna('')
    mask = emp.isin(_SE_TYPOS)
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n


def _deep_clean_emp_homemaker_sync(df: pd.DataFrame) -> int:
    """HOMEMAKER/HOUSEWIFE in employer → NOT EMPLOYED, sync occupation."""
    hm_values = {'HOMEMAKER', 'HOUSEWIFE', 'HOUSWIFE', 'HOUSEWIVES',
                 'STAY AT HOME MOM', 'STAY AT HOME DAD'}
    hm_mask = df['contributor_employer'].isin(hm_values)
    n = int(hm_mask.sum())
    if n:
        df.loc[hm_mask, 'contributor_employer'] = 'NOT EMPLOYED'
        hm_occ_fix = hm_mask & (
            df['contributor_occupation'].isna()
            | df['contributor_occupation'].isin(['NOT DISCLOSED', 'NOT EMPLOYED'])
        )
        df.loc[hm_occ_fix, 'contributor_occupation'] = 'HOMEMAKER'
    return n


def _deep_clean_emp_typo_patterns(df: pd.DataFrame) -> int:
    """Fix common employer typos (ASSOCAITE→ASSOCIATE, MANAGMENT→MANAGEMENT, etc.)."""
    _EMP_TYPO_PATTERNS = [
        (r'ASSOCAITE', 'ASSOCIATE'),
        (r'ASSOICATE', 'ASSOCIATE'),
        (r'ASOCIATE',  'ASSOCIATE'),
        (r'DERMATOLOGITS\b', 'DERMATOLOGISTS'),
        (r'MANAGMENT', 'MANAGEMENT'),
        (r'MANAGEMNT', 'MANAGEMENT'),
        (r'INVESTEMENT', 'INVESTMENT'),
    ]
    n = 0
    emp_col = df['contributor_employer']
    for typo_pat, fix in _EMP_TYPO_PATTERNS:
        mask = emp_col.str.contains(typo_pat, na=False, regex=True)
        if mask.any():
            df.loc[mask, 'contributor_employer'] = (
                emp_col[mask].str.replace(typo_pat, fix, regex=True)
            )
            n += int(mask.sum())
            emp_col = df['contributor_employer']
    return n


def _deep_clean_emp_truncated(df: pd.DataFrame) -> int:
    """Truncated 2-char employer/occupation → NaN (whitelisted real names excluded)."""
    _REAL_SHORT_EMPS = {
        '3M', 'HP', 'BP', 'GE', 'GM', 'LG',
        'EY', 'PW', 'BJ', 'C3', 'AT', 'QC',
    }
    _REAL_SHORT_OCCS = {'IT', 'MD', 'RN', 'PA', 'VP', 'DJ', 'GP', 'OT', 'PT'}
    n = 0

    emp_now = df['contributor_employer']
    short_emp = (
        emp_now.str.len().le(2) & emp_now.notna()
        & ~emp_now.isin(_REAL_SHORT_EMPS) & ~emp_now.isin(MISSING_VALUES)
    )
    if short_emp.any():
        n += int(short_emp.sum())
        df.loc[short_emp, 'contributor_employer'] = np.nan

    occ_now = df['contributor_occupation']
    short_occ = (
        occ_now.str.len().le(2) & occ_now.notna()
        & ~occ_now.isin(_REAL_SHORT_OCCS) & ~occ_now.isin(MISSING_VALUES)
    )
    if short_occ.any():
        n += int(short_occ.sum())
        df.loc[short_occ, 'contributor_occupation'] = np.nan

    return n


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Employer canonicalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Regex for corporate / professional suffixes to strip when grouping.
# Word-boundary anchored to avoid false matches (e.g. CPA ending in PA).
# Two-pass: first strip dotted forms (P.C., P.A.), then plain.
_DOTTED_SUFFIX_RE = re.compile(
    r',?\s*\b(P\.C\.?|P\.A\.?)\s*$'
)
_CORP_SUFFIX_RE = re.compile(
    r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PC|PA|PLLC|LP)\s*$'
)

# Tokens that PROTECT a trailing PA/PC from being stripped.
# e.g. "SCOTT FANE CPA PA" — the PA is after CPA, so CPA would become "C"
# if we blindly strip PA. We protect these contexts.
_PA_PC_PROTECT_RE = re.compile(
    r'\b(CPA|DPA|RPA|EPA|SEPA|SHERPA)\s+(PA|PC)\s*$', re.I
)


def _employer_group_key(name: str) -> str:
    """
    Build a grouping key for employer canonicalization.

    Strips corporate suffixes, THE, punctuation, and spacing to detect
    variants of the same company. Handles:
      - INC / INC. / , INC. / LLC / LLP / CORP / CO / COMPANY
      - P.C. / P.A. / PC / PA (professional corporation/association)
      - THE prefix
      - & vs AND
      - Whitespace / comma / period differences
    """
    s = name.strip().upper()

    # Fix mid-word commas first (EMA INVEST,MENT → EMA INVESTMENT)
    s = re.sub(r'([A-Z]),([A-Z])', r'\1\2', s)

    # Normalize spacing and punctuation for comparison
    s = re.sub(r'[,.\s]+', ' ', s)

    # Strip dotted professional suffixes (P.C., P.A.)
    s = _DOTTED_SUFFIX_RE.sub('', s).strip()

    # Protect CPA PA etc. from being stripped
    if not _PA_PC_PROTECT_RE.search(s):
        s = _CORP_SUFFIX_RE.sub('', s).strip()
    else:
        # Strip everything except PA/PC at the end
        # e.g. "SCOTT FANE CPA PA INC" — strip INC but keep PA
        s = re.sub(
            r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PLLC|LP)\s*$',
            '', s
        ).strip()

    # Strip THE
    s = re.sub(r'^\s*THE\s+', '', s)

    # Normalize & → AND for grouping
    s = s.replace('&', 'AND')

    # Collapse all whitespace for the key
    s = re.sub(r'\s+', '', s)

    return s


def _canonicalize_employers(df: pd.DataFrame) -> int:
    """
    Unify employer name variants into a canonical form.

    Algorithm:
      1. Pre-clean: fix mid-word commas, strip dotted suffixes
      2. Group by normalized key (suffix-stripped, punctuation-collapsed)
      3. For groups with 2+ variants, pick the most frequent as canonical
      4. Apply mapping in-place

    Protected names (SELF-EMPLOYED, RETIRED, NOT EMPLOYED) are excluded.

    Modifies df in-place. Returns number of rows changed.
    """
    SKIP_VALUES = {'SELF-EMPLOYED', 'RETIRED', 'NOT EMPLOYED', 'NOT DISCLOSED'}

    emp = df['contributor_employer']
    mask = emp.notna() & (~emp.isin(SKIP_VALUES))
    active = emp[mask]

    if active.empty:
        return 0

    # ── Pre-clean: fix mid-word commas in-place ──
    mid_comma = active.str.contains(r'[A-Z],[A-Z]', na=False, regex=True)
    if mid_comma.any():
        df.loc[mid_comma[mid_comma].index, 'contributor_employer'] = (
            active[mid_comma].str.replace(r'([A-Z]),([A-Z])', r'\1\2', regex=True)
        )
        # Refresh after in-place change
        active = df.loc[mask, 'contributor_employer']

    # ── Pre-clean: normalize dotted professional suffixes ──
    # P.C. → PC, P.A. → PA, P.C → PC, P.A → PA
    dotted = active.str.contains(r'\bP\.[CA]\.?\s*$', na=False, regex=True)
    if dotted.any():
        df.loc[dotted[dotted].index, 'contributor_employer'] = (
            active[dotted]
            .str.replace(r',?\s*\bP\.C\.?\s*$', ' PC', regex=True)
            .str.replace(r',?\s*\bP\.A\.?\s*$', ' PA', regex=True)
            .str.strip()
        )
        active = df.loc[mask, 'contributor_employer']

    # ── Build frequency counts ──
    counts = active.value_counts()

    # ── Group by normalized key ──

    groups = defaultdict(list)
    for name in counts.index:
        key = _employer_group_key(name)
        if key:
            groups[key].append(name)

    # ── Build mapping: variant → canonical (most frequent) ──
    mapping = {}
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        canonical = max(variants, key=lambda v: counts.get(v, 0))
        for v in variants:
            if v != canonical:
                mapping[v] = canonical

    if not mapping:
        return 0

    # ── Apply mapping ──
    to_fix = emp.isin(mapping)
    n_changed = int(to_fix.sum())
    if n_changed:
        df.loc[to_fix, 'contributor_employer'] = emp[to_fix].map(mapping)
        logger.info(
            "Canonicalized %d employer variants across %d groups → %d rows updated",
            len(mapping), len([v for v in groups.values() if len(v) > 1]), n_changed
        )

    return n_changed