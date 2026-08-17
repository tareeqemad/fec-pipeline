"""Deterministic street text fixes."""
import re

import numpy as np
import pandas as pd

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

_SPLIT_TYPES = (
    "AVE",
    "BLVD",
    "ST",
    "RD",
    "DR",
    "LN",
    "CT",
    "WAY",
    "PL",
    "TER",
    "CIR",
    "PKWY",
    "SQ",
    "TRL",
    "PLZ",
    "LOOP",
    "BROADWAY",
)
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
_TYPE_NUM_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$"
)
_TYPE_NUM_DIR_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+"
    r"(\d{1,5}\s+(?:NE|NW|SE|SW|N|S|E|W))$"
)
_REPEATED_ADDRESS_START_RE = re.compile(
    r"^(?P<start>\d+[A-Z]?(?:\s+(?:NE|NW|SE|SW|N|S|E|W))?)\s+"
    r"(?P<body>.+\b(?:" + "|".join(_SPLIT_TYPES) + r")\b)\s+(?P=start)$"
)

# "C/O <name>" forwarding prefix; the real street follows it
_CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.IGNORECASE)

# PO BOX / PMB: valid mail addresses with no precise physical point


def _fix_house_number(s):
    """Strip a leading non-alphanumeric char, then drop leading zeros from the house number (02393 -> 2393) unless all zeros."""
    if pd.isna(s):
        return s
    text = re.sub(r"^[^A-Z0-9]+", "", str(s)).strip()
    match = re.match(r"^(\d+)(\b.*)$", text)
    if match and set(match.group(1)) != {"0"}:
        text = (match.group(1).lstrip("0") or "0") + match.group(2)
    return text if text else np.nan


def _collapse_dup_words(s):
    """Collapse an adjacent exactly-equal duplicate word: 'E E' -> 'E'."""
    if pd.isna(s):
        return s
    out = []
    for word in str(s).split():
        if not out or out[-1] != word:
            out.append(word)
    return " ".join(out)


def _drop_repeated_address_start(s):
    """Drop a repeated house-number prefix from the end."""
    if pd.isna(s):
        return s
    match = _REPEATED_ADDRESS_START_RE.match(str(s))
    if not match:
        return s
    return f'{match.group("start")} {match.group("body")}'


def _strip_care_of(s):
    """Recover the street after a 'C/O' prefix; a name-only C/O with no house number is left for the report."""
    if pd.isna(s):
        return s
    text = str(s)
    if not _CO_RE.match(text):
        return text
    rest = _CO_RE.sub("", text).strip()
    match = re.search(
        r"(\d+\s+\S.*)$", rest
    )  # real address = from the first house number
    return match.group(1).strip() if match else text


def apply_safe_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Deterministic text fixes, applied; runs right after clean_streets so the cleaned values feed the per-donor dedup/recovery downstream."""
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}

    before = df[S1].copy()
    was_care_of = before.fillna("").astype(str).str.match(_CO_RE)
    df[S1] = df[S1].map(_strip_care_of)
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    df[S1] = df[S1].map(_drop_repeated_address_start)
    still_care_of = df[S1].fillna("").astype(str).str.match(_CO_RE)
    counts["care_of"] = int((was_care_of & ~still_care_of).sum())

    # trailing comma / whitespace on any field (city/state can carry a stray "TEMPLE,")
    for column in (S1, CITY, STATE):
        mask = df[column].notna() & (df[column].astype(str).str.strip() != "")
        df.loc[mask, column] = (
            df.loc[mask, column]
            .astype(str)
            .str.replace(r"[,\s]+$", "", regex=True)
            .str.strip()
        )
    counts["house_number"] = int((before.fillna("") != df[S1].fillna("")).sum())

    # split a trailing floor / bare unit-number into an empty street_2
    df[S2] = df[S2].astype(
        object
    )  # an all-NaN column is float64, which rejects strings
    s1 = df[S1].fillna("").astype(str)
    s2_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
    floor = s1.str.extract(_ORD_FLOOR_RE)  # "... 28TH FLOOR" -> unit kept as-is
    num = s1.str.extract(_TYPE_NUM_RE)  # "... DR 601"     -> unit prefixed "#"
    num_dir = s1.str.extract(_TYPE_NUM_DIR_RE)  # "... DR 601 N" -> "# 601 N"
    is_floor = floor[0].notna()
    is_num = num[0].notna()
    base = floor[0].where(is_floor, num[0].where(is_num, num_dir[0]))
    unit = floor[1].where(
        is_floor,
        ("# " + num[1].fillna("")).where(
            is_num,
            "# " + num_dir[1].fillna(""),
        ),
    )
    apply_mask = s2_blank & base.notna()
    if apply_mask.any():
        df.loc[apply_mask, S1] = base[apply_mask].str.strip()
        df.loc[apply_mask, S2] = unit[apply_mask].str.strip()
        counts["unit_split"] = int(apply_mask.sum())

    return df, counts
