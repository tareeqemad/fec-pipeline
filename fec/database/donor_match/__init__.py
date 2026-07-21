"""
donor_match — Smart donor deduplication with confidence scoring.

Instead of binary merge rules, each candidate pair gets a SCORE.
Only pairs scoring >= threshold (50) are merged.
"""

from .matcher import match_donors
from .output import (
    apply_donor_key, export_audit, merge_split_name_donors,
    apply_donor_dedup_merges,
    canonicalize_donor_names, canonicalize_donor_employers,
    canonicalize_donor_addresses, canonicalize_donor_addresses_geo,
    canonicalize_donor_pobox_typos, canonicalize_donor_units,
    align_org_donor_company_names,
    build_donor_dedup_review,
)
from .scoring import compute_score
from .profiles import build_profiles
from .cli import main

__all__ = [
    "match_donors",
    "apply_donor_key",
    "merge_split_name_donors",
    "apply_donor_dedup_merges",
    "canonicalize_donor_names",
    "canonicalize_donor_employers",
    "canonicalize_donor_addresses",
    "canonicalize_donor_addresses_geo",
    "canonicalize_donor_pobox_typos",
    "canonicalize_donor_units",
    "align_org_donor_company_names",
    "build_donor_dedup_review",
    "export_audit",
    "compute_score",
    "build_profiles",
    "main",
]
