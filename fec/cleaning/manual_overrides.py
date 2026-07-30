"""Hand-curated per-row employer/occupation/city corrections (record-specific fixes a global rule can't express safely), read from data/manual_employer_overrides.csv keyed by sub_id and applied as the LAST step so they survive every re-clean."""
from __future__ import annotations

import csv

from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = PROJECT_ROOT / "data" / "manual_employer_overrides.csv"


def apply_manual_employer_overrides(df) -> int:
    """Apply per-row overrides matched on sub_id; returns rows changed; safe no-op if the file is missing or empty."""
    if not OVERRIDES_CSV.exists():
        return 0

    overrides: dict[str, dict[str, str]] = {}
    with OVERRIDES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = (row.get("sub_id") or "").strip()
            fields = {
                col: (row.get(col) or "").strip()
                for col in ("contributor_employer", "contributor_occupation",
                            "contributor_city")
                if (row.get(col) or "").strip()
            }
            if sid and fields:
                overrides[sid] = fields

    if not overrides:
        return 0

    if "sub_id" not in df.columns:
        logger.warning("  manual_overrides: no sub_id column - skipping")
        return 0

    sub_id_str = df["sub_id"].astype(str).str.strip()
    n_changed = 0
    n_missing = 0
    for sid, fields in overrides.items():
        mask = sub_id_str == sid
        if mask.any():
            for col, val in fields.items():
                if col in df.columns:
                    df.loc[mask, col] = val
            n_changed += int(mask.sum())
        else:
            n_missing += 1

    if n_missing:
        logger.info(
            f"  manual_overrides: {n_missing} sub_id(s) in the override file "
            f"not found in this dataset (may be from a different committee/period)"
        )
    return n_changed
