"""Normalize employer names and fix misplaced occupations."""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _norm
from fec.cleaning.reports.trail import EMPLOYMENT_FIELDS, AuditTrail
from fec.cleaning.work.employer_canonical import tidy_employer_commas
from fec.cleaning.work.employer_aliases import (
    apply_reviewed_employer_names, preserved_employer_name, reviewed_employer_name,
)
from fec.cleaning.work.normalize import _categorize
from fec.config.not_employers import (
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
)

# Only these occupation words qualify for the SELF-EMPLOYED rule below.
_OCC_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'PHYSICIAN', 'CONSULTANT', 'PROFESSOR',
    'DENTIST', 'ACCOUNTANT', 'TEACHER', 'ENGINEER', 'REALTOR',
    'INVESTOR',
})
assert _OCC_AS_EMPLOYER <= (OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER), 'keep _OCC_AS_EMPLOYER inside the config lists'


def apply_employer_synonyms(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply explicit company rules to existing names."""
    return df, apply_reviewed_employer_names(df, ("contributor_employer",))


def expand_abbreviation_text(values: pd.Series) -> pd.Series:
    """Only reviewed full company names expand an abbreviation."""
    return values.map(reviewed_employer_name)


_LOOSE_SLASH_RE = re.compile(r'\s/|/\s|//')


def tidy_employer_slashes(name):
    """Normalize loose slashes; leave tight slashes like BRIDGESTONE/FIRESTONE alone."""
    if not isinstance(name, str) or not _LOOSE_SLASH_RE.search(name) or preserved_employer_name(name):
        return name
    return re.sub(r'\s*/+\s*', ' / ', name).strip()


def tidy_slash_spacing(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Tidy slash and comma spacing in individuals' employer names."""
    indiv_idx = _indiv_idx(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    tidied = emp.map(tidy_employer_slashes).map(tidy_employer_commas)
    changed = emp.notna() & (tidied != emp)
    if changed.any():
        df.loc[changed[changed].index, 'contributor_employer'] = tidied[changed]
    return df, int(changed.sum())


def finalize_employer_names(df: pd.DataFrame, trail=None) -> pd.DataFrame:
    """Reapply employer rules after donor-history repairs."""

    trail = trail or AuditTrail()
    for fix, step, reason in (
        (tidy_slash_spacing, 'final_employer_slash_spacing', 'slash_spacing_unified'),
        (apply_employer_synonyms, 'final_employer_synonyms', 'verified_same_company'),
    ):
        df, _ = trail.run(df, fix, step, reason, ('contributor_employer',))
    trail.run(
        df, apply_reviewed_employer_names, 'final_employer_recanonicalize',
        'documented_company_alias', EMPLOYMENT_FIELDS,
    )
    return df


def fix_occupation_as_employer(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """SELF-EMPLOYED when the employer is a different occupation."""
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])

    is_occ_emp = emp.isin(_OCC_AS_EMPLOYER)
    has_diff_occ = (occ != '') & (occ != emp)
    # Skip RETIRED people: ATTORNEY may be their previous employer.
    not_retired = ~occ.isin({'RETIRED', 'RETIRE', 'RETIREE'})

    hits = indiv_idx[is_occ_emp & has_diff_occ & not_retired]
    n_fixed = len(hits)

    if n_fixed:
        # Move the employer word to occupation if occupation is SELF-EMPLOYED.
        self_emp_idx = hits[occ[hits] == 'SELF-EMPLOYED']
        if len(self_emp_idx):
            df.loc[self_emp_idx, 'contributor_occupation'] = df.loc[self_emp_idx, 'contributor_employer']
            df.loc[self_emp_idx, 'occupation_category'] = _categorize(
                df.loc[self_emp_idx, 'contributor_occupation']
            )

        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'

    return df, n_fixed
