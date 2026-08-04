"""Cross-fill and swap fixes between the occupation and employer fields."""
import re

import pandas as pd

from fec.config.occupation_rules import (
    EMPLOYER_FROM_OCCUPATION,
    OCCUPATION_FROM_EMPLOYER,
    SWAP_JOB_TITLES,
)

from .normalize import _categorize

_CORP_NAME_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bP\.?A\.?\s*$'
    r'|\bPARTNERS\b|\bGROUP\b|\bASSOCIATES\b|\bVENTURES\b'
    r'|\bHOLDINGS\b|\bSERVICES\b|\bENTERPRISES\b'
    r'|\bUNIVERSITY\b|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bHOSPITAL\b|\bINSTITUTE\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bDEPARTMENT\b|\bBUREAU\b',
    re.I,
)

def _cross_fill(df: pd.DataFrame) -> None:
    """Fill occupation from employer (and vice versa) when the answer is obvious."""
    for employer_val, (occ_val, cat_val) in OCCUPATION_FROM_EMPLOYER.items():
        mask = (
            df['is_individual']
            & df['contributor_occupation'].isna()
            & (df['contributor_employer'] == employer_val)
        )
        df.loc[mask, 'contributor_occupation'] = occ_val
        df.loc[mask, 'occupation_category'] = cat_val
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'

    has_occ_no_emp = df['contributor_occupation'].notna() & df['contributor_employer'].isna()
    if has_occ_no_emp.any():
        # unmapped stay NaN: don't fill NOT DISCLOSED prematurely, the AI classifier reads this field
        df.loc[has_occ_no_emp, 'contributor_employer'] = (
            df.loc[has_occ_no_emp, 'contributor_occupation']
            .map(EMPLOYER_FROM_OCCUPATION)
        )


def _fix_swapped_occ_emp(df: pd.DataFrame) -> None:
    """Swap back rows where occupation holds a company name and employer holds a job title."""
    has_both = df['contributor_occupation'].notna() & df['contributor_employer'].notna()

    occ_is_corp = df['contributor_occupation'].str.contains(_CORP_NAME_RE, na=False)
    emp_is_job = df['contributor_employer'].isin(SWAP_JOB_TITLES)

    swap_mask = has_both & occ_is_corp & emp_is_job
    if swap_mask.any():
        old_occ = df.loc[swap_mask, 'contributor_occupation'].copy()
        old_emp = df.loc[swap_mask, 'contributor_employer'].copy()
        df.loc[swap_mask, 'contributor_occupation'] = old_emp
        df.loc[swap_mask, 'contributor_employer'] = old_occ

        df.loc[swap_mask, 'occupation_category'] = _categorize(
            df.loc[swap_mask, 'contributor_occupation']
        )
