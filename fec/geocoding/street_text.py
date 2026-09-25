"""Street text as geocoders read it: numbered streets, no suite or floor."""
import re

import pandas as pd

# require whitespace before the keyword and a word boundary after, so short
# abbreviations (FL, STE, RM, APT) never match inside street names like FLANDERS
_SUITE_RE = re.compile(
    r',?\s+(?:Suite|Ste|Floor|Fl|Unit|Apt|Apartment|Room|Rm|Bldg|PH)\b\.?\s*\S+.*$'
    r'|,?\s*#\s*\S+.*$',
    re.IGNORECASE,
)


# a trailing ordinal floor to drop: "123 MAIN ST, 3RD FLOOR"
_FLOOR_RE = re.compile(
    r',?\s*\d+(?:st|nd|rd|th)\s+Floor.*$',
    re.IGNORECASE,
)


# a building's name before its street address ('ONE WILLIAMS CENTER 101 E 2ND ST',
# 'CIRA CENTRE, 2929 ARCH ST'): the name ends in a building word and is followed by
# a house number and a street; 'ONE KENDALL SQ BUILDING 600 STE 380' names no street
_BUILDING_NAME_RE = re.compile(
    r"^[A-Z][A-Z'&.\- ]*?\b(?:CENTER|CENTRE|TOWERS?|PLAZA|BUILDING|BLDG|HALL|HOUSE|COMPLEX"
    r"|CAMPUS|PAVILION|ATRIUM)\s*,?\s+"
    r"(?=\d+[A-Z]?\s+(?!(?:STE|SUITE|FL|FLOOR|UNIT|APT|RM|ROOM|BLDG|BUILDING)\b)[A-Z0-9])",
    re.IGNORECASE,
)


# Spelled-out ordinal streets ("777 THIRD AVE") geocode to the wrong place far more
# often than the numbered form Census/TIGER and OSM use ("777 3RD AVE"), so geocode
# keys, and with them the queries, use numbers. Only before a street type, so named
# streets ("SECOND LAKE RD") keep their words.
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


# a spelled-out ordinal before a street type: "THIRD AVE" -> to become "3RD AVE"
_ORDINAL_STREET_RE = re.compile(
    rf"\b(?:({'|'.join(_ORDINAL_TENS)})[\s-]+)?({'|'.join(_ORDINAL_WORDS)})\b"
    r"(?=\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT"
    r"|CIR|CIRCLE|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|TER|TERRACE|SQ|SQUARE|TRL|TRAIL|WAY)\b)",
    re.IGNORECASE,
)


# A building named after its street ("120 FIFTH AVENUE PLACE") is found by that name,
# so such addresses keep their words.
_ORDINAL_BUILDING_RE = re.compile(
    rf"\b(?:{'|'.join(_ORDINAL_WORDS)})\s+(?:AVE|AVENUE|ST|STREET)"
    r"\s+(?:PLACE|PLAZA|TOWER|TOWERS|CENTER|CENTRE|BUILDING)\b",
    re.IGNORECASE,
)


# convert a matched spelled-out ordinal word into its numeral form
def _ordinal_number(match: re.Match) -> str:
    number = (_ORDINAL_TENS.get((match.group(1) or "").upper(), 0)
              + _ORDINAL_WORDS[match.group(2).upper()])
    suffix = "TH" if 10 <= number % 100 <= 20 else {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


# convert a spelled-out ordinal street name to numeral form
def numbered_street(street: str) -> str:
    """'777 THIRD AVE' -> '777 3RD AVE'; any other street is returned unchanged."""
    if _ORDINAL_BUILDING_RE.search(street):
        return street
    return _ORDINAL_STREET_RE.sub(_ordinal_number, street)


# vectorized numbered_street over a series of streets
def _numbered_streets(streets: pd.Series) -> pd.Series:
    return streets.map(numbered_street)


# strip building names and suite/floor/unit suffixes before geocoding
def _clean_street_for_geocoding(street: str) -> str:
    """Strip a leading building name and suite/floor/unit suffixes that confuse Nominatim and Census."""
    cleaned = _BUILDING_NAME_RE.sub('', street)
    cleaned = _FLOOR_RE.sub('', cleaned)
    cleaned = _SUITE_RE.sub('', cleaned)
    return cleaned.strip().rstrip(',')
