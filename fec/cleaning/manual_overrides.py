"""Hand-curated per-row corrections keyed by FEC sub_id."""

from __future__ import annotations

import csv

from fec.config.constants import NOT_REAL_EMPLOYER
from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = PROJECT_ROOT / "data" / "manual_employer_overrides.csv"
CLEAR_PREVIOUS_EMPLOYER = "[CLEAR]"
_ROW_FIELDS = (
    "contributor_employer",
    "contributor_occupation",
    "contributor_city",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_zip",
)


def _override_fields(row: dict, company_names_only: bool) -> dict[str, str]:
    fields = {
        column: (row.get(column) or "").strip()
        for column in _ROW_FIELDS
        if (row.get(column) or "").strip()
    }
    previous = (row.get("previous_employer") or "").strip()
    if previous:
        fields["previous_employer"] = (
            "" if previous.upper() == CLEAR_PREVIOUS_EMPLOYER else previous
        )
    if not company_names_only:
        return fields

    company_fields = {}
    employer = fields.get("contributor_employer")
    if employer and employer.upper() not in NOT_REAL_EMPLOYER:
        company_fields["contributor_employer"] = employer
    if "previous_employer" in fields:
        company_fields["previous_employer"] = fields["previous_employer"]
    return company_fields


def _read_overrides(company_names_only: bool) -> dict[str, dict[str, str]]:
    overrides = {}
    with OVERRIDES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = (row.get("sub_id") or "").strip()
            fields = _override_fields(row, company_names_only)
            if sid and fields:
                overrides[sid] = fields
    return overrides


def _apply_overrides(df, overrides: dict[str, dict[str, str]]) -> tuple[int, int]:
    sub_ids = df["sub_id"].astype(str).str.strip()
    changed = 0
    missing = 0
    for sid, fields in overrides.items():
        mask = sub_ids == sid
        if not mask.any():
            missing += 1
            continue
        for column, value in fields.items():
            if column in df.columns:
                df.loc[mask, column] = value
        changed += int(mask.sum())
    return changed, missing


def apply_manual_employer_overrides(
    df,
    *,
    company_names_only: bool = False,
) -> int:
    """Apply per-row overrides matched on sub_id."""
    if not OVERRIDES_CSV.exists():
        return 0

    overrides = _read_overrides(company_names_only)

    if not overrides:
        return 0

    if "sub_id" not in df.columns:
        logger.warning("  manual_overrides: no sub_id column - skipping")
        return 0

    changed, missing = _apply_overrides(df, overrides)
    if missing:
        logger.info(
            f"  manual_overrides: {missing} sub_id(s) in the override file "
            f"not found in this dataset (may be from a different committee/period)"
        )
    return changed
