"""One reading of a contributor's name, and the reviewed corrections to it."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.common.settings import CONTRIBUTOR_NAME_RULES_CSV
from fec.vocabulary.names import COMM_TAIL_RE
from fec.vocabulary.tables import words

RULE_TYPES = {"exact_name", "sub_id", "first_name", "committee_name"}

_ZIP5_RE = re.compile(r"\d{5}")


def _read_first_name_rule(
    number: int, raw: str, corrected: str, zip_field: str
) -> list[tuple[str, str, str]]:
    """Parse one first_name rule into (surname, garbled word, zip5) keys."""
    last, comma, first = raw.partition(",")
    last, first = last.strip(), first.strip()
    if not comma or "," in first or not last or not first or len(first.split()) != 1:
        raise ValueError(f"first_name rule on row {number} must read 'SURNAME, GARBLED': {raw}")
    if "," in corrected or len(corrected.split()) != 1:
        raise ValueError(f"first_name rule on row {number} must correct one word: {corrected}")
    zips = [part.strip() for part in zip_field.split("|")] if zip_field else []
    if not zips or not all(_ZIP5_RE.fullmatch(zipcode) for zipcode in zips):
        problem = f"first_name rule on row {number} needs the filer's ZIP5 ('|'-separated)"
        raise ValueError(f"{problem}: {zip_field!r}")
    return [(last, first, zipcode) for zipcode in zips]


def _add_name_rule(rules: dict[str, dict], number: int, row: dict[str, str]) -> None:
    """Check one row of the name rules and add it to the rules of its type."""
    rule_type = (row.get("rule_type") or "").strip()
    raw = (row.get("raw_value") or "").strip()
    corrected = (row.get("corrected_value") or "").strip()
    source = (row.get("source") or "").strip()
    zip_field = (row.get("zip5") or "").strip()
    if rule_type not in RULE_TYPES:
        raise ValueError(f"Invalid name rule type on row {number}: {rule_type}")
    if not raw or not corrected or not source:
        raise ValueError(f"Invalid name rule on row {number}")
    if rule_type == "first_name":
        keys = _read_first_name_rule(number, raw, corrected, zip_field)
    elif zip_field:
        raise ValueError(f"zip5 only scopes first_name rules (row {number}, {rule_type})")
    else:
        keys = [raw]
    for key in keys:
        if key in rules[rule_type]:
            raise ValueError(f"Duplicate {rule_type} rule on row {number}: {raw}")
        rules[rule_type][key] = corrected


def read_name_rules(path: Path = CONTRIBUTOR_NAME_RULES_CSV) -> dict[str, dict]:
    """Read the reviewed name rules, grouped by rule type."""
    if not path.exists():
        raise FileNotFoundError(f"Missing contributor name rules: {path}")

    rules = {rule_type: {} for rule_type in RULE_TYPES}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            _add_name_rule(rules, number, row)
    return rules


_RULES = read_name_rules()

EXACT_NAME_CORRECTIONS = _RULES["exact_name"]

ROW_NAME_CORRECTIONS = _RULES["sub_id"]

# (surname, garbled first word, zip5) -> corrected first word
FIRST_NAME_FIXES = _RULES["first_name"]

COMMITTEE_NAME_FIXES = _RULES["committee_name"]


_TITLES = words("name_titles")

_ENDINGS = words("name_endings")

# A first-name box that holds only a title or a credential: MRS, MD.
_ONLY_A_TITLE = words("pure_title")


def preclean_name_punctuation(df: pd.DataFrame) -> None:
    """Remove backticks, semicolons, stray dots, collapse double commas."""
    df["contributor_name"] = (
        df["contributor_name"]
        .astype(str)
        .str.replace("`", "", regex=False)
        .str.replace(";", "", regex=False)
        .str.replace(r"^\.+", "", regex=True)
        # Remove dots after the comma, keeping dotted tails like "N.A.".
        .str.replace(r",\s*\.+\s*", ", ", regex=True)
        .str.replace(r",{2,}", ",", regex=True)
        # trailing dash ("LEVY, ALLAN -") would leak into first_name
        .str.replace(r"\s*-\s*$", "", regex=True)
        # Separate "W.DAVID" while keeping abbreviations like "N.A.".
        .str.replace(r"\b([A-Z])\.([A-Z]{2,})", r"\1 \2", regex=True)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )
    df["contributor_first_name"] = (
        df["contributor_first_name"]
        .astype("string")
        .str.replace("`", "", regex=False)
        .str.replace(";", "", regex=False)
        .str.replace(r"^\.+\s*", "", regex=True)
        .str.strip()
        .replace("", pd.NA)
    )


def _tidy(text: str) -> str:
    """Remove backticks and semicolons, collapse whitespace and fix doubled quotes."""
    return re.sub(r"\s+", " ", re.sub(r"[`;]", "", text)).strip().replace("''", "'")


def _strip_periods(word: str) -> str:
    """Return a word without periods."""
    return word.replace(".", "")


def read_name(name: str, first: str, last: str) -> tuple[str, str, str]:
    """(contributor_name, first, last) of a person, from the three filed boxes."""
    name, first, last = _tidy(name), _tidy(first).lstrip(". "), _tidy(last)
    first = re.sub(r"\b([A-Z])\.(?=[A-Z]{2})", r"\1. ", first)  # W.DAVID
    # LAST, MD, FIRST: a credential between the two
    parts = [part.strip() for part in name.split(",")]
    if len(parts) == 3 and _strip_periods(parts[1]).replace(" ", "") in _ENDINGS:
        last, first = parts[0], parts[2].rstrip(".")
    if not last and len(parts) == 2:
        last = parts[0]
    # A title before the given name; a generation or credential after the surname.
    given = first.split()
    while len(given) > 1 and _strip_periods(given[0]) in _TITLES:
        given.pop(0)
    first = " ".join(given)
    family = last.replace(",", " ").split()
    while len(family) > 1 and (
        _strip_periods(family[-1]) in _ENDINGS or ("," in last and family[-1] in ("IV", "V"))
    ):
        family.pop()
    last = " ".join(family)
    # The whole name typed in both boxes.
    if first == last and " " in first:
        first, last = first.split()[0], " ".join(first.split()[1:])
    # More given names in the filed name than in the first-name box: HARRIS, S. WOLF.
    if first and parts[0] == last:
        first = _add_filed_given_names(name, first, given)
    if first in _ONLY_A_TITLE:
        first = ""
    if not first and len(last.split()) == 2:
        first, last = last.split()
    if first and last:
        name = f"{last}, {first}"
    return _remove_periods(name), first, last


def _add_filed_given_names(name: str, first: str, given: list[str]) -> str:
    """Return the given names of the filed name when they start with the first-name box's."""
    filed_first = name.partition(",")[2].strip().split()
    extra = filed_first[len(given) :]
    if filed_first[: len(given)] != given or not extra:
        return first
    if all(
        re.fullmatch(r"[A-Z][A-Z'-]+", word) and word not in _ONLY_A_TITLE and word not in given
        for word in extra
    ):
        return " ".join(filed_first)
    return first


def _remove_periods(name: str) -> str:
    """Contributor_name carries no period after an initial or a title."""
    name = re.sub(r"\b([A-Z]|MR|MRS|MS|DR|JR|SR|II|III|IV)\.", r"\1 ", name.replace("''", "'"))
    return re.sub(r"\s+", " ", name).strip()


def clean_names(df: pd.DataFrame) -> None:
    """Read every person's name once; committees have no first or last name."""
    people = df.index[df["entity_type"].eq("INDIVIDUAL")]
    columns = ("contributor_name", "contributor_first_name", "contributor_last_name")
    boxes = [df.loc[people, column].fillna("").astype(str).str.upper() for column in columns]
    filed = list(zip(*boxes))
    read = {key: read_name(*key) for key in set(filed)}
    for position, column in enumerate(columns):
        df.loc[people, column] = pd.Series(
            [read[key][position] or np.nan for key in filed], index=people, dtype=object
        )
    _fix_garbled_first_names(df, people)

    committees = df["entity_type"].eq("COMMITTEE/PAC")
    df.loc[committees, ["contributor_first_name", "contributor_last_name"]] = np.nan
    df.loc[committees, "contributor_name"] = (
        df.loc[committees, "contributor_name"]
        .astype(str)
        .str.replace(COMM_TAIL_RE, "", regex=True)
        .str.strip()
        .str.rstrip(",")
        .str.strip()
        .replace(COMMITTEE_NAME_FIXES)
        .map(_remove_periods)
    )


def _fix_garbled_first_names(df: pd.DataFrame, people: pd.Index) -> None:
    """Reviewed first-name fixes for the matching surname and ZIP5."""
    garbled = {word for _, word, _ in FIRST_NAME_FIXES}
    first = df.loc[people, "contributor_first_name"].fillna("").astype(str)
    rows = first.index[first.str.split().str[0].isin(garbled)]
    zip5 = (
        df.loc[rows, "contributor_zip"]
        .astype("string")
        .fillna("")
        .str.replace(r"\D", "", regex=True)
        .str[:5]
    )
    for row in rows:
        word, _, rest = first.at[row].partition(" ")
        last = df.at[row, "contributor_last_name"]
        if not isinstance(last, str):
            continue
        fixed = FIRST_NAME_FIXES.get((last, word, zip5.at[row]))
        if fixed:
            df.at[row, "contributor_first_name"] = f"{fixed} {rest}".strip()
            df.at[row, "contributor_name"] = f"{last}, {fixed} {rest}".strip()


def apply_name_corrections(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply manual name corrections."""
    corrected = df["contributor_name"].map(EXACT_NAME_CORRECTIONS)
    by_row = df["sub_id"].astype(str).map(ROW_NAME_CORRECTIONS)
    corrected = by_row.combine_first(corrected)

    changed = corrected.notna() & corrected.ne(df["contributor_name"])
    for index, new_name in corrected[changed].items():
        df.at[index, "contributor_name"] = new_name
        if df.at[index, "entity_type"] == "INDIVIDUAL" and "," in new_name:
            last, first = (part.strip() for part in new_name.split(",", 1))
            df.at[index, "contributor_first_name"] = first
            df.at[index, "contributor_last_name"] = last

    return df, int(changed.sum())
