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

from fec.donor_match.normalize import NICKNAME_MAP
from fec.donor_match.rules import identities_must_stay_separate, names_must_stay_separate
from fec.io import write_csv_atomic
from fec.log import get_logger

logger = get_logger(__name__)


def _related(first_a: str, first_b: str) -> str:
    """How two first names are related, or '' when they are not."""
    a, b = (first_a.split() or [""])[0], (first_b.split() or [""])[0]
    if not a or not b:
        return ""
    if a == b:
        return "same first name (different city?)"
    if NICKNAME_MAP.get(a, a) == NICKNAME_MAP.get(b, b):
        return f"nickname {a}/{b}"
    short, long = sorted((a, b), key=len)
    return f"initial/prefix {short}->{long}" if long.startswith(short) else ""


def build_donor_dedup_review(df: pd.DataFrame, out_dir) -> int:
    """Write the pairs to data/reports/donor_dedup_review.csv, largest combined amount first."""
    people = df[df["entity_type"].eq("INDIVIDUAL")].copy() if out_dir else df.iloc[:0]
    path = Path(out_dir or ".") / "donor_dedup_review.csv"
    if people.empty:
        return 0
    text = (
        people[
            [
                "contributor_last_name",
                "contributor_first_name",
                "contributor_city",
                "contributor_state",
                "contributor_zip",
                "contributor_name",
            ]
        ]
        .fillna("")
        .astype(str)
        .apply(lambda column: column.str.strip())
    )
    people[text.columns] = text
    people["amount"] = pd.to_numeric(people["contribution_receipt_amount"], errors="coerce").fillna(0)

    def first_filed(values: pd.Series) -> str:
        return next((value for value in values if value), "")

    donors = people.groupby("donor_key").agg(
        last=("contributor_last_name", lambda v: first_filed(v).upper()),
        first=("contributor_first_name", lambda v: first_filed(v).upper()),
        city=("contributor_city", first_filed),
        state=("contributor_state", first_filed),
        donations=("amount", "size"),
        amount=("amount", "sum"),
    )
    held = (
        set(people.loc[people["identity_status"].ne("confirmed"), "donor_key"])
        if "identity_status" in people
        else set()
    )
    filed = people.assign(
        name=people["contributor_name"].where(
            people["contributor_name"].ne(""), people["contributor_last_name"] + ", " + people["contributor_first_name"]
        )
    )
    identities = {
        key: rows.to_dict("records")
        for key, rows in filed.rename(
            columns={
                "contributor_city": "city",
                "contributor_state": "state",
                "contributor_zip": "zip5",
            }
        )[["donor_key", "name", "city", "state", "zip5"]]
        .drop_duplicates()
        .groupby("donor_key")
    }

    rows, seen = [], set()
    places = people.loc[people["contributor_zip"].ne(""), ["donor_key", "contributor_zip"]].drop_duplicates()
    places["last"] = places["donor_key"].map(donors["last"])
    for (zip_code, last), group in places[places["last"].ne("")].groupby(["contributor_zip", "last"]):
        keys = sorted(group["donor_key"].unique())
        for a, b in combinations(keys, 2):
            first_a, first_b = donors.at[a, "first"], donors.at[b, "first"]
            reason = _related(first_a, first_b)
            if (a, b) in seen or not reason or {a, b} & held:
                continue
            seen.add((a, b))
            if names_must_stay_separate(f"{last}, {first_a}", f"{last}, {first_b}") or any(
                identities_must_stay_separate(x, y) for x in identities[a] for y in identities[b]
            ):
                continue
            row = {"reason": reason, "zip": zip_code, "last_name": last}
            for side, key in (("a", a), ("b", b)):
                row |= {
                    f"donor_key_{side}": key,
                    f"first_{side}": donors.at[key, "first"],
                    f"city_{side}": donors.at[key, "city"],
                    f"state_{side}": donors.at[key, "state"],
                    f"donations_{side}": int(donors.at[key, "donations"]),
                    f"amount_{side}": round(float(donors.at[key, "amount"]), 2),
                }
            rows.append(row | {"combined_amount": round(float(donors.at[a, "amount"] + donors.at[b, "amount"]), 2)})
    if not rows:
        path.unlink(missing_ok=True)
        return 0
    rows.sort(key=lambda row: -row["combined_amount"])
    write_csv_atomic(pd.DataFrame(rows), path, index=False, na_rep="")
    logger.info("Review: %s donor pairs", f"{len(rows):,}")
    return len(rows)
