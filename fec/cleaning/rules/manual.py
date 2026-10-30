"""Manual corrections matched by FEC sub_id."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.normalize import _categorize, categorize_final
from fec.config.constants import EMPLOYER_STATUS_VALUES, STATUS_WORDS
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.config.occupation_rules.categories import VALID_CATEGORIES
from fec.env import MANUAL_EMPLOYER_OVERRIDES_CSV
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = MANUAL_EMPLOYER_OVERRIDES_CSV
CLEAR_PREVIOUS_EMPLOYER = "[CLEAR]"
EMPTY_OCCUPATION_CATEGORY = "OTHER"
FILED_WORK_FIELDS = (
    "sub_id", "contributor_employer", "contributor_occupation",
    "contributor_first_name", "contributor_last_name",
)
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
            fields[column] = pd.NA if value.upper() == CLEAR_PREVIOUS_EMPLOYER else value
    previous = (row.get("previous_employer") or "").strip()
    if previous:
        fields["previous_employer"] = (
            "" if previous.upper() == CLEAR_PREVIOUS_EMPLOYER else previous
        )
    # Row corrections use the shared company spelling.
    for column in ("contributor_employer", "previous_employer"):
        if isinstance(fields.get(column), str):
            fields[column] = reviewed_employer_name(fields[column])
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
    if "previous_employer" in fields:
        company_fields["previous_employer"] = fields["previous_employer"]
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
    display_previous_ids = set()
    for row in _override_rows(path, mtime_ns, size):
        after_matching = (row.get("after_matching") or "").strip().lower() == "true"
        if before_matching and after_matching:
            continue
        sid = (row.get("sub_id") or "").strip()
        fields = _override_fields(row, company_names_only)
        if sid and fields:
            overrides[sid] = fields
            display_previous_ids.discard(sid)
            if after_matching and "previous_employer" in fields:
                display_previous_ids.add(sid)
    columns = {}
    for sid, fields in overrides.items():
        for column, value in fields.items():
            columns.setdefault(column, {})[sid] = value
    return overrides, columns, frozenset(display_previous_ids)


def _prepared_overrides(company_names_only, before_matching=False):
    path = OVERRIDES_CSV.resolve()
    stat = path.stat()
    return _parsed_overrides(path, stat.st_mtime_ns, stat.st_size, company_names_only, before_matching)


def _read_overrides(
    company_names_only: bool, *, before_matching: bool = False,
    display_previous_ids: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    overrides, _, display_ids = _prepared_overrides(company_names_only, before_matching)
    if display_previous_ids is not None:
        display_previous_ids.difference_update(overrides)
        display_previous_ids.update(display_ids)
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


# Words of 2+ letters, plus runs of single letters joined.
def _words(text: str) -> list[str]:
    tokens = re.findall(r"[A-Z0-9]+", str(text).upper())
    joined = "".join(token for token in tokens if len(token) == 1)
    return [token for token in tokens if len(token) > 1] + ([joined] if len(joined) > 1 else [])


# Same word, or one edit (4+ letters), two (6+).
def _same_word(a: str, b: str) -> bool:
    shorter = min(len(a), len(b))
    return a == b or (shorter >= 4 and levenshtein(a, b) <= (2 if shorter >= 6 else 1))


# The value spells out the abbreviation: SFSS -> SAN FRANCISCO...
def _is_initialism(value_words: list[str], filed_words: list[str]) -> bool:
    initials = "".join(word[0] for word in value_words)
    return len(initials) > 1 and initials in filed_words


def _employer_evidence(filed: pd.DataFrame) -> str:
    """Occupation is company evidence only when not a known job."""
    employer, occupation = (
        " ".join(filed.reindex(columns=[column]).fillna("").astype(str).to_numpy().ravel())
        for column in _WORK_FIELDS
    )
    if occupation.strip():
        category = _categorize(pd.Series([occupation.strip().upper()])).iloc[0]
        if category == "OTHER":
            return f"{employer} {occupation}"
    return employer


def _brings_outside_work(filed: pd.DataFrame, fields: dict) -> bool:
    """Work details absent from the filing, excluding the donor's name."""
    def text(columns):
        values = filed.reindex(columns=list(columns)).fillna("").astype(str)
        return " ".join(values.to_numpy().ravel())

    own_name = set(_words(text(_NAME_FIELDS)))
    evidence = {
        "contributor_employer": _employer_evidence(filed),
        "contributor_occupation": text(_WORK_FIELDS),
    }
    for column in _WORK_FIELDS:
        value = fields.get(column)
        if value is pd.NA or not value or value.upper() in EMPLOYER_STATUS_VALUES | STATUS_WORDS:
            continue
        filed_words = [word for word in _words(evidence[column]) if word not in own_name]
        value_words = _words(value)
        shares_word = any(_same_word(a, b) for a in value_words for b in filed_words)
        if not shares_word and not _is_initialism(value_words, filed_words):
            return True
    return False


def outside_work_sub_ids(filed: pd.DataFrame) -> set[str]:
    """IDs whose override adds work absent from the raw filing."""
    if not OVERRIDES_CSV.exists():
        return set()
    overrides = _read_overrides(company_names_only=False, before_matching=True)
    rows = filed[filed["sub_id"].astype(str).str.strip().isin(overrides)]
    outside = set()
    for sub_id, row in zip(rows["sub_id"], rows.itertuples(index=False)):
        sid = str(sub_id).strip()
        filed_row = pd.DataFrame([row._asdict()])
        if _brings_outside_work(filed_row, overrides[sid]):
            outside.add(sid)
    return outside


# Apply corrections by column to matching filing IDs.
def _apply_overrides(
    df, overrides: dict[str, dict[str, str]], display_previous_ids=(),
    *, column_values=None,
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
            if column == "previous_employer":
                # A display correction can rename known history, never create it.
                has_previous = df[column].fillna("").astype(str).str.strip().ne("")
                mask &= ~sub_ids.isin(display_previous_ids) | has_previous
            if mask.any():
                df.loc[mask, column] = [values[sid] for sid in sub_ids[mask]]
    return changed, missing


# Every field an override can set, for the audit.
OVERRIDE_FIELDS = (
    "contributor_employer", "contributor_occupation", "occupation_category",
    "contributor_city", "contributor_street_1", "contributor_street_2",
    "contributor_zip", "previous_employer",
)


def apply_manual_employer_overrides(
    df,
    *,
    company_names_only: bool = False,
) -> int:
    """Apply per-row overrides matched on sub_id."""
    if not OVERRIDES_CSV.exists():
        return 0

    overrides, columns, display_previous_ids = _prepared_overrides(
        company_names_only, before_matching=not company_names_only,
    )

    if not overrides:
        return 0


    changed, missing = _apply_overrides(df, overrides, display_previous_ids, column_values=columns)
    if missing:
        logger.debug(
            f"  manual_overrides: {missing} sub_id(s) in the override file "
            f"not found in this dataset (may be from a different committee/period)"
        )
    return changed
