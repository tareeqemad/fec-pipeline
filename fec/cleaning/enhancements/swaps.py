"""Occupation/employer swap detection and occupation canonicalization."""
import re

import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.cleaning.occupations import _categorize
from fec.config.occupation_rules import OCCUPATION_CANONICAL, OCCUPATION_KEYWORDS

# Both patterns run against _norm() output (already uppercase), so they are
# compiled without IGNORECASE on purpose.
_CORP_IN_OCC_RE = re.compile(
    # company-name markers: the occupation field holds a company (filer swapped emp and occ)
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bLTD\b'
    r'|\bCOMPANY\b|\bCORPORATION\b|\bHOLDINGS\b|\bGROUP\b'
    r'|\bPARTNERS\b|\bVENTURES\b|\bCAPITAL\b|\bFUND\b'
    r'|\bASSOCIATES\b|\bENTERPRISES\b|\bPROPERTIES\b|\bREALTY\b'
    r'|\bADVISORS\b|\bINSURANCE\b|\bINDUSTRIES\b|\bBROTHERS\b'
    r'|\bBANK\b|\bFINANCIAL\b|\bMEDIA\b|\bSYSTEMS\b'
    r'|\bTECHNOLOGIES\b|\bSOLUTIONS\b|\bSERVICES\b|\bMANAGEMENT\b'
    r'|\bTRUST\b|\bINTERNATIONAL\b|\bGLOBAL\b'
    # two-word "X & Y" firms (BROWN & BROWN, KIRKLAND & ELLIS)
    r'|\b\w+\s*&\s*\w+\b'
    r'|& (?:PARTNERS|ASSOCIATES|CRUTCHER|DE LLANO|BUTLER)',
)

_ORG_IN_OCC_RE = re.compile(
    # institutional words: the occupation field holds an organization name
    r'\bHOSPITAL\b|\bUNIVERSITY\b|\bINSTITUTE\b'
    r'|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bBUREAU\b'
    r'|\bDEPARTMENT\b|\bMINISTRY\b'
    # the tracked committees themselves: "AIPAC" typed as occupation is a swap candidate
    r'|\bAIPAC\b|\bDMFI\b',
)

def _swap_occ_emp(df: pd.DataFrame, idx: pd.Index) -> None:
    """Swap occupation and employer for the given index and re-categorize."""
    old_occ = df.loc[idx, 'contributor_occupation'].copy()
    old_emp = df.loc[idx, 'contributor_employer'].copy()
    df.loc[idx, 'contributor_occupation'] = old_emp
    df.loc[idx, 'contributor_employer'] = old_occ
    df.loc[idx, 'occupation_category'] = _categorize(df.loc[idx, 'contributor_occupation'])


def fix_remaining_swapped_occ_emp(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Fix occ/emp swaps the core pipeline missed; returns (df, n_swapped, n_same_fixed)."""
    indiv_idx = _indiv_idx(df)
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])

    # A) occ looks like company, emp looks like job title
    occ_is_corp = occ.str.contains(_CORP_IN_OCC_RE, na=False)
    emp_is_job = emp.isin(OCCUPATION_KEYWORDS)
    occ_starts_corp = occ.str.startswith('CORP ', na=False)
    swap_a = indiv_idx[occ_is_corp & emp_is_job & ~occ_starts_corp]
    if len(swap_a):
        _swap_occ_emp(df, swap_a)

    # B) occ == emp and both are job words: emp = SELF-EMPLOYED
    same_idx = indiv_idx[(occ == emp) & emp.isin(OCCUPATION_KEYWORDS)]
    n_same = len(same_idx)
    if n_same:
        df.loc[same_idx, 'contributor_employer'] = 'SELF-EMPLOYED'

    # C) occ is an institution, emp is a job: swap
    occ_is_org = occ.str.contains(_ORG_IN_OCC_RE, na=False)
    swap_c = indiv_idx[occ_is_org & emp_is_job]
    if len(swap_c):
        _swap_occ_emp(df, swap_c)

    n_swapped = len(swap_a) + len(swap_c)
    return df, n_swapped, n_same


def normalize_occupation_canonical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Map occupation variants to canonical short forms (safe, unambiguous only); returns (df, n_changed)."""
    indiv_idx = _indiv_idx(df)
    occ = df.loc[indiv_idx, 'contributor_occupation']
    hits = indiv_idx[occ.isin(OCCUPATION_CANONICAL)]
    n_changed = len(hits)
    if n_changed:
        df.loc[hits, 'contributor_occupation'] = occ[hits].map(OCCUPATION_CANONICAL)
    return df, n_changed
