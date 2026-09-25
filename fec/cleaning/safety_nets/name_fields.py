"""Safety nets for a person's or company's name filed in the wrong work field."""
from __future__ import annotations

import pandas as pd

from fec.cleaning._helpers import _set_missing
from fec.cleaning.safety_nets.employer_swaps import (
    _COMPANY_NAME_RE,
    _COMPANY_SUFFIX_RE,
    _KNOWN_OCCUPATIONS,
    _had_legal_suffix,
    _is_own_name,
    _swap_occ_emp_fields,
)
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS
from fec.config.not_employers import JOB_TITLE_AS_EMPLOYER
from fec.config.occupation_rules.rules import (
    KNOWN_COMPANY_OCCUPATIONS,
)


def _fix_own_name_as_employer(df: pd.DataFrame) -> int:
    """AE2. Employer is the donor's own FULL name -> SELF-EMPLOYED; a shared surname alone is often a real firm, so both names required."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    hits = []
    for idx in df.index[mask]:
        if _is_own_name(df, idx, emp.at[idx]) and not _had_legal_suffix(df, idx):
            hits.append(idx)

    if hits:
        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'
    return len(hits)


def _fix_company_name_as_occupation(df: pd.DataFrame) -> int:
    """AK. emp='SELF-EMPLOYED' but occ is a frequent employer name in the dataset -> occ is the real employer; the occupation is left empty.

    The filing names no job, and what OTHER people at that company report is
    another person's occupation, never evidence for this one. The donor-stage
    fill (donor_consistency AJ) later recovers the role only from this same
    donor's own filings at this same employer; with none it stays missing.
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    se_mask = (
        is_indiv
        & (emp == 'SELF-EMPLOYED')
        & (occ != '')
        & ~occ.isin(SKIP_OCCUPATIONS)
        & ~occ.isin(_KNOWN_OCCUPATIONS)
    )

    emp_counts = df.loc[is_indiv, 'contributor_employer'].value_counts()
    known_employers = set(emp_counts[emp_counts >= 10].index)

    occ_is_company = se_mask & occ.isin(known_employers)
    n_fixed = int(occ_is_company.sum())
    if not n_fixed:
        return 0

    df.loc[occ_is_company, 'contributor_employer'] = df.loc[occ_is_company, 'contributor_occupation']
    _set_missing(df, occ_is_company)
    return n_fixed


def _fix_swapped_emp_occ_company(df: pd.DataFrame) -> int:
    """AL. emp=job title, occ=company name -> swap them."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')

    emp_is_title = is_indiv & emp.isin(JOB_TITLE_AS_EMPLOYER)
    occ_has_company = occ.str.contains(_COMPANY_SUFFIX_RE, na=False)
    mask = emp_is_title & occ_has_company
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    _swap_occ_emp_fields(df, mask)
    return n_fixed


def _fix_occ_emp_both_swapped(df: pd.DataFrame) -> int:
    """AO. Swap a curated company from occupation only when employer is a job."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    # Two company names are not a safe swap. That case needs a per-row
    # override because neither field tells us the person's actual role.
    emp_is_company = emp.str.contains(_COMPANY_NAME_RE, na=False)
    mask = is_indiv & occ.isin(KNOWN_COMPANY_OCCUPATIONS) & emp.ne('') & ~emp_is_company
    n_fixed = int(mask.sum())
    if n_fixed:
        _swap_occ_emp_fields(df, mask)
    return n_fixed
