"""Identify donors and make their filings consistent."""
from __future__ import annotations

import pandas as pd
from fec.donor_match.keys import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    keep_earlier_keys,
    validate_separations,
)
from fec.donor_match.matcher import match_donors
import re
import numpy as np
from fec.cleaning.donors.steps import apply_donor_consistency
from fec.cleaning.work.employer_aliases import canonicalize_current_employers
from fec.cleaning.names import apply_name_corrections
from fec.cleaning.addresses.fixes.unify import (
    canonicalize_donor_units,
)
from fec.cleaning.donors.names import canonicalize_committee_names, canonicalize_donor_names
from fec.cleaning.reports.donors import build_donor_dedup_review
from fec.donor_match.keys import non_individual_donor_key
from fec.log import get_logger

logger = get_logger(__name__)


def identify_donors(df_clean: pd.DataFrame, earlier_keys: dict | None = None) -> pd.DataFrame:
    """Assign one donor_key to each identity; earlier_keys keeps keys stable."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    # Hold rules and network classification can change this status later.
    df_clean["identity_status"] = "confirmed"
    rid_to_key, _ = match_donors(df_clean)
    df_clean = apply_donor_key(df_clean, rid_to_key)

    if earlier_keys:
        lost = keep_earlier_keys(df_clean, earlier_keys)
        if lost:
            logger.warning("WARNING: %s earlier donor keys no longer used (split or merged): %s",
                           len(lost), ", ".join(lost[:5]))
    # Saved by clean.py so the next run reuses these keys.
    df_clean["_matched_key"] = df_clean["donor_key"]
    apply_curated_key_merges(df_clean)
    hold_unproven_filings(df_clean)
    _validate_generational_suffixes(df_clean)
    validate_separations(df_clean)

    return df_clean


def standardize(df_clean: pd.DataFrame, out_dir: str | None = None) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    df = df_clean.reset_index(drop=True)
    _classify_network_organizations(df)
    # Keep unconfirmed filings separate during all donor-history repairs.
    if "identity_status" in df.columns:
        unconfirmed = df["identity_status"].ne("confirmed")
    else:
        unconfirmed = pd.Series(False, index=df.index)
    original_keys = df.loc[unconfirmed, "donor_key"].copy()
    filing_ids = df.loc[unconfirmed, "sub_id"].astype(str)
    df.loc[unconfirmed, "donor_key"] = original_keys + "|" + filing_ids

    canonicalize_donor_names(df)
    canonicalize_current_employers(df)
    canonicalize_donor_units(df)
    df, _ = apply_name_corrections(df)
    apply_donor_consistency(df)
    canonicalize_committee_names(df)
    build_donor_dedup_review(df, out_dir)

    _clear_non_individual_fields(df)
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


# Committees and organizations have no person name or employer.
def _clear_non_individual_fields(df: pd.DataFrame) -> int:
    fields = ["contributor_first_name", "contributor_last_name", "contributor_employer"]
    mask = (df["entity_type"] != "INDIVIDUAL") & df[fields].notna().any(axis=1)
    df.loc[mask, fields] = np.nan
    return int(mask.sum())
