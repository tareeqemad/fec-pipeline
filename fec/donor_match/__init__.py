"""Donor identity resolution: decide who is who, then make each identity consistent.

The flow (clean.py -> unify_donors -> here):

    matcher.match_donors
        build_profiles (matcher)      one profile per NAME|CITY|STATE record
        five pair phases (phases)     candidate pairs -> compute_score (scoring)
        chains + force merges (matcher)
        -> donor_key per cluster
    keys.apply_donor_key + key-level merges + curated dedup merges
    canonicalize.*                    one official name/employer/street per donor
    keys.build_donor_dedup_review     detect-only report for human triage

    after geocoding (called from geocode.py):
    canonicalize.canonicalize_donor_addresses_geo

constants.py holds every weight (documented), the nickname map, and the
look-alike trap pairs; the curated CSVs under data/database/ carry the
human merge/block decisions.
"""

from .matcher import match_donors
from .keys import (
    apply_donor_key, merge_split_name_donors,
    apply_donor_dedup_merges, build_donor_dedup_review,
)
from .canonicalize import (
    canonicalize_donor_names, canonicalize_donor_employers,
    align_org_donor_company_names,
    canonicalize_donor_addresses,
    canonicalize_donor_pobox_typos, canonicalize_donor_units,
)
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
