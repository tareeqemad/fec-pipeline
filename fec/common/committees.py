"""Committee names and IDs from data/reference/committees.csv."""

from __future__ import annotations

import csv
from functools import lru_cache

from fec.common.settings import COMMITTEES_CSV


@lru_cache(maxsize=1)
def read_committees() -> list[dict]:
    """Cache committee rows; empty CSV values become None."""
    if not COMMITTEES_CSV.exists():
        return []
    with open(COMMITTEES_CSV, newline="", encoding="utf-8") as handle:
        return [
            {column: (value if value != "" else None) for column, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def committee_id_to_name() -> dict[str, str]:
    """Map configured FEC committee IDs to their short names."""
    return {
        committee["committee_number"]: committee["committee_short"]
        for committee in read_committees()
        if committee.get("committee_number")
    }
