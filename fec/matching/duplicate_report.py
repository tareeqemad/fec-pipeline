"""Possible duplicate donors, listed for a person to review; nothing is merged here.

Two donors are listed when they share a ZIP and a surname and their first
names are the same, a nickname of one another (BOB, ROBERT) or one the
start of the other (J, JOHN). Pairs already decided are left out: a
reviewed rule keeps them apart, or the donor's identity is on hold (a
joint filing, an unproven owner).
"""

from itertools import combinations
from pathlib import Path

import pandas as pd

from fec.common.files import write_csv_atomic
from fec.common.log import get_logger
from fec.matching.identity_rules import identities_must_stay_separate, names_must_stay_separate
from fec.matching.name_compare import NICKNAME_MAP

logger = get_logger(__name__)


_TEXT_COLUMNS = [
    "contributor_last_name",
    "contributor_first_name",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "contributor_name",
]


def _describe_first_name_relation(first_a: str, first_b: str) -> str:
    word_a = (first_a.split() or [""])[0]
    word_b = (first_b.split() or [""])[0]
    if not word_a or not word_b:
        return ""
    if word_a == word_b:
        return "same first name (different city?)"
    if NICKNAME_MAP.get(word_a, word_a) == NICKNAME_MAP.get(word_b, word_b):
        return f"nickname {word_a}/{word_b}"
    short, long = sorted((word_a, word_b), key=len)
    return f"initial/prefix {short}->{long}" if long.startswith(short) else ""


def _find_first_filed(values: pd.Series) -> str:
    return next((value for value in values if value), "")


def _find_first_filed_upper(values: pd.Series) -> str:
    return _find_first_filed(values).upper()


def _summarize_donors(people: pd.DataFrame) -> pd.DataFrame:
    return people.groupby("donor_key").agg(
        last=("contributor_last_name", _find_first_filed_upper),
        first=("contributor_first_name", _find_first_filed_upper),
        city=("contributor_city", _find_first_filed),
        state=("contributor_state", _find_first_filed),
        donations=("amount", "size"),
        amount=("amount", "sum"),
    )


def _find_held_donors(people: pd.DataFrame) -> set:
    if "identity_status" not in people:
        return set()
    return set(people.loc[people["identity_status"].ne("confirmed"), "donor_key"])


def _list_identities(people: pd.DataFrame) -> dict[str, list[dict]]:
    filed_parts = people["contributor_last_name"] + ", " + people["contributor_first_name"]
    names = people["contributor_name"].where(people["contributor_name"].ne(""), filed_parts)
    places = people.assign(name=names).rename(
        columns={
            "contributor_city": "city",
            "contributor_state": "state",
            "contributor_zip": "zip5",
        }
    )
    identities = places[["donor_key", "name", "city", "state", "zip5"]].drop_duplicates()
    return {key: rows.to_dict("records") for key, rows in identities.groupby("donor_key")}


def _describe_donor(side: str, key: str, donors: pd.DataFrame) -> dict:
    return {
        f"donor_key_{side}": key,
        f"first_{side}": donors.at[key, "first"],
        f"city_{side}": donors.at[key, "city"],
        f"state_{side}": donors.at[key, "state"],
        f"donations_{side}": int(donors.at[key, "donations"]),
        f"amount_{side}": round(float(donors.at[key, "amount"]), 2),
    }


def _build_review_row(reason: str, zip_code: str, last: str, key_a: str, key_b: str, donors: pd.DataFrame) -> dict:
    row = {"reason": reason, "zip": zip_code, "last_name": last}
    row |= _describe_donor("a", key_a, donors)
    row |= _describe_donor("b", key_b, donors)
    combined = donors.at[key_a, "amount"] + donors.at[key_b, "amount"]
    row["combined_amount"] = round(float(combined), 2)
    return row


def _find_review_rows(people: pd.DataFrame, donors: pd.DataFrame, held: set, identities: dict) -> list[dict]:
    rows, seen = [], set()
    places = people.loc[people["contributor_zip"].ne(""), ["donor_key", "contributor_zip"]].drop_duplicates()
    places["last"] = places["donor_key"].map(donors["last"])
    named_places = places[places["last"].ne("")]
    for (zip_code, last), group in named_places.groupby(["contributor_zip", "last"]):
        keys = sorted(group["donor_key"].unique())
        for key_a, key_b in combinations(keys, 2):
            first_a, first_b = donors.at[key_a, "first"], donors.at[key_b, "first"]
            reason = _describe_first_name_relation(first_a, first_b)
            if (key_a, key_b) in seen or not reason or {key_a, key_b} & held:
                continue
            seen.add((key_a, key_b))
            if names_must_stay_separate(f"{last}, {first_a}", f"{last}, {first_b}"):
                continue
            if any(
                identities_must_stay_separate(identity_a, identity_b)
                for identity_a in identities[key_a]
                for identity_b in identities[key_b]
            ):
                continue
            rows.append(_build_review_row(reason, zip_code, last, key_a, key_b, donors))
    return rows


def write_duplicate_review(df: pd.DataFrame, out_dir: str | None) -> int:
    """Write the pairs to data/reports/donor_dedup_review.csv, largest combined amount first."""
    people = df[df["entity_type"].eq("INDIVIDUAL")].copy() if out_dir else df.iloc[:0]
    path = Path(out_dir or ".") / "donor_dedup_review.csv"
    if people.empty:
        return 0
    for column in _TEXT_COLUMNS:
        people[column] = people[column].fillna("").astype(str).str.strip()
    amounts = pd.to_numeric(people["contribution_receipt_amount"], errors="coerce")
    people["amount"] = amounts.fillna(0)

    donors = _summarize_donors(people)
    held = _find_held_donors(people)
    identities = _list_identities(people)
    rows = _find_review_rows(people, donors, held, identities)
    if not rows:
        path.unlink(missing_ok=True)
        return 0
    rows.sort(key=lambda row: -row["combined_amount"])
    write_csv_atomic(pd.DataFrame(rows), path, index=False, na_rep="")
    logger.info("Review: %s donor pairs", f"{len(rows):,}")
    return len(rows)
