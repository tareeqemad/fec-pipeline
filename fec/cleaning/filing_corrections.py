"""Reviewed corrections for single filings, matched by FEC sub_id (manual_employer_overrides.csv).

A filled cell replaces the filing's value and [CLEAR] empties it. Rows marked
after_matching wait until donors are matched, so the correction does not
change who the donor is.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from fec.cleaning.work.company_names import reviewed_employer_name
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.settings import MANUAL_EMPLOYER_OVERRIDES_CSV
from fec.vocabulary.occupations import VALID_CATEGORIES

CLEAR = "[CLEAR]"

FIELDS = (
    "contributor_employer",
    "contributor_occupation",
    "occupation_category",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_zip",
)


@lru_cache(maxsize=1)
def _read_reviewed_filings() -> pd.DataFrame:
    """Read the reviewed rows by sub_id, employers in their shared company spelling."""
    if not MANUAL_EMPLOYER_OVERRIDES_CSV.exists():
        return pd.DataFrame(columns=[*FIELDS, "after_matching"])
    rows = pd.read_csv(MANUAL_EMPLOYER_OVERRIDES_CSV, dtype=str, keep_default_na=False)
    rows = rows.apply(lambda column: column.str.strip()).set_index("sub_id")
    unknown = set(rows["occupation_category"]) - VALID_CATEGORIES - {""}
    if unknown:
        raise ValueError(f"Invalid manual occupation category: {sorted(unknown)}")
    rows["contributor_employer"] = rows["contributor_employer"].map(
        lambda name: reviewed_employer_name(name) if name and name.upper() != CLEAR else name
    )
    return rows


def apply_reviewed_filings(df: pd.DataFrame, matched: bool = False) -> None:
    """Write the reviewed cells; before donors are matched, the rows marked after_matching wait."""
    rows = _read_reviewed_filings()
    if not matched:
        rows = rows[rows["after_matching"].str.lower().ne("true")]
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in FIELDS:
        cells = rows.loc[rows[column].ne(""), column]
        found = sub_ids[sub_ids.isin(cells.index)].map(cells)
        df.loc[found.index, column] = found.where(found.str.upper().ne(CLEAR), np.nan)


def categorize_records(df: pd.DataFrame) -> pd.Series:
    """Return the category of each occupation, a reviewed category for the filing first."""
    reviewed = _read_reviewed_filings()["occupation_category"]
    reviewed = df["sub_id"].astype(str).str.strip().map(reviewed[reviewed.ne("")])
    return categorize_occupations(df["contributor_occupation"]).where(reviewed.isna(), reviewed)
