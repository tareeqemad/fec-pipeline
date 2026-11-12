"""SELF written beside something else in the employer box: SELF - ACME LLC, CONSULTANT (SELF-EMPLOYED)."""

import re

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, company_evidence_level
from fec.cleaning.work.employer_status import collapse_doubled_letters, is_job_title, is_real_employer
from fec.common.text import upper_dots_as_spaces
from fec.vocabulary.not_employers import OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER
from fec.vocabulary.tables import ordered_words, words

# SELF-EMPLOYED written after a name or trade.
_TAIL_RE = re.compile(r"[-,/(:]\s*SELF[\s-]*EMPLOY\w*\W*$")
# SELF written before a name or trade: "SELF - ACME LLC", "SELF EMPLOYED; DENTIST".
_PREFIX_RE = re.compile(r"^SELF(?:[\s-]*EMPLOY\w*)?(?:\s+AND)?[\s,;:/()-]+(?=\S)")
# A word making the text beside the marker a firm.
_FIRM_WORD_RE = re.compile(r"\b(?:" + "|".join(ordered_words("firm_words")) + r")\b")
_PERSON_TITLES = words("person_titles")


def split_self_marker(value: object) -> tuple[bool, str]:
    """(self_employed, rest): a SELF marker before or after the text, and what else it names."""
    if not isinstance(value, str):
        return False, ""
    upper = upper_dots_as_spaces(value)
    if match := _PREFIX_RE.match(upper):
        return True, upper[match.end() :].strip(" ,;:/()-")
    if match := _TAIL_RE.search(upper):
        return True, upper[: match.start()].strip(" ,;:/()-")
    letters = collapse_doubled_letters(re.sub(r"[^A-Z]", "", upper))
    marked = upper.startswith("SELF:") or letters.startswith("SELFEMPLOY") or upper in ("SELF", "SSELF", "SELFF")
    return marked, ""


def firm_after_self_marker(rest: str) -> str:
    """Return the firm a SELF marker was written beside (ACME LLC), or '' for a trade (DENTIST) or nothing."""
    if not rest or rest in ROLE_AS_EMPLOYER or rest in OCCUPATION_AS_EMPLOYER or is_job_title(rest):
        return ""
    level = company_evidence_level(rest)
    if level in COMPANY_LEVELS or _FIRM_WORD_RE.search(rest):
        return rest
    # DR RICHARD ALPERIN beside SELF is the person, not a firm.
    named = (
        level == "unknown"
        and len(rest.split()) >= 2
        and is_real_employer(rest)
        and not _PERSON_TITLES & set(rest.split())
    )
    return rest if named else ""
