"""Names filed in the wrong work field."""
from __future__ import annotations

import pandas as pd

from fec.cleaning._helpers import set_missing_occupation
from fec.cleaning.occupations.normalize import swap_employer_and_occupation
from fec.cleaning.safety_nets.employer_swaps import (
    COMPANY_NAME_RE,
    COMPANY_SUFFIX_RE,
    KNOWN_OCCUPATIONS,
)
from fec.cleaning.safety_nets.own_name import had_legal_suffix, is_own_name
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS
from fec.config.not_employers import JOB_TITLE_AS_EMPLOYER
from fec.config.occupation_rules.rules import (
    KNOWN_COMPANY_OCCUPATIONS,
)


# donor's full own name as employer: SELF-EMPLOYED
def fix_own_name_as_employer(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    hits = []
    for idx in df.index[mask]:
        if is_own_name(df, idx, emp.at[idx]) and not had_legal_suffix(df, idx):
            hits.append(idx)

    if hits:
        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'
    return len(hits)


# known employer in occupation moves; job unknown
def fix_company_name_as_occupation(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    se_mask = (
        is_indiv
        & (emp == 'SELF-EMPLOYED')
        & (occ != '')
        & ~occ.isin(SKIP_OCCUPATIONS)
        & ~occ.isin(KNOWN_OCCUPATIONS)
    )

    emp_counts = df.loc[is_indiv, 'contributor_employer'].value_counts()
    known_employers = set(emp_counts[emp_counts >= 10].index)

    occ_is_company = se_mask & occ.isin(known_employers)
    n_fixed = int(occ_is_company.sum())
    if not n_fixed:
        return 0

    df.loc[occ_is_company, 'contributor_employer'] = df.loc[occ_is_company, 'contributor_occupation']
    set_missing_occupation(df, occ_is_company)
    return n_fixed


# swap when employer is title, occupation company
def fix_swapped_emp_occ_company(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')

    emp_is_title = is_indiv & emp.isin(JOB_TITLE_AS_EMPLOYER)
    occ_has_company = occ.str.contains(COMPANY_SUFFIX_RE, na=False)
    mask = emp_is_title & occ_has_company
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    swap_employer_and_occupation(df, mask)
    return n_fixed


# curated occupation company swaps with non-company employer
def fix_occ_emp_both_swapped(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    # two company names are no safe swap
    emp_is_company = emp.str.contains(COMPANY_NAME_RE, na=False)
    mask = is_indiv & occ.isin(KNOWN_COMPANY_OCCUPATIONS) & emp.ne('') & ~emp_is_company
    n_fixed = int(mask.sum())
    if n_fixed:
        swap_employer_and_occupation(df, mask)
    return n_fixed
