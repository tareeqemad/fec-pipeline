"""Restore a house number the filing lost, from the donor's other filings."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.addresses.fixes.recovery import _STREET_TYPE_RE


# backfill a missing house number from the donor's numbered filing
def _recover_house_number_from_donor(df: pd.DataFrame) -> int:
    """Backfill a missing house number from the same donor's numbered filing of the same street."""
    # a typed-but-unnumbered street ("FAIRWAY DR") passes _is_usable_street yet
    # only geocodes to a centroid. conservative: the number-stripped street must
    # match EXACTLY, key is name+state, only the dominant numbered form is used.
    streets = df["contributor_street_1"].fillna("").astype(str).str.upper().str.strip()
    is_indiv = df["entity_type"] == "INDIVIDUAL"
    has_type = streets.str.contains(_STREET_TYPE_RE)
    starts_num = streets.str.match(r"^\d")
    no_number = (
        is_indiv
        & has_type
        & ~starts_num
        & ~streets.str.startswith("PO BOX")
        & (streets != "")
    )
    if not no_number.any():
        return 0

    numbered = df[is_indiv & starts_num]
    if numbered.empty:
        return 0

    # drop a leading house number from a street string
    def _strip_num(x: str) -> str:
        return re.sub(r"^\d+\s+", "", str(x).upper().strip())

    numbered_streets = numbered[
        ["contributor_name", "contributor_state", "contributor_street_1"]
    ].copy()
    numbered_streets["_stripped"] = numbered_streets["contributor_street_1"].map(
        _strip_num
    )
    key_full = (
        numbered_streets.groupby(
            ["contributor_name", "contributor_state", "_stripped"]
        )["contributor_street_1"]
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )

    n_filled = 0
    for idx in df[no_number].index:
        full = key_full.get(
            (
                df.at[idx, "contributor_name"],
                df.at[idx, "contributor_state"],
                streets.at[idx],
            )
        )
        if full and str(full).upper().strip() != streets.at[idx]:
            df.at[idx, "contributor_street_1"] = full
            n_filled += 1
    return n_filled


# decide if one house number is a truncation of another
def _house_number_replacement(first, second, counts) -> tuple | None:
    first_parts = str(first).split(" ", 1)
    second_parts = str(second).split(" ", 1)
    if len(first_parts) < 2 or len(second_parts) < 2:
        return None
    if first_parts[1] != second_parts[1]:
        return None

    first_number, second_number = first_parts[0], second_parts[0]
    if not first_number.isdigit() or not second_number.isdigit():
        return None
    if not (
        first_number.startswith(second_number) or second_number.startswith(first_number)
    ):
        return None

    if counts[first] <= 2 and counts[second] >= 5:
        return first, second
    if counts[second] <= 2 and counts[first] >= 5:
        return second, first
    return None


# replace a rare truncated house number with donor's common form
def _truncated_house_numbers(df: pd.DataFrame) -> int:
    """Replace a rare truncated house number with the donor's common form."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    changed = 0
    for donor_key, group in individuals.groupby("donor_key"):
        streets = group["contributor_street_1"].dropna().value_counts()
        for first in streets.index:
            for second in streets.index:
                if first >= second:
                    continue
                replacement = _house_number_replacement(first, second, streets)
                if replacement is None:
                    continue
                old, new = replacement
                mask = (df["donor_key"] == donor_key) & (
                    df["contributor_street_1"] == old
                )
                df.loc[mask, "contributor_street_1"] = new
                changed += int(mask.sum())
    return changed
