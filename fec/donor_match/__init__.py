"""Donor deduplication: each candidate pair gets a score, merged only at >= threshold."""

from .matcher import match_donors
from .keys import (
    apply_donor_key, merge_split_name_donors,
    apply_donor_dedup_merges,
)
from .canonicalize import (
    canonicalize_donor_names, canonicalize_donor_employers,
    align_org_donor_company_names,
    canonicalize_donor_addresses,
    canonicalize_donor_pobox_typos, canonicalize_donor_units,
)
from .reports import build_donor_dedup_review
from .scoring import compute_score

__all__ = [
    "match_donors",
    "apply_donor_key",
    "merge_split_name_donors",
    "apply_donor_dedup_merges",
    "canonicalize_donor_names",
    "canonicalize_donor_employers",
    "canonicalize_donor_addresses",
    "canonicalize_donor_pobox_typos",
    "canonicalize_donor_units",
    "align_org_donor_company_names",
    "build_donor_dedup_review",
    "compute_score",
]
