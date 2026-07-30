"""Contributor-name safety-net fixes."""
from __future__ import annotations

import numpy as np
import pandas as pd

_ORG_SURNAMES = frozenset({'FOUNDATION', 'FUND', 'TRUST', 'ASSOCIATION', 'SOCIETY', 'INSTITUTE'})

_TITLES = frozenset({'MR', 'MRS', 'MS', 'DR', 'MD', 'ESQ', 'JR', 'SR',
                     'II', 'III', 'IV', 'PHD', 'DDS', 'DO', 'RN', 'CPA'})


def _fix_misclassified_foundation(df: pd.DataFrame) -> int:
    """X. Org name typed into the person fields (last_name FOUNDATION/FUND/TRUST...) -> COMMITTEE/PAC."""
    from fec.cleaning.previous_employer import is_real_employer

    # TRUST, FUND, SOCIETY are also ordinary surnames, so two guards are required:
    # the given-name slot must hold 2+ words, and the employer must be a real
    # company before it is used as the entity name (a status word is not a name)
    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_last_name'].fillna('').str.upper().isin(_ORG_SURNAMES)
        & first.str.split().str.len().ge(2)
    )
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    df.loc[mask, 'entity_type'] = 'COMMITTEE/PAC'
    df.loc[mask, 'is_individual'] = False
    df.loc[mask, 'contributor_first_name'] = np.nan
    df.loc[mask, 'contributor_last_name'] = np.nan
    df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[mask, 'committee_type'] = 'ORGANIZATION'
    # use employer as committee name, but only a REAL company name
    emp = df.loc[mask, 'contributor_employer']
    has_emp = mask & emp.notna() & (emp.str.len() > 3) & emp.map(is_real_employer)
    df.loc[has_emp, 'contributor_name'] = emp[has_emp].str.upper()
    return n_fixed


def _fix_title_as_first_name(df: pd.DataFrame) -> int:
    """Y2. Titles/honorifics parsed as first names ('PECK, MD' / 'MOND, MRS') are cleared."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    first_names = df['contributor_first_name'].fillna('')

    # case 1: first_name is empty but contributor_name has a title after the comma
    empty_first = is_indiv & first_names.eq('')
    name = df.loc[empty_first, 'contributor_name'].fillna('')
    after_comma = name.str.split(',', n=1).str[1].str.strip()
    is_title = empty_first & after_comma.reindex(df.index, fill_value='').isin(_TITLES)

    # case 2: first_name IS a title (parsed incorrectly)
    first_is_title = is_indiv & first_names.isin(_TITLES)

    mask = is_title | first_is_title
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_first_name'] = np.nan
    return n_fixed


def _fix_choose_prefix(df: pd.DataFrame) -> int:
    """AC. Remove web-form '--CHOOSE--' prefix from employer ('--CHOOSE--AEI' -> 'AEI', bare '--CHOOSE--' -> NaN)."""
    emp = df['contributor_employer'].fillna('')
    mask = emp.str.startswith('--CHOOSE--')
    n_fixed = int(mask.sum())
    if n_fixed:
        cleaned = emp[mask].str.replace(r'^--CHOOSE--', '', regex=True).str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return n_fixed
