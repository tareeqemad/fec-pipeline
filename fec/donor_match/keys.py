"""Donor-key hashing, application, key-level merge passes, and the dedup review report."""

import hashlib
from itertools import combinations

import pandas as pd

from fec.donor_match.normalize import normalize_committee_name
from fec.donor_match.rules import (
    HELD_FILINGS,
    KEY_MERGES,
    identities_must_stay_separate,
    names_must_stay_separate,
    resolve_donor_key,
    split_zip,
)
from fec.log import get_logger

logger = get_logger(__name__)

# occupation categories too vague to prove two same-name donors are different people
_VAGUE_OCC_CATEGORIES = ("", "OTHER", "NOT EMPLOYED", "RETIRED")


# build the individual record id for matching and key assignment
def individual_record_id(name, city, state, suffix="", zip5="") -> str:
    """Build the individual record ID used by matching and key assignment (zip5 only for a ZIP-split name)."""
    base = f"{name or ''}|{city or ''}|{state or ''}"
    if zip5:
        base = f"{base}|@{zip5}"
    return f"{base}|{suffix}" if suffix else base


# hash the record id into the canonical donor_key
def individual_donor_key(name, city, state, suffix="", zip5="") -> str:
    """Canonical donor_key (sha256 of record-id, first 12 hex); must stay identical to leadership_matcher's."""
    rid = individual_record_id(name, city, state, suffix, zip5)
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


# build the shared donor key used by organizations and committees
def non_individual_donor_key(name) -> str:
    """Build the shared key used by organizations and committees."""
    normalized = normalize_committee_name(name)
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


# assign each row its scored or fallback donor_key
def apply_donor_key(df: pd.DataFrame, rid_to_key: dict) -> pd.DataFrame:
    """Apply scored donor_key: individuals take their cluster key from rid_to_key, silently falling back to a hash of their own rid when absent; non-individuals hash their normalized committee name instead."""

    # look up or fall back to compute this row's donor_key
    def _get_key(row):
        if row["entity_type"] != "INDIVIDUAL":
            return non_individual_donor_key(row.get("contributor_name"))
        zip5 = split_zip(row["contributor_name"], row.get("contributor_zip", ""))
        rid = individual_record_id(
            row["contributor_name"],
            row["contributor_city"],
            row["contributor_state"],
            row.get("_generational_suffix", ""),
            zip5,
        )
        return rid_to_key.get(rid, individual_donor_key(
            row["contributor_name"],
            row["contributor_city"],
            row["contributor_state"],
            row.get("_generational_suffix", ""),
            zip5,
        ))

    df["donor_key"] = df.apply(_get_key, axis=1)
    return df


# merge donor_keys name-blocking split for the same person
def merge_split_name_donors(df: pd.DataFrame) -> int:
    """Merge donor_keys split by name blocking: same sorted name tokens + same ZIP + no occupation conflict repoint to the dominant key; returns rows repointed."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    first = df["contributor_first_name"].fillna("").str.upper()
    last = df["contributor_last_name"].fillna("").str.upper()
    suffix = df.get("_generational_suffix", pd.Series("", index=df.index)).fillna("")
    full = (first + " " + last).str.strip()
    # fingerprint: sorted alpha tokens of the full name (order-independent)
    fp = full.str.findall(r"[A-Z]+").map(lambda toks: " ".join(sorted(toks)))
    zip5 = df["contributor_zip"].fillna("")

    eligible = ind & (fp != "") & (zip5 != "")
    work = pd.DataFrame({
        "fp": fp[eligible], "z": zip5[eligible],
        "key": df.loc[eligible, "donor_key"],
        "name": df.loc[eligible, "contributor_name"].fillna(""),
        "occ": df.loc[eligible, "occupation_category"].fillna(""),
        "suffix": suffix[eligible],
    })
    names_by_key = work.groupby("key")["name"].agg(set).to_dict()
    suffixes_by_key = work.groupby("key")["suffix"].agg(
        lambda values: frozenset(value for value in values if value)
    ).to_dict()

    remap: dict[str, str] = {}
    for _, grp in work.groupby(["fp", "z"]):
        keys = grp["key"].unique()
        if len(keys) < 2:
            continue
        signatures = {suffixes_by_key[key] for key in keys}
        if len(signatures) > 1:
            continue
        blocked = any(
            names_must_stay_separate(name_a, name_b)
            for key_a, key_b in combinations(keys, 2)
            for name_a in names_by_key[key_a]
            for name_b in names_by_key[key_b]
        )
        if blocked:
            continue
        # >1 distinct real occupation category: could be two people, skip
        real_occs = {o for o in grp["occ"].unique() if o not in _VAGUE_OCC_CATEGORIES}
        if len(real_occs) > 1:
            continue
        # winner = key with the most contribution rows
        winner = grp["key"].value_counts().idxmax()
        for k in keys:
            if k != winner:
                remap[k] = winner

    if not remap:
        return 0
    mask = df["donor_key"].isin(remap)
    df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(remap)
    return int(mask.sum())


# repoint verified duplicate keys to their preferred key
def apply_curated_key_merges(df: pd.DataFrame) -> int:
    """Repoint verified duplicate keys to their preferred key."""
    if "donor_key" not in df.columns or not KEY_MERGES:
        return 0

    mask = df["donor_key"].isin(KEY_MERGES)
    n = int(mask.sum())
    if n:
        df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(resolve_donor_key)
    return n


# give each held filing group its own key, outside anyone
def hold_unproven_filings(df: pd.DataFrame) -> int:
    """Give each held filing group its own key, outside every person.

    A hold rule lists filings whose owner is not proven (a joint name cut to
    one person, a second address two same-name people could share); the group
    keeps them together and away from any person's total until evidence decides.
    """
    if not HELD_FILINGS or "sub_id" not in df.columns:
        return 0
    groups = df["sub_id"].astype(str).map(HELD_FILINGS)
    held = groups.notna()
    missing = len(HELD_FILINGS) - int(held.sum())
    if missing:
        logger.warning("  %s held filings not in the data", missing)
    df.loc[held, "donor_key"] = groups[held].map(
        lambda group: hashlib.sha256(f"HOLD|{group}".encode()).hexdigest()[:12]
    )
    df.loc[held, "identity_status"] = "held"
    return int(held.sum())


# reject a donor whose rows contain a verified separation pair
def validate_separations(df: pd.DataFrame) -> None:
    """Reject a donor containing a verified separation pair."""
    columns = [
        "donor_key", "contributor_name", "contributor_city",
        "contributor_state", "contributor_zip", "entity_type",
    ]
    columns = [column for column in columns if column in df.columns]
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
                raise ValueError(
                    "Donor identity rules joined a separation pair under "
                    f"{donor_key}: {person_a} / {person_b}"
                )


