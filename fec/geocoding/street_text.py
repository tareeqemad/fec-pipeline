"""Street text cleanup for geocoder queries."""
import re

import pandas as pd

# boundaries stop FL matching inside FLANDERS
_SUITE_RE = re.compile(
    r',?\s+(?:Suite|Ste|Floor|Fl|Unit|Apt|Apartment|Room|Rm|Bldg|PH)\b\.?\s*\S+.*$'
    r'|,?\s*#\s*\S+.*$',
    re.IGNORECASE,
)


# a trailing ordinal floor like 3RD FLOOR
_FLOOR_RE = re.compile(
    r',?\s*\d+(?:st|nd|rd|th)\s+Floor.*$',
    re.IGNORECASE,
)


# a building name before a house number
_BUILDING_NAME_RE = re.compile(
    r"^[A-Z][A-Z'&.\- ]*?\b(?:CENTER|CENTRE|TOWERS?|PLAZA|BUILDING|BLDG|HALL|HOUSE|COMPLEX"
    r"|CAMPUS|PAVILION|ATRIUM)\s*,?\s+"
    r"(?=\d+[A-Z]?\s+(?!(?:STE|SUITE|FL|FLOOR|UNIT|APT|RM|ROOM|BLDG|BUILDING)\b)[A-Z0-9])",
    re.IGNORECASE,
)


# numbered ordinal streets geocode better than words
_ORDINAL_WORDS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
    "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11,
    "TWELFTH": 12, "THIRTEENTH": 13, "FOURTEENTH": 14, "FIFTEENTH": 15,
    "SIXTEENTH": 16, "SEVENTEENTH": 17, "EIGHTEENTH": 18, "NINETEENTH": 19,
    "TWENTIETH": 20, "THIRTIETH": 30, "FORTIETH": 40, "FIFTIETH": 50,
    "SIXTIETH": 60, "SEVENTIETH": 70, "EIGHTIETH": 80, "NINETIETH": 90,
}


_ORDINAL_TENS = {
    "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50,
    "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90,
}


# a spelled-out ordinal before a street type
_ORDINAL_STREET_RE = re.compile(
    rf"\b(?:({'|'.join(_ORDINAL_TENS)})[\s-]+)?({'|'.join(_ORDINAL_WORDS)})\b"
    r"(?=\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT"
    r"|CIR|CIRCLE|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|TER|TERRACE|SQ|SQUARE|TRL|TRAIL|WAY)\b)",
    re.IGNORECASE,
)


# buildings named after streets keep their words
_ORDINAL_BUILDING_RE = re.compile(
    rf"\b(?:{'|'.join(_ORDINAL_WORDS)})\s+(?:AVE|AVENUE|ST|STREET)"
    r"\s+(?:PLACE|PLAZA|TOWER|TOWERS|CENTER|CENTRE|BUILDING)\b",
    re.IGNORECASE,
)


# spelled-out ordinal match to its numeral
def _ordinal_number(match: re.Match) -> str:
    number = (_ORDINAL_TENS.get((match.group(1) or "").upper(), 0)
              + _ORDINAL_WORDS[match.group(2).upper()])
    suffix = "TH" if 10 <= number % 100 <= 20 else {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


# THIRD AVE to 3RD AVE; others unchanged
def numbered_street(street: str) -> str:
    if _ORDINAL_BUILDING_RE.search(street):
        return street
    return _ORDINAL_STREET_RE.sub(_ordinal_number, street)


# vectorized numbered_street over a series of streets
def numbered_streets(streets: pd.Series) -> pd.Series:
    return streets.map(numbered_street)


# strip building names and suite/floor/unit suffixes
def clean_street_for_geocoding(street: str) -> str:
    cleaned = _BUILDING_NAME_RE.sub('', street)
    cleaned = _FLOOR_RE.sub('', cleaned)
    cleaned = _SUITE_RE.sub('', cleaned)
    return cleaned.strip().rstrip(',')
