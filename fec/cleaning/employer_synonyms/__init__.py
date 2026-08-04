"""Employer name normalization and synonym merging."""
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS
from fec.cleaning.employer_synonyms.normalize import (
    normalize_employer_canonical,
    normalize_employer_display_name,
    restyle_legal_suffix,
)
from fec.cleaning.employer_synonyms.apply import (
    apply_employer_synonyms,
    expand_employer_abbreviations,
    expand_employer_associates,
    fix_occupation_as_employer,
    fix_normalized_mid_suffix,
)
from fec.cleaning.employer_synonyms.canonical import (
    canonical_key,
    restore_display_suffixes,
    _recanonicalize_employers,
)

__all__ = [
    "EMPLOYER_SYNONYMS",
    "normalize_employer_canonical",
    "normalize_employer_display_name",
    "restyle_legal_suffix",
    "apply_employer_synonyms",
    "expand_employer_abbreviations",
    "expand_employer_associates",
    "fix_occupation_as_employer",
    "fix_normalized_mid_suffix",
    "canonical_key",
    "restore_display_suffixes",
    "_recanonicalize_employers",
]
