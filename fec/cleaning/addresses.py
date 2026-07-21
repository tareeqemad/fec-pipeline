"""
cleaning/addresses.py — Street, city, and ZIP code cleaning.

Street cleaning:
  - Normalize PO BOX formats
  - Abbreviate directions (NORTH → N) and street types (STREET → ST)
  - Extract unit info (APT, STE, #) from street_1 → street_2

City cleaning:
  - Apply known corrections (typos + abbreviations)
  - Auto-detect new typos via fuzzy matching (rare vs. common names per state)
  - Remove trailing state codes ("ENCINO, CA" → "ENCINO")

ZIP cleaning:
  - Extract 5-digit ZIP from 9-digit
  - Zero-pad short ZIPs
  - Validate format, null out garbage
"""
import json
import re
from typing import Tuple
import difflib

import numpy as np
import pandas as pd
from pathlib import Path

from fec.config import (
    CITY_NORMALIZE, STATE_IN_CITY, POBOX_RE,
    DIR_PREFIX, DIR_SUFFIX, DIR_MID, STREET_TYPES, UNIT_RULES,
    UNIT_EXTRACT, HASH_EXTRACT, expand_city_abbreviations,
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Street cleaning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def clean_streets(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """
    Normalize street_1 and street_2, extract embedded units.

    Returns:
        (df, {'streets_normalized': int, 'units_extracted': int})
    """
    n_normalized = 0
    n_extracted = 0

    # ── Pre-fix: email mistakenly placed in street_1 ──
    if 'contributor_street_1' in df.columns:
        s1 = df['contributor_street_1'].astype('string')
        email_mask = s1.str.contains(r'@', na=False)
        df['_street_email_in_s1'] = email_mask

        if 'contributor_street_2' in df.columns:
            s2 = df['contributor_street_2'].astype('string')
            # Heuristic: street_2 looks like a street if it has a digit (house #) or PO BOX.
            looks_addr = s2.str.contains(r'\d', na=False) | s2.str.contains(r'PO\s*BOX', case=False, na=False)
            swap_mask = email_mask & looks_addr
            df['_street_swapped_from_s2'] = swap_mask
            df['_street_nulled_email'] = email_mask & ~swap_mask

            if swap_mask.any():
                df.loc[swap_mask, 'contributor_street_1'] = df.loc[swap_mask, 'contributor_street_2']
                df.loc[swap_mask, 'contributor_street_2'] = np.nan
            if (email_mask & ~swap_mask).any():
                df.loc[email_mask & ~swap_mask, 'contributor_street_1'] = np.nan
        else:
            df['_street_swapped_from_s2'] = False
            df['_street_nulled_email'] = email_mask
            if email_mask.any():
                df.loc[email_mask, 'contributor_street_1'] = np.nan


    if 'contributor_street_1' in df.columns:
        before = df['contributor_street_1'].copy()
        df['contributor_street_1'] = df['contributor_street_1'].apply(_normalize_street)
        n_normalized = int((before.fillna('') != df['contributor_street_1'].fillna('')).sum())

    if 'contributor_street_2' in df.columns:
        df['contributor_street_2'] = df['contributor_street_2'].apply(_normalize_unit)

    # Move units embedded in street_1 into street_2 (when street_2 is empty)
    if 'contributor_street_1' in df.columns and 'contributor_street_2' in df.columns:
        s1, s2, n_extracted = _extract_units(
            df['contributor_street_1'], df['contributor_street_2']
        )
        df['contributor_street_1'] = s1
        df['contributor_street_2'] = s2.apply(_normalize_unit)

        # Post-extraction cleanup: strip orphaned unit words left behind
        # after unit numbers were extracted (e.g. "14011 VENTURA BLVD SUITE" ← # was extracted)
        df['contributor_street_1'] = df['contributor_street_1'].str.replace(
            r'\s+(?:APT|UNIT|STE|SUITE)\s*$', '', regex=True
        )

    # ── Post-normalization: null out person names in street field ──
    # FEC sometimes has the donor's own name instead of their address.
    # Detect: street = first name, last name, or "FIRST LAST" / "LAST FIRST"
    if 'contributor_street_1' in df.columns and 'contributor_first_name' in df.columns:
        s = df['contributor_street_1'].fillna('')
        fn = df['contributor_first_name'].fillna('').str.strip().str.upper()
        ln = df['contributor_last_name'].fillna('').str.strip().str.upper()

        # Street is exactly the first name, last name, or "FIRST LAST" / "LAST FIRST"
        is_name = (
            ((s == fn) & (fn.str.len() >= 3))
            | ((s == ln) & (ln.str.len() >= 3))
            | (s == fn + ' ' + ln)
            | (s == ln + ' ' + fn)
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

    # ── Phase 0: Fix corrupted characters ──
    # HTML entities: &SHY; (soft hyphen), &AMP;, etc.
    s = re.sub(r'&SHY;', '', s)            # soft hyphen → remove
    s = re.sub(r'&AMP;', '&', s)           # &amp; → &
    s = re.sub(r'&[A-Z]+;', '', s)         # any remaining HTML entities → remove

    # Fix semicolons (end-of-line artifacts or corrupted chars)
    s = s.rstrip(';')                       # trailing semicolons
    s = s.replace(';', '')                  # embedded semicolons

    # Fix misplaced brackets
    s = s.replace('[', '').replace(']', '') # stray brackets (AP[T → APT)

    # Fix misplaced colons (OSPREY :AME → OSPREY LANE)
    s = re.sub(r'\s*:', ' ', s)

    # ── Phase 0b: Email detected in street field ──
    # Treat emails as invalid addresses.
    if '@' in s:
        return np.nan

    # ── Phase 0c: Junk values ──
    # Placeholder words, FEC committee IDs, numbers-only
    _JUNK_STREETS = {'HOME', 'YES', 'NO', 'SAME', 'N/A', 'NA', 'NONE',
                     'UNKNOWN', 'X', 'XX', 'XXX', 'NOT PROVIDED',
                     'UNITED STATES OF AMERICA', 'USA'}
    if s in _JUNK_STREETS:
        return np.nan
    # FEC committee IDs accidentally in street field (C00135541)
    if re.match(r'^C\d{7,}$', s):
        return np.nan
    # Numbers only — house number without street name (data entry error)
    if re.match(r'^\d+$', s):
        return np.nan

    # ── Phase 0c2: PO Box variants without "BOX" ──
    # PO 7759 → PO BOX 7759, POB 227 → PO BOX 227, P.O. 456 → PO BOX 456
    s = re.sub(r'^P\.?\s*O\.?\s*B?\s+(\d)', r'PO BOX \1', s)
    # BOX 137 → PO BOX 137 (the "PO" was dropped; digits required so street
    # names like "BOX CANYON RD" are untouched)
    s = re.sub(r'^BOX\s+(\d)', r'PO BOX \1', s)

    # ── Phase 0d: OCR digit/letter confusion ──
    # E118 → 3118, I3235 → 13235 (common OCR: E→3, I→1)
    # Only when followed by digits then a street name
    s = re.sub(r'^E(\d{2,})\b', lambda m: '3' + m.group(1), s)   # E118 → 3118
    s = re.sub(r'^I(\d{3,})\b', lambda m: '1' + m.group(1), s)   # I3235 → 13235

    # ── Phase 0e: Unit/Suite before address → reorder ──
    # UNIT 8606 7485 VICTORY LN → 7485 VICTORY LN UNIT 8606
    # SUITE 107-283 9208 NE HWY 9 → 9208 NE HWY 9 SUITE 107-283
    m = re.match(r'^((?:UNIT|APT|STE|SUITE)\s+\S+)\s+(\d+\s+.+)$', s)
    if m:
        s = m.group(2).strip() + ' ' + m.group(1).strip()

    # ── Phase 0f: Trailing APT/UNIT without number → strip ──
    # "1815 JOHN F KENNEDY BLVD APT" → "1815 JOHN F KENNEDY BLVD"
    s = re.sub(r'\s+(?:APT|UNIT|STE|SUITE)\s*$', '', s)

    s = POBOX_RE.sub('PO BOX', s)
    s = re.sub(r'\s+', ' ', s)

    # Fix missing space between house number and street name (10515STONEBRIDGE → 10515 STONEBRIDGE)
    s = re.sub(r'^(\d+)([A-Z])', r'\1 \2', s)

    # Abbreviate directions (NORTH → N, SOUTHWEST → SW, etc.)
    for pat, rep in DIR_PREFIX:
        s = pat.sub(rep, s)
    for pat, rep in DIR_MID:       # directions after house number
        s = pat.sub(rep, s)
    for pat, rep in STREET_TYPES:
        s = pat.sub(rep, s)
    for pat, rep in DIR_SUFFIX:
        s = pat.sub(rep, s)

    # Fix trailing direction: "101 WESTON LN S" → "101 S WESTON LN"
    # Moves a trailing single direction letter to after house number,
    # so it matches the prefix-direction form used by USPS/geocoders.
    # Fixes DEBORAH RUDY case: "101 WESTON LN S" and "S WESTON LN 101"
    # both become "101 S WESTON LN" → donor matcher merges them.
    s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)

    s = s.replace(',', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    # ── Phase 3: Deep period cleanup ──
    # After abbreviations, periods should be gone — but some slip through.
    # Order matters: specific patterns first, catch-all last.

    # Double periods (DR.. → DR)
    s = re.sub(r'\.{2,}', '.', s)

    # P.0. BOX → PO BOX  (zero instead of O — data entry error)
    s = re.sub(r'\bP\.0\.\s*BOX\b', 'PO BOX', s)

    # Period as digit-letter separator (88.W → 88 W)
    s = re.sub(r'(\d)\.([A-Z])', r'\1 \2', s)

    # SO. → S  (non-standard South abbreviation)
    s = re.sub(r'\bSO\.\s', 'S ', s)
    s = re.sub(r'\bSO\.(?=\s|$)', 'S', s)

    # Period after ordinals (39TH. → 39TH, 1ST. → 1ST)
    s = re.sub(r'(\d+(?:ST|ND|RD|TH))\.', r'\1', s)

    # Compound direction dots (N.W → NW, S.W → SW, N.E → NE, S.E → SE)
    # Must run BEFORE single-letter initial cleanup to avoid S.W → "S W"
    s = re.sub(r'\b([NS])\.([EW])\b\.?', r'\1\2', s)

    # Single-letter direction dot stuck to next word (W.PACES → W PACES)
    s = re.sub(r'\b([NSEW])\.\s*([A-Z])', r'\1 \2', s)

    # Period after single-letter initials (JOHN F. → JOHN F)
    s = re.sub(r'\b([A-Z])\.\s', r'\1 ', s)

    # Period after common mid-word abbreviations
    s = re.sub(r'\b(UNIV|PT|NO|CTR|DEPT|BLDG|GEN|GOVT|NATL)\.\s*', r'\1 ', s)

    # Catch-all: period stuck to any word of 2+ letters before a space
    # (HANOVER. ST → HANOVER ST)
    s = re.sub(r'([A-Z]{2,})\.\s', r'\1 ', s)

    # Stray state codes stuck at end (PA. 1, IL. 60) — a 2-letter code + period
    # followed by a stray digit (a ZIP fragment). The trailing digit is REQUIRED:
    # without it this also ate legitimate 2-letter street types ending in a
    # period ("FIGUEROA ST.", "MANDARIN RD." → losing ST/RD on 1,200+ rows).
    s = re.sub(r'\s+[A-Z]{2}\.\s*\d+$', '', s)

    s = s.rstrip('.')

    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_unit(s: str) -> str:
    """Normalize a unit/apt/suite string."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()
    for pat, rep in UNIT_RULES:
        s = pat.sub(rep, s)
    return s.strip()


def _extract_units(street1: pd.Series, street2: pd.Series) -> Tuple[pd.Series, pd.Series, int]:
    """
    Move unit info embedded in street_1 into street_2 when street_2 is empty.

    Handles two patterns:
      - Named units: "123 MAIN ST APT 5" → street_1="123 MAIN ST", street_2="APT 5"
      - Hash units:  "123 MAIN ST #5A"   → street_1="123 MAIN ST", street_2="# 5A"

    Returns: (street1, street2, n_extracted)
    """
    s1 = street1.fillna('').astype(str).replace({'nan': ''})
    s2 = street2.fillna('').astype(str).replace({'nan': ''})
    s2_blank = s2.str.strip().eq('')
    n_extracted = 0

    # Named units (APT, STE, UNIT, ...)
    match_named = s1.str.extract(UNIT_EXTRACT)
    has_named = match_named[0].notna() & s2_blank
    if has_named.any():
        n_extracted += int(has_named.sum())
        s2 = s2.where(~has_named, match_named[0].str.strip())
        s1 = s1.where(
            ~has_named,
            s1.str.replace(UNIT_EXTRACT, '', regex=True).str.strip().str.rstrip(',').str.strip()
        )
        s2_blank = s2.str.strip().eq('')

    # Dedup: street_1 still has unit info that's already in street_2
    # e.g. street_1="175 E 74TH ST APT 17A", street_2="APT 17A" → strip from street_1
    has_named_dup = match_named[0].notna() & ~s2_blank
    if has_named_dup.any():
        unit_in_s1 = match_named[0].str.strip().str.upper()
        unit_in_s2 = s2.str.strip().str.upper()
        is_same_unit = has_named_dup & (unit_in_s1 == unit_in_s2)
        if is_same_unit.any():
            s1 = s1.where(
                ~is_same_unit,
                s1.str.replace(UNIT_EXTRACT, '', regex=True).str.strip().str.rstrip(',').str.strip()
            )

    # Hash units (#5A, # 200, etc.)
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  City cleaning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def clean_cities(df: pd.DataFrame, fuzzy: bool = True, report_dir: str = None) -> Tuple[pd.DataFrame, dict]:
    """
    Clean city names.

    Args:
        fuzzy:       Enable auto fuzzy typo detection.
        report_dir:  If set, writes auto_city_fixes.json for human review.

    Returns:
        (df, {'known_fixes': int, 'fuzzy_fixes': int, 'punctuation_cleaned': int})
    """
    counts = {'known_fixes': 0, 'fuzzy_fixes': 0, 'punctuation_cleaned': 0}

    if 'contributor_city' not in df.columns:
        return df, counts

    s = df['contributor_city'].astype(str).str.strip().str.upper()

    # Strip leading junk characters
    s = s.str.lstrip('`~')

    # Strip embedded ZIP codes and state/city combos
    # e.g. "COLUMBUS, 43209" → "COLUMBUS"
    # e.g. "BETHESDA, MARYLAND 20817" → "BETHESDA"
    # e.g. "CHARLOTTE, NC 28226" → "CHARLOTTE"
    s = s.str.replace(r',?\s*\d{5}(-\d{4})?\s*$', '', regex=True)
    s = s.str.replace(r',?\s+[A-Z]{2}\s+\d{5}\s*$', '', regex=True)
    s = s.str.replace(r',?\s+(MARYLAND|CALIFORNIA|TEXAS)\s*\d*$', '', regex=True)
    s = s.str.replace(r',?\s+D\.C\.,?\s*(USA)?\s*\d*$', '', regex=True)
    s = s.str.replace(r',?\s+PA\.?\s*\d*$', '', regex=True)
    # "AUSTIN/TEXAS" → handled by CITY_NORMALIZE
    s = s.str.replace(r'/[A-Z]+$', '', regex=True)

    # "APT 5" as city name → NaN (clearly wrong field)
    s = s.where(~s.str.match(r'^APT\s|^STE\s|^UNIT\s|^#\d', case=False, na=False), other=np.nan)

    # Strip trailing punctuation
    before = s.copy()
    s = s.str.rstrip('.,;:')
    counts['punctuation_cleaned'] = int((before != s).sum())

    # Remove state codes stuck at end of city ("ENCINO, CA" → "ENCINO")
    before = s.copy()
    s = s.str.replace(STATE_IN_CITY, '', regex=True).str.strip()
    counts['punctuation_cleaned'] += int((before != s).sum())

    # Collapse double+ spaces
    s = s.str.replace(r'\s{2,}', ' ', regex=True)

    # Apply known corrections
    before = s.copy()
    s = s.replace(CITY_NORMALIZE)
    counts['known_fixes'] = int((before != s).sum())
    df['contributor_city'] = s

    # Auto-detect new typos via fuzzy matching
    if fuzzy:
        auto_fixes = _auto_detect_city_typos(df)
        if auto_fixes:
            before = df['contributor_city'].copy()
            df['contributor_city'] = df['contributor_city'].replace(auto_fixes)
            counts['fuzzy_fixes'] = int((before != df['contributor_city']).sum())

            # Write report for human review
            if report_dir:
                report_path = Path(report_dir) / 'auto_city_fixes.json'
                with open(report_path, 'w', encoding='utf-8') as f:
                    json.dump(auto_fixes, f, indent=2, ensure_ascii=False)

    # Expand any surviving city-name abbreviation (ST.→SAINT, MT.→MOUNT,
    # FT.→FORT) so the cleaned file carries no abbreviations. Runs last so the
    # fuzzy typo pass above still matches its "ST."-form protected list.
    df['contributor_city'] = df['contributor_city'].map(expand_city_abbreviations)

    return df, counts


def _auto_detect_city_typos(df: pd.DataFrame, cutoff: float = 0.88, min_common: int = 10, max_rare: int = 3) -> dict:
    """
    Fuzzy-match rare city names against common ones in the same state.

    Logic:
      - "Common" = appears ≥ min_common times in that state
      - "Rare" = appears ≤ max_rare times in that state
      - If a rare city is ≥ 88% similar to a common one → it's probably a typo

    Protected cities are excluded to avoid false corrections
    (e.g. MEDFORD vs BEDFORD are both real cities).

    Returns: {typo: correction} dict
    """
    protected = {
        'MEDFORD', 'BEDFORD',
        'WEST WINDSOR', 'EAST WINDSOR',
        'SANTA CLARA', 'SANTA CLARITA',
        'HOPKINSVILLE', 'TOMPKINSVILLE',
        'WEST BLOOMFIELD TOWNSHIP', 'BLOOMFIELD TOWNSHIP',
        'ST. LOUIS PARK', 'ST LOUIS PARK',
        'NORTH CAMBRIDGE', 'CAMBRIDGE',
    }

    city_counts = (
        df.groupby(['contributor_state', 'contributor_city'])
        .size()
        .reset_index(name='n')
    )

    fixes = {}
    for state in city_counts['contributor_state'].unique():
        state_cities = city_counts[city_counts['contributor_state'] == state]
        common_names = state_cities[state_cities['n'] >= min_common]['contributor_city'].tolist()
        rare_cities = state_cities[state_cities['n'] <= max_rare]

        for _, row in rare_cities.iterrows():
            city = row['contributor_city']
            if city in protected or city in CITY_NORMALIZE:
                continue

            matches = difflib.get_close_matches(city, common_names, n=1, cutoff=cutoff)
            if matches and matches[0] != city:
                fixes[city] = matches[0]

    return fixes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ZIP cleaning
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def clean_zips(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """
    Extract and normalize 5-digit ZIP codes.

    Returns:
        (df, {'cleaned': int, 'invalid_nulled': int})
    """
    counts = {'cleaned': 0, 'invalid_nulled': 0}

    # Clean the raw ZIP in place (FEC sends 5- or 9-digit; we keep the 5-digit
    # form under the same column name).
    if 'contributor_zip' in df.columns:
        df['contributor_zip'], counts['invalid_nulled'] = _clean_zip_raw(df['contributor_zip'])
        counts['cleaned'] = int(df['contributor_zip'].notna().sum())

    return df, counts


def _clean_zip_raw(raw: pd.Series) -> Tuple[pd.Series, int]:
    """
    Clean raw ZIP strings → 5-digit format.

    Handles:
      - "480844141" (9-digit) → "48084"
      - "7093"      (short)   → "07093"
      - "00000"     (invalid) → NaN
      - Non-numeric junk      → NaN

    Returns: (series, n_invalid_nulled)
    """
    raw = raw.astype(str).str.strip().str.replace(r'[^\d]', '', regex=True)

    is_short = raw.str.len() <= 5
    zip5_short = raw.str.zfill(5).str[:5]
    zip5_long = raw.str.zfill(9).str[:5]
    result = zip5_short.where(is_short, zip5_long)

    # Validate: must be 5 digits and an assigned range. The lowest ZIP the USPS
    # has ever assigned is 00501 (Holtsville NY); everything below is unassigned
    # — catches digit-drop typos like "00034" (a filer's mangled 90034), which
    # the old all-zeros check let through. Nulling is safe: the per-donor
    # same-street fill downstream restores the ZIP from the donor's own other
    # filings when they exist.
    invalid = ~result.str.match(r'^\d{5}$', na=False) | (result < '00501')
    n_invalid = int(invalid.sum())
    result[invalid] = np.nan

    return result, n_invalid
