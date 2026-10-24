"""Restore a house number the filing lost, from the donor's other filings."""
from __future__ import annotations

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

    numbered_streets = numbered[
        ["contributor_name", "contributor_state", "contributor_street_1"]
    ].copy()
    numbered_streets["_stripped"] = streets[is_indiv & starts_num].str.replace(
        r"^\d+\s+", "", regex=True
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
