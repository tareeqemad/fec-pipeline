"""Occupation and occupation_category safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.rules.employer_swaps import _KNOWN_OCCUPATIONS
from fec.cleaning.work.company import company_evidence, company_key, filers_of
from fec.cleaning.work.normalize import _categorize
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.config.occupation_rules.rules import (
    WEB_ARTIFACT_OCCUPATIONS,
)

# A legal form alone is the tail of a company name, never an occupation.
_BARE_LEGAL_FORM_RE = re.compile(r'(?:COMPANY|CORPORATION|CORP|INC|LLC|LLP|LTD|PLLC)\.?')
# A role, then the company: "PRESIDENT, ACME INC", "CEO AT ACME LLC".
_ROLE_THEN_COMPANY_RE = re.compile(r'^(?P<role>[A-Z][A-Z .&-]*?)\s*(?:,|/|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$')


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


# the occupation is a name this donor files as their employer on another row
def _own_employer(occ: pd.Series, emp: pd.Series, filers, is_indiv: pd.Series) -> pd.Series:
    """True where the donor's other filings name the occupation text as the employer."""
    if filers is None:
        return pd.Series(False, index=occ.index)
    names = filers.fillna('').astype(str).str.strip().str.upper()
    key_occ = occ.map(company_key)
    key_emp = emp.map(company_key)
    filed = set(zip(names[is_indiv], key_emp[is_indiv]))
    own = pd.Series(False, index=occ.index)
    own.loc[is_indiv] = [
        (name, key) in filed and key != key_emp.at[idx]
        for idx, name, key in zip(occ.index[is_indiv], names[is_indiv], key_occ[is_indiv])
    ]
    return own & key_occ.map(is_real_employer) & ~occ.isin(_KNOWN_OCCUPATIONS) & ~occ.map(is_job_title)


# clear non-occupations; a company moves to an empty or SELF-EMPLOYED employer
def _null_junk_occupation(df: pd.DataFrame) -> int:
    """An email or a company in the occupation is no occupation.

    A company is decided by fec.cleaning.work.company: a legal form, a
    reviewed name, a name several donors file as their employer, an
    organisation word beside a proper name, or the name this same donor
    files as their employer elsewhere. "PRESIDENT, ACME INC" keeps the role
    as the occupation. The company moves to the employer when that is
    empty or SELF-EMPLOYED; otherwise the filed employer stays.
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip()
    emp = df['contributor_employer'].fillna('')

    is_email = occ.str.contains('@', na=False, regex=False)
    level = pd.Series('', index=df.index)
    filers = filers_of(df, is_indiv)
    level.loc[is_indiv] = company_evidence(
        occ[is_indiv], employers=emp[is_indiv], filers=filers, min_filers=2,
    ).to_numpy()
    # A name with only an organisation word beside it is a company when
    # nothing was filed as the employer; beside SELF-EMPLOYED it may be a
    # misspelt or abbreviated field (INVESTMENT MGR), so it stays as filed,
    # unless the donor files that very name as their employer elsewhere.
    certain = level.isin(('legal_form', 'reviewed', 'frequent'))
    own = level.eq('named_org') & _own_employer(occ, emp, filers, is_indiv)
    is_company = certain | own | (level.eq('named_org') & emp.eq(''))
    company_mask = is_company & ~is_email

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


