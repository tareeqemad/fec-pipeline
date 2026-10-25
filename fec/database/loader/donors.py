"""Load the donors table."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.loader._base import to_native


# Load one donor per donor_key and return its ID.
def load_donors(cur: Any, df: pd.DataFrame) -> dict:
    donor_rows = []
    for donor_key, group in df.groupby('donor_key'):
        # Names are already cleaned.
        latest = group.sort_values('contribution_receipt_date', ascending=False).iloc[0]
        entity = to_native(latest['entity_type'])
        first = to_native(latest['contributor_first_name'])
        last = to_native(latest['contributor_last_name'])
        # Organizations use last_name.
        if not last and entity in ('COMMITTEE/PAC', 'ORGANIZATION'):
            last = to_native(latest['contributor_name'])
        # Older CSVs default to confirmed.
        status = to_native(latest.get('identity_status')) or 'confirmed'
        donor_rows.append((donor_key, entity, first, last, status))

    execute_values(cur,
        "INSERT INTO donors (donor_key, entity_type, first_name, last_name, identity_status) "
        "VALUES %s",
        donor_rows, page_size=5000)

    cur.execute("SELECT donor_id, donor_key FROM donors")
    donor_ids = {donor_key: donor_id for donor_id, donor_key in cur.fetchall()}
    return donor_ids
