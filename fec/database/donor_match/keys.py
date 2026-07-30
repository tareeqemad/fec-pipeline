"""Donor-key hashing, application, and key-level merge passes."""

import csv
import hashlib
from pathlib import Path

import pandas as pd

from .normalize import normalize_committee_name

# occupation categories too vague to prove two same-name donors are different people
_VAGUE_OCC_CATEGORIES = ("", "OTHER", "NOT EMPLOYED", "RETIRED")


def individual_record_id(name, city, state) -> str:
    """The name|city|state record-id string used to key an individual donor."""
    return f"{name or ''}|{city or ''}|{state or ''}"


def individual_donor_key(name, city, state) -> str:
    """Canonical donor_key (sha256 of record-id, first 12 hex); must stay identical to leadership_matcher's."""
    rid = individual_record_id(name, city, state)
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


def apply_donor_key(df: pd.DataFrame, rid_to_key: dict) -> pd.DataFrame:
    """Apply scored donor_key to DataFrame."""

    def _get_key(row):
        if row["entity_type"] != "INDIVIDUAL":
            norm = normalize_committee_name(row.get("contributor_name"))
            return hashlib.sha256(norm.encode()).hexdigest()[:12]
        rid = individual_record_id(
            row["contributor_name"], row["contributor_city"], row["contributor_state"]
        )
        return rid_to_key.get(rid, individual_donor_key(
            row["contributor_name"], row["contributor_city"], row["contributor_state"]
        ))

    df["donor_key"] = df.apply(_get_key, axis=1)
    return df


def merge_split_name_donors(df: pd.DataFrame) -> int:
    """Merge donor_keys split by name blocking: same sorted name tokens + same ZIP + no occupation conflict repoint to the dominant key; returns rows repointed."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    first = df["contributor_first_name"].fillna("").str.upper()
    last = df["contributor_last_name"].fillna("").str.upper()
    full = (first + " " + last).str.strip()
    # fingerprint: sorted alpha tokens of the full name (order-independent)
    fp = full.str.findall(r"[A-Z]+").map(lambda toks: " ".join(sorted(toks)))
    zip5 = df["contributor_zip"].fillna("")

    eligible = ind & (fp != "") & (zip5 != "")
    work = pd.DataFrame({
        "fp": fp[eligible], "z": zip5[eligible],
        "key": df.loc[eligible, "donor_key"],
        "occ": df.loc[eligible, "occupation_category"].fillna(""),
    })

    remap: dict[str, str] = {}
    for (fpv, z), grp in work.groupby(["fp", "z"]):
        keys = grp["key"].unique()
        if len(keys) < 2:
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


def apply_donor_dedup_merges(df: pd.DataFrame) -> int:
    """Apply curated merges from data/database/donor_dedup_merges.csv, repointing drop keys to keep keys (chains followed to a final key); returns rows repointed."""
    path = (Path(__file__).resolve().parents[3]
            / "data" / "database" / "donor_dedup_merges.csv")
    if not path.exists() or "donor_key" not in df.columns:
        return 0
    remap: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keep = (row.get("keep_donor_key") or "").strip()
            drop = (row.get("drop_donor_key") or "").strip()
            if keep and drop and keep != drop:
                remap[drop] = keep
    if not remap:
        return 0

    def _final(k):
        seen = set()
        while k in remap and k not in seen:
            seen.add(k)
            k = remap[k]
        return k

    mask = df["donor_key"].isin(remap)
    n = int(mask.sum())
    if n:
        df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(_final)
    return n
