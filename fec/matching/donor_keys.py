"""Assign donor keys, apply reviewed merges, and separate unresolved filings."""

import hashlib
from itertools import combinations

import pandas as pd

from fec.common.log import get_logger
from fec.matching.identity_rules import (
    HELD_FILINGS,
    KEY_MERGES,
    held_groups,
    identities_must_stay_separate,
    resolve_donor_key,
    split_zip,
)
from fec.matching.name_compare import normalize_committee_name

logger = get_logger(__name__)


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def individual_record_id(name: object, city: object, state: object, suffix: object, zip5: str) -> str:
    """Individual record ID for matching; ZIP5 only for split names."""
    # Missing cells must not become literal "nan".
    name, city, state, suffix = ("" if pd.isna(value) else value for value in (name, city, state, suffix))
    base = f"{name}|{city}|{state}"
    if zip5:
        base = f"{base}|@{zip5}"
    return f"{base}|{suffix}" if suffix else base


def non_individual_donor_key(name: object) -> str:
    """Build the shared key used by organizations and committees."""
    return _short_hash(normalize_committee_name(name))


def keys_for_groups(components: dict) -> dict:
    """Assign a group root's key to every member."""
    profile_keys = {}
    for root, members in components.items():
        profile_keys.update(dict.fromkeys(members, _short_hash(root)))
    return profile_keys


def _find_row_donor_key(row: pd.Series, profile_keys: dict) -> str:
    if row["entity_type"] != "INDIVIDUAL":
        return non_individual_donor_key(row.get("contributor_name"))
    name = row["contributor_name"]
    suffix = row.get("_generational_suffix", "")
    zip5 = split_zip(name, row.get("contributor_zip", ""))
    profile_id = individual_record_id(name, row["contributor_city"], row["contributor_state"], suffix, zip5)
    return profile_keys.get(profile_id, _short_hash(profile_id))


def apply_donor_key(df: pd.DataFrame, profile_keys: dict) -> None:
    """Apply scored donor keys; committees hash their normalized name."""
    df["donor_key"] = df.apply(_find_row_donor_key, axis=1, args=(profile_keys,))


def keep_earlier_keys(df: pd.DataFrame, earlier: dict, skip: pd.Series | None = None) -> list[str]:
    """Give new keys their earlier names (skip: held filings claim none); return earlier keys left unused."""
    people = df["entity_type"].eq("INDIVIDUAL") & ~(False if skip is None else skip)
    new_keys = df.loc[people, "donor_key"]
    old_keys = df.loc[people, "sub_id"].astype(str).map(earlier)
    pairs = pd.DataFrame({"new": new_keys, "old": old_keys}).dropna()
    counts = pairs.groupby(["old", "new"]).size().reset_index(name="rows")
    counts = counts.sort_values(["rows", "old", "new"], ascending=[False, True, True])
    rename, taken = {}, set()
    for old, new in zip(counts["old"], counts["new"]):
        if new not in rename and old not in taken:
            rename[new] = old
            taken.add(old)
    # A new key must not land on an earlier key now held by someone else.
    for new in set(new_keys) - set(rename):
        if new in taken:
            rename[new] = _short_hash(f"split|{new}")
    df.loc[people, "donor_key"] = new_keys.map(lambda key: rename.get(key, key))
    return sorted(set(pairs["old"]) - taken)


def apply_curated_key_merges(df: pd.DataFrame) -> None:
    """Repoint verified duplicate keys to their preferred key."""
    # A rule naming two keys that are both gone merges nobody.
    present = set(df["donor_key"])
    missing = sorted(drop for drop, keep in KEY_MERGES.items() if drop not in present and keep not in present)
    if missing:
        logger.warning("WARNING: %s donor merge rules found no donor: %s", len(missing), ", ".join(missing[:5]))
    merged = df["donor_key"].isin(KEY_MERGES)
    df.loc[merged, "donor_key"] = df.loc[merged, "donor_key"].map(resolve_donor_key)


def hold_unproven_filings(df: pd.DataFrame) -> None:
    """Unresolved filing groups get separate keys, away from totals."""
    groups = held_groups(df)
    held = groups != ""
    missing = len(set(HELD_FILINGS) - set(df.loc[held, "sub_id"].astype(str)))
    if missing:
        logger.warning("  %s held filings not in the data", missing)
    df.loc[held, "donor_key"] = ("HOLD|" + groups[held]).map(_short_hash)
    df.loc[held, "identity_status"] = "held"


def validate_separations(df: pd.DataFrame) -> None:
    """Reject a donor containing a verified separation pair."""
    columns = ["donor_key", "contributor_name", "contributor_city", "contributor_state", "contributor_zip"]
    profiles = df.loc[df["entity_type"].eq("INDIVIDUAL"), columns].drop_duplicates()
    for donor_key, group in profiles.groupby("donor_key"):
        people = [
            {"name": name, "city": city, "state": state, "zip5": str(zip_code or "")[:5]}
            for _, name, city, state, zip_code in group.itertuples(index=False)
        ]
        for person_a, person_b in combinations(people, 2):
            if identities_must_stay_separate(person_a, person_b):
                pair = f"{person_a} / {person_b}"
                raise ValueError(f"Donor identity rules joined a separation pair under {donor_key}: {pair}")
