"""Synonym merges, abbreviation expansion, employer-field fixes."""
import re

import pandas as pd

from fec.cleaning._helpers import individual_index, upper_text
from fec.cleaning.audit_trail import EMPLOYMENT_FIELDS, AuditTrail
from fec.cleaning.employer_synonyms.canonical import (
    recanonicalize_employers,
    tidy_employer_commas,
)
from fec.cleaning.employer_synonyms.normalize import restyle_legal_suffix
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS
from fec.cleaning.occupations.normalize import categorize_occupation
from fec.config.constants import SKIP_EMPLOYERS
from fec.config.employers import EMPLOYER_ABBREVIATIONS
from fec.config.not_employers import (
    LEGAL_SUFFIX_RE,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
)

# occupation-as-employer words; deliberate subset of config
_OCC_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'PHYSICIAN', 'CONSULTANT', 'PROFESSOR',
    'DENTIST', 'ACCOUNTANT', 'TEACHER', 'ENGINEER', 'REALTOR',
    'INVESTOR',
})
assert _OCC_AS_EMPLOYER <= (OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER), 'keep _OCC_AS_EMPLOYER inside the config lists'


# merge verified employer variants into canonical forms
def apply_employer_synonyms(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    hits = indiv_idx[emp.isin(EMPLOYER_SYNONYMS)]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'contributor_employer'] = emp[hits].map(EMPLOYER_SYNONYMS)

    # restyle suffixes only; never strips one
    emp = df.loc[indiv_idx, 'contributor_employer'].dropna()
    restyled = emp.map(restyle_legal_suffix)
    changed = restyled != emp
    if changed.any():
        df.loc[changed[changed].index, 'contributor_employer'] = restyled[changed]
        n_fixed += int(changed.sum())

    return df, n_fixed


# tokens without an expansion are never expanded
_EMPLOYER_ABBREV = [
    (re.compile(rf'\b{token}\b\.?'), expansion)
    for token, expansion in EMPLOYER_ABBREVIATIONS.items() if expansion
]


# expand verified abbreviations in employer names
def expand_abbreviation_text(values: pd.Series) -> pd.Series:
    new = values
    for regex, replacement in _EMPLOYER_ABBREV:
        new = new.str.replace(regex, replacement, regex=True)
    return new.str.replace(r'\s+', ' ', regex=True).str.strip().str.rstrip('.,').str.strip()


# expand unambiguous employer abbreviations, must run last
def expand_employer_abbreviations(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    has_emp = emp.notna() & ~emp.isin(SKIP_EMPLOYERS)
    target = indiv_idx[has_emp]
    if len(target) == 0:
        return df, 0

    vals = emp[has_emp]
    new = expand_abbreviation_text(vals)

    changed = new != vals
    if changed.any():
        df.loc[target[changed], 'contributor_employer'] = new[changed]
    return df, int(changed.sum())


# ASSOC or ASSOCS, optional trailing period
_ASSOC_RX = re.compile(r'\bASSOCS?\b\.?')
# the full word ASSOCIATION, whole token
_ASSOCIATION_RX = re.compile(r'\bASSOCIATION\b')


# expand ASSOC by context, checking filed original
def expand_employer_associates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    has_assoc = (emp.notna() & ~emp.isin(SKIP_EMPLOYERS)
           & emp.str.upper().str.contains(r'\bASSOCS?\b', regex=True, na=False))
    target = indiv_idx[has_assoc]
    if len(target) == 0:
        return df, 0

    current = emp[has_assoc].str.upper()
    if 'contributor_employer_original' in df.columns:
        original = df.loc[target, 'contributor_employer_original'].astype(str).str.upper()
    else:
        original = current
    is_association = (current.str.contains(_ASSOCIATION_RX, na=False)
                      | original.str.contains(_ASSOCIATION_RX, na=False)
                      | current.str.match(r'ASSOC\s+FOR\b'))

    as_association = current.str.replace(_ASSOC_RX, 'ASSOCIATION', regex=True)
    as_associates = current.str.replace(_ASSOC_RX, 'ASSOCIATES', regex=True)
    new = as_association.where(is_association, as_associates)
    new = new.str.replace(r'\s+', ' ', regex=True).str.strip().str.rstrip('.,').str.strip()

    changed = new != emp[has_assoc]
    if changed.any():
        df.loc[target[changed], 'contributor_employer'] = new[changed]
    return df, int(changed.sum())


# slash with uneven spacing, like "A//B"
_LOOSE_SLASH_RE = re.compile(r'\s/|/\s|//')


# space out loose slashes; tight ones stay
def tidy_employer_slashes(name):
    if not isinstance(name, str) or not _LOOSE_SLASH_RE.search(name):
        return name
    return re.sub(r'\s*/+\s*', ' / ', name).strip()


# tidy slash and comma spacing in employers
def tidy_slash_spacing(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    tidied = emp.map(tidy_employer_slashes).map(tidy_employer_commas)
    changed = emp.notna() & (tidied != emp)
    if changed.any():
        df.loc[changed[changed].index, 'contributor_employer'] = tidied[changed]
    return df, int(changed.sum())


# rerun employer normalization steps after donor-history repairs
def finalize_employer_names(df: pd.DataFrame, trail=None) -> tuple[pd.DataFrame, int]:
    trail = trail or AuditTrail()
    total = 0
    for fix, step, reason in (
        (tidy_slash_spacing, 'final_employer_slash_spacing', 'slash_spacing_unified'),
        (apply_employer_synonyms, 'final_employer_synonyms', 'verified_same_company'),
        (expand_employer_abbreviations, 'final_employer_abbreviations', 'employer_abbreviation_expanded'),
        (expand_employer_associates, 'final_employer_assoc', 'employer_assoc_expanded'),
    ):
        df, changed = trail.run(df, fix, step, reason, ('contributor_employer',))
        total += changed
    total += trail.run(
        df, recanonicalize_employers, 'final_employer_recanonicalize',
        'employer_variant_unified_by_canonical_key', EMPLOYMENT_FIELDS,
    )
    return df, total


# occupation word as employer means self-employed
def fix_occupation_as_employer(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = upper_text(df.loc[indiv_idx, 'contributor_employer'])
    occ = upper_text(df.loc[indiv_idx, 'contributor_occupation'])

    is_occ_emp = emp.isin(_OCC_AS_EMPLOYER)
    has_diff_occ = (occ != '') & (occ != emp)
    # retirees: the word may be past employer
    not_retired = ~occ.isin({'RETIRED', 'RETIRE', 'RETIREE'})

    hits = indiv_idx[is_occ_emp & has_diff_occ & not_retired]
    n_fixed = len(hits)

    if n_fixed:
        # SELF-EMPLOYED occupation takes the employer word
        self_emp_idx = hits[occ[hits] == 'SELF-EMPLOYED']
        if len(self_emp_idx):
            df.loc[self_emp_idx, 'contributor_occupation'] = df.loc[self_emp_idx, 'contributor_employer']
            df.loc[self_emp_idx, 'occupation_category'] = categorize_occupation(
                df.loc[self_emp_idx, 'contributor_occupation']
            )

        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'

    return df, n_fixed


# legal suffix mid-string, like "SMITH LLC, JONES"
_MID_SUFFIX_RE = re.compile(
    r'\b(?:LLC|LLP|INC\.?|CORP\.?|LTD\.?)[\s,./]+',
    re.IGNORECASE,
)


# strip mid-string legal suffixes; mirror to employer
def fix_normalized_mid_suffix(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    col = 'employer_name_normalized'
    vals = df[col].fillna('')
    has_mid = vals.str.contains(_MID_SUFFIX_RE, na=False)
    target = df.index[has_mid]

    if len(target) == 0:
        return df, 0

    cleaned = vals[has_mid].str.replace(_MID_SUFFIX_RE, ' ', regex=True)
    cleaned = cleaned.str.replace(LEGAL_SUFFIX_RE, '', regex=True)
    cleaned = cleaned.str.replace(r'\s+', ' ', regex=True).str.strip()

    changed = cleaned != vals[has_mid]
    n_fixed = int(changed.sum())
    if n_fixed:
        df.loc[target[changed], col] = cleaned[changed]
        df.loc[target[changed], 'contributor_employer'] = cleaned[changed]

    return df, n_fixed
