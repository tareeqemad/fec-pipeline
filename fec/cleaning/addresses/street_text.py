"""Normalize one street or unit string."""
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
                 'UNITED STATES OF AMERICA', 'USA', 'NO ADDRESS'}

# street_2 placeholders; bare X is a unit
_JUNK_UNITS = (_JUNK_STREETS - {'X'}) | {'NULL', 'N.A', 'N.A.', 'ADDR 2', 'ADDRESS 2', 'ADDRESS LINE 2'}


# true if no alphanumeric or # character
def _has_no_unit_text(value: str) -> bool:
    return not any(char.isalnum() or char == '#' for char in value)


# trailing unit word without a number
TRAILING_UNIT_RE = re.compile(r'\s+(?:APT|UNIT|STE|SUITE)\s*$')

# FEC committee id or bare number
_INVALID_STREET_RE = re.compile(r'^(?:C\d{7,}|\d+)$')

# a PO box missing the word BOX
_SHORT_POBOX_RE = re.compile(r'^(?:P\.?\s*O\.?\s*B?|BOX)\s+(\d)')


# house number token, optional letter suffix
_HOUSE_TOKEN_RE = re.compile(r'^\d+[A-Z]?$')


_DIRECTION_TOKENS = frozenset({'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'})


# drop a repeated, cut-off street copy
def drop_repeated_street(s):
    if not isinstance(s, str):
        return s
    tokens = s.split()
    if len(tokens) < 4 or not _HOUSE_TOKEN_RE.match(tokens[0]):
        return s
    for index in range(2, len(tokens) - 1):
        if tokens[index] != tokens[0]:
            continue
        # a separator between copies goes too
        head = tokens[:index]
        while head and not any(char.isalnum() for char in head[-1]):
            head.pop()
        first, copy = ' '.join(head), ' '.join(tokens[index:])
        # what is kept must name a street
        names_street = any(
            token not in _DIRECTION_TOKENS and re.search(r'[A-Z]', token) for token in head[1:]
        )
        if names_street and first.startswith(copy):
            return first
    return s


# drop HTML entities, semicolons, brackets, colons
def _strip_markup(s: str) -> str:
    s = re.sub(r'&SHY;', '', s)
    s = re.sub(r'&AMP;', '&', s)
    s = re.sub(r'&[A-Z]+;', '', s)
    s = s.replace(';', '')
    s = s.replace('[', '').replace(']', '')
    return re.sub(r'\s*:', ' ', s)


# remove periods; specific patterns before catch-all
def _tidy_periods(s: str) -> str:
    s = re.sub(r'\.{2,}', '.', s)
    s = re.sub(r'\bP\.0\.\s*BOX\b', 'PO BOX', s)
    s = re.sub(r'(\d)\.([A-Z])', r'\1 \2', s)
    s = re.sub(r'\bSO\.(?=\s|$)', 'S', s)
    s = re.sub(r'(\d+(?:ST|ND|RD|TH))\.', r'\1', s)
    # compound directions must run before single letters
    s = re.sub(r'\b([NS])\.([EW])\b\.?', r'\1\2', s)
    s = re.sub(r'\b([NSEW])\.\s*([A-Z])', r'\1 \2', s)
    s = re.sub(r'\b([A-Z])\.\s', r'\1 ', s)
    s = re.sub(r'\b(UNIV|PT|NO|CTR|DEPT|BLDG|GEN|GOVT|NATL)\.\s*', r'\1 ', s)
    s = re.sub(r'([A-Z]{2,})\.\s', r'\1 ', s)
    # digit required so 2-letter street types survive
    s = re.sub(r'\s+[A-Z]{2}\.\s*\d+$', '', s)

    return s.rstrip('.')


# clean and abbreviate one street string
def normalize_street(s: str) -> str:
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()

    s = _strip_markup(s)

    # an email is not an address
    if '@' in s:
        return np.nan

    if s in _JUNK_STREETS or _INVALID_STREET_RE.match(s):
        return np.nan

    # digits required so BOX CANYON RD survives
    s = _SHORT_POBOX_RE.sub(r'PO BOX \1', s)

    # fix OCR E/3 and I/1 confusion
    s = re.sub(r'^E(\d{2,})\b', lambda match: '3' + match.group(1), s)
    s = re.sub(r'^I(\d{3,})\b', lambda match: '1' + match.group(1), s)

    # move a leading unit to the end
    match = re.match(r'^((?:UNIT|APT|STE|SUITE)\s+\S+)\s+(\d+\s+.+)$', s)
    if match:
        s = match.group(2).strip() + ' ' + match.group(1).strip()

    s = TRAILING_UNIT_RE.sub('', s)

    s = POBOX_RE.sub('PO BOX', s)
    s = re.sub(r'\s+', ' ', s)

    for pattern, replacement in STREET_TYPO_RULES:
        s = pattern.sub(replacement, s)

    # stuck direction is not a house suffix
    s = re.sub(r'^(\d+)([NSEW])\s+', r'\1 \2 ', s)

    s = _split_fused_house_number(s)

    for rules in (DIR_PREFIX, DIR_MID, STREET_TYPES, DIR_SUFFIX):
        for pattern, replacement in rules:
            s = pattern.sub(replacement, s)

    s = s.replace(',', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    s = _tidy_periods(s)

    s = re.sub(r'\s+', ' ', s).strip()
    return s


# house number fused to the street name
_FUSED_HOUSE_NUMBER_RE = re.compile(r'^(\d+)([A-Z])(?=[A-Z])')


# a leading ordinal number, like 21ST
_LEADING_ORDINAL_RE = re.compile(r'^(\d+)(ST|ND|RD|TH)\b')


# compute a number's English ordinal suffix
def _ordinal_suffix(number: str) -> str:
    value = int(number)
    if value % 100 in (11, 12, 13):
        return 'TH'
    return {1: 'ST', 2: 'ND', 3: 'RD'}.get(value % 10, 'TH')


# split fused house number unless correct ordinal
def _split_fused_house_number(s: str) -> str:
    match = _LEADING_ORDINAL_RE.match(s)
    if match and match.group(2) == _ordinal_suffix(match.group(1)):
        return s
    return _FUSED_HOUSE_NUMBER_RE.sub(r'\1 \2', s)


# clean a unit string; placeholders become NaN
def normalize_unit(s: str) -> str:
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()
    # a hashed placeholder is still one
    hashed = s.strip(' .,-#')
    if s.strip(' .,-') in _JUNK_UNITS or (hashed and hashed in _JUNK_UNITS) or _has_no_unit_text(s):
        return np.nan
    for pattern, replacement in UNIT_RULES:
        s = pattern.sub(replacement, s)
    return s.strip()


# remove a trailing named unit from street_1
def _strip_named_unit(streets: pd.Series) -> pd.Series:
    stripped = streets.str.replace(UNIT_EXTRACT, '', regex=True).str.strip()
    return stripped.str.rstrip(',').str.strip()


# audit reasons for unit leftovers in street_1
UNIT_REPEATED_REASON = "street_1_unit_repeating_street_2_dropped"
DOUBLED_HASH_REASON = "doubled_hash_unit_moved_to_street_2"
FLOOR_ORDINAL_REASON = "floor_ordinal_left_in_street_1_dropped"

# a street_2 that is only a floor
_FLOOR_UNIT_RE = re.compile(r'^(?:FL|FLR|FLOOR)\.?\s+(\d{1,3})$')
# an ordinal right after the street type
_FLOOR_ECHO_RE = re.compile(r'(\s(?:ST|AVE|RD|DR|LN|CT|PL|WAY|BLVD|CIR|TER|HWY|PKWY))\s+(\d{1,3})(?:ST|ND|RD|TH)$')


# move street_1 units into empty street_2
def extract_units(
    street1: pd.Series, street2: pd.Series,
) -> tuple[pd.Series, pd.Series, int, pd.Series]:
    s1 = street1.fillna('').astype(str).replace({'nan': ''})
    s2 = street2.fillna('').astype(str).replace({'nan': ''})
    s2_blank = s2.str.strip().eq('')
    n_extracted = 0
    leftovers = pd.Series('', index=s1.index, dtype=object)

    named_unit = s1.str.extract(UNIT_EXTRACT)[0]
    move_named = named_unit.notna() & s2_blank
    if move_named.any():
        n_extracted += int(move_named.sum())
        s2 = s2.where(~move_named, named_unit.str.strip())
        s1 = s1.where(~move_named, _strip_named_unit(s1))
        s2_blank = s2.str.strip().eq('')

    # intentional second pass after the first strip
    same_named = named_unit.notna() & ~s2_blank & (
        named_unit.str.strip().str.upper() == s2.str.strip().str.upper()
    )
    if same_named.any():
        s1 = s1.where(~same_named, _strip_named_unit(s1))

    # drop a hash unit repeating street_2
    hash_unit = s1.str.extract(HASH_EXTRACT)[0]
    repeated = hash_unit.notna() & ~s2_blank & hash_unit.fillna('').map(_unit_number).eq(s2.map(_unit_number))
    if repeated.any():
        s1 = s1.where(~repeated, s1.str.replace(HASH_EXTRACT, '', regex=True).str.strip())
        leftovers[repeated] = UNIT_REPEATED_REASON

    match_hash = s1.str.extract(HASH_EXTRACT)
    has_hash = match_hash[0].notna() & s2_blank
    leftovers[has_hash & s1.str.contains('##', regex=False)] = DOUBLED_HASH_REASON
    if has_hash.any():
        n_extracted += int(has_hash.sum())
        s2 = s2.where(~has_hash, match_hash[0].str.strip())
        s1 = s1.where(
            ~has_hash,
            s1.str.replace(HASH_EXTRACT, '', regex=True).str.strip()
        )

    # drop a floor ordinal left behind
    floor = s2.str.extract(_FLOOR_UNIT_RE)[0]
    echo = s1.str.extract(_FLOOR_ECHO_RE)[1]
    drop_echo = floor.notna() & floor.eq(echo)
    if drop_echo.any():
        s1 = s1.where(~drop_echo, s1.str.replace(_FLOOR_ECHO_RE, r'\1', regex=True))
        leftovers[drop_echo] = FLOOR_ORDINAL_REASON

    return s1.replace({'': np.nan}), s2.replace({'': np.nan}), n_extracted, leftovers


# a unit's number without its keyword
def _unit_number(unit: str) -> str:
    unit = re.sub(r'^\s*(?:(?:APT|UNIT|STE|SUITE|RM|FL|FLOOR)\b|#)', '', str(unit).upper())
    return re.sub(r'[^A-Z0-9]', '', unit) or '-'
