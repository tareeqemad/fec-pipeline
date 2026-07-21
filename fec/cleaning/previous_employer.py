"""cleaning/previous_employer.py — the previous_employer column contract.

`previous_employer` obeys the SAME contract as `contributor_employer`: it holds
a real company name, the literal 'SELF-EMPLOYED', or nothing. Anything else — an
industry word, a bare job title, a status word, an email, FEC admin junk — is
cleared.

**Why this module exists.** Three post-merge steps write the column from three
different sources (AN from a donor's other filings, AP propagated within a donor,
AQ copied off contributor_employer) and the resolve stage writes it from its
cache. Each writer used to decide for itself what counted as a company, so the
column's contents depended on which stage happened to run last, and a status word
copied by one writer survived because another writer's guard didn't cover it.

The contract lives here — in cleaning, which both `fec/database/post_merge_fixes`
and `fec/resolve/pipeline/apply` may import — so there is exactly one answer.

Note that 'SELF-EMPLOYED' is KEPT, not cleared. It is real information: for a
retiree it records that they worked for themselves rather than at a company,
which is a different fact from an empty column ("we don't know"). This is why
`normalize_employer_display_name` alone is NOT the contract — it rejects
'SELF-EMPLOYED' as a display name, so it must be reached only AFTER the
self-employment check below.
"""
from __future__ import annotations

import re

import pandas as pd

from fec.config.constants import (
    NOT_REAL_EMPLOYER, SECTOR_AS_EMPLOYER, ROLE_AS_EMPLOYER,
    OCCUPATION_AS_EMPLOYER, JUNK_EMPLOYER_RE,
)
from fec.config.data import MISSING_VALUES
from fec.cleaning.employer_synonyms import (
    normalize_employer_display_name, EMPLOYER_SYNONYMS,
)

__all__ = [
    "is_real_employer",
    "normalize_previous_employer_value",
    "normalize_previous_employer_column",
]


def is_real_employer(emp) -> bool:
    """True if `emp` names a real company (not RETIRED, SELF-EMPLOYED, junk…)."""
    if emp is None or emp == '' or pd.isna(emp):
        return False
    s = str(emp).strip()
    if s.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    return s.upper() not in NOT_REAL_EMPLOYER


# ── Slash-format previous_employer handling ──────────────────────────
# Some legacy FEC filings encode the previous employer as "COMPANY/TITLE" or
# "STATUS/COMPANY" in a single field. Left alone, 599 rows (87 distinct values)
# leak these composite strings into the cleaned output — e.g.
# 'WILLIAM MORRIS AGENCY INC./EXEC. VI'.

_SLASH_STATUS_WORDS = frozenset({
    'RETIRED', 'REITRED', 'HOMEMAKER', 'UNEMPLOYED', 'NOT EMPLOYED',
    'NONE', 'N/A', 'NA', 'NAN', 'N A', 'VOLUNTEER', '',
})

_SLASH_SELF_WORDS = frozenset({
    'SELF', 'SELFEMPLOYED', 'SELF-EMPLOYED', 'SELF EMPLOYED',
})

# Sector / job-title strings that, when they appear ALONE on a side,
# mean the filer didn't name a real previous employer.
_SLASH_SECTOR_ONLY = frozenset({
    'INVESTMENTS', 'INSURANCE', 'BUILDER', 'DEVELOPER', 'INVESTOR',
    'REAL ESTATE AGENT', 'DEVELOPER R E', 'RETIRED LAWYER',
})

# Real brand names that actually contain a slash — keep verbatim.
_SLASH_KEEP = frozenset({
    'BRIDGESTONE/FIRESTONE',
})

_SLASH_ADMIN_PREFIX_RE = re.compile(r'^(LETTER SENT|REQUESTED)\b', re.I)


def _resolve_slash(s: str) -> str:
    """Collapse slash-format previous_employer strings.

    Strategy per value:
      - Real brand with slash (BRIDGESTONE/FIRESTONE)  -> verbatim
      - Admin prefix (LETTER SENT:, REQUESTED ...)     -> ''
      - SELF / SELFEMPLOYED on the left                -> '' (no
        prior employer — the donor worked for themselves)
      - Left side is a status word (RETIRED/X)         -> take right
        if right is a real company, else ''
      - Default (COMPANY/TITLE)                         -> take left
        unless left itself is sector-only or <=2 chars

    The returned string still needs to pass through
    normalize_employer_display_name afterwards.
    """
    if not s:
        return ''
    v_up = s.strip().upper()
    if v_up in _SLASH_KEEP:
        return s
    if _SLASH_ADMIN_PREFIX_RE.match(v_up):
        return ''

    parts = [p.strip() for p in s.split('/', 1)]
    if len(parts) != 2:
        return s
    left, right = parts
    left_up = left.upper()
    right_up = right.upper()

    # SELF/* -> '' (self-employed, no previous company)
    if left_up in _SLASH_SELF_WORDS or right_up in _SLASH_SELF_WORDS:
        return ''

    def _junk(u: str) -> bool:
        return (
            u in _SLASH_STATUS_WORDS
            or u in _SLASH_SECTOR_ONLY
            or len(u) <= 2
        )

    left_junk = _junk(left_up)
    right_junk = _junk(right_up)

    if left_junk and right_junk:
        return ''
    if left_junk and not right_junk:
        return right
    # Default: COMPANY/TITLE — take left
    return left


# ── The per-value contract ───────────────────────────────────────────
# MISSING_VALUES is the SAME canonical FEC-junk / admin-note set that clean.py
# nulls out of contributor_employer, so an admin note a committee wrote while
# chasing a donor's info ("REQUESTED", "2ND REQUEST LETTER MAILED") can't leak
# into previous_employer either. Single source of truth, not a second list.
_NULL_PREV = SECTOR_AS_EMPLOYER | MISSING_VALUES | {
    'MEDICAL', 'COMMUNITY VOLUNTEER', 'VOLUNTEER', 'HOMEMAKER', 'HOUSEWIFE',
    'NOT EMPLOYED', 'UNEMPLOYED', 'RETIRED', 'STUDENT', 'NONE', 'N/A', 'NA',
    # keyboard junk / bare-status stumps seen only in previous_employer
    'XXN', 'NOT',
}
_SE_PREV = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER
# FEC-API-sourced previous employers sometimes arrive with mangled spacing
# ("NOTEMPLOYED", "HOME MAKER") — a donor's OTHER filing where they typed a
# status word oddly. Match a whitespace-stripped form so a spacing variant is
# still recognised as a non-company status and cleared.
_NULL_PREV_NOSPACE = {re.sub(r'\s+', '', v) for v in _NULL_PREV}


def normalize_previous_employer_value(v) -> str:
    """The contract for ONE previous_employer value. Returns '' to clear it.

    Order matters: self-employment is recognised FIRST, because the
    display-name normalizer at the end treats 'SELF-EMPLOYED' as a non-name and
    would otherwise clear a fact we want to keep.
    """
    s = str(v)
    su = s.strip().upper()
    # explicit self-employment markers
    if (su.startswith('SELF:') or su.startswith('SELF EMPLOYED')
            or su.startswith('SELF-EMPLOYED') or su == 'SELF'):
        return 'SELF-EMPLOYED'
    # not a real prior company: email, industry word, status/volunteer, junk.
    # su_ns collapses whitespace so spacing-mangled statuses ("NOTEMPLOYED",
    # "HOME MAKER") match their canonical form ("NOT EMPLOYED", "HOMEMAKER").
    su_ns = re.sub(r'\s+', '', su)
    if ('@' in su or su in _NULL_PREV or su_ns in _NULL_PREV_NOSPACE
            or re.match(JUNK_EMPLOYER_RE, su)):
        return ''
    # a bare job title / role → worked for themselves in that profession
    if su in _SE_PREV:
        return 'SELF-EMPLOYED'
    # any other non-company value — a status word or its typo (RETIEED,
    # RETIREE, HOUSWIFE), a sector, refusal/junk — is not a real prior
    # employer. is_real_employer wraps NOT_REAL_EMPLOYER, so a status/typo
    # added there is cleared here too; the hand-kept _NULL_PREV list above
    # missed these exact spellings and let them leak through to
    # build_employers' "missing employer" warning.
    if not is_real_employer(su):
        return ''
    # company-name unification — the SAME synonym map that canonicalizes
    # contributor_employer, so a company collapses to ONE name across both
    # columns (e.g. GOLDMAN SACHS NEW JERSEY -> GOLDMAN SACHS).
    mapped = EMPLOYER_SYNONYMS.get(su)
    if mapped:
        s = mapped
    if '/' in s:
        s = _resolve_slash(s)
        if not s:
            return ''
    return normalize_employer_display_name(s) or ''


def normalize_previous_employer_column(df: pd.DataFrame) -> int:
    """Apply the contract to a whole frame. Returns the number of rows changed.

    Idempotent: canonical values pass through unchanged, so running it in both
    the cleaning and resolve stages is safe and the two agree.

    Also drops a previous_employer that is the DONOR'S OWN name. The
    cross-record / FEC-API recovery occasionally lifts the NAME field instead of
    the employer field ("MOSKOWITZ, MARVIN" -> "MOSKOWITZ MARVIN"), which then
    becomes a phantom one-person company in employers.csv. Compares the SET of
    name words, not the letters in order: the FEC stores names "LAST, FIRST"
    while filers write the employer "FIRST LAST", so an order-sensitive compare
    never fires on the common case. Single letters are dropped so a middle
    initial ("ERIK A COOPER") still matches. It must be the WHOLE name — a
    shared surname is normal and correct, and name-partner firms like
    "COMITER, SINGER, BASEMAN & BRAUN" are exactly where a Richard Comiter
    would have worked.
    """
    if 'previous_employer' not in df.columns:
        return 0
    pe = df['previous_employer']
    non_empty = pe.notna() & (pe.astype(str).str.strip() != '')
    if not non_empty.any():
        return 0

    before = pe[non_empty].astype(str)
    normed = before.map(normalize_previous_employer_value)
    df.loc[non_empty, 'previous_employer'] = normed

    if 'contributor_name' in df.columns:
        _words = lambda s: frozenset(
            w for w in re.sub(r'[^A-Z]', ' ', str(s).upper()).split() if len(w) > 1
        )
        own = (df.loc[non_empty, 'contributor_name'].map(_words)
               == df.loc[non_empty, 'previous_employer'].map(_words))
        own_idx = df.loc[non_empty].index[own & (df.loc[non_empty, 'previous_employer'] != '')]
        if len(own_idx):
            df.loc[own_idx, 'previous_employer'] = ''

    return int((df.loc[non_empty, 'previous_employer'] != before).sum())
