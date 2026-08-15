"""Contributor street, city, and ZIP normalization."""
import difflib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config.cities import CITY_NORMALIZE, expand_city_abbreviations
from fec.config.streets import (
    POBOX_RE, DIR_PREFIX, DIR_SUFFIX, DIR_MID, STREET_TYPES,
    UNIT_RULES, UNIT_EXTRACT, HASH_EXTRACT, STREET_TYPO_RULES,
    STATE_IN_CITY,
)

# Street cleaning

# placeholder values that mean "no address"
_JUNK_STREETS = {'HOME', 'YES', 'NO', 'SAME', 'N/A', 'NA', 'NONE',
                 'UNKNOWN', 'X', 'XX', 'XXX', 'NOT PROVIDED',
                 'UNITED STATES OF AMERICA', 'USA'}

# trailing unit word without a number
_TRAILING_UNIT_RE = re.compile(r'\s+(?:APT|UNIT|STE|SUITE)\s*$')
_INVALID_STREET_RE = re.compile(r'^(?:C\d{7,}|\d+)$')
_SHORT_POBOX_RE = re.compile(r'^(?:P\.?\s*O\.?\s*B?|BOX)\s+(\d)')


def clean_streets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Normalize street_1/street_2 and extract embedded units; returns (df, counts)."""
    # email mistakenly in street_1: promote street_2 if it looks like an address, else null
    s1 = df['contributor_street_1'].astype('string')
    email_mask = s1.str.contains('@', na=False, regex=False)
    df['_street_email_in_s1'] = email_mask

    s2 = df['contributor_street_2'].astype('string')
    looks_addr = s2.str.contains(r'\d', na=False) | s2.str.contains(r'PO\s*BOX', case=False, na=False)
    swap_mask = email_mask & looks_addr
    null_mask = email_mask & ~swap_mask
    df['_street_swapped_from_s2'] = swap_mask
    df['_street_nulled_email'] = null_mask

    if swap_mask.any():
        df.loc[swap_mask, 'contributor_street_1'] = df.loc[swap_mask, 'contributor_street_2']
        df.loc[swap_mask, 'contributor_street_2'] = np.nan
    if null_mask.any():
        df.loc[null_mask, 'contributor_street_1'] = np.nan

    before = df['contributor_street_1'].copy()
    df['contributor_street_1'] = df['contributor_street_1'].apply(_normalize_street)
    n_normalized = int((before.fillna('') != df['contributor_street_1'].fillna('')).sum())

    df['contributor_street_2'] = df['contributor_street_2'].apply(_normalize_unit)

    # move units embedded in street_1 into empty street_2
    s1, s2, n_extracted = _extract_units(
        df['contributor_street_1'], df['contributor_street_2']
    )
    df['contributor_street_1'] = s1
    df['contributor_street_2'] = s2.apply(_normalize_unit)

    # strip unit words orphaned by the extraction
    df['contributor_street_1'] = df['contributor_street_1'].str.replace(
        _TRAILING_UNIT_RE, '', regex=True
    )

    # FEC sometimes has the donor's own name instead of the address: null it
    street = df['contributor_street_1'].fillna('')
    first_names = df['contributor_first_name'].fillna('').str.strip().str.upper()
    last_names = df['contributor_last_name'].fillna('').str.strip().str.upper()

    is_name = (
        ((street == first_names) & (first_names.str.len() >= 3))
        | ((street == last_names) & (last_names.str.len() >= 3))
        | (street == first_names + ' ' + last_names)
        | (street == last_names + ' ' + first_names)
    )
    if is_name.any():
        df.loc[is_name, 'contributor_street_1'] = np.nan
        n_normalized += int(is_name.sum())

    counts = {'streets_normalized': n_normalized, 'units_extracted': n_extracted}
    return df, counts


def _normalize_street(s: str) -> str:
    """Normalize a single street address string."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()

    # HTML entities, stray semicolons / brackets / colons
    s = re.sub(r'&SHY;', '', s)
    s = re.sub(r'&AMP;', '&', s)
    s = re.sub(r'&[A-Z]+;', '', s)
    s = s.replace(';', '')
    s = s.replace('[', '').replace(']', '')
    s = re.sub(r'\s*:', ' ', s)

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

    # missing space between house number and street name
    s = re.sub(r'^(\d+)([A-Z])', r'\1 \2', s)

    for rules in (DIR_PREFIX, DIR_MID, STREET_TYPES, DIR_SUFFIX):
        for pattern, replacement in rules:
            s = pattern.sub(replacement, s)

    # trailing direction moved after the house number (USPS prefix form),
    # so "101 WESTON LN S" and "S WESTON LN 101" both match "101 S WESTON LN"
    s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)

    s = s.replace(',', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    # period cleanup: specific patterns first, catch-all last
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

    s = s.rstrip('.')

    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_unit(s: str) -> str:
    """Normalize a unit/apt/suite string."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()
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


# City cleaning

# real-city lookalikes that must never be "corrected" into each other
_PROTECTED_CITIES = {
    'MEDFORD', 'BEDFORD',
    'WEST WINDSOR', 'EAST WINDSOR',
    'SANTA CLARA', 'SANTA CLARITA',
    'HOPKINSVILLE', 'TOMPKINSVILLE',
    'WEST BLOOMFIELD TOWNSHIP', 'BLOOMFIELD TOWNSHIP',
    'ST. LOUIS PARK', 'ST LOUIS PARK',
    'NORTH CAMBRIDGE', 'CAMBRIDGE',
}


def clean_cities(df: pd.DataFrame, fuzzy: bool = True, report_dir: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Clean city names; report_dir writes auto_city_fixes.json for review. Returns (df, counts)."""
    counts = {'known_fixes': 0, 'fuzzy_fixes': 0, 'punctuation_cleaned': 0}

    cities = df['contributor_city'].astype(str).str.strip().str.upper()

    cities = cities.str.lstrip('`~')

    # embedded ZIPs and state/city combos ("BETHESDA, MARYLAND 20817", "CHARLOTTE, NC 28226")
    cities = cities.str.replace(r',?\s*\d{5}(-\d{4})?\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+[A-Z]{2}\s+\d{5}\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+(MARYLAND|CALIFORNIA|TEXAS)\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+D\.C\.,?\s*(USA)?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+PA\.?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r'/[A-Z]+$', '', regex=True)

    # a unit string is not a city
    cities = cities.where(~cities.str.match(r'^APT\s|^STE\s|^UNIT\s|^#\d', case=False, na=False), other=np.nan)

    before = cities.copy()
    cities = cities.str.rstrip('.,;:')
    counts['punctuation_cleaned'] = int((before != cities).sum())

    # state codes stuck at end of city
    before = cities.copy()
    cities = cities.str.replace(STATE_IN_CITY, '', regex=True).str.strip()
    counts['punctuation_cleaned'] += int((before != cities).sum())

    cities = cities.str.replace(r'\s{2,}', ' ', regex=True)

    before = cities.copy()
    cities = cities.replace(CITY_NORMALIZE)
    counts['known_fixes'] = int((before != cities).sum())
    df['contributor_city'] = cities

    if fuzzy:
        auto_fixes = _auto_detect_city_typos(df)
        if auto_fixes:
            before = df['contributor_city'].copy()
            df['contributor_city'] = df['contributor_city'].replace(auto_fixes)
            counts['fuzzy_fixes'] = int((before != df['contributor_city']).sum())

            if report_dir:
                report_path = Path(report_dir) / 'auto_city_fixes.json'
                with open(report_path, 'w', encoding='utf-8') as handle:
                    json.dump(auto_fixes, handle, indent=2, ensure_ascii=False)

    # expand ST./MT./FT. abbreviations LAST so the fuzzy pass above still
    # matches its "ST."-form protected list
    df['contributor_city'] = df['contributor_city'].map(expand_city_abbreviations)

    return df, counts


def _auto_detect_city_typos(df: pd.DataFrame, cutoff: float = 0.88, min_common: int = 10, max_rare: int = 3) -> dict:
    """Fuzzy-match rare city names against common ones in the same state; returns {typo: fix}."""
    city_counts = (
        df.groupby(['contributor_state', 'contributor_city'])
        .size()
        .reset_index(name='n')
    )

    fixes = {}
    for _, state_cities in city_counts.groupby('contributor_state', sort=False):
        common_names = state_cities[state_cities['n'] >= min_common]['contributor_city'].tolist()
        rare_cities = state_cities[state_cities['n'] <= max_rare]

        for _, row in rare_cities.iterrows():
            city = row['contributor_city']
            if city in _PROTECTED_CITIES or city in CITY_NORMALIZE:
                continue

            matches = difflib.get_close_matches(city, common_names, n=1, cutoff=cutoff)
            if matches and matches[0] != city:
                fixes[city] = matches[0]

    return fixes


# ZIP cleaning

def clean_zips(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Extract and normalize 5-digit ZIP codes; returns (df, counts)."""
    # FEC sends 5- or 9-digit; keep the 5-digit form under the same column name
    df['contributor_zip'], n_invalid = _clean_zip_raw(df['contributor_zip'])
    return df, {
        'cleaned': int(df['contributor_zip'].notna().sum()),
        'invalid_nulled': n_invalid,
    }


def _clean_zip_raw(raw: pd.Series) -> tuple[pd.Series, int]:
    """Clean raw ZIP strings to 5 digits; returns (series, n_invalid_nulled)."""
    raw = raw.astype(str).str.strip().str.replace(r'[^\d]', '', regex=True)

    result = raw.str.zfill(5).str[:5].where(
        raw.str.len() <= 5, raw.str.zfill(9).str[:5]
    )

    # lowest USPS-assigned ZIP is 00501; below is unassigned (catches digit-drop
    # typos like "00034"). nulling is safe: the per-donor same-street fill
    # downstream restores the ZIP from the donor's own other filings.
    invalid = ~result.str.match(r'^\d{5}$', na=False) | (result < '00501')
    n_invalid = int(invalid.sum())
    result[invalid] = np.nan

    return result, n_invalid
