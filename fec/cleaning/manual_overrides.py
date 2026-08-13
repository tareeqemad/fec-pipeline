"""Hand-curated per-row employer/occupation/city corrections (record-specific fixes a global rule can't express safely), read from data/manual_employer_overrides.csv keyed by sub_id and applied as the LAST step so they survive every re-clean."""
from __future__ import annotations

import csv

from fec.config.constants import NOT_REAL_EMPLOYER
from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = PROJECT_ROOT / "data" / "manual_employer_overrides.csv"
CLEAR_PREVIOUS_EMPLOYER = "[CLEAR]"


def apply_manual_employer_overrides(
    df, *, company_names_only: bool = False,
) -> int:
    """Apply per-row overrides matched on sub_id."""
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
            previous = (row.get("previous_employer") or "").strip()
            if previous:
                fields["previous_employer"] = (
                    "" if previous.upper() == CLEAR_PREVIOUS_EMPLOYER
                    else previous
                )
            if company_names_only:
                has_employer = "contributor_employer" in fields
                has_previous = "previous_employer" in fields
                employer = fields.get("contributor_employer", "")
                previous = fields.get("previous_employer", "")
                fields = {}
                if has_employer and employer.upper() not in NOT_REAL_EMPLOYER:
                    fields["contributor_employer"] = employer
                if has_previous:
                    fields["previous_employer"] = previous
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
