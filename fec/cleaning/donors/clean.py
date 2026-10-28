"""Identify donors and make their filings consistent."""
from __future__ import annotations

import pandas as pd
from fec.donor_match.keys import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    merge_split_name_donors,
    validate_separations,
)
from fec.donor_match.matcher import match_donors
import re
import numpy as np
from fec.cleaning.reports.trail import (
    NAME_FIELDS,
    PEOPLE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)
from fec.cleaning.donors.steps import apply_donor_consistency
from fec.cleaning.work.employer_names import finalize_employer_names
from fec.cleaning.work.employer_aliases import canonicalize_current_employers
from fec.cleaning.name_fixes import apply_name_corrections
from fec.cleaning.rules.manual import OVERRIDE_FIELDS, apply_manual_employer_overrides
from fec.cleaning.addresses.fixes.unify import (
    canonicalize_donor_addresses,
    canonicalize_donor_units,
)
from fec.cleaning.donors.employer_variants import align_org_donor_company_names
from fec.cleaning.donors.names import canonicalize_committee_names, canonicalize_donor_names
from fec.cleaning.reports.donors import build_donor_dedup_review
from fec.donor_match.keys import non_individual_donor_key


def identify_donors(df_clean: pd.DataFrame) -> pd.DataFrame:
    """Assign one donor_key to each identity."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    # Hold rules and network classification can change this status later.
    df_clean["identity_status"] = "confirmed"
    rid_to_key, _ = match_donors(df_clean)
    df_clean = apply_donor_key(df_clean, rid_to_key)

    merge_split_name_donors(df_clean)
    apply_curated_key_merges(df_clean)
    hold_unproven_filings(df_clean)
    _validate_generational_suffixes(df_clean)
    validate_separations(df_clean)

    return df_clean


def standardize(
    df_clean: pd.DataFrame,
    out_dir: str | None = None,
    trail: AuditTrail | None = None,
) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    trail = trail or AuditTrail()
    df = df_clean.reset_index(drop=True)
    trail.run(
        df,
        _classify_network_organizations,
        "network_organization_classification",
        "unresolved_network_name_is_not_a_person",
        PEOPLE_FIELDS + ("donor_key",),
        source="FEC_network_name_pattern",
    )
    # Keep unconfirmed filings separate during all donor-history repairs.
    if "identity_status" in df.columns:
        unconfirmed = df["identity_status"].ne("confirmed")
    else:
        unconfirmed = pd.Series(False, index=df.index)
    original_keys = df.loc[unconfirmed, "donor_key"].copy()
    filing_ids = df.loc[unconfirmed, "sub_id"].astype(str)
    df.loc[unconfirmed, "donor_key"] = original_keys + "|" + filing_ids

    _canonicalize(df, trail)

    df, _ = trail.run(
        df, apply_name_corrections, "curated_name_corrections", "manual_correction", NAME_FIELDS,
        source="data/rules/contributor_name_rules.csv",
    )
    apply_donor_consistency(df, trail)

    # Restore curated fields after donor-history inference.
    trail.run(
        df, lambda frame: apply_manual_employer_overrides(frame, company_names_only=True),
        "curated_employer_names", "curated_row_override", OVERRIDE_FIELDS,
        source="data/rules/manual_employer_overrides.csv",
    )
    df = finalize_employer_names(df, trail)
    trail.run(
        df, align_org_donor_company_names, "donor_align_org_names",
        "documented_company_name", NAME_FIELDS,
    )
    trail.run(
        df, canonicalize_committee_names, "reviewed_committee_names",
        "verified_same_fec_committee", NAME_FIELDS + ("donor_key",),
        source="data/rules/donor_identity_rules.csv",
    )
    build_donor_dedup_review(df, out_dir)

    trail.run(
        df, _clear_non_individual_names, "non_individual_names",
        "non_individual_person_names_cleared", NAME_FIELDS,
    )
    df.loc[original_keys.index, "donor_key"] = original_keys
    return df


def _validate_generational_suffixes(df: pd.DataFrame) -> None:
    """Reject a donor containing different explicit generation suffixes."""
    people = df[df["entity_type"] == "INDIVIDUAL"]
    suffixes = people[people["_generational_suffix"] != ""]
    conflicts = suffixes.groupby("donor_key")["_generational_suffix"].nunique()
    conflicts = conflicts[conflicts > 1]
    if not conflicts.empty:
        keys = ", ".join(conflicts.index.astype(str)[:3])
        raise ValueError(f"JR/SR identity guard rejected donor(s): {keys}")


# Match the region before or after "POLITICAL NETWORK".
_NETWORK_NAME_RE = re.compile(
    r"^(?:POLITICAL NETWORK,\s*(?P<region>.+)|(?P<leading>.+?)\s+POLITICAL NETWORK)$",
    re.IGNORECASE,
)


def _classify_network_organizations(df: pd.DataFrame) -> int:
    """Keep network-name filings separate; shared addresses prove nothing."""
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
    if "identity_status" in df.columns:
        df.loc[targets, "identity_status"] = "unresolved"
    sub_ids = df.loc[targets, "sub_id"].astype(str)
    df.loc[targets, "donor_key"] = (organization_names + " " + sub_ids).map(non_individual_donor_key)
    return int(targets.sum())


def _canonicalize(df: pd.DataFrame, trail: AuditTrail) -> None:
    steps = (
        (canonicalize_donor_names, "donor_canonical_names",
         "donor_name_unified_to_modal_last_and_longest_first", NAME_FIELDS),
        (canonicalize_current_employers, "donor_canonical_employers",
         "documented_company_alias", ("contributor_employer",)),
        (canonicalize_donor_addresses, "donor_canonical_addresses",
         "donor_street_spelling_unified", STREET_FIELDS),
        (canonicalize_donor_units, "donor_canonical_units",
         "donor_unit_designator_unified", STREET_FIELDS),
    )
    for fix, step, reason, fields in steps:
        trail.run(df, fix, step, reason, fields)


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
