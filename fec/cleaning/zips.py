"""Clean the ZIP field to a 5-digit or ZIP+4 string."""
import numpy as np
import pandas as pd


def clean_zips(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Extract and normalize 5-digit ZIP codes; returns (df, counts)."""
    # FEC sends 5- or 9-digit; keep the 5-digit form under the same column name
    df['contributor_zip'], n_invalid = _clean_zip_raw(df['contributor_zip'])
    return df, {
        'cleaned': int(df['contributor_zip'].notna().sum()),
        'invalid_nulled': n_invalid,
    }


def _clean_zip_raw(raw: pd.Series) -> tuple[pd.Series, int]:
    """Clean raw ZIP strings to 5 digits; returns (series, n_invalid_nulled)."""
    raw = raw.astype(str).str.strip().str.replace(r'[^\d]', '', regex=True)

    result = raw.str.zfill(5).str[:5].where(
        raw.str.len() <= 5, raw.str.zfill(9).str[:5]
    )

    # lowest USPS-assigned ZIP is 00501; below is unassigned (catches digit-drop
    # typos like "00034"). nulling is safe: the per-donor same-street fill
    # downstream restores the ZIP from the donor's own other filings.
    invalid = ~result.str.match(r'^\d{5}$', na=False) | (result < '00501')
    n_invalid = int(invalid.sum())
    result[invalid] = np.nan

    return result, n_invalid
