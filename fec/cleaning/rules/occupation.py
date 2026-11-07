"""Occupation and occupation_category safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.rules.employer_swaps import _KNOWN_OCCUPATIONS
from fec.cleaning.work.normalize import _categorize
from fec.cleaning.work.status import is_job_title
from fec.config.constants import SKIP_OCCUPATIONS
from fec.config.occupation_rules.rules import (
    KNOWN_COMPANY_OCCUPATIONS,
    WEB_ARTIFACT_OCCUPATIONS,
)

# A legal form ending a company name: ACME LLC, ACME INC.
_LEGAL_FORM_END_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|PC|P\.C|PLLC|LTD)\.?$'
)
# A legal form alone is the tail of a company name, never an occupation.
_BARE_LEGAL_FORM_RE = re.compile(r'(?:COMPANY|CORPORATION|CORP|INC|LLC|LLP|LTD|PLLC)\.?')
# A role, then the company: "PRESIDENT, ACME INC", "CEO AT ACME LLC".
_ROLE_THEN_COMPANY_RE = re.compile(r'^(?P<role>[A-Z][A-Z .&/-]*?)\s*(?:,|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$')


# Fill missing categories for individuals, then committees.
def _fill_null_occupation_category(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    n_fixed = 0
    for scope, category in (
        (is_indiv, 'OTHER'),
        (df['entity_type'] == 'COMMITTEE/PAC', 'POLITICAL COMMITTEE'),
    ):
        missing = scope & df['occupation_category'].isna()
        if missing.any():
            df.loc[missing, 'occupation_category'] = category
            n_fixed += int(missing.sum())
    return n_fixed


# clear web form artifact strings left in occupation field
def _fix_web_artifact_occupation(df: pd.DataFrame) -> int:
    """Web form artifacts in occupation become NaN."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & occ.isin(WEB_ARTIFACT_OCCUPATIONS)
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n_fixed


# clear non-occupations; a company moves to an empty or SELF-EMPLOYED employer
def _null_junk_occupation(df: pd.DataFrame) -> int:
    """An email or a company in the occupation is no occupation.

    A company is a name ending in a legal form (ACME LLC), a reviewed
    company name, or one filed as the employer at least 10 times. "PRESIDENT, ACME INC" keeps the role as
    the occupation. The company moves to the employer when that is empty or
    SELF-EMPLOYED; otherwise the filed employer stays.
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip()
    emp = df['contributor_employer'].fillna('')

    counts = emp[is_indiv].value_counts()
    frequent_employers = set(counts[counts >= 10].index)
    is_email = occ.str.contains('@', na=False, regex=False)
    is_company = occ.str.contains(_LEGAL_FORM_END_RE, na=False) | occ.isin(KNOWN_COMPANY_OCCUPATIONS) | (
        occ.isin(frequent_employers) & occ.ne('')
        & ~occ.isin(SKIP_OCCUPATIONS) & ~occ.isin(_KNOWN_OCCUPATIONS)
    )
    company_mask = is_indiv & is_company & ~is_email

    parts = occ[company_mask].str.extract(_ROLE_THEN_COMPANY_RE)
    roles = parts['role'].fillna('').str.strip()
    has_role = roles.isin(_KNOWN_OCCUPATIONS) | roles.map(is_job_title)
    role_rows = parts.index[has_role]
    company = occ[company_mask].copy()
    company.loc[role_rows] = parts.loc[role_rows, 'company'].str.strip()

    open_employer = emp.eq('') | emp.eq('SELF-EMPLOYED')
    move = company.index[open_employer.loc[company.index]]
    df.loc[move, 'contributor_employer'] = company.loc[move]

    df.loc[role_rows, 'contributor_occupation'] = roles.loc[role_rows]
    df.loc[role_rows, 'occupation_category'] = _categorize(roles.loc[role_rows])
    bare = is_indiv & occ.str.fullmatch(_BARE_LEGAL_FORM_RE, na=False)
    clear = df.index[(is_indiv & is_email) | company_mask | bare].difference(role_rows)
    df.loc[clear, 'contributor_occupation'] = np.nan
    df.loc[clear, 'occupation_category'] = 'OTHER'
    return int(company_mask.sum() + (is_indiv & (is_email | bare)).sum())


