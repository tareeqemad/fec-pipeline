"""Load the contribution records."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.connection import to_rows


def load_contributions(cur: Any, df: pd.DataFrame) -> None:
    """Load every filing with the IDs of its donor, committee, donor address and employment."""
    rows = df[
        [
            "sub_id",
            "transaction_id",
            "donor_id",
            "committee_id",
            "donor_address_id",
            "donor_employment_id",
            "contribution_receipt_amount",
        ]
    ].copy()
    rows["receipt_date"] = pd.to_datetime(df["contribution_receipt_date"]).dt.date
    rows["election_cycle"] = pd.to_numeric(df["two_year_transaction_period"]).astype(int)
    execute_values(
        cur,
        """
        INSERT INTO contributions (
            sub_id, transaction_id, donor_id, committee_id, donor_address_id,
            donor_employment_id, amount, receipt_date, election_cycle
        ) VALUES %s
        """,
        to_rows(rows),
        page_size=5000,
    )
