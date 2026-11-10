"""Occupation style: separators, joiners, CO- prefix, compounds, plurals."""

import re

from fec.vocabulary.tables import words

# Titles combined with "/"; longest first so VICE PRESIDENT wins.
_TITLES = (
    "VICE PRESIDENT",
    "MANAGING PARTNER",
    "MANAGING DIRECTOR",
    "MANAGING MEMBER",
    "GENERAL COUNSEL",
    "GENERAL MANAGER",
    "EXECUTIVE DIRECTOR",
    "CHIEF EXECUTIVE",
    "CO-FOUNDER",
    "CO-OWNER",
    "CO-CEO",
    "CO-CHAIRMAN",
    "CO-CHAIR",
    "CO-PRESIDENT",
    "CO-MANAGING PARTNER",
    "CHAIRMAN",
    "CHAIRWOMAN",
    "CHAIRPERSON",
    "CHAIR",
    "PRESIDENT",
    "FOUNDER",
    "OWNER",
    "PARTNER",
    "PRINCIPAL",
    "DIRECTOR",
    "MANAGER",
    "TREASURER",
    "SECRETARY",
    "TRUSTEE",
    "CEO",
    "CFO",
    "COO",
    "CTO",
    "CIO",
    "CMO",
    "CHRO",
    "EVP",
    "SVP",
    "VP",
    "GP",
    "PROPRIETOR",
    "OPERATOR",
    "PUBLISHER",
    "EDITOR",
    "WRITER",
    "PRODUCER",
    "AUTHOR",
    "ACTOR",
    "COMPOSER",
    "ARTIST",
    "PAINTER",
    "ATTORNEY",
    "LAWYER",
    "INVESTOR",
    "DEVELOPER",
    "BUILDER",
    "BROKER",
    "REALTOR",
    "CONSULTANT",
    "ADVISOR",
    "ENTREPRENEUR",
    "PHYSICIAN",
    "PROFESSOR",
    "PHILANTHROPIST",
    "HOMEMAKER",
)

_TITLE_ALT = "|".join(re.escape(title) for title in sorted(_TITLES, key=len, reverse=True))

# whole string = TITLE (& | AND | -) TITLE
_TITLE_PAIR_RE = re.compile(rf"^({_TITLE_ALT})\s*(?:&|\bAND\b|-)\s*({_TITLE_ALT})$")

# CO before a role word is the co- prefix.
_CO_PREFIX_RE = re.compile(
    r"\bCO\s*-?\s*(?=(?:FOUNDER|OWNER|CEO|COO|CFO|PRESIDENT|CHAIR|CHAIRMAN|"
    r"CHAIRWOMAN|DIRECTOR|MANAGER|MANAGING|PARTNER|HEAD|CHIEF|EXECUTIVE|"
    r"PRODUCER|TRUSTEE|PASTOR|PRINCIPAL|COUNSEL|LEADER|EDITOR|AUTHOR|CREATOR)\b)"
)

# Compound spellings of the same words, anywhere.
_COMPOUND_TOKENS = (
    (re.compile(r"\bHEALTH[\s-]+CARE\b"), "HEALTHCARE"),
    (re.compile(r"\bHOME[\s-]*BUILDER"), "HOME BUILDER"),
    (re.compile(r"\bREAL[\s-]*ESTATE\b"), "REAL ESTATE"),
    (re.compile(r"\bNON[\s-]*PROFIT\b"), "NONPROFIT"),
    (re.compile(r"\bNON[\s-]*EXECUTIVE\b"), "NON-EXECUTIVE"),
    (re.compile(r"\bVICE[\s-]+PRESIDENT\b"), "VICE PRESIDENT"),
    (re.compile(r"\bSPEECH[\s-]+LANGUAGE\b"), "SPEECH LANGUAGE"),
    (re.compile(r"\bHOME[\s-]*MAKER\b"), "HOMEMAKER"),
)

# Role nouns de-pluralized as the last word, never after OF.
_ROLE_NOUNS = words("role_nouns")

# Last word ending in S, without the S.
_LAST_WORD_RE = re.compile(r"([A-Z]+)S$")


def _depluralize_last_word(value: str) -> str:
    """Drop trailing S from a known role noun."""
    if " OF " in f" {value} ":
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


def normalize_occupation_style(value: str) -> str:
    """Apply every style rule to one occupation string."""
    text = value
    # Separator spacing; short abbreviations like R&D stay tight.
    text = re.sub(r"\s*/+\s*", "/", text)
    text = re.sub(r"\s*&\s*", " & ", text)
    text = re.sub(r"\b([A-Z]{1,2}) & ([A-Z]{1,2})\b", r"\1&\2", text)
    for regex, replacement in _COMPOUND_TOKENS:
        text = regex.sub(replacement, text)
    text = _CO_PREFIX_RE.sub("CO-", text)
    text = _TITLE_PAIR_RE.sub(r"\1/\2", text)
    text = _depluralize_last_word(text)
    return re.sub(r"\s{2,}", " ", text).strip()
