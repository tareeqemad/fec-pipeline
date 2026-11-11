"""Assign donor keys, apply reviewed merges, and separate unresolved filings."""

import hashlib
from itertools import combinations

import pandas as pd

from fec.common.log import get_logger
from fec.matching.identity_rules import (
    HELD_ADDRESSES,
    HELD_FILINGS,
    KEY_MERGES,
    held_groups,
    identities_must_stay_separate,
    resolve_donor_key,
    split_zip,
)
from fec.matching.name_compare import normalize_committee_name

logger = get_logger(__name__)


def individual_record_id(name: object, city: object, state: object, suffix: object = "", zip5: object = "") -> str:
    """Individual record ID for matching; ZIP5 only for split names."""
    # Missing cells must not become literal "nan".
    name, city, state, suffix, zip5 = ("" if pd.isna(value) else value for value in (name, city, state, suffix, zip5))
    base = f"{name or ''}|{city or ''}|{state or ''}"
    if zip5:
        base = f"{base}|@{zip5}"
    return f"{base}|{suffix}" if suffix else base


def individual_donor_key(name: object, city: object, state: object, suffix: object = "", zip5: object = "") -> str:
    """Canonical donor_key; the roster loader (fec/database/roster_people.py) finds donors by it."""
    record_id = individual_record_id(name, city, state, suffix, zip5)
    return hashlib.sha256(record_id.encode()).hexdigest()[:12]


def non_individual_donor_key(name: object) -> str:
    """Build the shared key used by organizations and committees."""
    normalized = normalize_committee_name(name)
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def keys_for_groups(components: dict) -> dict:
    """Assign a group root's key to every member."""
    profile_keys = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for profile_id in members:
            profile_keys[profile_id] = key
    return profile_keys


def _find_row_donor_key(row: pd.Series, profile_keys: dict) -> str:
    if row["entity_type"] != "INDIVIDUAL":
        return non_individual_donor_key(row.get("contributor_name"))
    name = row["contributor_name"]
    city = row["contributor_city"]
    state = row["contributor_state"]
    suffix = row.get("_generational_suffix", "")
    zip5 = split_zip(name, row.get("contributor_zip", ""))
    profile_id = individual_record_id(name, city, state, suffix, zip5)
    if profile_id in profile_keys:
        return profile_keys[profile_id]
    return individual_donor_key(name, city, state, suffix, zip5)


def apply_donor_key(df: pd.DataFrame, profile_keys: dict) -> pd.DataFrame:
    """Apply scored donor keys; committees hash their normalized name."""
    df["donor_key"] = df.apply(_find_row_donor_key, axis=1, args=(profile_keys,))
    return df


def keep_earlier_keys(df: pd.DataFrame, earlier: dict, skip: pd.Series | None = None) -> list[str]:
    """Give new keys their earlier names; return earlier keys left unused.

    Filings marked skip (held ones, no donor's) claim no earlier key.
    """
    people = df["entity_type"].eq("INDIVIDUAL") & ~(False if skip is None else skip)
    pairs = pd.DataFrame(
        {
            "new": df.loc[people, "donor_key"],
            "old": df.loc[people, "sub_id"].astype(str).map(earlier),
        }
    ).dropna()
    counts = pairs.groupby(["old", "new"]).size().reset_index(name="rows")
    counts = counts.sort_values(["rows", "old", "new"], ascending=[False, True, True])
    rename, taken = {}, set()
    for old, new in zip(counts["old"], counts["new"]):
        if new not in rename and old not in taken:
            rename[new] = old
            taken.add(old)
    # A new key must not land on an earlier key now held by someone else.
    for new in set(df.loc[people, "donor_key"]) - set(rename):
        if new in taken:
            rename[new] = hashlib.sha256(f"split|{new}".encode()).hexdigest()[:12]
    df.loc[people, "donor_key"] = df.loc[people, "donor_key"].map(lambda key: rename.get(key, key))
    return sorted(set(pairs["old"]) - taken)


def apply_curated_key_merges(df: pd.DataFrame) -> int:
    """Repoint verified duplicate keys to their preferred key."""
    if not KEY_MERGES:
        return 0
    # A rule naming two keys that are both gone merges nobody.
    present = set(df["donor_key"])
    missing = sorted(drop for drop, keep in KEY_MERGES.items() if drop not in present and keep not in present)
    if missing:
        logger.warning("WARNING: %s donor merge rules found no donor: %s", len(missing), ", ".join(missing[:5]))

    merged = df["donor_key"].isin(KEY_MERGES)
    merged_count = int(merged.sum())
    if merged_count:
        df.loc[merged, "donor_key"] = df.loc[merged, "donor_key"].map(resolve_donor_key)
    return merged_count


def _build_held_key(group: str) -> str:
    return hashlib.sha256(f"HOLD|{group}".encode()).hexdigest()[:12]


def hold_unproven_filings(df: pd.DataFrame) -> int:
    """Unresolved filing groups get separate keys, away from totals."""
    groups = held_groups(df, HELD_FILINGS, HELD_ADDRESSES)
    held = groups != ""
    found = set(df.loc[held, "sub_id"].astype(str))
    missing = len(set(HELD_FILINGS) - found)
    if missing:
        logger.warning("  %s held filings not in the data", missing)
    df.loc[held, "donor_key"] = groups[held].map(_build_held_key)
    df.loc[held, "identity_status"] = "held"
    return int(held.sum())


def validate_separations(df: pd.DataFrame) -> None:
    """Reject a donor containing a verified separation pair."""
    columns = [
        "donor_key",
        "contributor_name",
        "contributor_city",
        "contributor_state",
        "contributor_zip",
        "entity_type",
    ]
    profiles = df.loc[df["entity_type"].eq("INDIVIDUAL"), columns].drop_duplicates()

    for donor_key, group in profiles.groupby("donor_key"):
        people = [
            {
                "name": row.contributor_name,
                "city": row.contributor_city,
                "state": row.contributor_state,
                "zip5": str(getattr(row, "contributor_zip", "") or "")[:5],
            }
            for row in group.itertuples(index=False)
        ]
        for person_a, person_b in combinations(people, 2):
            if identities_must_stay_separate(person_a, person_b):
                pair = f"{person_a} / {person_b}"
                raise ValueError(f"Donor identity rules joined a separation pair under {donor_key}: {pair}")
