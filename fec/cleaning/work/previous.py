"""Normalize previous employers to a company name, SELF-EMPLOYED, or empty text."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.work.status import is_job_title, is_real_employer, single_letters
from fec.cleaning.work.employer_normalize import (
    normalize_employer_display_name,
    restyle_legal_suffix,
)
from fec.cleaning.work.employer_aliases import (
    EMPLOYER_SYNONYMS, documented_employer_name, preserved_employer_name,
)
from fec.config.constants import (
    ADMIN_NOTE_EMPLOYER_RE,
    JUNK_EMPLOYER_RE,
    SLASH_BRAND_EMPLOYERS,
    STATUS_WORDS,
)
from fec.config.data import MISSING_VALUES
from fec.config.not_employers import (
    LEGAL_SUFFIX_RE,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
)

__all__ = [
    "is_real_employer",
    "normalize_previous_employer_value",
    "normalize_previous_employer_column",
    "preserve_own_named_legal_employer",
]


# Slash values can contain a company, title, or work status.
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

# a leading admin-request marker on a slash value: "LETTER SENT/ACME CORP"
_SLASH_ADMIN_PREFIX_RE = re.compile(
    r'^(LETTER SENT|REQUESTED)\b',
    re.IGNORECASE,
)

_JUNK_EMPLOYER_RE = re.compile(JUNK_EMPLOYER_RE)
_WS_RE = re.compile(r'\s+')

# Match whole-word RETIRED around a company; preserve RETIREE and RETIREMENT names.
_RETIRED_WORD = r'(?:(?:SEMI|MOSTLY|PARTIALLY|PARTLY)[\s-]?)?RETIRED'
_RETIRED_PREFIX_RE = re.compile(
    rf'^\(?{_RETIRED_WORD}\b\)?(?:\s+FROM\b)?[\s,;:/-]*'
)
_RETIRED_SUFFIX_RE = re.compile(rf'[\s,;:/-]*\(?\s*{_RETIRED_WORD}\s*\)?$')
# Keep RETIRED when it belongs to a truncated organization name.
_NAME_BEFORE_RETIRED_RE = re.compile(r'\b(?:OF|FOR|THE|AND|&)$')


# Remove a retirement marker without cutting an organization name.
def _strip_retired_marker(upper: str) -> tuple[str, bool]:
    stripped = _RETIRED_PREFIX_RE.sub('', upper, count=1)
    if stripped != upper:
        return stripped.strip(), True
    match = _RETIRED_SUFFIX_RE.search(upper)
    if match and match.start() > 0:
        head = upper[:match.start()].strip()
        if head and not _NAME_BEFORE_RETIRED_RE.search(head):
            return head, True
    return upper, False


# part time / full time written after the company: "UMIAMI P/T"
_SCHEDULE_SUFFIX_RE = re.compile(r'\s+[PF]/T\s*$', re.IGNORECASE)


# Keep slash brands; otherwise choose the first usable company part.
def _resolve_slash(s: str) -> str:
    if not s:
        return ''
    # a trailing work schedule is no second company: "UMIAMI P/T" is UMIAMI
    s = _SCHEDULE_SUFFIX_RE.sub('', s)
    value_upper = s.strip().upper()
    if value_upper in SLASH_BRAND_EMPLOYERS:
        return s
    if _SLASH_ADMIN_PREFIX_RE.match(value_upper):
        return ''

    parts = [part.strip() for part in s.split('/')]
    if len(parts) < 2:
        return s
    upper_parts = [part.upper() for part in parts]

    # SELF/* -> '' (self-employed, no previous company)
    if any(part in _SLASH_SELF_WORDS for part in upper_parts):
        return ''

    for part, upper in zip(parts, upper_parts):
        if upper in _SLASH_STATUS_WORDS or upper in _SLASH_SECTOR_ONLY or len(upper) <= 2:
            continue
        return part
    return ''


# Share current-employer junk and status rules, but keep explicit SELF-EMPLOYED.
_NULL_PREV = SECTOR_AS_EMPLOYER | MISSING_VALUES | (STATUS_WORDS - {'SELF-EMPLOYED'}) | {
    # keyboard junk / bare-status stumps seen only in previous_employer
    'COMMUNITY VOLUNTEER', 'VOLUNTEER', 'XXN', 'NOT',
    # Prior work only: adding BUSINESS to the shared sector set would trigger current-work inference.
    'BUSINESS',
    # Degrees and pronouns name no company.
    'MD', 'M.D', 'M.D.', 'ME', 'PHD', 'DDS', 'DO',
    # Misspellings of NOT EMPLOYED.
    'NOT EMPLYED', 'NOT EMPOLYED', 'NOT EMPLOYEED', 'NOT EMPLOYD', 'NOT EMLOYED',
}
_SE_PREV = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER
# Recognize status words with missing or extra spaces.
_NULL_PREV_NOSPACE = {_WS_RE.sub('', value) for value in _NULL_PREV}
_CANONICAL_SYNONYM_NAMES = frozenset(
    str(value).strip().upper() for value in EMPLOYER_SYNONYMS.values()
)


# Identify values that name no previous employer.
def _is_null_previous(upper: str) -> bool:
    upper_nospace = _WS_RE.sub('', upper)
    return (
        '@' in upper
        or upper in _NULL_PREV
        or upper.endswith(' VOLUNTEER')
        or upper_nospace in _NULL_PREV_NOSPACE
        or bool(_JUNK_EMPLOYER_RE.match(upper))
    )


# SELF-EMPLOYED written after a name or trade.
SELF_EMPLOYED_TAIL_RE = re.compile(r'[-,/(:]\s*SELF[\s-]*EMPLOY\w*\W*$')
# a word that makes the text before the marker a firm, not the filer's name
_FIRM_WORD_RE = re.compile(
    r'\b(?:GROUP|INC|LLC|LLP|LTD|CORP|CORPORATION|CO|COMPANY|ASSOCIATES|PARTNERS'
    r'|FIRM|PC|PA|PLLC|ENTERPRISES|CONSULTING|SERVICES|STUDIO|AGENCY)\b'
)


# Keep a named firm before a trailing SELF-EMPLOYED marker.
def _firm_before_self_marker(upper: str) -> str:
    match = SELF_EMPLOYED_TAIL_RE.search(upper)
    if not match:
        return ''
    firm = upper[:match.start()].strip(' -,/(:')
    return firm if _FIRM_WORD_RE.search(firm) else ''


# Recognize explicit self-employment, including doubled letters.
def _is_explicit_self(upper: str) -> bool:
    letters = single_letters(re.sub(r'[^A-Z]', '', upper))
    return (upper.startswith('SELF:') or letters.startswith('SELFEMPLOY')
            or upper in ('SELF', 'SSELF', 'SELFF') or bool(SELF_EMPLOYED_TAIL_RE.search(upper)))


# Return a company, SELF-EMPLOYED, or empty text.
def normalize_previous_employer_value(v) -> str:
    text = _WS_RE.sub(' ', str(v)).strip()
    reviewed = documented_employer_name(text)
    if reviewed:
        text = reviewed
        if is_real_employer(reviewed):
            return reviewed
    upper = text.upper()

    # Strip retirement markers, then judge the remaining company or profession.
    remainder, had_marker = _strip_retired_marker(upper)
    if had_marker:
        if not remainder or remainder in _SE_PREV or remainder == 'MILITARY':
            return ''
        cleaned = normalize_previous_employer_value(remainder)
        # A former profession does not establish self-employment.
        return '' if cleaned == 'SELF-EMPLOYED' else cleaned

    # Keep a named firm before recognizing SELF-EMPLOYED as a standalone status.
    firm = _firm_before_self_marker(upper)
    if firm:
        return normalize_previous_employer_value(firm) or 'SELF-EMPLOYED'
    if _is_explicit_self(upper):
        return 'SELF-EMPLOYED'

    if re.match(ADMIN_NOTE_EMPLOYER_RE, upper):
        return ''
    if _is_null_previous(upper):
        return ''

    # Listed professions imply self-employment; other job titles name no company.
    if upper in _SE_PREV:
        return 'SELF-EMPLOYED'
    if is_job_title(upper):
        return ''
    if not is_real_employer(upper):
        return ''
    return _previous_company_name(text, upper)


# Apply company aliases and display rules to a previous employer.
def _previous_company_name(text: str, upper: str) -> str:
    if '/' in text:
        if preserved_employer_name(text) and text.upper() not in SLASH_BRAND_EMPLOYERS:
            parts = [part.strip() for part in text.split('/')
                     if is_real_employer(part.strip()) and not is_job_title(part.strip())]
            text = parts[0] if len(parts) == 1 else ''
        else:
            text = _resolve_slash(text)
        if not text:
            return ''
    if preserved := preserved_employer_name(text):
        return preserved if is_real_employer(preserved) else ''

    # Normalization can expose another alias; stop once the name settles.
    normalized = text
    for _ in range(3):
        next_value = normalize_employer_display_name(normalized) or ''
        if next_value == normalized:
            break
        normalized = next_value
        if not normalized:
            return ''
        if normalized.upper() in _CANONICAL_SYNONYM_NAMES:
            break

    upper = normalized.upper()
    if upper.startswith(('SELF EMPLOYED', 'SELF-EMPLOYED')):
        return 'SELF-EMPLOYED'
    if _is_null_previous(upper):
        return ''
    if upper in _SE_PREV:
        return 'SELF-EMPLOYED'
    return normalized if is_real_employer(normalized) else ''


# Compare name words without punctuation or middle initials.
def _name_words(value) -> frozenset[str]:
    return frozenset(
        word for word in re.sub(r'[^A-Z]', ' ', str(value).upper()).split()
        if len(word) > 1
    )


# Keep an own-named company with a legal suffix; reject a bare donor name.
def preserve_own_named_legal_employer(
    cleaned: str, original, contributor_name,
) -> str:
    if not cleaned or _name_words(cleaned) != _name_words(contributor_name):
        return cleaned

    original = str(original).strip().upper()
    if LEGAL_SUFFIX_RE.search(original):
        return restyle_legal_suffix(original).rstrip('.')
    return ''


# Normalize previous employers and count changed rows.
def normalize_previous_employer_column(df: pd.DataFrame) -> int:
    if 'previous_employer' not in df.columns:
        return 0
    previous = df['previous_employer']
    has_previous = previous.notna() & (previous.astype(str).str.strip() != '')
    if not has_previous.any():
        return 0

    before = previous[has_previous].astype(str)
    normalized = before.map(normalize_previous_employer_value)
    df.loc[has_previous, 'previous_employer'] = normalized

    if 'contributor_name' in df.columns:
        # A legal suffix distinguishes an own-named firm from a bare person name.
        current = df.loc[has_previous, 'previous_employer']
        donor_names = df.loc[has_previous, 'contributor_name']
        values = zip(current, before, donor_names)
        df.loc[has_previous, 'previous_employer'] = [
            preserve_own_named_legal_employer(cleaned, original, donor)
            for cleaned, original, donor in values
        ]

    return int((df.loc[has_previous, 'previous_employer'] != before).sum())
