"""Mechanical style normalization for occupation text: separator spacing, title joiners, CO- prefix, compound tokens, plural role nouns.

Only forms of the SAME words are unified here (PRESIDENT / CEO -> PRESIDENT/CEO,
HEALTH CARE -> HEALTHCARE, ADVISORS -> ADVISOR). Synonyms (LAWYER vs ATTORNEY)
are deliberately never touched.
"""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx
from fec.config.occupation_rules.fixes import OCCUPATION_TYPO_FIXES

# Job titles that combine with "/" (PRESIDENT/CEO). Longest first so the
# alternation prefers VICE PRESIDENT over PRESIDENT.
_TITLES = (
    'VICE PRESIDENT', 'MANAGING PARTNER', 'MANAGING DIRECTOR', 'MANAGING MEMBER',
    'GENERAL COUNSEL', 'GENERAL MANAGER', 'EXECUTIVE DIRECTOR', 'CHIEF EXECUTIVE',
    'CO-FOUNDER', 'CO-OWNER', 'CO-CEO', 'CO-CHAIRMAN', 'CO-CHAIR', 'CO-PRESIDENT',
    'CO-MANAGING PARTNER',
    'CHAIRMAN', 'CHAIRWOMAN', 'CHAIRPERSON', 'CHAIR', 'PRESIDENT', 'FOUNDER',
    'OWNER', 'PARTNER', 'PRINCIPAL', 'DIRECTOR', 'MANAGER', 'TREASURER',
    'SECRETARY', 'TRUSTEE', 'CEO', 'CFO', 'COO', 'CTO', 'CIO', 'CMO', 'CHRO',
    'EVP', 'SVP', 'VP', 'GP', 'PROPRIETOR', 'OPERATOR', 'PUBLISHER', 'EDITOR',
    'WRITER', 'PRODUCER', 'AUTHOR', 'ACTOR', 'COMPOSER', 'ARTIST', 'PAINTER',
    'ATTORNEY', 'LAWYER', 'INVESTOR', 'DEVELOPER', 'BUILDER', 'BROKER',
    'REALTOR', 'CONSULTANT', 'ADVISOR', 'ENTREPRENEUR', 'PHYSICIAN', 'PROFESSOR',
    'PHILANTHROPIST', 'HOMEMAKER',
)
_TITLE_ALT = '|'.join(re.escape(t) for t in sorted(_TITLES, key=len, reverse=True))
# whole string = TITLE (& | AND | -) TITLE
_TITLE_PAIR_RE = re.compile(
    rf'^({_TITLE_ALT})\s*(?:&|\bAND\b|-)\s*({_TITLE_ALT})$'
)

# CO OWNER / CO- CEO / CO -FOUNDER -> CO-OWNER ... : the word CO directly
# before a role word is always the "co-" prefix.
_CO_PREFIX_RE = re.compile(
    r'\bCO\s*-?\s*(?=(?:FOUNDER|OWNER|CEO|COO|CFO|PRESIDENT|CHAIR|CHAIRMAN|'
    r'CHAIRWOMAN|DIRECTOR|MANAGER|MANAGING|PARTNER|HEAD|CHIEF|EXECUTIVE|'
    r'PRODUCER|TRUSTEE|PASTOR|PRINCIPAL|COUNSEL|LEADER|EDITOR|AUTHOR|CREATOR)\b)'
)

# Compound spellings of the same word(s), applied at word level anywhere.
_COMPOUND_TOKENS = (
    (re.compile(r'\bHEALTH[\s-]+CARE\b'), 'HEALTHCARE'),
    (re.compile(r'\bHOME[\s-]*BUILDER'), 'HOME BUILDER'),
    (re.compile(r'\bREAL[\s-]*ESTATE\b'), 'REAL ESTATE'),
    (re.compile(r'\bNON[\s-]*PROFIT\b'), 'NONPROFIT'),
    (re.compile(r'\bNON[\s-]*EXECUTIVE\b'), 'NON-EXECUTIVE'),
    (re.compile(r'\bVICE[\s-]+PRESIDENT\b'), 'VICE PRESIDENT'),
    (re.compile(r'\bSPEECH[\s-]+LANGUAGE\b'), 'SPEECH LANGUAGE'),
    (re.compile(r'\bHOME[\s-]*MAKER\b'), 'HOMEMAKER'),
)

# Person-role nouns whose plural is just the singular + S. Only the LAST word
# of the occupation is de-pluralized, and never after "OF" (BOARD OF DIRECTORS).
# Industry plurals (INVESTMENTS, SALES, RESTAURANTS, COMMUNICATIONS) are
# intentionally absent: the plural IS the occupation there.
_ROLE_NOUNS = frozenset({
    'ACCOUNTANT', 'ACTOR', 'ACTIVIST', 'ADMINISTRATOR', 'ADVISER', 'ADVISOR',
    'AGENT', 'ANALYST', 'ANESTHESIOLOGIST', 'ARCHITECT', 'ARTIST', 'ASSISTANT',
    'ATTORNEY', 'AUDITOR', 'AUTHOR', 'BANKER', 'BROKER', 'BUILDER',
    'CAPITALIST', 'CARDIOLOGIST', 'CHIROPRACTOR', 'CLERK', 'COMPOSER',
    'CONSULTANT', 'CONTRACTOR', 'COORDINATOR', 'COUNSELOR', 'DEALER',
    'DENTIST', 'DERMATOLOGIST', 'DESIGNER', 'DEVELOPER', 'DIRECTOR',
    'DISTRIBUTOR', 'DOCTOR', 'DRIVER', 'ECONOMIST', 'EDITOR', 'EDUCATOR',
    'ELECTRICIAN', 'ENGINEER', 'ENTREPRENEUR', 'EXECUTIVE', 'EXPORTER',
    'FARMER', 'FILMMAKER', 'FINANCIER', 'FOUNDER', 'HOMEMAKER',
    'HOSPITALIST', 'IMPORTER', 'INSTRUCTOR', 'INTERNIST', 'INVESTOR',
    'JOURNALIST', 'LAWYER', 'LOBBYIST', 'MANAGER', 'MANUFACTURER', 'MECHANIC',
    'MERCHANT', 'MUSICIAN', 'NEUROLOGIST', 'NURSE', 'OFFICER', 'ONCOLOGIST',
    'OPERATOR', 'OPHTHALMOLOGIST', 'OPTOMETRIST', 'ORGANIZER', 'OWNER',
    'PAINTER', 'PARALEGAL', 'PARTNER', 'PASTOR', 'PATHOLOGIST',
    'PEDIATRICIAN', 'PERFORMER', 'PHARMACIST', 'PHILANTHROPIST',
    'PHOTOGRAPHER', 'PHYSICIAN', 'PILOT', 'PLANNER', 'PLUMBER', 'PODIATRIST',
    'PRINCIPAL', 'PRODUCER', 'PROFESSIONAL', 'PROFESSOR', 'PROGRAMMER',
    'PROPRIETOR', 'PSYCHIATRIST', 'PSYCHOLOGIST', 'PSYCHOTHERAPIST',
    'PUBLISHER', 'RABBI', 'RADIOLOGIST', 'RANCHER', 'REALTOR', 'RESEARCHER',
    'RETAILER', 'SCIENTIST', 'SPECIALIST', 'STRATEGIST', 'STUDENT',
    'SUPERVISOR', 'SURGEON', 'TEACHER', 'TECHNICIAN', 'THERAPIST', 'TRADER',
    'TRAINER', 'UNDERWRITER', 'UROLOGIST', 'VETERINARIAN', 'VOLUNTEER',
    'WHOLESALER', 'WORKER', 'WRITER',
})
_LAST_WORD_RE = re.compile(r'([A-Z]+)S$')


# drop trailing S from a known role noun
def _depluralize_last_word(value: str) -> str:
    if ' OF ' in f' {value} ':
        return value
    match = _LAST_WORD_RE.search(value)
    if not match:
        return value
    singular = match.group(1)
    if singular not in _ROLE_NOUNS:
        return value
    # the plural must be the whole last word, not a suffix (TRADERS ok, RETRADERS no)
    start = match.start(1)
    if start and value[start - 1].isalpha():
        return value
    return value[:start] + singular


# apply every style rule to one occupation string
def normalize_occupation_style(value: str) -> str:
    """Apply every style rule to one occupation string."""
    s = value
    # separator spacing: "A / B" -> "A/B", "A&B" -> "A & B"; abbreviations
    # with short sides (R&D, M&A, FP&A, P&L) stay tight
    s = re.sub(r'\s*/+\s*', '/', s)
    s = re.sub(r'\s*&\s*', ' & ', s)
    s = re.sub(r'\b([A-Z]{1,2}) & ([A-Z]{1,2})\b', r'\1&\2', s)
    for regex, replacement in _COMPOUND_TOKENS:
        s = regex.sub(replacement, s)
    s = _CO_PREFIX_RE.sub('CO-', s)
    s = _TITLE_PAIR_RE.sub(r'\1/\2', s)
    s = _depluralize_last_word(s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s


# non-capturing: str.contains only needs a yes/no, and a capture group makes
# pandas warn "this pattern has match groups" on every run
_LEGAL_SUFFIX_IN_OCC_RE = re.compile(r'\b(?:LLC|LLP|INC|CORP|CORPORATION|LTD|PLLC|PC|LP)\b')


# pipeline step applying occupation style normalization to individuals
def normalize_occupation_style_step(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Pipeline step: unify separator, joiner, compound and plural style of individuals' occupations.

    Must run after the swap safety nets. A company name still sitting in the
    occupation column (swapped filing) is never restyled: it has to stay
    byte-identical to the employer set so the nets can recognise it.
    """
    individuals = _indiv_idx(df)
    occupation = df.loc[individuals, 'contributor_occupation'].dropna().astype(str)
    if occupation.empty:
        return df, 0
    employer_names = set(df.loc[individuals, 'contributor_employer'].dropna().astype(str))
    is_company = occupation.isin(employer_names) | occupation.str.contains(_LEGAL_SUFFIX_IN_OCC_RE, na=False)
    occupation = occupation[~is_company]
    if occupation.empty:
        return df, 0
    restyled = occupation.map(normalize_occupation_style)
    changed = restyled != occupation
    if changed.any():
        df.loc[changed[changed].index, 'contributor_occupation'] = restyled[changed]
    return df, int(changed.sum())


# pipeline step applying curated occupation typo fixes
def apply_occupation_typo_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Pipeline step: curated typo/abbreviation -> canonical spelling (runs right after the style step)."""
    individuals = _indiv_idx(df)
    occupation = df.loc[individuals, 'contributor_occupation']
    hits = individuals[occupation.isin(OCCUPATION_TYPO_FIXES)]
    if len(hits):
        df.loc[hits, 'contributor_occupation'] = occupation[hits].map(OCCUPATION_TYPO_FIXES)
    return df, int(len(hits))
