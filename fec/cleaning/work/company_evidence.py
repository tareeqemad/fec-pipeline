"""Whether a work-field value names a company, as a level of evidence, strongest first.

legal_form (ACME LLC), reviewed (employer_name_rules.csv), job (REAL ESTATE), frequent (filed as the employer
by several donors), named_org (PENSAM CAPITAL), unknown (MATTEL), or blank. A job is never a company.
"""

import re
from functools import lru_cache

import pandas as pd

from fec.cleaning.work.company_names import employer_rule_key, read_company_name_rules
from fec.cleaning.work.employer_status import is_job_title, is_real_employer
from fec.vocabulary.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    JOB_TITLE_HEADS,
    JOB_TITLE_MODIFIERS,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
)
from fec.vocabulary.occupations import KNOWN_COMPANY_OCCUPATIONS, OCCUPATION_CATEGORIES
from fec.vocabulary.statuses import SKIP_EMPLOYERS, SKIP_OCCUPATIONS, STATUS_WORDS
from fec.vocabulary.tables import ordered_words, words

COMPANY_LEVELS = frozenset(("legal_form", "reviewed", "frequent", "named_org"))
# Legal forms, after punctuation is removed: "L.L.C." -> "LLC".
_LEGAL_FORM_END_RE = re.compile(r"\b(?:" + "|".join(ordered_words("legal_form_endings")) + r")$")
# "CO" alone is a word (COMPANY) only at the end: "TRADING CO".
_CO_AFTER_WORD_RE = re.compile(r"\b[A-Z0-9]{2,}\s+CO$")
# Words that mark an organisation when a proper name stands beside them.
ORGANISATION_WORDS = words("organisation_words")
# Common words that name no company on their own.
_FILLER_WORDS = words("filler_words")
_LEGAL_TOKENS = words("legal_tokens")
_WORDS_RE = re.compile(r"[A-Z0-9][A-Z0-9'&-]*")
_PART_SPLIT_RE = re.compile(r"\s*(?:/|,|;| AND | & )\s*")

_JOB_WORDS = frozenset(
    {text for text, category in OCCUPATION_CATEGORIES.items() if category != "OTHER"}
    | ROLE_AS_EMPLOYER
    | OCCUPATION_AS_EMPLOYER
    | JOB_TITLE_AS_EMPLOYER
    | SECTOR_AS_EMPLOYER
    | STATUS_WORDS
    | SKIP_EMPLOYERS
    | SKIP_OCCUPATIONS
    | NOT_REAL_EMPLOYER
)


def _read_reviewed_names() -> frozenset[str]:
    aliases, scoped = read_company_name_rules()
    names = set(aliases) | set(aliases.values()) | set(KNOWN_COMPANY_OCCUPATIONS)
    names |= {variant for _anchor, _cycle, variant, _canonical in scoped}
    names |= {canonical for _anchor, _cycle, _variant, canonical in scoped}
    names |= {employer_rule_key(name) for name in names}
    return frozenset(names - _JOB_WORDS)


_REVIEWED_NAMES = _read_reviewed_names()


def _is_job(text: str) -> bool:
    if text in _JOB_WORDS or is_job_title(text):
        return True
    words = _WORDS_RE.findall(text)
    # "HEALTHCARE EXECUTIVE": a title word last, unless it also names an organisation (CRESCENT REALTY ADVISORS).
    return len(words) > 1 and _is_head_word(words[-1]) and words[-1] not in ORGANISATION_WORDS


def _is_head_word(word: str) -> bool:
    return word in JOB_TITLE_HEADS or (word.endswith("S") and word[:-1] in JOB_TITLE_HEADS)


def _has_only_common_words(text: str) -> bool:
    words = _WORDS_RE.findall(text)
    return bool(words) and all(word in ORGANISATION_WORDS or word in _FILLER_WORDS or word == "AND" for word in words)


def _has_legal_form_end(key: str) -> bool:
    if " " not in key or not _LEGAL_FORM_END_RE.search(key):
        return False
    if not key.endswith(" CO"):
        return True
    return bool(_CO_AFTER_WORD_RE.search(key)) and not _has_only_common_words(key[:-3])


def _describes_job(key: str) -> bool:
    if _is_job(key) or _is_job(key.replace(" AND ", " & ")):
        return True
    # "CHAIRMAN AND CEO", "RETIRED/REAL ESTATE": every part a job or status.
    parts = [part.strip() for part in _PART_SPLIT_RE.split(key) if part.strip()]
    if len(parts) > 1 and all(_is_job(part) or _has_only_common_words(part) for part in parts):
        return True
    if any(part in STATUS_WORDS for part in parts):
        return True
    words = _WORDS_RE.findall(key)
    # "DIRECTOR OF POLICY", "VP, INVESTMENTS AND PROGRAMS": a described role.
    return bool(words) and (words[0] in JOB_TITLE_HEADS or words[0] in ROLE_AS_EMPLOYER)


def _read_proper_name_evidence(words: list[str]) -> str:
    rest = [word for word in words if word not in ORGANISATION_WORDS and word not in _FILLER_WORDS and word != "AND"]
    if not rest:
        # Organisation words alone (CAPITAL GROUP): a name or a field; only other filings can tell.
        return "unknown"
    if _is_job(" ".join(rest)) or any(_is_head_word(word) for word in rest):
        return "job"
    # Every remaining word is a title word: a described role, not a name.
    if all(word in JOB_TITLE_HEADS or word in JOB_TITLE_MODIFIERS for word in rest):
        return "job"
    return "named_org"


@lru_cache(maxsize=65536)
def _read_text_evidence(key: str, ampersand: bool) -> str:
    # ampersand says the filing wrote "&" (SMITH & JONES), which the key spells AND.
    if not key:
        return ""
    if _has_legal_form_end(key):
        return "legal_form"
    if _describes_job(key):
        return "job"
    if key in _REVIEWED_NAMES:
        return "reviewed"
    words = _WORDS_RE.findall(key)
    if not any(word in ORGANISATION_WORDS for word in words) and not ampersand:
        return "unknown"
    return _read_proper_name_evidence(words)


def company_evidence_level(text: str) -> str:
    """Return the evidence level of a text on its own."""
    return _read_text_evidence(employer_rule_key(text), "&" in text)


class Filings:
    """What the filings say together: who names which text as their employer."""

    def __init__(self, names: pd.Series, employers: pd.Series) -> None:
        """Count the different filers who name each employer text."""
        keys = employers.map(employer_rule_key)
        filed = keys.ne("")
        self.filers = names[filed].groupby(keys[filed].to_numpy()).nunique().to_dict()
        self.own = set(zip(names[filed], keys[filed]))

    def level(self, text: str, min_filers: int = 3) -> str:
        """Return the evidence level of a text; 'frequent' when enough different filers name it."""
        key = employer_rule_key(text)
        level = company_evidence_level(text)
        if (
            level in ("unknown", "named_org")
            and self.filers.get(key, 0) >= min_filers
            and len(re.sub(r"[^A-Z]", "", key)) >= 3
            and is_real_employer(key)
            and not _has_only_common_words(key)
        ):
            return "frequent"
        return level


def has_legal_form_token(values: pd.Series) -> pd.Series:
    """Find a legal form anywhere in the text, as a name filed LAST, FIRST may carry one."""
    return values.map(lambda value: any(word in _LEGAL_TOKENS for word in employer_rule_key(value).split()))
