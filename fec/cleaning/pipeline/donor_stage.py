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
_IDENTITY_FIELDS = (
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "contributor_employer",
    "contributor_occupation",
)


def _identity_signatures(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    parts = pd.DataFrame(index=df.index)
    for field in _IDENTITY_FIELDS:
        parts[field] = (
            df[field]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.replace(r"[^A-Z0-9]", "", regex=True)
        )

    required = [field for field in _IDENTITY_FIELDS if field != "contributor_street_2"]
    complete = parts[required].ne("").all(axis=1)
    return parts.agg("|".join, axis=1), complete


def _recover_network_donors(df: pd.DataFrame) -> tuple[int, int]:
    """Join network-labeled rows to one exact known identity."""
    required = {"entity_type", "contributor_name", "donor_key", *_IDENTITY_FIELDS}
    if not required.issubset(df.columns):
        return 0, 0

    people = df["entity_type"].eq("INDIVIDUAL")
    placeholders = df["contributor_name"].fillna("").str.match(_NETWORK_NAME_RE)
    signatures, complete = _identity_signatures(df)

    candidates = people & ~placeholders & complete & df["donor_key"].notna()
    known = pd.DataFrame(
        {
            "signature": signatures[candidates],
            "donor_key": df.loc[candidates, "donor_key"],
        }
    )
    by_signature = known.groupby("signature")["donor_key"].agg(
        lambda values: frozenset(values)
    )

    changed = 0
    targets = df.index[people & placeholders & complete]
    for index in targets:
        matches = by_signature.get(signatures.at[index], frozenset())
        if len(matches) != 1:
            continue
        donor_key = next(iter(matches))
        if df.at[index, "donor_key"] != donor_key:
            df.at[index, "donor_key"] = donor_key
            df.loc[
                index,
                [
                    "contributor_name",
                    "contributor_first_name",
                    "contributor_last_name",
                ],
            ] = pd.NA
            changed += 1

    return changed, len(targets) - changed


def _classify_network_organizations(df: pd.DataFrame) -> int:
    """Keep unresolved network names as organizations, not people."""
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
    if "occupation_status" in df.columns:
        df.loc[targets, "occupation_status"] = "NOT_APPLICABLE"
    if "committee_type" in df.columns:
        df.loc[targets, "committee_type"] = "ORGANIZATION"
    df.loc[targets, "donor_key"] = organization_names.map(non_individual_donor_key)
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

    recovered, remaining = trail.run(
        df,
        _recover_network_donors,
        "network_identity_recovery",
        "unique_address_employer_occupation_match",
        ("donor_key",) + NAME_FIELDS,
        source="dataset_identity_history",
    )
    organizations = trail.run(
        df,
        _classify_network_organizations,
        "network_organization_classification",
        "unresolved_network_name_is_not_a_person",
        PEOPLE_FIELDS + ("donor_key",),
        source="FEC_network_name_pattern",
    )
    if recovered or remaining:
        logger.info(
            "  Political-network identities: %s people recovered, %s organizations",
            f"{recovered:,}",
            f"{organizations:,}",
        )

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
