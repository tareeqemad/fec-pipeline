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
    "classify_employer_status",
    "classify_employer_statuses",
    "current_employer_name",
    "referenced_employers",
    "normalize_previous_employer_value",
    "normalize_previous_employer_column",
    "preserve_own_named_legal_employer",
]

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})
_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


def _status_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip().upper()


def is_real_employer(emp) -> bool:
    """True if emp names a real company (not RETIRED, SELF-EMPLOYED, junk)."""
    if pd.isna(emp):
        return False
    text = str(emp).strip()
    if text.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    return text.upper() not in NOT_REAL_EMPLOYER


def classify_employer_status(employer, occupation="", category="") -> str:
    """Classify work status without changing the reported company."""
    emp = _status_text(employer)
    occ = _status_text(occupation)
    cat = _status_text(category)

    if emp == "RETIRED":
        return "retired"
    no_work = (
        emp in _NO_WORK_OCCUPATIONS
        or cat in _NO_WORK_CATEGORIES
        or occ in _NO_WORK_OCCUPATIONS
    )
    if no_work:
        return "not_employed"
    if emp in {"SELF-EMPLOYED", "SELF EMPLOYED"} or cat == "SELF-EMPLOYED":
        return "self_employed"
    return "active" if is_real_employer(employer) else "missing"


def classify_employer_statuses(df: pd.DataFrame) -> pd.Series:
    """Classify every row in a frame."""
    blank = pd.Series("", index=df.index)
    employers = df.get("contributor_employer", blank)
    occupations = df.get("contributor_occupation", blank)
    categories = df.get("occupation_category", blank)
    return pd.Series([
        classify_employer_status(employer, occupation, category)
        for employer, occupation, category in zip(
            employers, occupations, categories,
        )
    ], index=df.index)


def current_employer_name(status, employer) -> str:
    """Return a real current company only when the status permits one."""
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


def referenced_employers(df: pd.DataFrame) -> set[str]:
    """Companies the final database must contain."""
    rows = df
    if "entity_type" in rows.columns:
        rows = rows[rows["entity_type"].eq("INDIVIDUAL")]

    names: set[str] = set()
    for status, employer in rows[["employer_status", "contributor_employer"]].itertuples(index=False):
        current = current_employer_name(status, employer)
        if current:
            names.add(current)

    retired = rows[rows["employer_status"].eq("retired")]["previous_employer"]
    names.update(str(name).strip() for name in retired if is_real_employer(name))
    return names


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

_SLASH_ADMIN_PREFIX_RE = re.compile(
    r'^(LETTER SENT|REQUESTED)\b',
    re.IGNORECASE,
)

_JUNK_EMPLOYER_RE = re.compile(JUNK_EMPLOYER_RE)
_WS_RE = re.compile(r'\s+')

# A retirement marker written around the company ("GOLDMAN SACHS-RETIRED",
# "UCLA RETIRED", "REICH AND TRUAX (SEMI RETIRED)", "SEMI RETIRED X",
# "RETIRED FROM X"). Whole word RETIRED only, so RETIREE / RETIREMENT
# (ERICKSON RETIREMENT COMMUNITIES, RETIREE CHAPTER) are never touched.
_RETIRED_WORD = r'(?:(?:SEMI|MOSTLY|PARTIALLY|PARTLY)[\s-]?)?RETIRED'
_RETIRED_PREFIX_RE = re.compile(
    rf'^\(?{_RETIRED_WORD}\b\)?(?:\s+FROM\b)?[\s,;:/-]*'
)
_RETIRED_SUFFIX_RE = re.compile(rf'[\s,;:/-]*\(?\s*{_RETIRED_WORD}\s*\)?$')
# "... ASSOCIATION OF RETIRED" (a name cut at the FEC's 38 characters) is a
# name, not a marker
_NAME_BEFORE_RETIRED_RE = re.compile(r'\b(?:OF|FOR|THE|AND|&)$')


def _strip_retired_marker(upper: str) -> tuple[str, bool]:
    """Return (value without a leading/trailing retirement marker, marker found)."""
    stripped = _RETIRED_PREFIX_RE.sub('', upper, count=1)
    if stripped != upper:
        return stripped.strip(), True
    match = _RETIRED_SUFFIX_RE.search(upper)
    if match and match.start() > 0:
        head = upper[:match.start()].strip()
        if head and not _NAME_BEFORE_RETIRED_RE.search(head):
            return head, True
    return upper, False


def _resolve_slash(s: str) -> str:
    """Collapse slash-format values: keep real brands, drop admin/self forms, prefer the company side; result still passes normalize_employer_display_name."""
    if not s:
        return ''
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
_CANONICAL_SYNONYM_NAMES = frozenset(
    str(value).strip().upper() for value in EMPLOYER_SYNONYMS.values()
)


def _is_null_previous(upper: str) -> bool:
    upper_nospace = _WS_RE.sub('', upper)
    return (
        '@' in upper
        or upper in _NULL_PREV
        or upper.endswith(' VOLUNTEER')
        or upper_nospace in _NULL_PREV_NOSPACE
        or bool(_JUNK_EMPLOYER_RE.match(upper))
    )


def _is_explicit_self(upper: str) -> bool:
    return (upper.startswith('SELF:') or upper.startswith('SELF EMPLOYED')
            or upper.startswith('SELF-EMPLOYED') or upper == 'SELF')


def normalize_previous_employer_value(v) -> str:
    """Contract for ONE value ('' clears it); self-employment is recognised FIRST because the display-name normalizer would clear 'SELF-EMPLOYED', a fact we keep."""
    text = _WS_RE.sub(' ', str(v)).strip()
    upper = text.upper()
    # "GOLDMAN SACHS-RETIRED" / "SEMI RETIRED X": the marker is a status, the
    # rest is judged on its own by this same contract. A remainder that is a
    # listed profession (ROLE/OCCUPATION_AS_EMPLOYER, or NOT_REAL_EMPLOYER's
    # OCCUPATION_TITLE_EMPLOYERS: "SEMI RETIRED PSYCHOLOGIST", "RETIRED
    # JUDGE") is the donor's old occupation and clears - the same reading
    # normalize_employer_display_name gives "RETIRED PHYSICIAN". An unlisted
    # remainder is kept as filed; no word-level guess is made here.
    remainder, had_marker = _strip_retired_marker(upper)
    if had_marker:
        if not remainder or remainder in _SE_PREV or remainder == 'MILITARY':
            return ''
        cleaned = normalize_previous_employer_value(remainder)
        # "RETIRED LAWYER/EXECUTIVE", "SELF RETIRED": a marker never turns a
        # title into SELF-EMPLOYED (FEC-cache ingestion only keeps
        # SELF-EMPLOYED when the filer wrote it outright)
        return '' if cleaned == 'SELF-EMPLOYED' else cleaned
    # explicit self-employment markers
    if _is_explicit_self(upper):
        return 'SELF-EMPLOYED'
    # not a real prior company: email, industry word, status/volunteer, junk
    # (upper_nospace collapses whitespace so spacing variants match their canonical form)
    if _is_null_previous(upper):
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
        # canonical names are uppercase; a manual override typed
        # "WhatsApp LLC" must not keep its own casing
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
