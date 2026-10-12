"""Fill the clean.py columns a test leaves out."""
import pandas as pd

from fec.contract import CLEAN_COLUMNS

DEFAULTS = {"identity_status": "confirmed"}


def with_clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add every missing clean column, blank by default."""
    for column in CLEAN_COLUMNS:
        if column not in frame.columns:
            frame[column] = DEFAULTS.get(column, "")
    return frame


def with_clean_fields(row: pd.Series) -> pd.Series:
    """The same for one row."""
    missing = {column: DEFAULTS.get(column, "") for column in CLEAN_COLUMNS if column not in row.index}
    return pd.concat([row, pd.Series(missing, dtype=object)]) if missing else row
