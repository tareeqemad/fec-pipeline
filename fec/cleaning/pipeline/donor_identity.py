"""Give every filing a donor_key: match, apply rules, hold unproven."""
from __future__ import annotations

import pandas as pd

from fec.donor_match import (
    apply_curated_key_merges,
    apply_donor_key,
    hold_unproven_filings,
    match_donors,
    merge_split_name_donors,
    validate_separations,
)
from fec.log import get_logger

logger = get_logger(__name__)


def _validate_generational_suffixes(df: pd.DataFrame) -> None:
    """Reject a donor containing different explicit generation suffixes."""
    people = df[df["entity_type"] == "INDIVIDUAL"]
    suffixes = people[people["_generational_suffix"] != ""]
    conflicts = suffixes.groupby("donor_key")["_generational_suffix"].nunique()
    conflicts = conflicts[conflicts > 1]
    if not conflicts.empty:
        keys = ", ".join(conflicts.index.astype(str)[:3])
        raise ValueError(f"JR/SR identity guard rejected donor(s): {keys}")


def identify_donors(
    df_clean: pd.DataFrame
) -> pd.DataFrame:
    """Assign one donor_key to each identity."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    logger.info("\n-- Donor identity --")

    # confirmed unless a hold rule (held) or the network step (unresolved) says otherwise
    df_clean["identity_status"] = "confirmed"
    rid_to_key, match_audit = match_donors(df_clean)
    df_clean = apply_donor_key(df_clean, rid_to_key)

    repointed = merge_split_name_donors(df_clean)
    repointed += apply_curated_key_merges(df_clean)
    held = hold_unproven_filings(df_clean)
    _validate_generational_suffixes(df_clean)
    validate_separations(df_clean)

    profiles = len(rid_to_key)
    donors = df_clean.loc[
        df_clean["entity_type"].eq("INDIVIDUAL"), "donor_key"
    ].nunique()
    logger.info(
        "  %s profiles -> %s donors (%s merged; %s pairs scored)",
        f"{profiles:,}",
        f"{donors:,}",
        f"{profiles - donors:,}",
        f"{len(match_audit):,}",
    )
    if repointed:
        logger.info("  %s rows joined by identity rules", f"{repointed:,}")
    if held:
        logger.info("  %s filings held outside every person", f"{held:,}")
    return df_clean
