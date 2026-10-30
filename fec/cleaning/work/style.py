"""Occupation style: separators, joiners, CO- prefix, compounds, plurals."""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _map_occupations
from fec.config.occupation_rules.rules import OCCUPATION_TYPO_FIXES

# Titles combined with "/"; longest first so VICE PRESIDENT wins.
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

# CO before a role word is the co- prefix.
_CO_PREFIX_RE = re.compile(
    r'\bCO\s*-?\s*(?=(?:FOUNDER|OWNER|CEO|COO|CFO|PRESIDENT|CHAIR|CHAIRMAN|'
    r'CHAIRWOMAN|DIRECTOR|MANAGER|MANAGING|PARTNER|HEAD|CHIEF|EXECUTIVE|'
    r'PRODUCER|TRUSTEE|PASTOR|PRINCIPAL|COUNSEL|LEADER|EDITOR|AUTHOR|CREATOR)\b)'
)

# Compound spellings of the same words, anywhere.
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

# Role nouns de-pluralized as the last word, never after OF.
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
# Last word ending in S, without the S.
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
    # The plural must be the whole last word.
    start = match.start(1)
    if start and value[start - 1].isalpha():
        return value
    return value[:start] + singular


# apply every style rule to one occupation string
def normalize_occupation_style(value: str) -> str:
    """Apply every style rule to one occupation string."""
    s = value
    # Separator spacing; short abbreviations like R&D stay tight.
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


# Non-capturing, so pandas does not warn on every run.
_LEGAL_SUFFIX_IN_OCC_RE = re.compile(r'\b(?:LLC|LLP|INC|CORP|CORPORATION|LTD|PLLC|PC|LP)\b')


# pipeline step applying occupation style normalization to individuals
def normalize_occupation_style_step(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Unify individuals' occupation style; runs after the swap safety nets."""
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
    """Curated typo and abbreviation fixes, right after style."""
    return _map_occupations(df, OCCUPATION_TYPO_FIXES)
