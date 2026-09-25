"""Committee identities come from data/database/committees.csv (single source of truth); to track a new committee, add a row there - no code change."""
from __future__ import annotations

import csv
from functools import lru_cache

from fec.env import COMMITTEES_CSV


# load all committee rows from committees.csv, cached
@lru_cache(maxsize=1)
def load_committees() -> list[dict]:
    """All committee rows from committees.csv (cached). Empty strings -> None."""
    if not COMMITTEES_CSV.exists():
        return []
    with open(COMMITTEES_CSV, newline="", encoding="utf-8") as fh:
        return [{k: (v if v != "" else None) for k, v in row.items()}
                for row in csv.DictReader(fh)]


# map FEC committee_number to committee_short name
def committee_id_to_name() -> dict[str, str]:
    """{FEC committee_number -> committee_short}, only rows with an FEC number."""
    return {r["committee_number"]: r["committee_short"]
            for r in load_committees() if r.get("committee_number")}
