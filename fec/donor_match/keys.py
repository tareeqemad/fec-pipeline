"""Donor-key hashing, application, key-level merge passes, and the dedup review report."""

import csv
import hashlib
from itertools import combinations
from pathlib import Path

import pandas as pd

from fec.env import DATA_DIR
from fec.log import get_logger

from .constants import NICKNAME_MAP, _is_blocked_merge
from .scoring import normalize_committee_name

logger = get_logger(__name__)

# occupation categories too vague to prove two same-name donors are different people
_VAGUE_OCC_CATEGORIES = ("", "OTHER", "NOT EMPLOYED", "RETIRED")


def individual_record_id(name, city, state) -> str:
    """The name|city|state record-id string used to key an individual donor; must produce exactly the same string as the rid built in profiles.build_profiles (which strips and maps NaN to ""), or apply_donor_key's rid_to_key lookup silently falls back to a per-record key."""
    return f"{name or ''}|{city or ''}|{state or ''}"


def individual_donor_key(name, city, state) -> str:
    """Canonical donor_key (sha256 of record-id, first 12 hex); must stay identical to leadership_matcher's."""
    rid = individual_record_id(name, city, state)
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


def apply_donor_key(df: pd.DataFrame, rid_to_key: dict) -> pd.DataFrame:
    """Apply scored donor_key: individuals take their cluster key from rid_to_key, silently falling back to a hash of their own rid when absent; non-individuals hash their normalized committee name instead."""

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
        "name": df.loc[eligible, "contributor_name"].fillna(""),
        "occ": df.loc[eligible, "occupation_category"].fillna(""),
    })
    names_by_key = work.groupby("key")["name"].agg(set).to_dict()

    remap: dict[str, str] = {}
    for _, grp in work.groupby(["fp", "z"]):
        keys = grp["key"].unique()
        if len(keys) < 2:
            continue
        blocked = any(
            _is_blocked_merge(name_a, name_b)
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


def apply_donor_dedup_merges(df: pd.DataFrame) -> int:
    """Apply curated merges from data/database/donor_dedup_merges.csv, repointing drop keys to keep keys (chains followed to a final key); returns rows repointed."""
    path = DATA_DIR / "database" / "donor_dedup_merges.csv"
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


def build_donor_dedup_review(df: pd.DataFrame, out_dir) -> int:
    """Write data/donor_dedup_review.csv of likely-same-person donor pairs (shared ZIP + surname, related first names) for human review; never merges; returns pairs written."""
    if not out_dir:
        return 0
    need = {"donor_key", "entity_type", "contributor_last_name",
            "contributor_first_name", "contributor_city",
            "contributor_state", "contributor_zip"}
    if not need <= set(df.columns):
        return 0
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    if ind.empty:
        return 0
    amt = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").fillna(0)
    work = df.assign(_amt=amt).loc[ind.index]

    def _first_str(s):
        s = s.dropna().astype(str)
        s = s[s.str.strip() != ""]
        return s.iloc[0] if len(s) else ""

    g = work.groupby("donor_key")
    summ = pd.DataFrame({
        "last":   g["contributor_last_name"].agg(_first_str).str.upper(),
        "first":  g["contributor_first_name"].agg(_first_str).str.upper(),
        "city":   g["contributor_city"].agg(_first_str),
        "state":  g["contributor_state"].agg(_first_str),
        "n":      g.size(),
        "amount": g["_amt"].sum(),
    })

    # blocked by (ZIP, surname); a donor with two ZIPs appears in both blocks
    zp = ind[["donor_key", "contributor_zip"]].dropna()
    zp = zp[zp["contributor_zip"].astype(str).str.strip() != ""].drop_duplicates()
    zp = zp.join(summ["last"], on="donor_key")

    def _root(first):
        f0 = first.split()[0] if first else ""
        return NICKNAME_MAP.get(f0, f0)

    def _relation(fa, fb):
        a0 = fa.split()[0] if fa else ""
        b0 = fb.split()[0] if fb else ""
        if not a0 or not b0:
            return ""
        if a0 == b0:
            return "same first name (different city?)"
        if _root(fa) == _root(fb):
            return f"nickname {a0}/{b0}"
        short, long = sorted((a0, b0), key=len)
        if long.startswith(short) and short != long:
            return f"initial/prefix {short}->{long}"
        return ""

    seen, out = set(), []
    for (z, last), grp in zp.groupby(["contributor_zip", "last"]):
        if not last:
            continue
        keys = sorted(grp["donor_key"].unique())
        if len(keys) < 2:
            continue
        for ka, kb in combinations(keys, 2):
            if (ka, kb) in seen:
                continue
            seen.add((ka, kb))
            fa, fb = summ.at[ka, "first"], summ.at[kb, "first"]
            reason = _relation(fa, fb)
            if not reason:
                continue
            if _is_blocked_merge(f"{fa} {last}", f"{fb} {last}"):
                continue
            out.append({
                "reason": reason, "zip": z, "last_name": last,
                "donor_key_a": ka, "first_a": fa,
                "city_a": summ.at[ka, "city"], "state_a": summ.at[ka, "state"],
                "donations_a": int(summ.at[ka, "n"]), "amount_a": round(float(summ.at[ka, "amount"]), 2),
                "donor_key_b": kb, "first_b": fb,
                "city_b": summ.at[kb, "city"], "state_b": summ.at[kb, "state"],
                "donations_b": int(summ.at[kb, "n"]), "amount_b": round(float(summ.at[kb, "amount"]), 2),
                "combined_amount": round(float(summ.at[ka, "amount"] + summ.at[kb, "amount"]), 2),
            })
    if not out:
        return 0
    out.sort(key=lambda r: -r["combined_amount"])
    path = Path(out_dir) / "donor_dedup_review.csv"
    pd.DataFrame(out).to_csv(path, index=False, na_rep="")
    logger.info(f"  Donor-dedup review -> {path} ({len(out):,} candidate pairs)")
    return len(out)
