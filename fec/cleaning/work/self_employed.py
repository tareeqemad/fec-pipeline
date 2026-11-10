"""SELF written beside something else in the employer box: SELF - ACME LLC, CONSULTANT (SELF-EMPLOYED)."""

from __future__ import annotations

import re

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, evidence
from fec.cleaning.work.employer_status import is_job_title, is_real_employer, single_letters
from fec.vocabulary.not_employers import OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER
from fec.vocabulary.tables import words

# SELF-EMPLOYED written after a name or trade.
_TAIL_RE = re.compile(r"[-,/(:]\s*SELF[\s-]*EMPLOY\w*\W*$")
# SELF written before a name or trade: "SELF - ACME LLC", "SELF EMPLOYED; DENTIST".
_PREFIX_RE = re.compile(r"^SELF(?:[\s-]*EMPLOY\w*)?(?:\s+AND)?[\s,;:/()-]+(?=\S)")
# A word making the text beside the marker a firm.
_FIRM_WORD_RE = re.compile(
    r"\b(?:GROUP|INC|LLC|LLP|LTD|CORP|CORPORATION|CO|COMPANY|ASSOCIATES|PARTNERS"
    r"|FIRM|PC|PA|PLLC|ENTERPRISES|CONSULTING|SERVICES|STUDIO|AGENCY)\b"
)
_PERSON_TITLES = words("person_titles")


def split_self_marker(value: object) -> tuple[bool, str]:
    """(self_employed, rest): a SELF marker before or after the text, and what else it names."""
    if not isinstance(value, str):
        return False, ""
    upper = " ".join(value.upper().replace(".", " ").split())
    if match := _PREFIX_RE.match(upper):
        return True, upper[match.end() :].strip(" ,;:/()-")
    if match := _TAIL_RE.search(upper):
        return True, upper[: match.start()].strip(" ,;:/()-")
    letters = single_letters(re.sub(r"[^A-Z]", "", upper))
    return upper.startswith("SELF:") or letters.startswith("SELFEMPLOY") or upper in ("SELF", "SSELF", "SELFF"), ""


def firm_after_self_marker(rest: str) -> str:
    """Return the firm a SELF marker was written beside, or '' for a trade or nothing.

    A job or field (DENTIST, CONSULTING) describes the self-employment; a
    company name (ACME LLC, ROUND ROCK PERIODONTICS) is the firm.
    """
    if not rest or rest in ROLE_AS_EMPLOYER or rest in OCCUPATION_AS_EMPLOYER or is_job_title(rest):
        return ""
    level = evidence(rest)
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
