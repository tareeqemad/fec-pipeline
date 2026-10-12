"""Deterministic street text fixes."""
import re

import numpy as np
import pandas as pd

from fec.config.geography import US_STATES

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
# street then a trailing ordinal floor
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
# street type then a bare unit number
_TYPE_NUM_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$"
)
# street type, unit number, then direction
_TYPE_NUM_DIR_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+"
    r"(\d{1,5}\s+(?:NE|NW|SE|SW|N|S|E|W))$"
)
# a number after these names the road
_NUMBERED_NAME_BASE_RE = re.compile(
    r"(?:\b(?:COUNTY|CO|STATE|PRIVATE|TOWNSHIP|TWP|PARISH|RANCH|FARM|FM|FARM TO MARKET"
    r"|RANCH TO MARKET)\s+(?:RD|AVE|LOOP)"
    r"|^\S*\d\S*\s+(?:(?:NE|NW|SE|SW|N|S|E|W)\s+)?(?:RD|AVE|LOOP))$"
)
# house number repeated at start and end
_REPEATED_ADDRESS_START_RE = re.compile(
    r"^(?P<start>\d+[A-Z]?(?:\s+(?:NE|NW|SE|SW|N|S|E|W))?)\s+"
    r"(?P<body>.+\b(?:" + "|".join(_SPLIT_TYPES) + r")\b)\s+(?P=start)$"
)

# care-of prefix; the real street follows
CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.IGNORECASE)


# strip junk prefix and house number zeros
def _fix_house_number(s):
    if pd.isna(s):
        return s
    text = re.sub(r"^[^A-Z0-9]+", "", str(s)).strip()
    match = re.match(r"^(\d+)(\b.*)$", text)
    if match and set(match.group(1)) != {"0"}:
        text = (match.group(1).lstrip("0") or "0") + match.group(2)
    return text if text else np.nan


# collapse adjacent duplicate words
def _collapse_dup_words(s):
    if pd.isna(s):
        return s
    out = []
    for word in str(s).split():
        if not out or out[-1] != word:
            out.append(word)
    return " ".join(out)


# drop house number repeated at the end
def _drop_repeated_address_start(s):
    if pd.isna(s):
        return s
    match = _REPEATED_ADDRESS_START_RE.match(str(s))
    if not match:
        return s
    return f'{match.group("start")} {match.group("body")}'


# recover the street after a C/O prefix
def _strip_care_of(s):
    if pd.isna(s):
        return s
    text = str(s)
    if not CO_RE.match(text):
        return text
    rest = CO_RE.sub("", text).strip()
    match = re.search(
        r"(\d+\s+\S.*)$", rest
    )  # address starts at first house number
    if not match:
        return text
    street = match.group(1).strip()
    # blank a cut fragment; donor history refills
    return street if _looks_like_street(street) else np.nan


_STREET_TYPE_WORDS = set(_SPLIT_TYPES) | {"HWY", "EXPY", "TPKE", "PATH", "RUN", "ROW", "PT", "PLZ", "PIKE", "XING", "BND", "HTS", "ALY"}
_DIRECTION_WORDS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


# true if house number plus name word
def _looks_like_street(street: str) -> bool:
    tokens = str(street).upper().split()
    if len(tokens) < 2 or not re.match(r"^\d", tokens[0]):
        return False
    names = [t for t in tokens[1:] if t not in _STREET_TYPE_WORDS and t not in _DIRECTION_WORDS]
    return any(len(t) >= 2 for t in names)


# drop row's own city/state/zip from street_1
def _strip_city_state_tail(street, city, state, zip_code):
    if pd.isna(street):
        return street
    # typed commas must not hide the tail
    tokens = [t for t in (t.strip(",") for t in str(street).strip().split()) if t]
    state_code = str(state or "").upper().strip(" ,")
    city_tokens = [t for t in (t.strip(",") for t in str(city or "").upper().split()) if t]
    if len(city_tokens) > 1 and city_tokens[-1] == state_code:  # city field also holds the state
        city_tokens = city_tokens[:-1]
    zip5 = re.sub(r"\D", "", str(zip_code or ""))[:5]
    original = list(tokens)
    if len(tokens) >= 3 and tokens[-1].isdigit() and 3 <= len(tokens[-1]) <= 5 and zip5.startswith(tokens[-1]):
        tokens.pop()
        stripped_zip = True
    else:
        stripped_zip = False
    city_long_enough = len("".join(city_tokens)) >= 4
    ends_with_city = city_long_enough and len(tokens) > len(city_tokens) \
        and [t.upper() for t in tokens[-len(city_tokens):]] == city_tokens
    if len(tokens) >= 3 and state_code and tokens[-1].upper() == state_code:
        before = tokens[:-1]
        before_ends_with_city = city_long_enough and len(before) > len(city_tokens) \
            and [t.upper() for t in before[-len(city_tokens):]] == city_tokens
        if stripped_zip or before_ends_with_city or before[-1].upper() in _STREET_TYPE_WORDS:
            tokens = before
            ends_with_city = before_ends_with_city
    if ends_with_city:
        tokens = tokens[:-len(city_tokens)]
    if tokens == original:
        return street
    candidate = " ".join(tokens)
    return candidate if _looks_like_street(candidate) else street


# flags rows with a blanked care-of street
CARE_OF_BLANKED = "_care_of_blanked"


# apply street text fixes, split trailing units
def apply_safe_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}

    before = df[S1].copy()
    was_care_of = before.fillna("").astype(str).str.match(CO_RE)
    df[S1] = df[S1].map(_strip_care_of)
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    df[S1] = df[S1].map(_drop_repeated_address_start)
    still_care_of = df[S1].fillna("").astype(str).str.match(CO_RE)
    counts["care_of"] = int((was_care_of & ~still_care_of).sum())
    # blanked care-of rows keep their filed city
    df[CARE_OF_BLANKED] = was_care_of & df[S1].isna()

    df[S1] = [
        _strip_city_state_tail(s, c, st, z)
        for s, c, st, z in zip(df[S1], df.get(CITY, pd.Series(index=df.index, dtype=object)),
                               df.get(STATE, pd.Series(index=df.index, dtype=object)),
                               df.get(ZIP, pd.Series(index=df.index, dtype=object)))
    ]

    for column in (S1, CITY, STATE):
        mask = df[column].notna() & (df[column].astype(str).str.strip() != "")
        df.loc[mask, column] = (
            df.loc[mask, column]
            .astype(str)
            .str.replace(r"[,\s]+$", "", regex=True)
            .str.strip()
        )
    counts["house_number"] = int((before.fillna("") != df[S1].fillna("")).sum())

    # street_2 repeating street_1 holds no unit
    same = df[S2].notna() & (df[S2].astype(str).str.strip() != "") \
        & (df[S2].astype(str).str.strip() == df[S1].fillna("").astype(str).str.strip())
    df.loc[same, S2] = np.nan

    # split a trailing unit into empty street_2
    df[S2] = df[S2].astype(
        object
    )  # all-NaN column is float64, rejects strings
    s1 = df[S1].fillna("").astype(str)
    s2_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
    floor = s1.str.extract(_ORD_FLOOR_RE)
    num = s1.str.extract(_TYPE_NUM_RE)
    num_dir = s1.str.extract(_TYPE_NUM_DIR_RE)
    is_floor = floor[0].notna()
    # "COUNTY RD 102": number names the road
    is_num = num[0].notna() & ~num[0].fillna("").str.contains(_NUMBERED_NAME_BASE_RE)
    is_num_dir = num_dir[0].notna() & ~num_dir[0].fillna("").str.contains(_NUMBERED_NAME_BASE_RE)
    base = floor[0].where(is_floor, num[0].where(is_num, num_dir[0].where(is_num_dir)))
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


# city tail, then state glued to ZIP
_CITY_TAIL_STATE_ZIP_RE = re.compile(r"^([A-Z]+)\s+([A-Z]{2})\d{3,5}$")
# words that make a unit id
_UNIT_WORDS = {"APT", "STE", "SUITE", "UNIT", "BLDG", "FL", "FLR", "FLOOR", "RM", "ROOM", "PH",
               "OFFICE", "OFF", "DEPT", "LOT", "SPC", "SPACE", "TRLR", "PMB", "NO", "BOX"}


# true for a state/ZIP/country spill fragment
def is_state_zip_fragment(value: str) -> bool:
    core = str(value or "").strip().upper().lstrip("#").strip()
    if core == "USA":
        return True
    city_tail = _CITY_TAIL_STATE_ZIP_RE.match(core)
    if city_tail and city_tail.group(1) not in _UNIT_WORDS and city_tail.group(2) in US_STATES:
        return True
    return len(core) >= 3 and core[:2] in US_STATES and (core[2:].isdigit() or (len(core) == 3 and core[2:].isalpha()))
