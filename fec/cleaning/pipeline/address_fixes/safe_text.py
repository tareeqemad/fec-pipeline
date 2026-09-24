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
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
_TYPE_NUM_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$"
)
_TYPE_NUM_DIR_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+"
    r"(\d{1,5}\s+(?:NE|NW|SE|SW|N|S|E|W))$"
)
# A number after these is the street's route/name number, not a unit, so it stays in
# street_1: "3831 COUNTY RD 102", "289 W STATE RD 130", "18 PRIVATE RD 20" (a qualifier
# right before the type), and a type that is the whole name, "1704 N AVE 54" (Los
# Angeles' Avenue 54), "1100 NW LOOP 410" (San Antonio's Loop 410), "1234 RD 20".
# COUNTY LINE RD or FOREST RD are ordinary names: a number after them is still a unit.
_NUMBERED_NAME_BASE_RE = re.compile(
    r"(?:\b(?:COUNTY|CO|STATE|PRIVATE|TOWNSHIP|TWP|PARISH|RANCH|FARM|FM|FARM TO MARKET"
    r"|RANCH TO MARKET)\s+(?:RD|AVE|LOOP)"
    r"|^\S*\d\S*\s+(?:(?:NE|NW|SE|SW|N|S|E|W)\s+)?(?:RD|AVE|LOOP))$"
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
    if not match:
        return text
    street = match.group(1).strip()
    # "C/O MCCARTER & ENGLISH, LLP, 100 M": the FEC cut left only a house number and a
    # letter; an unusable fragment is blanked so the donor's own history can refill it
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


def _strip_city_state_tail(street, city, state, zip_code):
    """Drop the row's own city / state / ZIP when the filer typed them into street_1.

    "3841 HAYVENHURST DR ENCINO CA" -> "3841 HAYVENHURST DR"; "76 WALLACKS DR STAMFORD CT 0690" ->
    "76 WALLACKS DR". Only the row's own values count as a tail, a state code is removed only when it
    followed a city or ZIP or a street type ("7 DANIEL CT" in Connecticut is a court), and the
    remainder must still look like a street ("5000 PKWY CALABASAS" is Parkway Calabasas, kept)."""
    if pd.isna(street):
        return street
    tokens = str(street).strip().split()
    city_tokens = str(city or "").upper().split()
    state_code = str(state or "").upper().strip()
    zip5 = re.sub(r"\D", "", str(zip_code or ""))[:5]
    original = list(tokens)
    if len(tokens) >= 3 and tokens[-1].isdigit() and 3 <= len(tokens[-1]) <= 5 and zip5.startswith(tokens[-1]):
        tokens.pop()
        stripped_zip = True
    else:
        stripped_zip = False
    ends_with_city = len(city_tokens) >= 1 and len(city_tokens[0]) >= 4 and len(tokens) > len(city_tokens) \
        and [t.upper() for t in tokens[-len(city_tokens):]] == city_tokens
    if len(tokens) >= 3 and state_code and tokens[-1].upper() == state_code:
        before = tokens[:-1]
        before_ends_with_city = len(city_tokens) >= 1 and len(city_tokens[0]) >= 4 and len(before) > len(city_tokens) \
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

    df[S1] = [
        _strip_city_state_tail(s, c, st, z)
        for s, c, st, z in zip(df[S1], df.get(CITY, pd.Series(index=df.index, dtype=object)),
                               df.get(STATE, pd.Series(index=df.index, dtype=object)),
                               df.get(ZIP, pd.Series(index=df.index, dtype=object)))
    ]

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
    # "COUNTY RD 102": the number names the road, it is not a unit
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


# "ANTA GA3034": the end of the city, then the state glued to the start of the ZIP
_CITY_TAIL_STATE_ZIP_RE = re.compile(r"^([A-Z]+)\s+([A-Z]{2})\d{3,5}$")
# a unit word before "<2 letters><digits>" makes it a unit id ("STE NE200"), not a spill
_UNIT_WORDS = {"APT", "STE", "SUITE", "UNIT", "BLDG", "FL", "FLR", "FLOOR", "RM", "ROOM", "PH",
               "OFFICE", "OFF", "DEPT", "LOT", "SPC", "SPACE", "TRLR", "PMB", "NO", "BOX"}


def is_state_zip_fragment(value: str) -> bool:
    """'# NY1179', '# CA9213', '# NJU', 'ANTA GA3034', 'USA': a state/ZIP/country tail that a 34-char FEC street_1 spilled into street_2."""
    core = str(value or "").strip().upper().lstrip("#").strip()
    if core == "USA":
        return True
    city_tail = _CITY_TAIL_STATE_ZIP_RE.match(core)
    if city_tail and city_tail.group(1) not in _UNIT_WORDS and city_tail.group(2) in US_STATES:
        return True
    return len(core) >= 3 and core[:2] in US_STATES and (core[2:].isdigit() or (len(core) == 3 and core[2:].isalpha()))
