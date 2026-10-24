"""Employer deep cleaning: junk values, semicolon lists, title prefixes, status-word variants, typos, truncated values."""
import re

import numpy as np
import pandas as pd

from fec.cleaning.reports.trail import WORK_FIELDS
from fec.cleaning.work.employer_aliases import preserved_employer_name
from fec.config.constants import (
    OK_SHORT_EMPLOYERS,
    OK_SHORT_OCCUPATIONS,
    SELF_EMPLOYED_TYPOS,
)
from fec.config.data import MISSING_VALUES
from fec.config.occupation_rules.fixes import EMPLOYER_TYPO_FIXES, OCCUPATION_FIXES
from fec.config.occupation_rules.rules import HOMEMAKER_EMPLOYER_VALUES

# a leading job title before the employer name: "CEO, ACME CORP"
_TITLE_PREFIX_RE = re.compile(
    r'^(?:CEO|CFO|COO|CTO|CIO|CMO|PRESIDENT|VICE PRESIDENT|VP|EVP|SVP|'
    r'CHAIRMAN|CHAIRPERSON|CHAIR|FOUNDER|CO-FOUNDER|PARTNER|PRINCIPAL|OWNER|'
    r'MANAGING DIRECTOR|EXECUTIVE DIRECTOR|DIRECTOR|MANAGER)\s*,\s*(?=\S)',
    re.IGNORECASE,
)

# Run employer fixes in order and count changes.
def _deep_clean_employer(df: pd.DataFrame, trail) -> int:
    n_changed = 0
    for pass_fn, reason in DEEP_CLEAN_PASSES:
        n_changed += trail.run(
            df, pass_fn, f'occ_deep_clean_{pass_fn.__name__.removeprefix("_deep_clean_emp_")}',
            reason, WORK_FIELDS,
        )
    return n_changed


# Clear numbers and emails; restore DUN & BRADSTREET when it contains @.
def _deep_clean_emp_numeric_email(df: pd.DataFrame) -> int:
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


# Clear short placeholders; keep the known self-employed and retired meanings.
def _deep_clean_emp_short_junk(df: pd.DataFrame) -> int:
    emp = df['contributor_employer']
    n_changed = 0

    junk = (emp.str.len().eq(1) & emp.notna()) | emp.eq('XX')
    if junk.any():
        n_changed += int(junk.sum())
        df.loc[junk, 'contributor_employer'] = np.nan

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


# normalize separators without discarding any filed employer
def _deep_clean_emp_semicolons(df: pd.DataFrame) -> int:
    emp = df['contributor_employer'].fillna('')
    normalized = emp.str.replace(r'\s*;\s*', ' ; ', regex=True).str.strip()
    changed = emp.str.contains(';', na=False, regex=False) & normalized.ne(emp)
    n_changed = int(changed.sum())
    if n_changed:
        df.loc[changed, 'contributor_employer'] = normalized[changed]
    return n_changed


# Strip configured job titles followed by a comma.
def _deep_clean_emp_title_prefix(df: pd.DataFrame) -> int:
    emp = df['contributor_employer'].fillna('')
    stripped = emp.str.replace(_TITLE_PREFIX_RE, '', regex=True).str.strip()
    mask = (stripped != emp) & (stripped != '')
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = stripped[mask]
    return n_changed


# normalize retired/self-employed phrasing variants to standard values
def _deep_clean_emp_retired_variants(df: pd.DataFrame) -> int:
    emp = df['contributor_employer']

    ret_prefix = emp.str.match(
        r'^RETIRED\s+(FROM|TEACHER|LAWYER|PHYSICIAN|MILITARY|'
        r'EXECUTIVE|DOCTOR|PROFESSOR|NURSE)',
        na=False,
    )
    # Fix employer typos here; the later retirement rule also syncs occupation.
    ret_typo = emp.isin(['RETIEED', 'RETIREE', 'RETIERD', 'RETIREED'])
    semi_ret = emp.str.match(r'^SEMI\s*RETIRED', na=False)
    self_ret = emp.eq('SELF RETIRED')
    retired = ret_prefix | ret_typo | semi_ret | self_ret
    n_changed = int(retired.sum())
    if n_changed:
        df.loc[retired, 'contributor_employer'] = 'RETIRED'

    consult_self = emp.eq('CONSULTANT (SELF-EMPLOYED)')
    if consult_self.any():
        n_changed += int(consult_self.sum())
        df.loc[consult_self, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n_changed


# Normalize self-employment typos before work inference.
def _deep_clean_emp_self_employed_typos(df: pd.DataFrame) -> int:
    emp = df['contributor_employer'].fillna('')
    mask = emp.isin(SELF_EMPLOYED_TYPOS)
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n_changed


# turn homemaker/housewife employer into NOT EMPLOYED and sync occupation
def _deep_clean_emp_homemaker_sync(df: pd.DataFrame) -> int:
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


# fix common employer typo patterns via configured regex replacements
def _deep_clean_emp_typo_patterns(df: pd.DataFrame) -> int:
    n_changed = 0
    emp_col = df['contributor_employer']
    editable = emp_col.map(preserved_employer_name).isna()
    for typo_pattern, fix in EMPLOYER_TYPO_FIXES:
        mask = emp_col.str.contains(typo_pattern, na=False, regex=True)
        mask &= editable
        if mask.any():
            df.loc[mask, 'contributor_employer'] = (
                emp_col[mask].str.replace(typo_pattern, fix, regex=True)
            )
            n_changed += int(mask.sum())
            emp_col = df['contributor_employer']
    return n_changed


# Clear short work values unless an allowed name or occupation fix preserves them.
def _deep_clean_emp_truncated(df: pd.DataFrame) -> int:
    n_changed = 0
    for column, allowed in (
        ('contributor_employer', OK_SHORT_EMPLOYERS),
        ('contributor_occupation', OK_SHORT_OCCUPATIONS),
    ):
        values = df[column]
        short = (
            values.str.len().le(2) & values.notna()
            & ~values.isin(allowed) & ~values.isin(MISSING_VALUES)
        )
        if column == 'contributor_occupation':
            short &= ~values.isin(OCCUPATION_FIXES)
        if short.any():
            n_changed += int(short.sum())
            df.loc[short, column] = np.nan
    return n_changed


# (pass, audit reason)
DEEP_CLEAN_PASSES = (
    (_deep_clean_emp_numeric_email, 'numeric_or_email_employer_nulled'),
    (_deep_clean_emp_short_junk, 'short_placeholder_employer_nulled'),
    (_deep_clean_emp_semicolons, 'semicolon_list_spacing_normalized'),
    (_deep_clean_emp_title_prefix, 'leading_job_title_stripped'),
    (_deep_clean_emp_retired_variants, 'retired_or_self_employed_variant_normalized'),
    (_deep_clean_emp_self_employed_typos, 'self_employed_typo_fixed'),
    (_deep_clean_emp_homemaker_sync, 'homemaker_employer_is_not_employed'),
    (_deep_clean_emp_typo_patterns, 'employer_typo_pattern_fixed'),
    (_deep_clean_emp_truncated, 'truncated_two_char_value_nulled'),
)
