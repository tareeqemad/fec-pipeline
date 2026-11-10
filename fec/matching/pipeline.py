"""Give every person one donor key and make each donor's filings consistent.

Steps, in order:
    identify_donors
        1. match_donors                 score name pairs and join the same person (matcher.py, phases.py)
        2. apply_donor_key              write the key on every filing
        3. keep_earlier_keys            reuse last month's keys (data/cache/donor_keys.csv)
        4. apply_curated_key_merges     reviewed merges (data/rules/donor_identity_rules.csv)
        5. hold_unproven_filings        filings whose owner is not proven count toward nobody
        6. validate_separations         stop if a reviewed separation was joined
    standardize_donors
        1. _separate_network_filings    network names filed as people
        2. canonicalize_donor_names     one surname and given name per donor
        3. canonicalize_current_employers, unify_units, apply_name_corrections
        4. apply_filing_corrections, DONOR_WORK_STEPS, apply_filing_corrections
        5. canonicalize_committee_names, write_duplicate_review
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.addresses.donor_addresses import unify_units
from fec.cleaning.contributor_names import apply_name_corrections
from fec.cleaning.entity_types import apply_entity_overrides
from fec.cleaning.filing_corrections import apply_filing_corrections
from fec.cleaning.work.company_names import canonicalize_current_employers
from fec.common.log import get_logger
from fec.matching.donor_keys import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    keep_earlier_keys,
    non_individual_donor_key,
    validate_separations,
)
from fec.matching.donor_names import canonicalize_committee_names, canonicalize_donor_names
from fec.matching.donor_work import (
    fill_occupation_from_donor,
    fill_status_employer,
    recategorize_occupations,
    settle_retired_employer,
)
from fec.matching.duplicate_report import write_duplicate_review
from fec.matching.identity_rules import held_groups
from fec.matching.matcher import match_donors

logger = get_logger(__name__)

# In order; reviewed row decisions are applied before and after them.
DONOR_WORK_STEPS = (
    fill_occupation_from_donor,
    fill_status_employer,
    apply_entity_overrides,
    recategorize_occupations,
    settle_retired_employer,
)


def identify_donors(df_clean: pd.DataFrame, earlier_keys: dict | None = None) -> pd.DataFrame:
    """Assign one donor_key to each identity; earlier_keys keeps keys stable."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    # Hold rules and network classification can change this status later.
    df_clean["identity_status"] = "confirmed"
    profile_keys = match_donors(df_clean)
    df_clean = apply_donor_key(df_clean, profile_keys)

    if earlier_keys:
        lost = keep_earlier_keys(df_clean, earlier_keys, skip=held_groups(df_clean).ne(""))
        if lost:
            logger.warning(
                "WARNING: %s earlier donor keys no longer used (split or merged): %s",
                len(lost),
                ", ".join(lost[:5]),
            )
    # Saved by clean.py so the next run reuses these keys.
    df_clean["_matched_key"] = df_clean["donor_key"]
    apply_curated_key_merges(df_clean)
    hold_unproven_filings(df_clean)
    _validate_generational_suffixes(df_clean)
    validate_separations(df_clean)

    return df_clean


def standardize_donors(df_clean: pd.DataFrame, out_dir: str | None = None) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    df = df_clean.reset_index(drop=True)
    _separate_network_filings(df)
    # An unconfirmed filing stands alone during every donor-history repair.
    unconfirmed = df["identity_status"].ne("confirmed")
    matched_keys = df.loc[unconfirmed, "donor_key"].copy()
    unconfirmed_sub_ids = df.loc[unconfirmed, "sub_id"].astype(str)
    df.loc[unconfirmed, "donor_key"] = matched_keys + "|" + unconfirmed_sub_ids

    canonicalize_donor_names(df)
    canonicalize_current_employers(df)
    unify_units(df, donor="donor_key")
    apply_name_corrections(df)
    apply_filing_corrections(df, matched=True)
    for step in DONOR_WORK_STEPS:
        step(df)
    apply_filing_corrections(df, matched=True)
    canonicalize_committee_names(df)
    write_duplicate_review(df, out_dir)

    # Committees and organisations have no person name or employer.
    other = df["entity_type"].ne("INDIVIDUAL")
    person_columns = ["contributor_first_name", "contributor_last_name", "contributor_employer"]
    df.loc[other, person_columns] = np.nan
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
    """Make a person filed under a network's name that network, one unresolved donor per filing."""
    names = df["contributor_name"].fillna("").astype(str)
    region_parts = names.str.extract(_NETWORK_NAME_RE)
    region = region_parts.bfill(axis=1)[0]
    rows = df.index[df["entity_type"].eq("INDIVIDUAL") & region.notna()]
    network_names = region[rows].str.strip().str.upper() + " POLITICAL NETWORK"
    df.loc[rows, "contributor_name"] = network_names
    df.loc[rows, ["entity_type", "is_individual", "occupation_category", "identity_status"]] = [
        "ORGANIZATION",
        False,
        "ORGANIZATION",
        "unresolved",
    ]
    person_columns = [
        "contributor_first_name",
        "contributor_last_name",
        "contributor_employer",
        "contributor_occupation",
        "previous_employer",
    ]
    df.loc[rows, person_columns] = np.nan
    filing_names = network_names + " " + df.loc[rows, "sub_id"].astype(str)
    df.loc[rows, "donor_key"] = filing_names.map(non_individual_donor_key)
