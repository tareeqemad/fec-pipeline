"""Identify donors and make their filings consistent."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.addresses.align import unify_units
from fec.cleaning.donors.names import canonicalize_committee_names, canonicalize_donor_names
from fec.cleaning.donors.work import (
    fill_occupation_from_donor, fill_status_employer, rederive_occupation_category, settle_retired_employer,
)
from fec.cleaning.entities import apply_entity_overrides
from fec.cleaning.names import apply_name_corrections
from fec.cleaning.reports.donors import build_donor_dedup_review
from fec.cleaning.rules.manual import apply_reviewed_filings
from fec.cleaning.work.employer_aliases import canonicalize_current_employers
from fec.donor_match.keys import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    keep_earlier_keys,
    non_individual_donor_key,
    validate_separations,
)
from fec.donor_match.matcher import match_donors
from fec.donor_match.rules import held_groups
from fec.log import get_logger

logger = get_logger(__name__)

# In order; reviewed row decisions are applied before and after them.
DONOR_WORK_STEPS = (
    fill_occupation_from_donor, fill_status_employer, apply_entity_overrides,
    rederive_occupation_category, settle_retired_employer,
)


def identify_donors(df_clean: pd.DataFrame, earlier_keys: dict | None = None) -> pd.DataFrame:
    """Assign one donor_key to each identity; earlier_keys keeps keys stable."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    # Hold rules and network classification can change this status later.
    df_clean["identity_status"] = "confirmed"
    rid_to_key = match_donors(df_clean)
    df_clean = apply_donor_key(df_clean, rid_to_key)

    if earlier_keys:
        lost = keep_earlier_keys(df_clean, earlier_keys, skip=held_groups(df_clean).ne(""))
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
    _separate_network_filings(df)
    # An unconfirmed filing stands alone during every donor-history repair.
    unconfirmed = df["identity_status"].ne("confirmed")
    matched_keys = df.loc[unconfirmed, "donor_key"].copy()
    df.loc[unconfirmed, "donor_key"] = matched_keys + "|" + df.loc[unconfirmed, "sub_id"].astype(str)

    canonicalize_donor_names(df)
    canonicalize_current_employers(df)
    unify_units(df, donor="donor_key")
    apply_name_corrections(df)
    apply_reviewed_filings(df, matched=True)
    for step in DONOR_WORK_STEPS:
        step(df)
    apply_reviewed_filings(df, matched=True)
    canonicalize_committee_names(df)
    build_donor_dedup_review(df, out_dir)

    # Committees and organisations have no person name or employer.
    other = df["entity_type"].ne("INDIVIDUAL")
    df.loc[other, ["contributor_first_name", "contributor_last_name", "contributor_employer"]] = np.nan
    df.loc[matched_keys.index, "donor_key"] = matched_keys
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


# The region before or after POLITICAL NETWORK.
_NETWORK_NAME_RE = re.compile(r"^(?:POLITICAL NETWORK,\s*(.+)|(.+?)\s+POLITICAL NETWORK)$", re.IGNORECASE)


def _separate_network_filings(df: pd.DataFrame) -> None:
    """A person filed under a network's name is that network, one unresolved donor per filing."""
    region = df["contributor_name"].fillna("").astype(str).str.extract(_NETWORK_NAME_RE).bfill(axis=1)[0]
    rows = df.index[df["entity_type"].eq("INDIVIDUAL") & region.notna()]
    names = region[rows].str.strip().str.upper() + " POLITICAL NETWORK"
    df.loc[rows, "contributor_name"] = names
    df.loc[rows, ["entity_type", "is_individual", "occupation_category", "identity_status"]] = [
        "ORGANIZATION", False, "ORGANIZATION", "unresolved"]
    df.loc[rows, ["contributor_first_name", "contributor_last_name", "contributor_employer",
                  "contributor_occupation", "previous_employer"]] = np.nan
    df.loc[rows, "donor_key"] = (names + " " + df.loc[rows, "sub_id"].astype(str)).map(non_individual_donor_key)
