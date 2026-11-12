"""Reviewed corrections for single filings, by FEC sub_id (filing_corrections.csv).

A filled cell replaces the filing's value and [CLEAR] empties it; rows marked after_matching wait until donors
are matched, so the correction does not change who the donor is.
"""

from functools import lru_cache

import numpy as np
import pandas as pd

from fec.cleaning.work.company_names import reviewed_employer_name
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.settings import FILING_CORRECTIONS_CSV
from fec.vocabulary.occupations import VALID_CATEGORIES

CLEAR = "[CLEAR]"
# Columns of the file that say about the row, not about the filing.
_ROW_NOTES = ["note", "source", "after_matching"]


def _review_employer_name(name: str) -> str:
    if name and name.upper() != CLEAR:
        return reviewed_employer_name(name)
    return name


@lru_cache(maxsize=1)
def _read_reviewed_filings() -> pd.DataFrame:
    rows = pd.read_csv(FILING_CORRECTIONS_CSV, dtype=str, keep_default_na=False)
    rows = rows.apply(lambda column: column.str.strip()).set_index("sub_id")
    unknown = set(rows["occupation_category"]) - VALID_CATEGORIES - {""}
    if unknown:
        raise ValueError(f"Invalid manual occupation category: {sorted(unknown)}")
    rows["contributor_employer"] = rows["contributor_employer"].map(_review_employer_name)
    return rows


def apply_filing_corrections(df: pd.DataFrame, matched: bool = False) -> None:
    """Write the reviewed cells; before donors are matched, the rows marked after_matching wait."""
    rows = _read_reviewed_filings()
    if not matched:
        rows = rows[rows["after_matching"].str.lower().ne("true")]
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in rows.columns.drop(_ROW_NOTES):
        cells = rows.loc[rows[column].ne(""), column]
        found = sub_ids[sub_ids.isin(cells.index)].map(cells)
        df.loc[found.index, column] = found.where(found.str.upper().ne(CLEAR), np.nan)


def categorize_with_corrections(df: pd.DataFrame) -> pd.Series:
    """Return the category of each occupation, a reviewed category for the filing first."""
    reviewed = _read_reviewed_filings()["occupation_category"]
    reviewed = df["sub_id"].astype(str).str.strip().map(reviewed[reviewed.ne("")])
    return categorize_occupations(df["contributor_occupation"]).where(reviewed.isna(), reviewed)
