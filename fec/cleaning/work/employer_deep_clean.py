"""Employer deep cleaning: junk values, semicolon lists, title prefixes, status-word variants, typos, truncated values."""
import re

import numpy as np
import pandas as pd

from fec.cleaning.reports.trail import WORK_FIELDS
from fec.cleaning.work.employer_aliases import documented_employer_name
from fec.config.constants import (
    OK_SHORT_EMPLOYERS,
    OK_SHORT_OCCUPATIONS,
    SELF_EMPLOYED_TYPOS,
)
from fec.config.data import MISSING_VALUES
from fec.config.occupation_rules.fixes import OCCUPATION_FIXES
from fec.config.occupation_rules.rules import HOMEMAKER_EMPLOYER_VALUES

# a leading job title before the employer name: "CEO, ACME CORP"
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


# Clear numbers and emails; keep exact company corrections from the rules.
def _deep_clean_emp_numeric_email(df: pd.DataFrame) -> None:
    emp = df['contributor_employer']

    numeric = emp.str.match(r'^\d+$', na=False)
    if numeric.any():
        df.loc[numeric, 'contributor_employer'] = np.nan

    email = emp.str.contains('@', na=False, regex=False)
    if email.any():
        reviewed = emp[email].map(documented_employer_name)
        df.loc[email, 'contributor_employer'] = reviewed.where(reviewed.notna(), np.nan)


# Clear short placeholders; keep the known self-employed and retired meanings.
def _deep_clean_emp_short_junk(df: pd.DataFrame) -> None:
    emp = df['contributor_employer']

    junk = (emp.str.len().eq(1) & emp.notna()) | emp.eq('XX')
    if junk.any():
        df.loc[junk, 'contributor_employer'] = np.nan

    me_mask = emp.eq('ME') & df['contributor_occupation'].eq('ME')
    if me_mask.any():
        df.loc[me_mask, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[me_mask, 'contributor_occupation'] = 'SELF-EMPLOYED'

    nd_mask = emp.eq('ND') & df['contributor_occupation'].eq('ND')
    if nd_mask.any():
        df.loc[nd_mask, 'contributor_employer'] = np.nan
        df.loc[nd_mask, 'contributor_occupation'] = np.nan

    rd_retired = emp.isin(['RD', 'RE']) & df['contributor_occupation'].eq('RETIRED')
    if rd_retired.any():
        df.loc[rd_retired, 'contributor_employer'] = 'RETIRED'


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


# normalize retired/self-employed phrasing variants to standard values
def _deep_clean_emp_retired_variants(df: pd.DataFrame) -> None:
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
    if retired.any():
        df.loc[retired, 'contributor_employer'] = 'RETIRED'

    consult_self = emp.eq('CONSULTANT (SELF-EMPLOYED)')
    if consult_self.any():
        df.loc[consult_self, 'contributor_employer'] = 'SELF-EMPLOYED'


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


# Clear short work values unless an allowed name or occupation fix preserves them.
def _deep_clean_emp_truncated(df: pd.DataFrame) -> None:
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
            df.loc[short, column] = np.nan


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
