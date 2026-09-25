"""Report likely duplicate donors for human review; never merge."""
from itertools import combinations
from pathlib import Path

import pandas as pd

from fec.donor_match.constants import NICKNAME_MAP
from fec.donor_match.joint import given_tokens, joint_partners
from fec.donor_match.rules import (
    names_must_stay_separate,
)
from fec.log import get_logger

logger = get_logger(__name__)

_REVIEW_COLUMNS = {
    "donor_key", "entity_type", "contributor_last_name",
    "contributor_first_name", "contributor_city",
    "contributor_state", "contributor_zip",
}


# the first non-blank value in a series, else ''
def _first_nonempty(values) -> str:
    values = values.dropna().astype(str)
    values = values[values.str.strip() != ""]
    return values.iloc[0] if len(values) else ""


# summarize the fields shown for each donor in the review
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


# describe a review-worthy relationship between two first names
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


# true if one first name is the other's joint filing
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


# build one candidate-duplicate-pair row for the review CSV
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


# find distinct donors sharing ZIP, surname and related first names
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


# write likely duplicate donor pairs for human review; never merge
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
