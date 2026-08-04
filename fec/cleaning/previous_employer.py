"""The previous_employer contract, shared by every writer of the column: a real company name, the literal 'SELF-EMPLOYED' (kept, it is real information), or nothing."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.constants import (
    NOT_REAL_EMPLOYER, SECTOR_AS_EMPLOYER, ROLE_AS_EMPLOYER,
    OCCUPATION_AS_EMPLOYER, JUNK_EMPLOYER_RE, STATUS_WORDS,
    SLASH_BRAND_EMPLOYERS, LEGAL_SUFFIX_RE,
)
from fec.config.data import MISSING_VALUES
from fec.cleaning.employer_synonyms import (
    normalize_employer_display_name, restyle_legal_suffix, EMPLOYER_SYNONYMS,
)

__all__ = [
    "is_real_employer",
    "normalize_previous_employer_value",
    "normalize_previous_employer_column",
    "preserve_own_named_legal_employer",
]


def is_real_employer(emp) -> bool:
    """True if emp names a real company (not RETIRED, SELF-EMPLOYED, junk)."""
    if pd.isna(emp):
        return False
    text = str(emp).strip()
    if text.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    return text.upper() not in NOT_REAL_EMPLOYER


# Some legacy FEC filings encode the previous employer as "COMPANY/TITLE" or
# "STATUS/COMPANY" in a single field.

_SLASH_STATUS_WORDS = frozenset({
    'RETIRED', 'REITRED', 'HOMEMAKER', 'UNEMPLOYED', 'NOT EMPLOYED',
    'NONE', 'N/A', 'NA', 'NAN', 'N A', 'VOLUNTEER', '',
})

_SLASH_SELF_WORDS = frozenset({
    'SELF', 'SELFEMPLOYED', 'SELF-EMPLOYED', 'SELF EMPLOYED',
})

# sector / job-title strings that, alone on a side, mean no real previous employer
_SLASH_SECTOR_ONLY = frozenset({
    'INVESTMENTS', 'INSURANCE', 'BUILDER', 'DEVELOPER', 'INVESTOR',
    'REAL ESTATE AGENT', 'DEVELOPER R E', 'RETIRED LAWYER',
})

# real brand names that actually contain a slash: keep verbatim
_SLASH_KEEP = SLASH_BRAND_EMPLOYERS

_SLASH_ADMIN_PREFIX_RE = re.compile(r'^(LETTER SENT|REQUESTED)\b', re.I)

_JUNK_EMPLOYER_RE = re.compile(JUNK_EMPLOYER_RE)
_WS_RE = re.compile(r'\s+')


def _resolve_slash(s: str) -> str:
    """Collapse slash-format values: keep real brands, drop admin/self forms, prefer the company side; result still passes normalize_employer_display_name."""
    if not s:
        return ''
    value_upper = s.strip().upper()
    if value_upper in _SLASH_KEEP:
        return s
    if _SLASH_ADMIN_PREFIX_RE.match(value_upper):
        return ''

    parts = [part.strip() for part in s.split('/', 1)]
    if len(parts) != 2:
        return s
    left, right = parts
    left_upper = left.upper()
    right_upper = right.upper()

    # SELF/* -> '' (self-employed, no previous company)
    if left_upper in _SLASH_SELF_WORDS or right_upper in _SLASH_SELF_WORDS:
        return ''

    def _junk(u: str) -> bool:
        return (
            u in _SLASH_STATUS_WORDS
            or u in _SLASH_SECTOR_ONLY
            or len(u) <= 2
        )

    left_junk = _junk(left_upper)
    right_junk = _junk(right_upper)

    if left_junk and right_junk:
        return ''
    if left_junk and not right_junk:
        return right
    # default COMPANY/TITLE: take left
    return left


# MISSING_VALUES is the SAME FEC-junk / admin-note set clean.py nulls out of
# contributor_employer, and STATUS_WORDS the same life-status set: single
# sources of truth, not second lists. SELF-EMPLOYED is excluded because the
# contract KEEPS it (real information, handled first in the normalizer).
_NULL_PREV = SECTOR_AS_EMPLOYER | MISSING_VALUES | (STATUS_WORDS - {'SELF-EMPLOYED'}) | {
    # keyboard junk / bare-status stumps seen only in previous_employer
    'COMMUNITY VOLUNTEER', 'VOLUNTEER', 'XXN', 'NOT',
}
_SE_PREV = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER
# whitespace-stripped forms so spacing-mangled statuses ("NOTEMPLOYED",
# "HOME MAKER") from the FEC API are still recognised and cleared
_NULL_PREV_NOSPACE = {_WS_RE.sub('', value) for value in _NULL_PREV}


def normalize_previous_employer_value(v) -> str:
    """Contract for ONE value ('' clears it); self-employment is recognised FIRST because the display-name normalizer would clear 'SELF-EMPLOYED', a fact we keep."""
    text = str(v)
    upper = text.strip().upper()
    # explicit self-employment markers
    if (upper.startswith('SELF:') or upper.startswith('SELF EMPLOYED')
            or upper.startswith('SELF-EMPLOYED') or upper == 'SELF'):
        return 'SELF-EMPLOYED'
    # not a real prior company: email, industry word, status/volunteer, junk
    # (upper_nospace collapses whitespace so spacing variants match their canonical form)
    upper_nospace = _WS_RE.sub('', upper)
    if ('@' in upper or upper in _NULL_PREV or upper_nospace in _NULL_PREV_NOSPACE
            or _JUNK_EMPLOYER_RE.match(upper)):
        return ''
    # a bare job title / role -> worked for themselves in that profession
    if upper in _SE_PREV:
        return 'SELF-EMPLOYED'
    # any other non-company value (status typo, sector, refusal) is cleared;
    # is_real_employer wraps NOT_REAL_EMPLOYER, catching spellings _NULL_PREV misses
    if not is_real_employer(upper):
        return ''
    # same synonym map as contributor_employer so a company collapses to ONE name
    mapped = EMPLOYER_SYNONYMS.get(upper)
    if mapped:
        text = mapped
    if '/' in text:
        text = _resolve_slash(text)
        if not text:
            return ''
    return normalize_employer_display_name(text) or ''


def _name_words(value) -> frozenset[str]:
    """Comparable name words, ignoring punctuation and middle initials."""
    return frozenset(
        word for word in re.sub(r'[^A-Z]', ' ', str(value).upper()).split()
        if len(word) > 1
    )


def preserve_own_named_legal_employer(
    cleaned: str, original, contributor_name,
) -> str:
    """Keep an own-named legal company, but reject a bare donor name."""
    if not cleaned or _name_words(cleaned) != _name_words(contributor_name):
        return cleaned

    original = str(original).strip().upper()
    if LEGAL_SUFFIX_RE.search(original):
        return restyle_legal_suffix(original).rstrip('.')
    return ''


def normalize_previous_employer_column(df: pd.DataFrame) -> int:
    """Apply the contract to a whole frame (idempotent, safe in both cleaning and resolve stages); also drops a previous_employer that is the donor's own name; returns rows changed."""
    if 'previous_employer' not in df.columns:
        return 0
    prev_emp = df['previous_employer']
    non_empty = prev_emp.notna() & (prev_emp.astype(str).str.strip() != '')
    if not non_empty.any():
        return 0

    before = prev_emp[non_empty].astype(str)
    normed = before.map(normalize_previous_employer_value)
    df.loc[non_empty, 'previous_employer'] = normed

    if 'contributor_name' in df.columns:
        # A bare donor name is not a company. A legal entity named after the
        # donor is real information, so retain its LLC/PLLC/INC suffix.
        df.loc[non_empty, 'previous_employer'] = [
            preserve_own_named_legal_employer(cleaned, original, donor)
            for cleaned, original, donor in zip(
                df.loc[non_empty, 'previous_employer'],
                before,
                df.loc[non_empty, 'contributor_name'],
            )
        ]

    return int((df.loc[non_empty, 'previous_employer'] != before).sum())
