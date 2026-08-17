"""Donor standardization stage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fec.cleaning.audit_trail import NAME_FIELDS, STREET_FIELDS, AuditTrail
from fec.log import get_logger, log_count

logger = get_logger(__name__)


def _canonicalize(df: pd.DataFrame, trail: AuditTrail) -> int:
    from fec.donor_match import (
        align_org_donor_company_names,
        canonicalize_donor_addresses,
        canonicalize_donor_employers,
        canonicalize_donor_names,
        canonicalize_donor_pobox_typos,
        canonicalize_donor_units,
    )

    steps = (
        (canonicalize_donor_names, "donor_canonical_names",
         "donor_name_unified_to_modal_last_and_longest_first", NAME_FIELDS),
        (canonicalize_donor_employers, "donor_canonical_employers",
         "donor_employer_variant_merged_to_fullest_name", ("contributor_employer",)),
        (align_org_donor_company_names, "donor_align_org_names",
         "organization_name_aligned_to_employer_spelling", NAME_FIELDS),
        (canonicalize_donor_addresses, "donor_canonical_addresses",
         "donor_street_spelling_unified", STREET_FIELDS),
        (canonicalize_donor_units, "donor_canonical_units",
         "donor_unit_designator_unified", STREET_FIELDS),
        (canonicalize_donor_pobox_typos, "donor_pobox_typos",
         "donor_pobox_digit_typo_fixed_to_majority", STREET_FIELDS),
    )
    total = 0
    for fix, step, reason, fields in steps:
        count = trail.run(df, fix, step, reason, fields)
        total += count
        log_count(logger, fix.__name__.lstrip("_").replace("_", " "), count)
    return total


def _finalize_employers(df: pd.DataFrame, trail: AuditTrail) -> tuple[pd.DataFrame, int]:
    from fec.cleaning.employer_synonyms import finalize_employer_names
    from fec.donor_match import canonicalize_donor_employers

    df, total = finalize_employer_names(df, trail)
    log_count(logger, "final employer names", total)
    final_canonical = trail.run(
        df, canonicalize_donor_employers, "final_donor_employers",
        "donor_employer_variant_merged_to_fullest_name", ("contributor_employer",),
    )
    log_count(logger, "final donor employers", final_canonical)
    return df, total + final_canonical


def _clear_non_individual_names(df: pd.DataFrame) -> int:
    name_cols = [
        column
        for column in ("contributor_first_name", "contributor_last_name")
        if column in df.columns
    ]
    if "entity_type" not in df.columns or not name_cols:
        return 0
    mask = (df["entity_type"] != "INDIVIDUAL") & df[name_cols].notna().any(axis=1)
    if mask.any():
        df.loc[mask, name_cols] = np.nan
    return int(mask.sum())


def standardize(df: pd.DataFrame, out_dir, trail: AuditTrail) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    df = df.reset_index(drop=True)
    logger.info("\n-- Donor consistency --")
    from fec.cleaning.donor_consistency import apply_donor_consistency
    from fec.cleaning.entity_classification import apply_name_corrections
    from fec.cleaning.manual_overrides import apply_manual_employer_overrides
    from fec.donor_match import build_donor_dedup_review

    canonical_updates = _canonicalize(df, trail)

    df, manual_updates = trail.run(
        df, apply_name_corrections, "curated_name_corrections", "manual_correction", NAME_FIELDS,
        evidence="fec/cleaning/name_rules.py",
    )
    log_count(logger, "curated name corrections", manual_updates)

    consistency_updates = apply_donor_consistency(df, trail)

    df, employer_updates = _finalize_employers(df, trail)
    canonical_updates += employer_updates

    protected = trail.run(
        df, lambda frame: apply_manual_employer_overrides(frame, company_names_only=True),
        "curated_employer_names", "curated_row_override", ("contributor_employer", "previous_employer"),
        evidence="data/manual_employer_overrides.csv",
    )
    log_count(logger, "curated employer names", protected)
    canonical_updates += protected
    build_donor_dedup_review(df, out_dir)

    cleared = trail.run(
        df, _clear_non_individual_names, "non_individual_names",
        "non_individual_person_names_cleared", NAME_FIELDS,
    )
    log_count(logger, "non-individual names cleared", cleared)

    logger.info(
        "  %s canonical updates, %s consistency updates",
        f"{canonical_updates + manual_updates + cleared:,}",
        f"{consistency_updates:,}",
    )
    return df
