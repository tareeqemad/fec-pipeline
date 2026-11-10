"""Set each filing's employer status and its donor's previous employer."""

import pandas as pd

from fec.cleaning.work.status import classify_employer_statuses
from fec.resolve.previous import previous_employers


def apply_results(df: pd.DataFrame) -> pd.DataFrame:
    status = classify_employer_statuses(df)
    status[df["entity_type"].eq("COMMITTEE/PAC")] = "committee"
    status[df["entity_type"].eq("ORGANIZATION")] = "organization"
    df["employer_status"] = status
    df["previous_employer"] = previous_employers(df, status)
    return df
