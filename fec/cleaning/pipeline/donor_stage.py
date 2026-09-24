"""Donor standardization stage."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.audit_trail import (
    NAME_FIELDS,
    PEOPLE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)
from fec.log import get_logger, log_count

logger = get_logger(__name__)

_NETWORK_NAME_RE = re.compile(
    r"^(?:POLITICAL NETWORK,\s*(?P<region>.+)|(?P<leading>.+?)\s+POLITICAL NETWORK)$",
    re.IGNORECASE,
)
def _classify_network_organizations(df: pd.DataFrame) -> int:
    """Keep a filing under a network name as its own unresolved donor, never a person's.

    The name ("POLITICAL NETWORK, LA VALLEY") says nothing about who gave: an
    address and job matching a known donor is not proof (a $7,000 filing went
    to DE TOLEDO, PHILIP and a $500 one to COMANOR, WILLIAM that way), and two
    filings under one network name are not proof of one donor. Each filing gets
    its own key, outside every person's total.
    """
    from fec.donor_match.keys import non_individual_donor_key

    names = df["contributor_name"].fillna("").astype(str)
    matches = names.str.extract(_NETWORK_NAME_RE)
    targets = df["entity_type"].eq("INDIVIDUAL") & matches.notna().any(axis=1)
    if not targets.any():
        return 0

    regions = matches["region"].fillna(matches["leading"]).str.strip().str.upper()
    organization_names = regions[targets] + " POLITICAL NETWORK"

    df.loc[targets, "entity_type"] = "ORGANIZATION"
    if "is_individual" in df.columns:
        df.loc[targets, "is_individual"] = False
    df.loc[targets, "contributor_name"] = organization_names
    df.loc[targets, ["contributor_first_name", "contributor_last_name"]] = pd.NA
    df.loc[targets, ["contributor_employer", "contributor_occupation"]] = pd.NA
    df.loc[targets, "occupation_category"] = "ORGANIZATION"
    if "previous_employer" in df.columns:
        df.loc[targets, "previous_employer"] = pd.NA
    sub_ids = df.loc[targets, "sub_id"].astype(str)
    df.loc[targets, "donor_key"] = (organization_names + " " + sub_ids).map(non_individual_donor_key)
    return int(targets.sum())


def _canonicalize(df: pd.DataFrame, trail: AuditTrail) -> int:
    from fec.donor_match import (
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


def _align_organization_names(df: pd.DataFrame, trail: AuditTrail) -> int:
    """Give organization donors the final spelling of the same company.

    Runs after the curated entity overrides (a law firm such as PACHULSKI
    STANG ZIEHL & JONES only becomes an ORGANIZATION there) and after employer
    finalisation (so the target is the spelling the employers table shows).
    Only contributor_name changes; donor_keys were assigned earlier.
    """
    from fec.donor_match.canonicalize import (
        align_org_donor_company_names,
        unify_org_donor_suffix_variants,
    )

    steps = (
        (align_org_donor_company_names, "donor_align_org_names",
         "organization_name_aligned_to_employer_spelling"),
        (unify_org_donor_suffix_variants, "donor_org_suffix_variants",
         "organization_name_unified_to_suffix_free_spelling"),
    )
    total = 0
    for fix, step, reason in steps:
        count = trail.run(df, fix, step, reason, NAME_FIELDS)
        total += count
        log_count(logger, fix.__name__.replace("_", " "), count)
    return total


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

    organizations = trail.run(
        df,
        _classify_network_organizations,
        "network_organization_classification",
        "unresolved_network_name_is_not_a_person",
        PEOPLE_FIELDS + ("donor_key",),
        source="FEC_network_name_pattern",
    )
    log_count(logger, "political-network filings kept unresolved", organizations)

    canonical_updates = _canonicalize(df, trail)

    df, manual_updates = trail.run(
        df, apply_name_corrections, "curated_name_corrections", "manual_correction", NAME_FIELDS,
        source="data/database/contributor_name_rules.csv",
    )
    log_count(logger, "curated name corrections", manual_updates)

    consistency_updates = apply_donor_consistency(df, trail)

    df, employer_updates = _finalize_employers(df, trail)
    canonical_updates += employer_updates

    protected = trail.run(
        df, lambda frame: apply_manual_employer_overrides(frame, company_names_only=True),
        "curated_employer_names", "curated_row_override", ("contributor_employer", "previous_employer"),
        source="data/manual_employer_overrides.csv",
    )
    log_count(logger, "curated employer names", protected)
    canonical_updates += protected
    canonical_updates += _align_organization_names(df, trail)
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
