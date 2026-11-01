"""Apply each filing's employer status and previous employer."""

import pandas as pd
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.work.employer_names import expand_abbreviation_text
from fec.cleaning.work.previous import normalize_previous_employer_column
from fec.cleaning.work.status import classify_employer_statuses
from fec.resolve.previous import previous_employers


def apply_results(df: pd.DataFrame) -> pd.DataFrame:
    status = classify_employer_statuses(df)
    status[df["entity_type"].eq("COMMITTEE/PAC")] = "committee"
    status[df["entity_type"].eq("ORGANIZATION")] = "organization"
    df["employer_status"] = status
    df["previous_employer"] = previous_employers(df, status)

    normalize_previous_employer_column(df)
    _expand_previous_employer_abbreviations(df)
    apply_reviewed_employer_names(df)
    return df


def _expand_previous_employer_abbreviations(df: pd.DataFrame) -> None:
    """Apply reviewed names to previous employers."""
    previous = df["previous_employer"].fillna("").astype(str)
    named = previous.str.strip().ne("")
    if named.any():
        df.loc[named, "previous_employer"] = expand_abbreviation_text(previous[named])
