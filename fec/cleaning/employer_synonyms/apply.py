"""Apply synonym merges, abbreviation expansion, and employer-field fixes."""
import re

import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.cleaning.occupations import _categorize
from fec.config.constants import LEGAL_SUFFIX_RE, SKIP_EMPLOYERS
from fec.config.employers import EMPLOYER_ABBREVIATIONS

from fec.cleaning.employer_synonyms.normalize import restyle_legal_suffix
from fec.config.constants import OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS

# Occupation words filed as the employer: these people are self-employed.
# A deliberate subset of the config lists (the full lists are applied by the safety nets later).
_OCC_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'PHYSICIAN', 'CONSULTANT', 'PROFESSOR',
    'DENTIST', 'ACCOUNTANT', 'TEACHER', 'ENGINEER', 'REALTOR',
    'INVESTOR',
})
assert _OCC_AS_EMPLOYER <= (OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER), 'keep _OCC_AS_EMPLOYER inside the config lists'


def apply_employer_synonyms(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Merge employer variants into canonical forms (donor-overlap verified only); returns (df, n_fixed)."""
    indiv_idx = _indiv_idx(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    hits = indiv_idx[emp.isin(EMPLOYER_SYNONYMS)]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'contributor_employer'] = emp[hits].map(EMPLOYER_SYNONYMS)

    # suffix style only (", INC" -> " INC"); never strips a suffix
    emp = df.loc[indiv_idx, 'contributor_employer'].dropna()
    restyled = emp.map(restyle_legal_suffix)
    changed = restyled != emp
    if changed.any():
        df.loc[changed[changed].index, 'contributor_employer'] = restyled[changed]
        n_fixed += int(changed.sum())

    return df, n_fixed


# Whole-word token + optional trailing period, derived from THE abbreviation
# table; tokens without a verified expansion (e.g. the ambiguous ASSOC,
# handled contextually in expand_employer_associates) are never auto-expanded.
_EMPLOYER_ABBREV = [
    (re.compile(rf'\b{token}\b\.?'), expansion)
    for token, expansion in EMPLOYER_ABBREVIATIONS.items() if expansion
]


def expand_employer_abbreviations(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Expand unambiguous abbreviations (MGMT -> MANAGEMENT, ...); must run LAST, after synonyms, so abbreviations a synonym target reintroduces get expanded too."""
    indiv_idx = _indiv_idx(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    has_emp = emp.notna() & ~emp.isin(SKIP_EMPLOYERS)
    target = indiv_idx[has_emp]
    if len(target) == 0:
        return df, 0

    vals = emp[has_emp]
    new = vals
    for regex, replacement in _EMPLOYER_ABBREV:
        new = new.str.replace(regex, replacement, regex=True)
    new = new.str.replace(r'\s+', ' ', regex=True).str.strip().str.rstrip('.,').str.strip()

    changed = new != vals
    if changed.any():
        df.loc[target[changed], 'contributor_employer'] = new[changed]
    return df, int(changed.sum())


_ASSOC_RX = re.compile(r'\bASSOCS?\b\.?')
_ASSOCIATION_RX = re.compile(r'\bASSOCIATION\b')


def expand_employer_associates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Expand ASSOC contextually: real associations -> ASSOCIATION, everything else -> ASSOCIATES; checks the as-filed original too so truncated '...ASSOC' is caught."""
    indiv_idx = _indiv_idx(df)
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


def finalize_employer_names(df: pd.DataFrame, trail=None) -> tuple[pd.DataFrame, int]:
    """Reapply employer rules after donor-history repairs."""
    from fec.cleaning.audit_trail import EMPLOYMENT_FIELDS, AuditTrail

    from .canonical import _recanonicalize_employers

    trail = trail or AuditTrail()
    total = 0
    for fix, step, reason in (
        (apply_employer_synonyms, 'final_employer_synonyms', 'verified_same_company'),
        (expand_employer_abbreviations, 'final_employer_abbreviations', 'employer_abbreviation_expanded'),
        (expand_employer_associates, 'final_employer_assoc', 'employer_assoc_expanded'),
    ):
        df, changed = trail.run(df, fix, step, reason, ('contributor_employer',))
        total += changed
    total += trail.run(
        df, _recanonicalize_employers, 'final_employer_recanonicalize',
        'employer_variant_unified_by_canonical_key', EMPLOYMENT_FIELDS,
    )
    return df, total


def fix_occupation_as_employer(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Set employer to SELF-EMPLOYED when it is an occupation word and the person has a different real occupation; returns (df, n_fixed)."""
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])

    is_occ_emp = emp.isin(_OCC_AS_EMPLOYER)
    has_diff_occ = (occ != '') & (occ != emp)
    # don't touch RETIRED people - "ATTORNEY" might be their previous employer
    not_retired = ~occ.isin({'RETIRED', 'RETIRE', 'RETIREE'})

    hits = indiv_idx[is_occ_emp & has_diff_occ & not_retired]
    n_fixed = len(hits)

    if n_fixed:
        # move the employer word to occupation if current occ is SELF-EMPLOYED
        self_emp_idx = hits[occ[hits] == 'SELF-EMPLOYED']
        if len(self_emp_idx):
            df.loc[self_emp_idx, 'contributor_occupation'] = df.loc[self_emp_idx, 'contributor_employer']
            df.loc[self_emp_idx, 'occupation_category'] = _categorize(
                df.loc[self_emp_idx, 'contributor_occupation']
            )

        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'

    return df, n_fixed


_MID_SUFFIX_RE = re.compile(
    r'\b(?:LLC|LLP|INC\.?|CORP\.?|LTD\.?)[\s,./]+',
    re.IGNORECASE,
)


def fix_normalized_mid_suffix(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip LLC/LLP/INC appearing mid-string in employer_name_normalized (and mirror into contributor_employer); returns (df, n_fixed)."""
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
