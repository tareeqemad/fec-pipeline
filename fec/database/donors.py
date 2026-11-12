"""Load one donor per donor key."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.connection import to_rows


def load_donors(cur: Any, df: pd.DataFrame) -> None:
    """Load each donor as named on their latest filing and add donor_id to df."""
    latest = df.sort_values("contribution_receipt_date", ascending=False).drop_duplicates("donor_key")
    latest = latest.sort_values("donor_key")
    # A committee or organization has only a full name; it is stored as the last name.
    unnamed = latest["contributor_last_name"].isna() & latest["entity_type"].ne("INDIVIDUAL")
    latest.loc[unnamed, "contributor_last_name"] = latest["contributor_name"]
    columns = ["donor_key", "entity_type", "contributor_first_name", "contributor_last_name", "identity_status"]
    donor_ids = execute_values(
        cur,
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name, identity_status) VALUES %s
        RETURNING donor_key, donor_id
        """,
        to_rows(latest[columns]),
        page_size=5000,
        fetch=True,
    )
    df["donor_id"] = df["donor_key"].map(dict(donor_ids))
