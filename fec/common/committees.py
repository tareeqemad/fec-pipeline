"""Committee names and IDs from data/reference/committees.csv."""

from __future__ import annotations

from functools import lru_cache

from fec.common.files import read_rows
from fec.common.settings import COMMITTEES_CSV


@lru_cache(maxsize=1)
def read_committees() -> list[dict]:
    """Cache committee rows; empty CSV values become None."""
    if not COMMITTEES_CSV.exists():
        return []
    rows = read_rows(COMMITTEES_CSV)
    return [{column: (value if value != "" else None) for column, value in row.items()} for row in rows]


def committee_id_to_name() -> dict[str, str]:
    """Map configured FEC committee IDs to their short names."""
    return {
        committee["committee_number"]: committee["committee_short"]
        for committee in read_committees()
        if committee.get("committee_number")
    }
