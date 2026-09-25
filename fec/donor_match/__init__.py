"""Donor identity resolution: decide who is who, then make each identity consistent.

The flow (clean.py -> identify_donors -> here):

    matcher.match_donors
        build_profiles (matcher)      one profile per name/location/suffix
        five pair phases (phases)     candidate pairs -> compute_score (scoring)
        chains + force merges (matcher)
        -> donor_key per cluster
    keys.apply_donor_key + verified identity rules
    canonicalize (+ name_choice)      one official name per donor
    canonical_employers               one employer name per donor
    canonical_addresses               one street/unit/PO Box per donor
    keys.build_donor_dedup_review     detect-only report for human triage

constants.py holds matching weights and nickname rules. Every human identity
decision lives in data/database/donor_identity_rules.csv.
"""

from fec.donor_match.canonical_addresses import (
    canonicalize_donor_addresses,
    canonicalize_donor_pobox_typos,
    canonicalize_donor_units,
)
from fec.donor_match.canonical_employers import (
    align_org_donor_company_names,
    canonicalize_donor_employers,
)
from fec.donor_match.canonicalize import canonicalize_donor_names
from fec.donor_match.dedup_review import build_donor_dedup_review
from fec.donor_match.keys import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    merge_split_name_donors,
    validate_separations,
)
from fec.donor_match.matcher import match_donors
from fec.donor_match.scoring import compute_score

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
