"""Donor identity resolution: decide who is who, then make each identity consistent.

The flow (clean.py -> identify_donors -> here):

    matcher.match_donors
        build_profiles (matcher)      one profile per name/location/suffix
        five pair phases (phases)     candidate pairs -> compute_score (scoring)
        chains + force merges (matcher)
        -> donor_key per cluster
    keys.apply_donor_key + verified identity rules
    canonicalize.*                    one official name/employer/street per donor
    keys.build_donor_dedup_review     detect-only report for human triage

constants.py holds matching weights and nickname rules. Every human identity
decision lives in data/database/donor_identity_rules.csv.
"""

from .matcher import match_donors
from .keys import (
    apply_donor_key, merge_split_name_donors, validate_separations,
    apply_curated_key_merges, build_donor_dedup_review, hold_unproven_filings,
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
    "validate_separations",
    "apply_curated_key_merges",
    "hold_unproven_filings",
    "canonicalize_donor_names",
    "canonicalize_donor_employers",
    "canonicalize_donor_addresses",
    "canonicalize_donor_pobox_typos",
    "canonicalize_donor_units",
    "align_org_donor_company_names",
    "build_donor_dedup_review",
    "compute_score",
]
