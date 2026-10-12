"""Employer and occupation text normalization, categorization."""
import numpy as np
import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES, SKIP_EMPLOYERS
from fec.config.data import COMM_PATTERNS, MISSING_VALUES, RETIRE_RE
from fec.config.occupation_rules.categories import (
    CATEGORY_PATTERNS,
    RECLASSIFY_CATEGORY_RULES,
)
from fec.config.occupation_rules.category_overrides import CATEGORY_OVERRIDES
from fec.config.occupation_rules.fixes import OCCUPATION_FIXES
from fec.config.occupation_rules.normalize import OCCUPATION_CANONICAL

# junk values to NaN in one replace
_JUNK_TO_NAN = {val: np.nan for val in MISSING_VALUES}

# fix categories the general regex cannot infer
_FINAL_OCCUPATION_CATEGORIES = {
    final_occupation: category
    for final_occupation, category in OCCUPATION_FIXES.values()
    if category
}

# Cyrillic lookalikes to ASCII, common in FEC
_CYRILLIC_TO_ASCII = str.maketrans({
    '\u0410': 'A', '\u0412': 'B', '\u0421': 'C', '\u0415': 'E',
    '\u041d': 'H', '\u041a': 'K', '\u041c': 'M', '\u041e': 'O',
    '\u0420': 'P', '\u0422': 'T', '\u0423': 'Y', '\u0425': 'X',
})


_WORD_DIGITS_RE = r'(\b[A-Z]{3,})\d+\b'
# only status text loses trailing keying digits
EMPLOYER_STATUS_TEXT = frozenset(EMPLOYER_STATUS_VALUES) | frozenset(SKIP_EMPLOYERS)


# clean a text column, optionally collapsing retired
def normalize_text(series: pd.Series, normalize_map: dict, collapse_retire: bool = False,
                    digits_only_before: frozenset | set | None = None) -> tuple[pd.Series, int]:
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

    cleaned = cleaned.str.replace(r'[{}]', '', regex=True)

    # employer digits stripped only before status text
    stripped = cleaned.str.replace(_WORD_DIGITS_RE, r'\1', regex=True)
    if digits_only_before is None:
        cleaned = stripped
    else:
        to_status = stripped.str.strip().isin(digits_only_before).fillna(False)
        cleaned = cleaned.where(~to_status, stripped)

    cleaned = cleaned.str.replace(r'[\.\,\;\:\/\\]+\s*$', '', regex=True).str.strip()

    cleaned = cleaned.str.replace(r'\s{2,}', ' ', regex=True).str.strip()

    cleaned = cleaned.replace(_JUNK_TO_NAN)

    if collapse_retire:
        # cleaned is uppercase, so case flags unneeded
        cleaned.loc[cleaned.str.contains(RETIRE_RE.pattern, na=False, regex=True)] = 'RETIRED'

    cleaned = cleaned.replace(normalize_map)
    result = cleaned.astype(object)

    n_changed = int(
        (before.fillna('__NULL__').astype(str) != result.fillna('__NULL__').astype(str)).sum()
    )
    return result, n_changed


# category by override, else first matching regex
def categorize_occupation(occupation_series: pd.Series) -> pd.Series:
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


# categorize final occupations with every configured rule
def categorize_final(occupation_series: pd.Series) -> pd.Series:
    occupation = occupation_series.fillna('').astype(str).str.strip().str.upper()
    result = categorize_occupation(occupation)

    exact = occupation.map(_FINAL_OCCUPATION_CATEGORIES)
    result.loc[exact.notna()] = exact.loc[exact.notna()]

    for pattern, category in RECLASSIFY_CATEGORY_RULES:
        matches = result.eq('OTHER') & occupation.str.contains(
            pattern, case=False, na=False,
        )
        result.loc[matches] = category

    return result


# classify committee names by keyword
def classify_committee_names(names: pd.Series) -> pd.Series:
    result = pd.Series('POLITICAL COMMITTEE', index=names.index)
    filled = names.fillna('').astype(str)

    for label, pattern in COMM_PATTERNS:
        # deliberately case-sensitive: pass the pattern string
        matches = filled.str.contains(pattern.pattern, na=False, regex=True) & (result == 'POLITICAL COMMITTEE')
        result[matches] = label

    return result


# apply occupation fixes; return changed originals
def map_occupation_fixes(df: pd.DataFrame, rows) -> pd.Series:
    original = df.loc[rows, 'contributor_occupation']
    original = original[original.isin(OCCUPATION_FIXES)]
    if not original.empty:
        df.loc[original.index, 'contributor_occupation'] = original.map(
            {key: value[0] for key, value in OCCUPATION_FIXES.items()}
        )
        df.loc[original.index, 'occupation_category'] = original.map(
            {key: value[1] for key, value in OCCUPATION_FIXES.items()}
        )
    return original


# swap employer and occupation, then recategorize
def swap_employer_and_occupation(df: pd.DataFrame, rows, *, canonical: bool = True, status=None) -> None:
    old_employer = df.loc[rows, 'contributor_employer'].copy()
    old_occupation = df.loc[rows, 'contributor_occupation'].copy()
    new_occupation = old_employer.replace(OCCUPATION_CANONICAL) if canonical else old_employer
    df.loc[rows, 'contributor_employer'] = old_occupation
    df.loc[rows, 'contributor_occupation'] = new_occupation
    df.loc[rows, 'occupation_category'] = categorize_occupation(new_occupation)
    if status is not None:
        df.loc[rows, 'occupation_status'] = status
