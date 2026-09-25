"""Normalize one street or unit string: types, directions, ordinals, units."""
import re

import numpy as np
import pandas as pd

from fec.config.streets import (
    DIR_MID,
    DIR_PREFIX,
    DIR_SUFFIX,
    HASH_EXTRACT,
    POBOX_RE,
    STREET_TYPES,
    STREET_TYPO_RULES,
    UNIT_EXTRACT,
    UNIT_RULES,
)

# placeholder values that mean "no address"
_JUNK_STREETS = {'HOME', 'YES', 'NO', 'SAME', 'N/A', 'NA', 'NONE',
                 'UNKNOWN', 'X', 'XX', 'XXX', 'NOT PROVIDED',
                 'UNITED STATES OF AMERICA', 'USA'}

# the same placeholders in street_2, except a bare X: "UNIT X" is written "X" there
_JUNK_UNITS = (_JUNK_STREETS - {'X'}) | {'NULL', 'N.A', 'N.A.'}


def _has_no_unit_text(value: str) -> bool:
    """True for a street_2 holding no letter or digit ('.', '-', '..'); a bare '#' is kept for address_review, which reports it as a unit keyword without a number."""
    return not any(char.isalnum() or char == '#' for char in value)


# trailing unit word without a number
_TRAILING_UNIT_RE = re.compile(r'\s+(?:APT|UNIT|STE|SUITE)\s*$')

_INVALID_STREET_RE = re.compile(r'^(?:C\d{7,}|\d+)$')

_SHORT_POBOX_RE = re.compile(r'^(?:P\.?\s*O\.?\s*B?|BOX)\s+(\d)')


_HOUSE_TOKEN_RE = re.compile(r'^\d+[A-Z]?$')


_DIRECTION_TOKENS = frozenset({'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'})


def _drop_repeated_street(s):
    """'11425 TWINING LN 11425 TWINING L' -> '11425 TWINING LN': the filer typed the street twice and the 34-character FEC field cut the copy.

    Only a copy that starts with the same house number and is a prefix of the
    street before it is dropped ('396 FOREST AVE 396 FOREST AVE' too); a grid
    address such as '1300 E 1300 S' is not a copy and stays."""
    if not isinstance(s, str):
        return s
    tokens = s.split()
    if len(tokens) < 4 or not _HOUSE_TOKEN_RE.match(tokens[0]):
        return s
    for index in range(2, len(tokens) - 1):
        if tokens[index] != tokens[0]:
            continue
        # a separator between the two copies ('1020 HULL ST / 1020 HULL ST') goes too
        head = tokens[:index]
        while head and not any(char.isalnum() for char in head[-1]):
            head.pop()
        first, copy = ' '.join(head), ' '.join(tokens[index:])
        # what is kept must still name a street ('159 W 159 WEST' is not '159 W')
        names_street = any(
            token not in _DIRECTION_TOKENS and re.search(r'[A-Z]', token) for token in head[1:]
        )
        if names_street and first.startswith(copy):
            return first
    return s


# drop HTML entities and stray semicolons, brackets and colons
def _strip_markup(s: str) -> str:
    s = re.sub(r'&SHY;', '', s)
    s = re.sub(r'&AMP;', '&', s)
    s = re.sub(r'&[A-Z]+;', '', s)
    s = s.replace(';', '')
    s = s.replace('[', '').replace(']', '')
    return re.sub(r'\s*:', ' ', s)


# remove abbreviation periods; specific patterns first, catch-all last
def _tidy_periods(s: str) -> str:
    s = re.sub(r'\.{2,}', '.', s)
    s = re.sub(r'\bP\.0\.\s*BOX\b', 'PO BOX', s)
    s = re.sub(r'(\d)\.([A-Z])', r'\1 \2', s)
    s = re.sub(r'\bSO\.(?=\s|$)', 'S', s)
    s = re.sub(r'(\d+(?:ST|ND|RD|TH))\.', r'\1', s)
    # compound direction dots must run before single-letter cleanup (S.W stays SW)
    s = re.sub(r'\b([NS])\.([EW])\b\.?', r'\1\2', s)
    s = re.sub(r'\b([NSEW])\.\s*([A-Z])', r'\1 \2', s)
    s = re.sub(r'\b([A-Z])\.\s', r'\1 ', s)
    s = re.sub(r'\b(UNIV|PT|NO|CTR|DEPT|BLDG|GEN|GOVT|NATL)\.\s*', r'\1 ', s)
    s = re.sub(r'([A-Z]{2,})\.\s', r'\1 ', s)
    # stray state code + ZIP fragment at end; the trailing digit is required
    # or this eats legitimate 2-letter street types ("FIGUEROA ST.")
    s = re.sub(r'\s+[A-Z]{2}\.\s*\d+$', '', s)

    return s.rstrip('.')


def _normalize_street(s: str) -> str:
    """Normalize a single street address string."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()

    s = _strip_markup(s)

    # an email is not an address
    if '@' in s:
        return np.nan

    # placeholder words, FEC committee ids, bare house numbers
    if s in _JUNK_STREETS or _INVALID_STREET_RE.match(s):
        return np.nan

    # PO box variants missing PO or BOX; digits required so street names
    # like BOX CANYON RD are untouched
    s = _SHORT_POBOX_RE.sub(r'PO BOX \1', s)

    # leading OCR digit/letter confusion (E to 3, I to 1)
    s = re.sub(r'^E(\d{2,})\b', lambda match: '3' + match.group(1), s)
    s = re.sub(r'^I(\d{3,})\b', lambda match: '1' + match.group(1), s)

    # unit/suite written before the address: move it to the end
    match = re.match(r'^((?:UNIT|APT|STE|SUITE)\s+\S+)\s+(\d+\s+.+)$', s)
    if match:
        s = match.group(2).strip() + ' ' + match.group(1).strip()

    s = _TRAILING_UNIT_RE.sub('', s)

    s = POBOX_RE.sub('PO BOX', s)
    s = re.sub(r'\s+', ' ', s)

    for pattern, replacement in STREET_TYPO_RULES:
        s = pattern.sub(replacement, s)

    # A direction stuck to the house number is not a house suffix.
    s = re.sub(r'^(\d+)([NSEW])\s+', r'\1 \2 ', s)

    # Missing space between house number and street name. A single other
    # letter followed by whitespace is part of the house number (14A, 704C);
    # a leading ordinal (3RD FLOOR, 21ST ST) is one word, never split.
    s = _split_fused_house_number(s)

    for rules in (DIR_PREFIX, DIR_MID, STREET_TYPES, DIR_SUFFIX):
        for pattern, replacement in rules:
            s = pattern.sub(replacement, s)

    s = s.replace(',', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    s = _tidy_periods(s)

    s = re.sub(r'\s+', ' ', s).strip()
    return s


_FUSED_HOUSE_NUMBER_RE = re.compile(r'^(\d+)([A-Z])(?=[A-Z])')


_LEADING_ORDINAL_RE = re.compile(r'^(\d+)(ST|ND|RD|TH)\b')


def _ordinal_suffix(number: str) -> str:
    """English ordinal suffix of a number: 1 -> ST, 3 -> RD, 12 -> TH, 23 -> RD."""
    value = int(number)
    if value % 100 in (11, 12, 13):
        return 'TH'
    return {1: 'ST', 2: 'ND', 3: 'RD'}.get(value % 10, 'TH')


def _split_fused_house_number(s: str) -> str:
    """'123MAIN ST' -> '123 MAIN ST', but a correct ordinal ('3RD FLOOR', '21ST ST') stays one word; '12ST JAMES PL' (12 takes TH) is still split."""
    match = _LEADING_ORDINAL_RE.match(s)
    if match and match.group(2) == _ordinal_suffix(match.group(1)):
        return s
    return _FUSED_HOUSE_NUMBER_RE.sub(r'\1 \2', s)


def _normalize_unit(s: str) -> str:
    """Normalize a unit/apt/suite string; placeholders ('NONE', '.', 'N/A', 'HOME') are no unit."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()
    if s.strip(' .,-') in _JUNK_UNITS or _has_no_unit_text(s):
        return np.nan
    for pattern, replacement in UNIT_RULES:
        s = pattern.sub(replacement, s)
    return s.strip()


def _strip_named_unit(streets: pd.Series) -> pd.Series:
    """Remove a trailing named unit and its leftover comma/whitespace."""
    stripped = streets.str.replace(UNIT_EXTRACT, '', regex=True).str.strip()
    return stripped.str.rstrip(',').str.strip()


def _extract_units(street1: pd.Series, street2: pd.Series) -> tuple[pd.Series, pd.Series, int]:
    """Move unit info embedded in street_1 into empty street_2; returns (s1, s2, n_extracted)."""
    s1 = street1.fillna('').astype(str).replace({'nan': ''})
    s2 = street2.fillna('').astype(str).replace({'nan': ''})
    s2_blank = s2.str.strip().eq('')
    n_extracted = 0

    # named units (APT, STE, UNIT, ...)
    named_unit = s1.str.extract(UNIT_EXTRACT)[0]
    move_named = named_unit.notna() & s2_blank
    if move_named.any():
        n_extracted += int(move_named.sum())
        s2 = s2.where(~move_named, named_unit.str.strip())
        s1 = s1.where(~move_named, _strip_named_unit(s1))
        s2_blank = s2.str.strip().eq('')

    # A second pass is intentional: after SUITE is removed, a preceding
    # "OFFICE PARK" can itself match the legacy unit rule.
    same_named = named_unit.notna() & ~s2_blank & (
        named_unit.str.strip().str.upper() == s2.str.strip().str.upper()
    )
    if same_named.any():
        s1 = s1.where(~same_named, _strip_named_unit(s1))

    # hash units (#5A, # 200)
    match_hash = s1.str.extract(HASH_EXTRACT)
    has_hash = match_hash[0].notna() & s2_blank
    if has_hash.any():
        n_extracted += int(has_hash.sum())
        s2 = s2.where(~has_hash, match_hash[0].str.strip())
        s1 = s1.where(
            ~has_hash,
            s1.str.replace(HASH_EXTRACT, '', regex=True).str.strip()
        )

    return s1.replace({'': np.nan}), s2.replace({'': np.nan}), n_extracted
