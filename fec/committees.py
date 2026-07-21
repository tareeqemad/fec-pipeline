"""Committee reference data — single source of truth.

All committee identities (FEC number, display name, logo, fundraising totals)
live in ``data/database/committees.csv``. This module reads that file so the
cleaner, the web panel and the DB loader all agree on names without any
hard-coded mapping. To track a new committee, add a row to the CSV — nothing
in the code needs to change.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from typing import Dict, List

from fec.env import COMMITTEES_CSV


@lru_cache(maxsize=1)
def load_committees() -> List[dict]:
    """All committee rows from committees.csv (cached). Empty strings → None."""
    if not COMMITTEES_CSV.exists():
        return []
    with open(COMMITTEES_CSV, newline="", encoding="utf-8") as fh:
        return [{k: (v if v != "" else None) for k, v in row.items()}
                for row in csv.DictReader(fh)]


def committee_id_to_name() -> Dict[str, str]:
    """{FEC committee_number → committee_short}, only rows with an FEC number."""
    return {r["committee_number"]: r["committee_short"]
            for r in load_committees() if r.get("committee_number")}

