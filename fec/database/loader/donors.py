"""Load the donors table."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.log import get_logger

from ._base import to_native

logger = get_logger(__name__)


def load_donors(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 1: one donors row per donor_key; returns donor_key -> donor_id."""
    logger.info("\n-- 1/8 Loading donors --")
    start = time.time()

    donor_rows = []
    for donor_key, group in df.groupby('donor_key'):
        # Names are already canonical per donor_key (clean.py); any row gives the same pair.
        latest = group.sort_values('contribution_receipt_date', ascending=False).iloc[0]
        entity = to_native(latest['entity_type'])
        first = to_native(latest['contributor_first_name'])
        last = to_native(latest['contributor_last_name'])
        # Committees: first/last are NULL, full name goes in last_name
        if not last and entity in ('COMMITTEE/PAC', 'ORGANIZATION'):
            last = to_native(latest['contributor_name'])
        donor_rows.append((donor_key, entity, first, last))

    execute_values(cur,
        "INSERT INTO donors (donor_key, entity_type, first_name, last_name) "
        "VALUES %s",
        donor_rows, page_size=5000)
    conn.commit()

    cur.execute("SELECT donor_id, donor_key FROM donors")
    donor_key_to_id = {row[1]: row[0] for row in cur.fetchall()}
    logger.info(f"  donors: {len(donor_key_to_id):,} ({time.time()-start:.1f}s)")
    return donor_key_to_id
