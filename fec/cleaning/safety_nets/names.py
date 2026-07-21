"""cleaning/safety_nets/names.py — contributor-name safety-net fixes."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _fix_misclassified_foundation(df: pd.DataFrame) -> int:
    """X. 'FOUNDATION, WEINER MARC' etc. — last_name is FOUNDATION → COMMITTEE/PAC.

    The shape being caught is an ORGANISATION name typed into the person fields,
    which leaves the org's remaining words crammed into the given-name slot
    ("FOUNDATION, WEINER MARC" -> first='WEINER MARC').

    TRUST, FUND and SOCIETY are also ordinary surnames, so the entity word alone
    does NOT prove a misclassification — the same trap as the house rule that
    'NULL' is a real surname. Mark Trust of Atlanta was being reclassified to
    COMMITTEE/PAC and RENAMED to his employer field, which read 'NOT EMPLOYED';
    a real donor became a committee called "NOT EMPLOYED".

    Two guards, each of which alone would have saved that row:
      - the given-name slot must hold 2+ words (an org name doesn't fit in a
        single given name, but a real person's does)
      - the employer must be a real company before it is used as the entity
        name — a status word is not an organisation's name
    """
    from fec.cleaning.previous_employer import is_real_employer

    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_last_name'].fillna('').str.upper().isin(
            {'FOUNDATION', 'FUND', 'TRUST', 'ASSOCIATION', 'SOCIETY', 'INSTITUTE'}
        )
        & first.str.split().str.len().ge(2)
    )
    n = int(mask.sum())
    if not n:
        return 0

    df.loc[mask, 'entity_type'] = 'COMMITTEE/PAC'
    df.loc[mask, 'is_individual'] = False
    df.loc[mask, 'contributor_first_name'] = np.nan
    df.loc[mask, 'contributor_last_name'] = np.nan
    df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[mask, 'committee_type'] = 'ORGANIZATION'
    # Use employer as committee name — but only a REAL company name.
    emp = df.loc[mask, 'contributor_employer']
    has_emp = mask & emp.notna() & (emp.str.len() > 3) & emp.map(is_real_employer)
    df.loc[has_emp, 'contributor_name'] = emp[has_emp].str.upper()
    return n


def _fix_title_as_first_name(df: pd.DataFrame) -> int:
    """Y2. Fix titles/honorifics parsed as first names.
    e.g. 'PECK, MD' → first_name was MD (doctor title, not a name).
         'MOND, MRS' → first_name was MRS (honorific, not a name).
    Clears first_name and fixes contributor_name to 'LAST, FIRST' format
    when the real first name can be found from same donor's other records."""
    _TITLES = {'MR', 'MRS', 'MS', 'DR', 'MD', 'ESQ', 'JR', 'SR',
               'II', 'III', 'IV', 'PHD', 'DDS', 'DO', 'RN', 'CPA'}
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    fn = df['contributor_first_name'].fillna('')

    # Case 1: first_name is empty but contributor_name has a title
    empty_fn = is_indiv & (df['contributor_first_name'].isna() | fn.eq(''))
    name = df.loc[empty_fn, 'contributor_name'].fillna('')
    # Extract what's after the comma
    after_comma = name.str.split(',', n=1).str[1].str.strip()
    is_title = empty_fn & after_comma.reindex(df.index, fill_value='').isin(_TITLES)

    # Case 2: first_name IS a title (parsed incorrectly)
    fn_is_title = is_indiv & fn.isin(_TITLES) & fn.ne('')

    mask = is_title | fn_is_title
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_first_name'] = np.nan
    return n


def _fix_choose_prefix(df: pd.DataFrame) -> int:
    """AC. Remove '--CHOOSE--' prefix from employer (web form junk).
    e.g. '--CHOOSE--AEI' -> 'AEI', '--CHOOSE--' -> NaN."""
    emp = df['contributor_employer'].fillna('')
    mask = emp.str.startswith('--CHOOSE--')
    n = int(mask.sum())
    if n:
        cleaned = emp[mask].str.replace(r'^--CHOOSE--', '', regex=True).str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return n
