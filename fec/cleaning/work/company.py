"""One definition of a company name, as graded evidence.

Every rule that needs to know whether a work-field value names a company
asks here. The answer is a level, strongest first:

    legal_form   ends in a legal form: ACME LLC, SMITH & JONES PC
    reviewed     a reviewed company name (employer_name_rules.csv)
    job          a known job, field or status: REAL ESTATE, CONSULTANT
    frequent     filed as the employer by several different donors
    named_org    an organisation word plus a proper name: PENSAM CAPITAL
    unknown      no evidence either way: MATTEL, CITY OF PHILADELPHIA
    (blank)      empty value

A job is never a company, however many organisation words it holds
(VENTURE CAPITALIST, INVESTMENT MANAGER). An unknown value is left as
filed: it may stay an employer, but it is never moved out of the
occupation.
"""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd

from fec.cleaning.work.employer_aliases import _load_rules, employer_rule_key
from fec.cleaning.work.normalize import _FINAL_OCCUPATION_CATEGORIES
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS, STATUS_WORDS
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    JOB_TITLE_HEADS,
    JOB_TITLE_MODIFIERS,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
)
from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES, KNOWN_COMPANY_OCCUPATIONS

LEVELS = ("legal_form", "reviewed", "frequent", "named_org")
COMPANY_LEVELS = frozenset(LEVELS)

# Legal forms, after punctuation is removed: "L.L.C." -> "LLC".
_LEGAL_FORMS = (
    "LLC", "PLLC", "LLP", "LP", "INC", "INCORPORATED", "CORP", "CORPORATION",
    "LTD", "LIMITED", "PLC", "PC", "PA", "CO",
)
_LEGAL_FORM_END_RE = re.compile(r"\b(?:" + "|".join(_LEGAL_FORMS) + r")$")
# "CO" alone is a word (COMPANY) only at the end: "TRADING CO".
_CO_AFTER_WORD_RE = re.compile(r"\b[A-Z0-9]{2,}\s+CO$")

# Words that mark an organisation when a proper name stands beside them.
ORGANISATION_WORDS = frozenset({
    "GROUP", "PARTNERS", "PARTNERSHIP", "HOLDING", "HOLDINGS", "ASSOCIATES",
    "ENTERPRISE", "ENTERPRISES", "INDUSTRIES", "VENTURE", "VENTURES",
    "PROPERTIES", "PROPERTY", "REALTY", "BANK", "FOUNDATION", "UNIVERSITY",
    "COLLEGE", "HOSPITAL", "INSTITUTE", "SCHOOL", "ACADEMY", "COMPANY",
    "COMPANIES", "BROTHERS", "TRUST", "FUND", "FUNDS", "CAPITAL", "ADVISORS",
    "ADVISERS", "SERVICES", "SYSTEMS", "SOLUTIONS", "TECHNOLOGIES",
    "INTERNATIONAL", "GLOBAL", "MANAGEMENT", "INSURANCE", "FINANCIAL",
    "MEDIA", "CENTER", "CENTRE", "INVESTMENT", "INVESTMENTS", "EQUITIES",
    "LAW", "AGENCY", "DEPARTMENT", "BUREAU", "OFFICE", "OFFICES", "STRATEGIES",
    "CONSULTING", "DEVELOPMENT", "CONSTRUCTION", "STEEL", "LENDING",
    "ACQUISITIONS", "TELECOM", "CONGREGATION", "UNION", "COUNCIL",
    "ASSOCIATION", "HEALTH", "HEALTHCARE", "ENERGY", "FINANCE", "WEALTH",
    "ENTERTAINMENT", "COMMUNICATIONS", "LOGISTICS", "MARKETING", "TRADING",
    "ESTATE", "MEDICAL", "DENTAL", "RADIOLOGY", "CLINIC", "PHARMACY", "STUDIOS",
    "CARE", "ADVISORY", "AFFAIRS", "INDUSTRY", "SECTOR", "TELECOMMUNICATIONS",
    "ACQUISITION", "CONSULTANTS", "TRADES", "ASSOC",
})
# Common words that name no company on their own.
_FILLER_WORDS = frozenset({
    "THE", "OF", "AND", "AT", "IN", "FOR", "A", "AN", "GREATER", "COMMERCIAL", "PRIVATE", "PUBLIC", "GENERAL",
    "SENIOR", "JUNIOR", "INDEPENDENT", "FAMILY", "NATIONAL", "AMERICAN",
    "FEDERAL", "STATE", "CITY", "COUNTY", "SELF", "EMPLOYED", "REAL", "CORPORATE",
    "GLOBAL", "REGIONAL", "LOCAL", "ASSOCIATED", "UNITED",
})

_WORDS_RE = re.compile(r"[A-Z0-9][A-Z0-9'&-]*")
_PART_SPLIT_RE = re.compile(r"\s*(?:/|,|;| AND | & )\s*")


def _is_job(text: str) -> bool:
    if text in _job_words() or is_job_title(text):
        return True
    words = _WORDS_RE.findall(text)
    # "INVESTMENT PORTFOLIO MANAGER", "HEALTHCARE EXECUTIVE": a title word last,
    # unless it also names an organisation (CRESCENT REALTY ADVISORS).
    return len(words) > 1 and _head_word(words[-1]) and words[-1] not in ORGANISATION_WORDS


def _head_word(word: str) -> bool:
    return word in JOB_TITLE_HEADS or (word.endswith("S") and word[:-1] in JOB_TITLE_HEADS)


# Organisation and filler words only, so a field rather than a name.
def _only_common_words(text: str) -> bool:
    words = _WORDS_RE.findall(text)
    return bool(words) and all(w in ORGANISATION_WORDS or w in _FILLER_WORDS or w == "AND" for w in words)


@lru_cache(maxsize=1)
def _reviewed_names() -> frozenset[str]:
    aliases, _separate, scoped = _load_rules()
    names = set(aliases) | set(aliases.values()) | set(KNOWN_COMPANY_OCCUPATIONS)
    names |= {variant for _anchor, _cycle, variant, _canonical in scoped}
    names |= {canonical for _anchor, _cycle, _variant, canonical in scoped}
    names |= {employer_rule_key(name) for name in names}
    return frozenset(names - _job_words())


@lru_cache(maxsize=1)
def _job_words() -> frozenset[str]:
    """Exact values that are a job, field or status, never a company."""
    return frozenset(
        {k for k, v in _FINAL_OCCUPATION_CATEGORIES.items() if v != "OTHER"}
        | {k for k, v in CATEGORY_OVERRIDES.items() if v != "OTHER"}
        | ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER
        | SECTOR_AS_EMPLOYER | STATUS_WORDS | SKIP_EMPLOYERS | SKIP_OCCUPATIONS
        | NOT_REAL_EMPLOYER
    )


# upper text with the punctuation that varies between filings removed
def company_key(value) -> str:
    return employer_rule_key(value)


@lru_cache(maxsize=65536)
def _text_evidence(key: str, ampersand: bool = False) -> str:
    """Evidence from the text alone: legal_form, reviewed, job, named_org or unknown.

    ampersand says the filing wrote "&" (SMITH & JONES), which the key spells AND.
    """
    if not key:
        return ""
    if " " in key and _LEGAL_FORM_END_RE.search(key):
        if not key.endswith(" CO") or (_CO_AFTER_WORD_RE.search(key) and not _only_common_words(key[:-3])):
            return "legal_form"
    if _is_job(key) or _is_job(key.replace(" AND ", " & ")):
        return "job"
    # "CHAIRMAN AND CEO", "RETIRED/REAL ESTATE": every part a job or status.
    parts = [p.strip() for p in _PART_SPLIT_RE.split(key) if p.strip()]
    if len(parts) > 1 and all(_is_job(p) or _only_common_words(p) for p in parts):
        return "job"
    if any(p in STATUS_WORDS for p in parts):
        return "job"
    words = _WORDS_RE.findall(key)
    # "DIRECTOR OF POLICY", "VP, INVESTMENTS AND PROGRAMS": a described role.
    if words and (words[0] in JOB_TITLE_HEADS or words[0] in ROLE_AS_EMPLOYER):
        return "job"
    if key in _reviewed_names():
        return "reviewed"
    if not any(word in ORGANISATION_WORDS for word in words) and not ampersand:
        return "unknown"
    # The proper name left after organisation and filler words.
    rest = [w for w in words if w not in ORGANISATION_WORDS and w not in _FILLER_WORDS and w != "AND"]
    if not rest:
        # Organisation words alone (CAPITAL GROUP, INSURANCE SERVICES): a
        # name or a field; only other filings can tell.
        return "unknown"
    rest_text = " ".join(rest)
    if _is_job(rest_text) or any(_head_word(w) for w in rest):
        return "job"
    # Every remaining word is a title word: a described role, not a name.
    if all(w in JOB_TITLE_HEADS or w in JOB_TITLE_MODIFIERS for w in rest):
        return "job"
    return "named_org"


def company_evidence(
    values: pd.Series,
    *,
    employers: pd.Series | None = None,
    filers: pd.Series | None = None,
    min_filers: int = 3,
) -> pd.Series:
    """The evidence level of each value.

    With employers and filers (the same length), a value filed as the
    employer by at least min_filers different filers is 'frequent' when the
    text alone says 'unknown' or 'named_org'.
    """
    keys = values.map(company_key)
    ampersands = values.map(lambda v: isinstance(v, str) and "&" in v)
    levels = pd.Series([_text_evidence(k, a) for k, a in zip(keys, ampersands)], index=values.index, dtype=object)
    if employers is not None and filers is not None:
        emp_keys = employers.map(company_key)
        counts = filers.groupby(emp_keys.to_numpy()).nunique()
        frequent = (
            levels.isin(("unknown", "named_org"))
            & keys.map(counts).fillna(0).ge(min_filers)
            & keys.str.replace(r"[^A-Z]", "", regex=True).str.len().ge(3)
            & keys.map(is_real_employer)
            & ~keys.map(_only_common_words)
        )
        levels = levels.mask(frequent, "frequent")
    return levels


def filers_of(df: pd.DataFrame, rows=None) -> pd.Series | None:
    """The filer names for rows, or None when the frame has none."""
    if "contributor_name" not in df.columns:
        return None
    return df["contributor_name"] if rows is None else df.loc[rows, "contributor_name"]


def is_company(values: pd.Series, **context) -> pd.Series:
    """True where the evidence is strong enough to treat the value as a company."""
    return company_evidence(values, **context).isin(COMPANY_LEVELS)


_LEGAL_TOKENS = frozenset({"LLC", "PLLC", "LLP", "LP", "INC", "INCORPORATED", "CORP", "CORPORATION", "LTD", "PLC"})


def has_legal_form_token(values: pd.Series) -> pd.Series:
    """A legal form anywhere in the text, as a name filed LAST, FIRST may carry one."""
    return values.map(company_key).map(lambda key: any(w in _LEGAL_TOKENS for w in key.split()))


def ends_with_legal_form(values: pd.Series) -> pd.Series:
    return values.map(company_key).map(_text_evidence).eq("legal_form")
