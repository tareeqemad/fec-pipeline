"""Normalize ZIP codes to five digits."""

import numpy as np
import pandas as pd


def clean_zips(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the contributor ZIP column."""
    df["contributor_zip"] = _clean_zip_raw(df["contributor_zip"])
    return df


def _clean_zip_raw(raw: pd.Series) -> pd.Series:
    """Strip non-digits, pad/truncate to 5 digits, and null unassigned ZIPs."""
    raw = raw.astype(str).str.strip().str.replace(r"[^\d]", "", regex=True)

    result = raw.str.zfill(5).str[:5].where(raw.str.len() <= 5, raw.str.zfill(9).str[:5])

    # ZIPs below 00501 are unassigned typos; nulling them is safe.
    invalid = ~result.str.match(r"^\d{5}$", na=False) | (result < "00501")
    result[invalid] = np.nan

    return result
