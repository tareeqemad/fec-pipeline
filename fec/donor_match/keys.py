"""Donor-key hashing, application, key-level merge passes, and the dedup review report."""

import hashlib
from itertools import combinations
from pathlib import Path

import pandas as pd

from fec.log import get_logger

from .constants import NICKNAME_MAP
from .joint import given_tokens, joint_partners
from .rules import (
    HELD_FILINGS,
    KEY_MERGES,
    identities_must_stay_separate,
    names_must_stay_separate,
    resolve_donor_key,
    split_zip,
)
from .scoring import normalize_committee_name

logger = get_logger(__name__)

# occupation categories too vague to prove two same-name donors are different people
_VAGUE_OCC_CATEGORIES = ("", "OTHER", "NOT EMPLOYED", "RETIRED")


def individual_record_id(name, city, state, suffix="", zip5="") -> str:
    """Build the individual record ID used by matching and key assignment (zip5 only for a ZIP-split name)."""
    base = f"{name or ''}|{city or ''}|{state or ''}"
    if zip5:
        base = f"{base}|@{zip5}"
    return f"{base}|{suffix}" if suffix else base


def individual_donor_key(name, city, state, suffix="", zip5="") -> str:
    """Canonical donor_key (sha256 of record-id, first 12 hex); must stay identical to leadership_matcher's."""
    rid = individual_record_id(name, city, state, suffix, zip5)
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


def non_individual_donor_key(name) -> str:
    """Build the shared key used by organizations and committees."""
    normalized = normalize_committee_name(name)
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def apply_donor_key(df: pd.DataFrame, rid_to_key: dict) -> pd.DataFrame:
    """Apply scored donor_key: individuals take their cluster key from rid_to_key, silently falling back to a hash of their own rid when absent; non-individuals hash their normalized committee name instead."""

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


def apply_curated_key_merges(df: pd.DataFrame) -> int:
    """Repoint verified duplicate keys to their preferred key."""
    if "donor_key" not in df.columns or not KEY_MERGES:
        return 0

    mask = df["donor_key"].isin(KEY_MERGES)
    n = int(mask.sum())
    if n:
        df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(resolve_donor_key)
    return n


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


_REVIEW_COLUMNS = {
    "donor_key", "entity_type", "contributor_last_name",
    "contributor_first_name", "contributor_city",
    "contributor_state", "contributor_zip",
}


def _first_nonempty(values) -> str:
    values = values.dropna().astype(str)
    values = values[values.str.strip() != ""]
    return values.iloc[0] if len(values) else ""


def _review_summary(work: pd.DataFrame) -> pd.DataFrame:
    """Summarize the fields shown for each donor in the review."""
    donors = work.groupby("donor_key")
    return pd.DataFrame({
        "last": donors["contributor_last_name"].agg(_first_nonempty).str.upper(),
        "first": donors["contributor_first_name"].agg(_first_nonempty).str.upper(),
        "city": donors["contributor_city"].agg(_first_nonempty),
        "state": donors["contributor_state"].agg(_first_nonempty),
        "n": donors.size(),
        "amount": donors["_amt"].sum(),
    })


def _first_name_relation(first_a: str, first_b: str) -> str:
    """Describe a review-worthy relationship between two first names."""
    name_a = first_a.split()[0] if first_a else ""
    name_b = first_b.split()[0] if first_b else ""
    if not name_a or not name_b:
        return ""
    if name_a == name_b:
        return "same first name (different city?)"

    root_a = NICKNAME_MAP.get(name_a, name_a)
    root_b = NICKNAME_MAP.get(name_b, name_b)
    if root_a == root_b:
        return f"nickname {name_a}/{name_b}"

    short, long_name = sorted((name_a, name_b), key=len)
    if long_name.startswith(short) and short != long_name:
        return f"initial/prefix {short}->{long_name}"
    return ""


def _is_joint_pair(first_a: str, first_b: str, household_firsts) -> bool:
    """One first name is the other's joint filing with a co-filer of the same
    ZIP and surname ("MARC MELISSA" / "MARC" next to MELISSA, "GAYLEDAVID" /
    "GAYLE" next to DAVID): two donors on purpose, not a pending duplicate."""
    tokens_a, tokens_b = given_tokens(first_a), given_tokens(first_b)
    for joint, solo in ((tokens_a, tokens_b), (tokens_b, tokens_a)):
        if not joint or not solo or joint == solo:
            continue
        same_filer = joint[0] == solo[0] or (
            len(joint) == 1 and joint[0].startswith(solo[0])
        )
        others = {given_tokens(first) for first in household_firsts} - {joint, solo}
        if same_filer and joint_partners(joint, {joint, solo}, others):
            return True
    return False


def _review_row(
    summary: pd.DataFrame, zip_code: str, last_name: str,
    key_a: str, key_b: str, reason: str,
) -> dict:
    amount_a = float(summary.at[key_a, "amount"])
    amount_b = float(summary.at[key_b, "amount"])
    return {
        "reason": reason, "zip": zip_code, "last_name": last_name,
        "donor_key_a": key_a, "first_a": summary.at[key_a, "first"],
        "city_a": summary.at[key_a, "city"],
        "state_a": summary.at[key_a, "state"],
        "donations_a": int(summary.at[key_a, "n"]),
        "amount_a": round(amount_a, 2),
        "donor_key_b": key_b, "first_b": summary.at[key_b, "first"],
        "city_b": summary.at[key_b, "city"],
        "state_b": summary.at[key_b, "state"],
        "donations_b": int(summary.at[key_b, "n"]),
        "amount_b": round(amount_b, 2),
        "combined_amount": round(amount_a + amount_b, 2),
    }


def _review_candidates(
    individuals: pd.DataFrame, summary: pd.DataFrame,
) -> list[dict]:
    """Find distinct donors sharing ZIP, surname and related first names."""
    zip_profiles = individuals[["donor_key", "contributor_zip"]].dropna()
    has_zip = zip_profiles["contributor_zip"].astype(str).str.strip() != ""
    zip_profiles = zip_profiles[has_zip].drop_duplicates()
    zip_profiles = zip_profiles.join(summary["last"], on="donor_key")

    seen: set[tuple[str, str]] = set()
    rows = []
    for (zip_code, last_name), group in zip_profiles.groupby(
        ["contributor_zip", "last"]
    ):
        donor_keys = sorted(group["donor_key"].unique())
        if not last_name or len(donor_keys) < 2:
            continue
        household_firsts = {summary.at[key, "first"] for key in donor_keys}
        for key_a, key_b in combinations(donor_keys, 2):
            if (key_a, key_b) in seen:
                continue
            seen.add((key_a, key_b))
            first_a = summary.at[key_a, "first"]
            first_b = summary.at[key_b, "first"]
            reason = _first_name_relation(first_a, first_b)
            if not reason:
                continue
            if names_must_stay_separate(
                f"{last_name}, {first_a}", f"{last_name}, {first_b}"
            ) or _is_joint_pair(first_a, first_b, household_firsts):
                continue
            rows.append(_review_row(
                summary, zip_code, last_name, key_a, key_b, reason,
            ))
    return rows


def build_donor_dedup_review(df: pd.DataFrame, out_dir) -> int:
    """Write likely duplicate donor pairs for human review; never merge."""
    if not out_dir or not _REVIEW_COLUMNS <= set(df.columns):
        return 0
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    if individuals.empty:
        return 0

    amount = pd.to_numeric(
        df["contribution_receipt_amount"], errors="coerce"
    ).fillna(0)
    work = df.assign(_amt=amount).loc[individuals.index]
    rows = _review_candidates(individuals, _review_summary(work))
    path = Path(out_dir) / "donor_dedup_review.csv"
    if not rows:
        path.unlink(missing_ok=True)
        return 0

    rows.sort(key=lambda row: -row["combined_amount"])
    pd.DataFrame(rows).to_csv(path, index=False, na_rep="")
    logger.info(f"  Donor-dedup review -> {path} ({len(rows):,} candidate pairs)")
    return len(rows)
