"""Previous employer contract: company, SELF-EMPLOYED, or nothing."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.employer_status import is_job_title, is_real_employer, single_letters
from fec.cleaning.employer_synonyms.normalize import (
    normalize_employer_display_name,
    restyle_legal_suffix,
)
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS
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
from fec.io import cell_text


# slash values: COMPANY/TITLE or STATUS/COMPANY

_SLASH_STATUS_WORDS = frozenset({
    'RETIRED', 'REITRED', 'HOMEMAKER', 'UNEMPLOYED', 'NOT EMPLOYED',
    'NONE', 'N/A', 'NA', 'NAN', 'N A', 'VOLUNTEER', '',
})

_SLASH_SELF_WORDS = frozenset({
    'SELF', 'SELFEMPLOYED', 'SELF-EMPLOYED', 'SELF EMPLOYED',
})

# sector or title sides naming no employer
_SLASH_SECTOR_ONLY = frozenset({
    'INVESTMENTS', 'INSURANCE', 'BUILDER', 'DEVELOPER', 'INVESTOR',
    'REAL ESTATE AGENT', 'DEVELOPER R E', 'RETIRED LAWYER',
})

# leading admin marker: "LETTER SENT/ACME CORP"
_SLASH_ADMIN_PREFIX_RE = re.compile(
    r'^(LETTER SENT|REQUESTED)\b',
    re.IGNORECASE,
)

# compiled structural-junk pattern: dates, digits-only, masked SSNs
_JUNK_EMPLOYER_RE = re.compile(JUNK_EMPLOYER_RE)
_WS_RE = re.compile(r'\s+')

# whole word RETIRED only, never RETIREE/RETIREMENT
_RETIRED_WORD = r'(?:(?:SEMI|MOSTLY|PARTIALLY|PARTLY)[\s-]?)?RETIRED'
# leading marker: "RETIRED FROM", "(SEMI RETIRED)"
_RETIRED_PREFIX_RE = re.compile(
    rf'^\(?{_RETIRED_WORD}\b\)?(?:\s+FROM\b)?[\s,;:/-]*'
)
# trailing marker: "GOLDMAN SACHS-RETIRED"
_RETIRED_SUFFIX_RE = re.compile(rf'[\s,;:/-]*\(?\s*{_RETIRED_WORD}\s*\)?$')
# "ASSOCIATION OF RETIRED" is a truncated name
_NAME_BEFORE_RETIRED_RE = re.compile(r'\b(?:OF|FOR|THE|AND|&)$')


# strip a retirement marker, report if found
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


# a trailing "P/T" or "F/T" schedule
_SCHEDULE_SUFFIX_RE = re.compile(r'\s+[PF]/T\s*$', re.IGNORECASE)


# keep the company side of slash values
def _resolve_slash(s: str) -> str:
    if not s:
        return ''
    # a work schedule is no second company
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

    # a SELF side means no previous company
    if any(part in _SLASH_SELF_WORDS for part in upper_parts):
        return ''

    # status word, sector-only term, or too short
    def _junk(u: str) -> bool:
        return (
            u in _SLASH_STATUS_WORDS
            or u in _SLASH_SECTOR_ONLY
            or len(u) <= 2
        )

    useful = [
        part for part, upper in zip(parts, upper_parts)
        if not _junk(upper)
    ]
    if not useful:
        return ''
    return useful[0]


# reuses cleaning's junk sets; SELF-EMPLOYED is kept
_NULL_PREV = SECTOR_AS_EMPLOYER | MISSING_VALUES | (STATUS_WORDS - {'SELF-EMPLOYED'}) | {
    # junk seen only in previous_employer
    'COMMUNITY VOLUNTEER', 'VOLUNTEER', 'XXN', 'NOT',
    # a kind of work, not a workplace
    'BUSINESS',
    # a degree or pronoun names no company
    'MD', 'M.D', 'M.D.', 'ME', 'PHD', 'DDS', 'DO',
    # NOT EMPLOYED mistyped
    'NOT EMPLYED', 'NOT EMPOLYED', 'NOT EMPLOYEED', 'NOT EMPLOYD', 'NOT EMLOYED',
}
_SE_PREV = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER
# spaceless forms so "NOTEMPLOYED" still matches
_NULL_PREV_NOSPACE = {_WS_RE.sub('', value) for value in _NULL_PREV}
_CANONICAL_SYNONYM_NAMES = frozenset(
    str(value).strip().upper() for value in EMPLOYER_SYNONYMS.values()
)


# true if value names no previous employer
def _is_null_previous(upper: str) -> bool:
    upper_nospace = _WS_RE.sub('', upper)
    return (
        '@' in upper
        or upper in _NULL_PREV
        or upper.endswith(' VOLUNTEER')
        or upper_nospace in _NULL_PREV_NOSPACE
        or bool(_JUNK_EMPLOYER_RE.match(upper))
    )


# SELF EMPLOYED after a name or trade
SELF_EMPLOYED_TAIL_RE = re.compile(r'[-,/(:]\s*SELF[\s-]*EMPLOY\w*\W*$')
# words marking a firm, not a name
_FIRM_WORD_RE = re.compile(
    r'\b(?:GROUP|INC|LLC|LLP|LTD|CORP|CORPORATION|CO|COMPANY|ASSOCIATES|PARTNERS'
    r'|FIRM|PC|PA|PLLC|ENTERPRISES|CONSULTING|SERVICES|STUDIO|AGENCY)\b'
)


# firm before trailing SELF EMPLOYED, or ''
def _firm_before_self_marker(upper: str) -> str:
    match = SELF_EMPLOYED_TAIL_RE.search(upper)
    if not match:
        return ''
    firm = upper[:match.start()].strip(' -,/(:')
    return firm if _FIRM_WORD_RE.search(firm) else ''


# true if the value explicitly states self-employment
def _is_explicit_self(upper: str) -> bool:
    # letters only, typed once: "SELFF EMPLOYED" counts
    letters = single_letters(re.sub(r'[^A-Z]', '', upper))
    return (upper.startswith('SELF:') or letters.startswith('SELFEMPLOY')
            or upper in ('SELF', 'SSELF', 'SELFF') or bool(SELF_EMPLOYED_TAIL_RE.search(upper)))


# strict self-employed check for FEC, cache text
def explicitly_self_employed(value) -> bool:
    upper = cell_text(value).upper()
    compact = single_letters(re.sub(r"[^A-Z]", "", upper))
    return (
        upper == "SELF"
        or upper.startswith(("SELF:", "SELF (", "SELF /"))
        or compact.startswith("SELFEMPLOY")
        or bool(SELF_EMPLOYED_TAIL_RE.search(upper))
    )


# apply the contract to one value
def normalize_previous_employer_value(v) -> str:
    text = _WS_RE.sub(' ', str(v)).strip()
    upper = text.upper()
    # after a retirement marker, judge the rest
    remainder, had_marker = _strip_retired_marker(upper)
    if had_marker:
        if not remainder or remainder in _SE_PREV or remainder == 'MILITARY':
            return ''
        cleaned = normalize_previous_employer_value(remainder)
        # a marker never yields SELF-EMPLOYED
        return '' if cleaned == 'SELF-EMPLOYED' else cleaned
    # a self-run firm keeps its name
    firm = _firm_before_self_marker(upper)
    if firm:
        return normalize_previous_employer_value(firm) or 'SELF-EMPLOYED'
    if _is_explicit_self(upper):
        return 'SELF-EMPLOYED'
    if re.match(ADMIN_NOTE_EMPLOYER_RE, upper):
        return ''
    if _is_null_previous(upper):
        return ''
    # a bare role means self-employed in it
    if upper in _SE_PREV:
        return 'SELF-EMPLOYED'
    # other job titles name no company
    if is_job_title(upper):
        return ''
    # catches spellings _NULL_PREV misses
    if not is_real_employer(upper):
        return ''
    return _previous_company_name(text, upper)


# canonical display form of a prior company
def _previous_company_name(text: str, upper: str) -> str:
    # same synonyms as contributor_employer, one name
    mapped = EMPLOYER_SYNONYMS.get(upper)
    mapped_from_alias = bool(mapped)
    if mapped:
        text = mapped
        upper = text.upper()
        if _is_null_previous(upper):
            return ''
    if '/' in text:
        text = _resolve_slash(text)
        if not text:
            return ''
    if not mapped_from_alias and text.upper() in _CANONICAL_SYNONYM_NAMES:
        # canonical names are uppercase, whatever the input
        return text.upper()
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
    if upper.startswith('SELF EMPLOYED') or upper.startswith('SELF-EMPLOYED'):
        return 'SELF-EMPLOYED'
    if _is_null_previous(upper):
        return ''
    if upper in _SE_PREV:
        return 'SELF-EMPLOYED'
    return normalized if is_real_employer(normalized) else ''


# comparable name words, no punctuation or initials
def _name_words(value) -> frozenset[str]:
    return frozenset(
        word for word in re.sub(r'[^A-Z]', ' ', str(value).upper()).split()
        if len(word) > 1
    )


# keep own-named legal company, reject bare name
def preserve_own_named_legal_employer(
    cleaned: str, original, contributor_name,
) -> str:
    if not cleaned or _name_words(cleaned) != _name_words(contributor_name):
        return cleaned

    original = str(original).strip().upper()
    if LEGAL_SUFFIX_RE.search(original):
        return restyle_legal_suffix(original).rstrip('.')
    return ''


# apply the contract to every row
def normalize_previous_employer_column(df: pd.DataFrame) -> int:
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
        # drop bare donor names, keep own-named LLCs
        df.loc[non_empty, 'previous_employer'] = [
            preserve_own_named_legal_employer(cleaned, original, donor)
            for cleaned, original, donor in zip(
                df.loc[non_empty, 'previous_employer'],
                before,
                df.loc[non_empty, 'contributor_name'],
            )
        ]

    return int((df.loc[non_empty, 'previous_employer'] != before).sum())
