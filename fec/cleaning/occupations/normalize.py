"""Text normalization and regex categorization for the employer and occupation columns."""
import numpy as np
import pandas as pd

from fec.config.data import COMM_PATTERNS, MISSING_VALUES, RETIRE_RE
from fec.config.occupation_rules.categories import (
    CATEGORY_OVERRIDES,
    CATEGORY_PATTERNS,
    RECLASSIFY_CATEGORY_RULES,
)
from fec.config.occupation_rules.rules import OCCUPATION_FIXES

# junk value -> NaN, one batch replace
_JUNK_TO_NAN = {val: np.nan for val in MISSING_VALUES}

# Some raw fixes intentionally give the cleaned value a category that the
# general regex cannot infer (for example COMPLIANCE -> LEGAL). Keep that
# knowledge available when a later step re-categorizes the final occupation.
_FINAL_OCCUPATION_CATEGORIES = {
    final_occupation: category
    for final_occupation, category in OCCUPATION_FIXES.values()
    if category
}

# Cyrillic lookalikes -> ASCII (common in FEC data entry)
_CYRILLIC_TO_ASCII = str.maketrans({
    '\u0410': 'A', '\u0412': 'B', '\u0421': 'C', '\u0415': 'E',
    '\u041d': 'H', '\u041a': 'K', '\u041c': 'M', '\u041e': 'O',
    '\u0420': 'P', '\u0422': 'T', '\u0423': 'Y', '\u0425': 'X',
})


def _normalize_text(series: pd.Series, normalize_map: dict, collapse_retire: bool = False) -> tuple[pd.Series, int]:
    """Strip/uppercase, fix entities and junk, optionally collapse RETIRE* to RETIRED, then apply normalize_map; returns (cleaned, n_changed)."""
    before = series.copy()

    cleaned = series.astype('string').str.strip().str.upper()

    cleaned = cleaned.str.translate(_CYRILLIC_TO_ASCII)

    # HTML entities
    cleaned = cleaned.str.replace('&RSQUO;', "'", regex=False)
    cleaned = cleaned.str.replace('&LSQUO;', "'", regex=False)
    cleaned = cleaned.str.replace('&AMP;', '&', regex=False)
    cleaned = cleaned.str.replace('&RDQUO;', '"', regex=False)
    cleaned = cleaned.str.replace('&LDQUO;', '"', regex=False)
    cleaned = cleaned.str.replace(r'&[A-Z]+;', '', regex=True)

    # curly braces
    cleaned = cleaned.str.replace(r'[{}]', '', regex=True)

    # trailing digits stuck to words (OWNER3 -> OWNER)
    cleaned = cleaned.str.replace(r'(\b[A-Z]{3,})\d+\b', r'\1', regex=True)

    # trailing punctuation
    cleaned = cleaned.str.replace(r'[\.\,\;\:\/\\]+\s*$', '', regex=True).str.strip()

    # double+ spaces
    cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True).str.strip()

    cleaned = cleaned.replace(_JUNK_TO_NAN)

    if collapse_retire:
        # The bare pattern drops IGNORECASE; cleaned is already uppercase.
        # so this match is (and always was) effectively case-insensitive
        cleaned.loc[cleaned.str.contains(RETIRE_RE.pattern, na=False, regex=True)] = 'RETIRED'

    cleaned = cleaned.replace(normalize_map)
    result = cleaned.astype(object)

    n_changed = int(
        (before.fillna('__NULL__').astype(str) != result.fillna('__NULL__').astype(str)).sum()
    )
    return result, n_changed


def _categorize(occupation_series: pd.Series) -> pd.Series:
    """Assign an occupation category: explicit overrides first, then first matching regex wins."""
    result = pd.Series('OTHER', index=occupation_series.index)
    result[occupation_series.isna()] = pd.NA

    override_mask = occupation_series.isin(CATEGORY_OVERRIDES)
    if override_mask.any():
        result[override_mask] = occupation_series[override_mask].map(CATEGORY_OVERRIDES)

    filled = occupation_series.fillna('').astype(str)
    for category, pattern in CATEGORY_PATTERNS:
        matches = filled.str.contains(pattern, na=False) & (result == 'OTHER')
        result[matches] = category

    return result


def _categorize_final(occupation_series: pd.Series) -> pd.Series:
    """Categorize final cleaned occupations using every configured rule."""
    occupation = occupation_series.fillna('').astype(str).str.strip().str.upper()
    result = _categorize(occupation)

    exact = occupation.map(_FINAL_OCCUPATION_CATEGORIES)
    result.loc[exact.notna()] = exact.loc[exact.notna()]

    for pattern, category in RECLASSIFY_CATEGORY_RULES:
        matches = result.eq('OTHER') & occupation.str.contains(
            pattern, case=False, na=False,
        )
        result.loc[matches] = category

    return result


def _classify_committee_names(names: pd.Series) -> pd.Series:
    """Classify committee names into sub-types by keyword match."""
    result = pd.Series('POLITICAL COMMITTEE', index=names.index)
    filled = names.fillna('').astype(str)

    for label, pattern in COMM_PATTERNS:
        # COMM_PATTERNS use IGNORECASE, but this call site has always
        # matched case-sensitively: pass the pattern string, not the object
        matches = filled.str.contains(pattern.pattern, na=False, regex=True) & (result == 'POLITICAL COMMITTEE')
        result[matches] = label

    return result
