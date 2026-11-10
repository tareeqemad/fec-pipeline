"""Committee names and IDs from data/reference/committees.csv."""

from __future__ import annotations

import csv
from functools import lru_cache

from fec.env import COMMITTEES_CSV


@lru_cache(maxsize=1)
def load_committees() -> list[dict]:
    """Cache committee rows; empty CSV values become None."""
    if not COMMITTEES_CSV.exists():
        return []
    with open(COMMITTEES_CSV, newline="", encoding="utf-8") as fh:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(fh)]


def committee_id_to_name() -> dict[str, str]:
    """Map configured FEC committee IDs to their short names."""
    return {r["committee_number"]: r["committee_short"] for r in load_committees() if r.get("committee_number")}
