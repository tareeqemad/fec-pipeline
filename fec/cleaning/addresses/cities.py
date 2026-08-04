"""City-name cleaning: junk stripping, known corrections, fuzzy typo detection."""
import difflib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config import CITY_NORMALIZE, STATE_IN_CITY, expand_city_abbreviations

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
