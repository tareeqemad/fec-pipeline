"""Employer deep cleaning: junk values, semicolon lists, title prefixes, status-word variants, typos, truncated values."""
import re

import numpy as np
import pandas as pd

from fec.config.data import MISSING_VALUES
from fec.config.constants import (
    OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS, SELF_EMPLOYED_TYPOS,
)
from fec.config.occupation_rules.rules import (
    EMPLOYER_TYPO_FIXES,
    HOMEMAKER_EMPLOYER_VALUES,
)

_TITLE_PREFIX_RE = re.compile(
    r'^(?:CEO|CFO|COO|CTO|CIO|CMO|PRESIDENT|VICE PRESIDENT|VP|EVP|SVP|'
    r'CHAIRMAN|CHAIRPERSON|CHAIR|FOUNDER|CO-FOUNDER|PARTNER|PRINCIPAL|OWNER|'
    r'MANAGING DIRECTOR|EXECUTIVE DIRECTOR|DIRECTOR|MANAGER)\s*,\s*(?=\S)',
    re.IGNORECASE,
)

def _deep_clean_employer(df: pd.DataFrame, trail) -> int:
    """Employer-specific cleaning beyond text normalization; returns number of values changed."""
    from fec.cleaning.audit_trail import WORK_FIELDS

    n_changed = 0
    for pass_fn, reason in DEEP_CLEAN_PASSES:
        n_changed += trail.run(
            df, pass_fn, f'occ_deep_clean_{pass_fn.__name__.removeprefix("_deep_clean_emp_")}',
            reason, WORK_FIELDS,
        )
    return n_changed


def _deep_clean_emp_numeric_email(df: pd.DataFrame) -> int:
    """Numeric-only -> NaN, email addresses -> NaN (except DUN & BRADSTREET)."""
    emp = df['contributor_employer']
    n_changed = 0

    numeric = emp.str.match(r'^\d+$', na=False)
    if numeric.any():
        n_changed += int(numeric.sum())
        df.loc[numeric, 'contributor_employer'] = np.nan

    email = emp.str.contains('@', na=False, regex=False)
    if email.any():
        dun = email & emp.str.contains('DUN.*BRADSTREET', na=False, case=False)
        pure_email = email & ~dun
        if dun.any():
            df.loc[dun, 'contributor_employer'] = 'DUN & BRADSTREET'
        if pure_email.any():
            df.loc[pure_email, 'contributor_employer'] = np.nan
        n_changed += int(email.sum())

    return n_changed


def _deep_clean_emp_short_junk(df: pd.DataFrame) -> int:
    """Single-char junk -> NaN, two-char placeholders (XX, ME=ME, ND=ND, RD/RE+RETIRED)."""
    emp = df['contributor_employer']
    n_changed = 0

    single = emp.str.len().eq(1) & emp.notna()
    if single.any():
        n_changed += int(single.sum())
        df.loc[single, 'contributor_employer'] = np.nan

    xx_mask = emp.eq('XX')
    if xx_mask.any():
        n_changed += int(xx_mask.sum())
        df.loc[xx_mask, 'contributor_employer'] = np.nan

    me_mask = emp.eq('ME') & df['contributor_occupation'].eq('ME')
    if me_mask.any():
        n_changed += int(me_mask.sum())
        df.loc[me_mask, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[me_mask, 'contributor_occupation'] = 'SELF-EMPLOYED'

    nd_mask = emp.eq('ND') & df['contributor_occupation'].eq('ND')
    if nd_mask.any():
        n_changed += int(nd_mask.sum())
        df.loc[nd_mask, 'contributor_employer'] = np.nan
        df.loc[nd_mask, 'contributor_occupation'] = np.nan

    rd_retired = emp.isin(['RD', 'RE']) & df['contributor_occupation'].eq('RETIRED')
    if rd_retired.any():
        n_changed += int(rd_retired.sum())
        df.loc[rd_retired, 'contributor_employer'] = 'RETIRED'

    return n_changed


def _deep_clean_emp_semicolons(df: pd.DataFrame) -> int:
    """Semicolons separating multiple employers -> keep first."""
    emp = df['contributor_employer']
    semi = emp.str.contains(r'\s;\s', na=False, regex=True)
    n_changed = int(semi.sum())
    if n_changed:
        df.loc[semi, 'contributor_employer'] = (
            emp.loc[semi].str.split(r'\s*;\s*', regex=True).str[0].str.strip()
        )
    return n_changed


def _deep_clean_emp_title_prefix(df: pd.DataFrame) -> int:
    """Strip a leading job title ('COO, X' -> 'X'); known titles only so surname-led firms survive, and the lookahead means it never blanks the field."""
    emp = df['contributor_employer'].fillna('')
    stripped = emp.str.replace(_TITLE_PREFIX_RE, '', regex=True).str.strip()
    mask = (stripped != emp) & (stripped != '')
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = stripped[mask]
    return n_changed


def _deep_clean_emp_retired_variants(df: pd.DataFrame) -> int:
    """Normalize 'RETIRED FROM ...', 'SEMI RETIRED', 'SELF RETIRED', 'CONSULTANT (SELF-EMPLOYED)'."""
    emp = df['contributor_employer']
    n_changed = 0

    ret_prefix = emp.str.match(
        r'^RETIRED\s+(FROM|TEACHER|LAWYER|PHYSICIAN|MILITARY|'
        r'EXECUTIVE|DOCTOR|PROFESSOR|NURSE)',
        na=False,
    )
    if ret_prefix.any():
        n_changed += int(ret_prefix.sum())
        df.loc[ret_prefix, 'contributor_employer'] = 'RETIRED'

    # exact typos RETIRE_RE misses because the misspelling breaks the RETIRE
    # substring. Subset overlap with RETIRED_TYPO_EMPLOYERS is deliberate:
    # this fixes the employer field only, safety net T also syncs occupation.
    ret_typo = emp.isin(['RETIEED', 'RETIREE', 'RETIERD', 'RETIREED'])
    if ret_typo.any():
        n_changed += int(ret_typo.sum())
        df.loc[ret_typo, 'contributor_employer'] = 'RETIRED'

    semi_ret = emp.str.match(r'^SEMI\s*RETIRED', na=False)
    if semi_ret.any():
        n_changed += int(semi_ret.sum())
        df.loc[semi_ret, 'contributor_employer'] = 'RETIRED'

    self_ret = emp.eq('SELF RETIRED')
    if self_ret.any():
        n_changed += int(self_ret.sum())
        df.loc[self_ret, 'contributor_employer'] = 'RETIRED'

    consult_self = emp.eq('CONSULTANT (SELF-EMPLOYED)')
    if consult_self.any():
        n_changed += int(consult_self.sum())
        df.loc[consult_self, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n_changed


def _deep_clean_emp_self_employed_typos(df: pd.DataFrame) -> int:
    """Normalize SELF-EMPLOYED typos; must run before _cross_fill and the safety nets, which would treat them as real employers."""
    emp = df['contributor_employer'].fillna('')
    mask = emp.isin(SELF_EMPLOYED_TYPOS)
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n_changed


def _deep_clean_emp_homemaker_sync(df: pd.DataFrame) -> int:
    """HOMEMAKER/HOUSEWIFE in employer -> NOT EMPLOYED, sync occupation."""
    homemaker_mask = df['contributor_employer'].isin(HOMEMAKER_EMPLOYER_VALUES)
    n_changed = int(homemaker_mask.sum())
    if n_changed:
        df.loc[homemaker_mask, 'contributor_employer'] = 'NOT EMPLOYED'
        homemaker_occ_fix = homemaker_mask & (
            df['contributor_occupation'].isna()
            | df['contributor_occupation'].isin(['NOT DISCLOSED', 'NOT EMPLOYED'])
        )
        df.loc[homemaker_occ_fix, 'contributor_occupation'] = 'HOMEMAKER'
    return n_changed


def _deep_clean_emp_typo_patterns(df: pd.DataFrame) -> int:
    """Fix common employer typos (ASSOCAITE, MANAGMENT, INVESTEMENT...)."""
    n_changed = 0
    emp_col = df['contributor_employer']
    for typo_pattern, fix in EMPLOYER_TYPO_FIXES:
        mask = emp_col.str.contains(typo_pattern, na=False, regex=True)
        if mask.any():
            df.loc[mask, 'contributor_employer'] = (
                emp_col[mask].str.replace(typo_pattern, fix, regex=True)
            )
            n_changed += int(mask.sum())
            emp_col = df['contributor_employer']
    return n_changed


def _deep_clean_emp_truncated(df: pd.DataFrame) -> int:
    """Truncated 2-char employer/occupation -> NaN (whitelisted real names excluded)."""
    n_changed = 0

    emp_now = df['contributor_employer']
    short_emp = (
        emp_now.str.len().le(2) & emp_now.notna()
        & ~emp_now.isin(OK_SHORT_EMPLOYERS) & ~emp_now.isin(MISSING_VALUES)
    )
    if short_emp.any():
        n_changed += int(short_emp.sum())
        df.loc[short_emp, 'contributor_employer'] = np.nan

    occ_now = df['contributor_occupation']
    short_occ = (
        occ_now.str.len().le(2) & occ_now.notna()
        & ~occ_now.isin(OK_SHORT_OCCUPATIONS) & ~occ_now.isin(MISSING_VALUES)
    )
    if short_occ.any():
        n_changed += int(short_occ.sum())
        df.loc[short_occ, 'contributor_occupation'] = np.nan

    return n_changed


# (pass, audit reason)
DEEP_CLEAN_PASSES = (
    (_deep_clean_emp_numeric_email, 'numeric_or_email_employer_nulled'),
    (_deep_clean_emp_short_junk, 'short_placeholder_employer_nulled'),
    (_deep_clean_emp_semicolons, 'first_of_semicolon_list_kept'),
    (_deep_clean_emp_title_prefix, 'leading_job_title_stripped'),
    (_deep_clean_emp_retired_variants, 'retired_or_self_employed_variant_normalized'),
    (_deep_clean_emp_self_employed_typos, 'self_employed_typo_fixed'),
    (_deep_clean_emp_homemaker_sync, 'homemaker_employer_is_not_employed'),
    (_deep_clean_emp_typo_patterns, 'employer_typo_pattern_fixed'),
    (_deep_clean_emp_truncated, 'truncated_two_char_value_nulled'),
)
