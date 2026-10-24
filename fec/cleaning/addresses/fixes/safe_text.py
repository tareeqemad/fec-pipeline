"""Deterministic street text fixes."""
import re

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign import kept_as_filed_mask
from fec.cleaning.addresses.street_text import _extract_units, normalize_street
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
# street with trailing floor unit: "123 MAIN ST 28TH FLOOR" -> street + "28TH FLOOR"
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
# street type followed by a trailing unit number: "123 MAIN DR 601" -> street + "601"
_TYPE_NUM_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$"
)
# street type followed by a unit number and direction: "123 MAIN DR 601 N"
_TYPE_NUM_DIR_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+"
    r"(\d{1,5}\s+(?:NE|NW|SE|SW|N|S|E|W))$"
)
# Keep route numbers in street_1: "COUNTY RD 102", "N AVE 54", "NW LOOP 410".
# A number after an ordinary name such as COUNTY LINE RD can still be a unit.
_NUMBERED_NAME_BASE_RE = re.compile(
    r"(?:\b(?:COUNTY|CO|STATE|PRIVATE|TOWNSHIP|TWP|PARISH|RANCH|FARM|FM|FARM TO MARKET"
    r"|RANCH TO MARKET)\s+(?:RD|AVE|LOOP)"
    r"|^\S*\d\S*\s+(?:(?:NE|NW|SE|SW|N|S|E|W)\s+)?(?:RD|AVE|LOOP))$"
)
# house number repeated at start and end: "123 MAIN ST 123" -> "123 MAIN ST"
_REPEATED_ADDRESS_START_RE = re.compile(
    r"^(?P<start>\d+[A-Z]?(?:\s+(?:NE|NW|SE|SW|N|S|E|W))?)\s+"
    r"(?P<body>.+\b(?:" + "|".join(_SPLIT_TYPES) + r")\b)\s+(?P=start)$"
)

# "C/O <name>" forwarding prefix; the real street follows it
_CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.IGNORECASE)


def _fix_house_number(s):
    """Remove leading zeros; preserve unclear house numbers."""
    if pd.isna(s):
        return s
    text = str(s).strip()
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
    """Keep the street after C/O; leave name-only entries for review."""
    if pd.isna(s):
        return s
    text = str(s)
    if not _CO_RE.match(text):
        return text
    rest = _CO_RE.sub("", text).strip()
    match = re.search(r"(\d+\s+\S.*)$", rest)
    if not match:
        return text
    street = match.group(1).strip()
    # A truncated fragment such as "100 M" is not a usable street.
    return street if _looks_like_street(street) else np.nan


_STREET_TYPE_WORDS = set(_SPLIT_TYPES) | {"HWY", "EXPY", "TPKE", "PATH", "RUN", "ROW", "PT", "PLZ", "PIKE", "XING", "BND", "HTS", "ALY"}
_DIRECTION_WORDS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _looks_like_street(street: str) -> bool:
    """House number followed by at least one real street-name word (not only a type or direction)."""
    tokens = str(street).upper().split()
    if len(tokens) < 2 or not re.match(r"^\d", tokens[0]):
        return False
    names = [t for t in tokens[1:] if t not in _STREET_TYPE_WORDS and t not in _DIRECTION_WORDS]
    return any(len(t) >= 2 for t in names)


def _move_street_from_second_line(df: pd.DataFrame) -> int:
    """Put a filed street first and keep its C/O name, mailbox and unit."""
    first = df[S1].fillna("").astype(str).str.strip()
    second = df[S2].fillna("").astype(str).str.strip()
    care_of = first.str.match(_CO_RE) & ~first.str.contains(r"\d")
    mailbox = first.str.fullmatch(r"PMB\s*#?\s*\d+[A-Z]?", case=False)
    eligible = (first.eq("") | care_of | mailbox) & second.ne("")
    eligible &= df[STATE].fillna("").str.upper().isin(US_STATES)
    eligible &= ~kept_as_filed_mask(df)
    if not eligible.any():
        return 0

    df[S1], df[S2] = df[S1].astype(object), df[S2].astype(object)
    candidates = second[eligible].map(normalize_street)
    streets, _, _, _ = _extract_units(candidates, pd.Series("", index=candidates.index))
    moved = 0
    for index, street in streets.items():
        if pd.isna(street):
            continue
        floor = _ORD_FLOOR_RE.match(street)
        if floor:
            street = floor.group(1)
        words = street.split()
        if not re.fullmatch(r"\d+[A-Z]?(?:-\d+[A-Z]?)?", words[0]):
            continue
        if not _looks_like_street(street) or not set(words[1:]) & _STREET_TYPE_WORDS:
            continue
        if words[1] in _UNIT_WORDS:
            continue
        # Keep the whole filed suffix, including combined building/floor/suite details.
        unit = candidates.at[index][len(street):].strip(" ,")
        df.at[index, S1] = street
        df.at[index, S2] = " ".join(part for part in (unit, first.at[index]) if part) or np.nan
        moved += 1
    return moved


def _strip_city_state_tail(street, city, state, zip_code):
    """Remove the row's city/state/ZIP tail only when a usable street remains."""
    if pd.isna(street):
        return street
    tokens = [token.strip(",") for token in str(street).strip().split()]
    tokens = [token for token in tokens if token]
    state_code = str(state or "").upper().strip(" ,")
    city_tokens = [token.strip(",") for token in str(city or "").upper().split()]
    city_tokens = [token for token in city_tokens if token]
    if len(city_tokens) > 1 and city_tokens[-1] == state_code:  # city "ENCINO, CA"
        city_tokens = city_tokens[:-1]
    zip5 = re.sub(r"\D", "", str(zip_code or ""))[:5]
    original = list(tokens)
    stripped_zip = (
        len(tokens) >= 3 and tokens[-1].isdigit()
        and 3 <= len(tokens[-1]) <= 5 and zip5.startswith(tokens[-1])
    )
    if stripped_zip:
        tokens.pop()
    city_long_enough = len("".join(city_tokens)) >= 4
    ends_with_city = city_long_enough and len(tokens) > len(city_tokens) \
        and [t.upper() for t in tokens[-len(city_tokens):]] == city_tokens
    if len(tokens) >= 3 and state_code and tokens[-1].upper() == state_code:
        before = tokens[:-1]
        before_ends_with_city = city_long_enough and len(before) > len(city_tokens) \
            and [t.upper() for t in before[-len(city_tokens):]] == city_tokens
        # A state code alone may be a street type, as in "7 DANIEL CT".
        if stripped_zip or before_ends_with_city or before[-1].upper() in _STREET_TYPE_WORDS:
            tokens = before
            ends_with_city = before_ends_with_city
    if ends_with_city:
        tokens = tokens[:-len(city_tokens)]
    if tokens == original:
        return street
    candidate = " ".join(tokens)
    return candidate if _looks_like_street(candidate) else street


# Internal flag: the C/O fragment was too short to use.
CARE_OF_BLANKED = "_care_of_blanked"


def apply_safe_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Fix street text and split units before donor address recovery."""
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}

    before = df[S1].copy()
    counts["second_line"] = _move_street_from_second_line(df)
    was_care_of = before.fillna("").astype(str).str.match(_CO_RE)
    df[S1] = df[S1].map(_strip_care_of)
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    df[S1] = df[S1].map(_drop_repeated_address_start)
    still_care_of = df[S1].fillna("").astype(str).str.match(_CO_RE)
    counts["care_of"] = int((was_care_of & ~still_care_of).sum())
    # A blanked C/O address must not inherit the donor's home city.
    df[CARE_OF_BLANKED] = was_care_of & df[S1].isna()

    empty = pd.Series(index=df.index, dtype=object)
    cities = df.get(CITY, empty)
    states = df.get(STATE, empty)
    zips = df.get(ZIP, empty)
    df[S1] = [
        _strip_city_state_tail(street, city, state, zip_code)
        for street, city, state, zip_code in zip(df[S1], cities, states, zips)
    ]

    # Remove trailing commas and spaces.
    for column in (S1, CITY, STATE):
        mask = df[column].notna() & (df[column].astype(str).str.strip() != "")
        df.loc[mask, column] = (
            df.loc[mask, column]
            .astype(str)
            .str.replace(r"[,\s]+$", "", regex=True)
            .str.strip()
        )
    counts["house_number"] = int((before.fillna("") != df[S1].fillna("")).sum())

    # A copy of street_1 in street_2 is not a unit.
    same = df[S2].notna() & (df[S2].astype(str).str.strip() != "") \
        & (df[S2].astype(str).str.strip() == df[S1].fillna("").astype(str).str.strip())
    df.loc[same, S2] = np.nan

    # Allow strings when street_2 started as an all-NaN float column.
    df[S2] = df[S2].astype(object)
    street = df[S1].fillna("").astype(str)
    unit_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
    floor = street.str.extract(_ORD_FLOOR_RE)
    number = street.str.extract(_TYPE_NUM_RE)
    number_direction = street.str.extract(_TYPE_NUM_DIR_RE)
    has_floor = floor[0].notna()
    # Route numbers such as "COUNTY RD 102" stay in the street.
    has_number = number[0].notna() & ~number[0].fillna("").str.contains(_NUMBERED_NAME_BASE_RE)
    has_number_direction = (
        number_direction[0].notna()
        & ~number_direction[0].fillna("").str.contains(_NUMBERED_NAME_BASE_RE)
    )

    # Prefer a floor, then a bare number, then a number with a direction.
    base = number_direction[0].where(has_number_direction)
    base = number[0].where(has_number, base)
    base = floor[0].where(has_floor, base)
    unit = "# " + number_direction[1].fillna("")
    unit = ("# " + number[1].fillna("")).where(has_number, unit)
    unit = floor[1].where(has_floor, unit)

    apply_mask = unit_blank & base.notna()
    if apply_mask.any():
        df.loc[apply_mask, S1] = base[apply_mask].str.strip()
        df.loc[apply_mask, S2] = unit[apply_mask].str.strip()
        counts["unit_split"] = int(apply_mask.sum())

    return df, counts


# City tail followed by state and partial ZIP: "ANTA GA3034".
_CITY_TAIL_STATE_ZIP_RE = re.compile(r"^([A-Z]+)\s+([A-Z]{2})\d{3,5}$")
# "STE NE200" is a unit, not a state/ZIP fragment.
_UNIT_WORDS = {"APT", "STE", "SUITE", "UNIT", "BLDG", "FL", "FLR", "FLOOR", "RM", "ROOM", "PH",
               "OFFICE", "OFF", "DEPT", "LOT", "SPC", "SPACE", "TRLR", "PMB", "NO", "BOX"}


def is_state_zip_fragment(value: str) -> bool:
    """Recognize state/ZIP/country text spilled into street_2."""
    core = str(value or "").strip().upper().lstrip("#").strip()
    if core == "USA":
        return True
    city_tail = _CITY_TAIL_STATE_ZIP_RE.match(core)
    if city_tail and city_tail.group(1) not in _UNIT_WORDS and city_tail.group(2) in US_STATES:
        return True
    return len(core) >= 3 and core[:2] in US_STATES and (core[2:].isdigit() or (len(core) == 3 and core[2:].isalpha()))
