"""Manual corrections matched by FEC sub_id."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import pandas as pd

from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.normalize import categorize_final
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.config.occupation_rules.categories import VALID_CATEGORIES
from fec.env import MANUAL_EMPLOYER_OVERRIDES_CSV
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = MANUAL_EMPLOYER_OVERRIDES_CSV
CLEAR = "[CLEAR]"
EMPTY_OCCUPATION_CATEGORY = "OTHER"
_WORK_FIELDS = ("contributor_employer", "contributor_occupation")
_NAME_FIELDS = ("contributor_first_name", "contributor_last_name")
_ROW_FIELDS = (
    "contributor_employer",
    "contributor_occupation",
    "contributor_city",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_zip",
)


def _override_fields(row: dict, company_names_only: bool) -> dict[str, str]:
    """Read selected fields; [CLEAR] removes a value."""
    fields = {}
    for column in _ROW_FIELDS:
        value = (row.get(column) or "").strip()
        if value:
            fields[column] = pd.NA if value.upper() == CLEAR else value
    # Row corrections use the shared company spelling.
    if isinstance(fields.get("contributor_employer"), str):
        fields["contributor_employer"] = reviewed_employer_name(fields["contributor_employer"])
    category = (row.get("occupation_category") or "").strip()
    if category:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid manual occupation category for sub_id {row.get('sub_id')}: {category}")
        fields["occupation_category"] = category
    if not company_names_only:
        return fields

    # Keep cleared cells empty after donor-history repairs.
    company_fields = {column: value for column, value in fields.items() if value is pd.NA}
    if "contributor_occupation" in company_fields:
        company_fields["occupation_category"] = EMPTY_OCCUPATION_CATEGORY
    employer = fields.get("contributor_employer")
    if employer is not pd.NA and employer and employer.upper() not in NOT_REAL_EMPLOYER:
        company_fields["contributor_employer"] = employer
    if category:
        company_fields["occupation_category"] = category
    # Restore only the fields explicitly marked for preservation.
    preserved = [
        name.strip()
        for name in (row.get("preserve_fields") or "").split(";")
        if name.strip()
    ]
    if set(preserved) - set(_ROW_FIELDS):
        raise ValueError(f"Invalid preserved fields for sub_id {row.get('sub_id')}: {preserved}")
    company_fields.update({column: fields[column] for column in preserved if column in fields})
    return company_fields


@lru_cache(maxsize=8)
def _override_rows(path: Path, mtime_ns: int, size: int) -> tuple[dict, ...]:
    """Read each file version once; modes share the same rows."""
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(csv.DictReader(handle))


@lru_cache(maxsize=32)
def _parsed_overrides(path, mtime_ns, size, company_names_only, before_matching):
    overrides = {}
    for row in _override_rows(path, mtime_ns, size):
        after_matching = (row.get("after_matching") or "").strip().lower() == "true"
        if before_matching and after_matching:
            continue
        sid = (row.get("sub_id") or "").strip()
        fields = _override_fields(row, company_names_only)
        if sid and fields:
            overrides[sid] = fields
    columns = {}
    for sid, fields in overrides.items():
        for column, value in fields.items():
            columns.setdefault(column, {})[sid] = value
    return overrides, columns


def _prepared_overrides(company_names_only, before_matching=False):
    path = OVERRIDES_CSV.resolve()
    stat = path.stat()
    return _parsed_overrides(path, stat.st_mtime_ns, stat.st_size, company_names_only, before_matching)


def _read_overrides(company_names_only: bool, *, before_matching: bool = False) -> dict[str, dict[str, str]]:
    overrides, _ = _prepared_overrides(company_names_only, before_matching)
    return {sid: fields.copy() for sid, fields in overrides.items()}


def categorize_records(df: pd.DataFrame) -> pd.Series:
    """Derive categories, honoring reviewed exceptions for exact filing IDs."""
    expected = categorize_final(df["contributor_occupation"])
    if not OVERRIDES_CSV.exists():
        return expected
    categories = {
        sid: fields["occupation_category"]
        for sid, fields in _read_overrides(company_names_only=False).items()
        if "occupation_category" in fields
    }
    scoped = df["sub_id"].astype(str).str.strip().map(categories)
    return expected.where(scoped.isna(), scoped)


# Apply corrections by column to matching filing IDs.
def _apply_overrides(
    df, overrides: dict[str, dict[str, str]], *, column_values=None,
) -> tuple[int, int]:
    sub_ids = df["sub_id"].astype(str).str.strip()
    changed = int(sub_ids.isin(overrides).sum())
    missing = len(overrides.keys() - set(sub_ids))
    for column in df.columns:
        values = column_values.get(column, {}) if column_values is not None else {
            sid: fields[column]
            for sid, fields in overrides.items()
            if column in fields
        }
        if values:
            mask = sub_ids.isin(values)
            if mask.any():
                df.loc[mask, column] = [values[sid] for sid in sub_ids[mask]]
    return changed, missing


def apply_manual_employer_overrides(
    df,
    *,
    company_names_only: bool = False,
) -> int:
    """Apply per-row overrides matched on sub_id."""
    if not OVERRIDES_CSV.exists():
        return 0

    overrides, columns = _prepared_overrides(
        company_names_only, before_matching=not company_names_only,
    )

    if not overrides:
        return 0


    changed, missing = _apply_overrides(df, overrides, column_values=columns)
    if missing:
        logger.debug(
            f"  manual_overrides: {missing} sub_id(s) in the override file "
            f"not found in this dataset (may be from a different committee/period)"
        )
    return changed
