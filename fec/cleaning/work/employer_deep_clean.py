"""Employer deep cleaning: junk, lists, title prefixes, typos, truncation."""
import re

import numpy as np
import pandas as pd

from fec.cleaning.reports.trail import WORK_FIELDS
from fec.config.constants import OK_SHORT_OCCUPATIONS, SELF_EMPLOYED_TYPOS
from fec.config.data import MISSING_VALUES
from fec.config.occupation_rules.rules import HOMEMAKER_EMPLOYER_VALUES, OCCUPATION_FIXES

# A job title before the employer: "CEO, ACME CORP".
_TITLE_PREFIX_RE = re.compile(
    r'^(?:CEO|CFO|COO|CTO|CIO|CMO|PRESIDENT|VICE PRESIDENT|VP|EVP|SVP|'
    r'CHAIRMAN|CHAIRPERSON|CHAIR|FOUNDER|CO-FOUNDER|PARTNER|PRINCIPAL|OWNER|'
    r'MANAGING DIRECTOR|EXECUTIVE DIRECTOR|DIRECTOR|MANAGER)\s*,\s*(?=\S)',
    re.IGNORECASE,
)

# Run employer fixes in order.
def _deep_clean_employer(df: pd.DataFrame, trail) -> None:
    for pass_fn, reason in DEEP_CLEAN_PASSES:
        trail.run(
            df, pass_fn, f'occ_deep_clean_{pass_fn.__name__.removeprefix("_deep_clean_emp_")}',
            reason, WORK_FIELDS,
        )


# Short codes with a known meaning, before short values are cleared.
def _deep_clean_emp_short_junk(df: pd.DataFrame) -> None:
    emp = df['contributor_employer']

    me_mask = emp.eq('ME') & df['contributor_occupation'].eq('ME')
    if me_mask.any():
        df.loc[me_mask, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[me_mask, 'contributor_occupation'] = 'SELF-EMPLOYED'

    nd_mask = emp.eq('ND') & df['contributor_occupation'].eq('ND')
    if nd_mask.any():
        df.loc[nd_mask, 'contributor_employer'] = np.nan
        df.loc[nd_mask, 'contributor_occupation'] = np.nan



# normalize separators without discarding any filed employer
def _deep_clean_emp_semicolons(df: pd.DataFrame) -> None:
    emp = df['contributor_employer'].fillna('')
    normalized = emp.str.replace(r'\s*;\s*', ' ; ', regex=True).str.strip()
    changed = emp.str.contains(';', na=False, regex=False) & normalized.ne(emp)
    if changed.any():
        df.loc[changed, 'contributor_employer'] = normalized[changed]


# Strip configured job titles followed by a comma.
def _deep_clean_emp_title_prefix(df: pd.DataFrame) -> None:
    emp = df['contributor_employer'].fillna('')
    stripped = emp.str.replace(_TITLE_PREFIX_RE, '', regex=True).str.strip()
    mask = (stripped != emp) & (stripped != '')
    if mask.any():
        df.loc[mask, 'contributor_employer'] = stripped[mask]


# Normalize self-employment typos before work inference.
def _deep_clean_emp_self_employed_typos(df: pd.DataFrame) -> None:
    emp = df['contributor_employer'].fillna('')
    mask = emp.isin(SELF_EMPLOYED_TYPOS)
    if mask.any():
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'


# turn homemaker/housewife employer into NOT EMPLOYED and sync occupation
def _deep_clean_emp_homemaker_sync(df: pd.DataFrame) -> None:
    homemaker_mask = df['contributor_employer'].isin(HOMEMAKER_EMPLOYER_VALUES)
    if homemaker_mask.any():
        df.loc[homemaker_mask, 'contributor_employer'] = 'NOT EMPLOYED'
        homemaker_occ_fix = homemaker_mask & (
            df['contributor_occupation'].isna()
            | df['contributor_occupation'].isin(['NOT DISCLOSED', 'NOT EMPLOYED'])
        )
        df.loc[homemaker_occ_fix, 'contributor_occupation'] = 'HOMEMAKER'


# Apply documented full-name corrections, not word substitutions.
def _deep_clean_emp_typo_patterns(df: pd.DataFrame) -> None:
    from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
    apply_reviewed_employer_names(df, ("contributor_employer",))


# Clear placeholder employers and short occupations.
def _deep_clean_emp_truncated(df: pd.DataFrame) -> None:
    from fec.cleaning.rules.junk import clear_placeholder_employers
    clear_placeholder_employers(df, words=False)
    occupation = df['contributor_occupation']
    short = (
        occupation.str.len().le(2) & occupation.notna()
        & ~occupation.isin(OK_SHORT_OCCUPATIONS) & ~occupation.isin(MISSING_VALUES)
        & ~occupation.isin(OCCUPATION_FIXES)
    )
    if short.any():
        df.loc[short, 'contributor_occupation'] = np.nan


# (pass, audit reason)
DEEP_CLEAN_PASSES = (
    (_deep_clean_emp_short_junk, 'short_code_employer_resolved'),
    (_deep_clean_emp_semicolons, 'semicolon_list_spacing_normalized'),
    (_deep_clean_emp_title_prefix, 'leading_job_title_stripped'),
    (_deep_clean_emp_self_employed_typos, 'self_employed_typo_fixed'),
    (_deep_clean_emp_homemaker_sync, 'homemaker_employer_is_not_employed'),
    (_deep_clean_emp_typo_patterns, 'employer_typo_pattern_fixed'),
    (_deep_clean_emp_truncated, 'placeholder_or_truncated_value_nulled'),
)
