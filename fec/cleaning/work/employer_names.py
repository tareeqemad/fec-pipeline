"""Normalize employer names and fix misplaced occupations."""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx
from fec.cleaning.reports.trail import EMPLOYMENT_FIELDS, AuditTrail
from fec.cleaning.work.employer_canonical import tidy_employer_commas
from fec.cleaning.work.employer_aliases import (
    apply_reviewed_employer_names, preserved_employer_name, reviewed_employer_name,
)

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
    ):
        df, _ = trail.run(df, fix, step, reason, ('contributor_employer',))
    trail.run(
        df, apply_reviewed_employer_names, 'final_employer_recanonicalize',
        'documented_company_alias', EMPLOYMENT_FIELDS,
    )
    return df
