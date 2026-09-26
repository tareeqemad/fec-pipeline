"""Hand-curated per-row corrections keyed by FEC sub_id."""

from __future__ import annotations

import csv
import re

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import EMPLOYER_STATUS_VALUES, STATUS_WORDS
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = PROJECT_ROOT / "data" / "manual_employer_overrides.csv"
CLEAR_PREVIOUS_EMPLOYER = "[CLEAR]"
EMPTY_OCCUPATION_CATEGORY = "OTHER"  # the category every filing without an occupation gets
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


# build the override values for one row, honoring [CLEAR] markers
def _override_fields(row: dict, company_names_only: bool) -> dict[str, str]:
    # "[CLEAR]" removes a filed value that is not this donor's (a committee
    # that typed another person's employer and address under the donor's name)
    fields = {
        column: (
            pd.NA if (row.get(column) or "").strip().upper() == CLEAR_PREVIOUS_EMPLOYER
            else (row.get(column) or "").strip()
        )
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

    # a cleared cell stays cleared: the late pass undoes any refill from the
    # donor's other filings (TUCHIN's occupation came back as ATTORNEY)
    company_fields = {column: value for column, value in fields.items() if value is pd.NA}
    if "contributor_occupation" in company_fields:
        company_fields["occupation_category"] = EMPTY_OCCUPATION_CATEGORY
    employer = fields.get("contributor_employer")
    if employer is not pd.NA and employer and employer.upper() not in NOT_REAL_EMPLOYER:
        company_fields["contributor_employer"] = employer
    if "previous_employer" in fields:
        company_fields["previous_employer"] = fields["previous_employer"]
    return company_fields


# load the overrides CSV into a sub_id -> fields mapping
def _read_overrides(company_names_only: bool) -> dict[str, dict[str, str]]:
    overrides = {}
    with OVERRIDES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = (row.get("sub_id") or "").strip()
            fields = _override_fields(row, company_names_only)
            if sid and fields:
                overrides[sid] = fields
    return overrides


# words of two letters or more, plus a run of single letters joined ("F A" -> "FA")
def _words(text: str) -> list[str]:
    tokens = re.findall(r"[A-Z0-9]+", str(text).upper())
    joined = "".join(token for token in tokens if len(token) == 1)
    return [token for token in tokens if len(token) > 1] + ([joined] if len(joined) > 1 else [])


# same word, or a close spelling: one edit from four letters, two from six
def _same_word(a: str, b: str) -> bool:
    shorter = min(len(a), len(b))
    return a == b or (shorter >= 4 and levenshtein(a, b) <= (2 if shorter >= 6 else 1))


# the value spells out the filing's abbreviation: SFSS -> SAN FRANCISCO SPINE SURGEONS
def _is_initialism(value_words: list[str], filed_words: list[str]) -> bool:
    initials = "".join(word[0] for word in value_words)
    return len(initials) > 1 and initials in filed_words


# an override names an employer or occupation the filing's work fields never did
def _brings_outside_work(filed: pd.DataFrame, fields: dict) -> bool:
    """True when a new employer or occupation shares no word (or initials) with the
    employer and occupation this filing reported. The filer's own name is not a
    work detail (GIVNER filed as the employer names no firm), nor are statuses
    such as SELF-EMPLOYED or cleared cells."""
    text = lambda columns: " ".join(filed.reindex(columns=list(columns)).fillna("").astype(str).to_numpy().ravel())
    own_name = set(_words(text(_NAME_FIELDS)))
    filed_words = [word for word in _words(text(_WORK_FIELDS)) if word not in own_name]
    for column in _WORK_FIELDS:
        value = fields.get(column)
        if value is pd.NA or not value or value.upper() in EMPLOYER_STATUS_VALUES | STATUS_WORDS:
            continue
        value_words = _words(value)
        if not any(_same_word(a, b) for a in value_words for b in filed_words) \
                and not _is_initialism(value_words, filed_words):
            return True
    return False


# sub_ids whose override names work their raw filing never did
def outside_work_sub_ids(filed: pd.DataFrame) -> set[str]:
    """filed holds the raw FILED_WORK_FIELDS, taken before any cleaning step."""
    if not OVERRIDES_CSV.exists():
        return set()
    overrides = _read_overrides(company_names_only=False)
    rows = filed[filed["sub_id"].astype(str).str.strip().isin(overrides)]
    return {
        str(sub_id).strip()
        for sub_id, row in zip(rows["sub_id"], rows.itertuples(index=False))
        if _brings_outside_work(pd.DataFrame([row._asdict()]), overrides[str(sub_id).strip()])
    }


# write each override's fields onto its matching sub_id row
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


# apply per-row overrides matched on sub_id
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
