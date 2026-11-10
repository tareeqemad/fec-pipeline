"""One reading of RETIRED in the work fields.

A filer writes retirement in many ways: RETIRED, RETIREE, SEMI-RETIRED, a
typo (TETIRED), or RETIRED beside what they used to do (RETIRED TEACHER,
RETIRED FROM ACME, UCLA RETIRED, PHYSICIAN, RETIRED). split_retired reads
them all the same way and returns the retirement marker and whatever else
the text names; settle_retired_fields puts each part where it belongs:

    employer           RETIRED
    occupation         the former profession when one is named, else RETIRED
    previous_employer  the former company when one is named

RETIRED inside the text, after a current role (OFFICE MANAGER, RETIRED
NURSE; VOLUNTEER (RETIRED ATTORNEY); ARBITRATOR/RETIRED JUDGE), qualifies
only its own part: the filer still works, and the role before it is the
occupation. The employer stays as filed.
"""

from __future__ import annotations

import re

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, company_evidence_level
from fec.cleaning.work.employer_status import is_job_title, is_real_employer
from fec.cleaning.work.status_spellings import status_word
from fec.vocabulary.statuses import STATUS_WORDS
from fec.vocabulary.tables import words

_RETIRED_WORDS = words("retired_words")
_PARTLY = r"(?:(?:SEMI|MOSTLY|PARTIALLY|PARTLY|NOW)[\s-]?)?"
_WORD_RE = re.compile(r"[A-Z][A-Z']*")
# RETIREDPHYSICIAN: the space was dropped.
_GLUED_RE = re.compile(r"^RETIRED([A-Z]{4,})$")
# "RETIRED FROM ACME", "(RETIRED) ACME", "RETIRED - ACME"
_PREFIX_RE = re.compile(rf"^\(?{_PARTLY}(?P<word>[A-Z]+)\)?(?:\s+FROM\b)?[\s,;:/()-]*")
# "ACME RETIRED", "PHYSICIAN, RETIRED", "DENTIST (RETIRED)"
_SUFFIX_RE = re.compile(rf"[\s,;:/(-]*\(?\s*{_PARTLY}(?P<word>[A-Z]+)\s*\)?\.?$")
# Keep RETIRED when it ends a truncated organisation name.
_NAME_BEFORE_RE = re.compile(r"\b(?:OF|FOR|THE|AND|&)$")
# Parts of one occupation text: "OFFICE MANAGER, RETIRED NURSE", "VOLUNTEER (RETIRED ATTORNEY)"
_PART_RE = re.compile(r"\s*(?:[,;:()]|/|\s-\s)\s*")
# "I'M RETIRED", "SELF RETIRED": the rest names nothing.
_NO_ROLE = words("no_role")
_NOTHING = _NO_ROLE | STATUS_WORDS


def is_retired_word(word: str) -> bool:
    """RETIRED, a known variant, or a slip away from it."""
    return word in _RETIRED_WORDS or (word.isalpha() and status_word(word) == "RETIRED")


def split_retired(value: object) -> tuple[bool, str]:
    """(retired, rest): whether the text says retired, and what else it names."""
    if not isinstance(value, str):
        return False, ""
    upper = " ".join(value.upper().replace(".", " ").split())
    if not upper:
        return False, ""
    upper = _GLUED_RE.sub(r"RETIRED \1", upper)
    match = _PREFIX_RE.match(upper)
    if match and is_retired_word(match.group("word")):
        return True, _tidy(upper[match.end() :])
    match = _SUFFIX_RE.search(upper)
    if match and match.start() > 0 and is_retired_word(match.group("word")):
        head = _tidy(upper[: match.start()])
        if _NAME_BEFORE_RE.search(head):
            return False, upper  # "BOARD OF RETIRED ...": a truncated name
        return True, head
    words = _WORD_RE.findall(upper)
    if not words or all(not is_retired_word(word) for word in words):
        return False, upper
    # RETIRED inside one part of the text: "OFFICE MANAGER, RETIRED NURSE".
    # The parts before it name the current role; the retired part is a
    # former one and goes.
    parts = [part for part in _PART_RE.split(upper) if part.strip()]
    current = [part for part in parts if not any(is_retired_word(word) for word in _WORD_RE.findall(part))]
    if current and len(current) < len(parts):
        role = _tidy(" ".join(current))
        # A status word (HOMEMAKER) is a current role too.
        if role not in _NO_ROLE and len(re.sub(r"[^A-Z]", "", role)) >= 3:
            return False, role
    kept = re.sub(r"\b[A-Z']+\b", _drop_retired_word, upper)
    return True, _tidy(kept)


def _drop_retired_word(match: re.Match) -> str:
    """Return a matched word, or '' when it is a retired word."""
    return "" if is_retired_word(match.group()) else match.group()


def _to_plain_text(value: object) -> str:
    """Return the text as split_retired reads it before any word is removed."""
    return " ".join(value.upper().replace(".", " ").split()) if isinstance(value, str) else ""


def _tidy(text: str) -> str:
    """Spacing and dangling punctuation after a word was removed."""
    text = " ".join(text.split())
    text = re.sub(r"\s*([,;:])\s*", r"\1 ", text)
    text = re.sub(r"\s*([/-])\s*", r"\1", text)
    text = re.sub(r"\(\s+", "(", text).replace("()", "")
    text = re.sub(r"\s+\)", ")", text).strip(" ,;:/-")
    if text.startswith("(") and ")" not in text:
        text = text[1:]
    if text.endswith(")") and "(" not in text:
        text = text[:-1]
    return text.strip()


def _names_nothing(rest: str) -> bool:
    """Return whether the rest of a text names nothing."""
    return not rest or rest in _NOTHING or len(re.sub(r"[^A-Z]", "", rest)) < 3


def read_retired(employer: str, occupation: str) -> tuple[str, str, str, bool]:
    """(employer, occupation, former company, said retired) of one filed pair.

    A box without the marker is left as filed: it already names the
    profession or the company.
    """
    employer_retired, employer_rest = split_retired(employer)
    occupation_retired, occupation_rest = split_retired(occupation)
    if not (employer_retired or occupation_retired):
        # Not retired, but a former part was dropped: OFFICE MANAGER, RETIRED NURSE.
        part_dropped = occupation_rest and occupation_rest != _to_plain_text(occupation)
        return employer, (occupation_rest if part_dropped else occupation), "", False
    employer_rest = employer_rest if employer_retired and not _names_nothing(employer_rest) else ""
    occupation_rest = occupation_rest if occupation_retired and not _names_nothing(occupation_rest) else ""
    level = company_evidence_level(employer_rest)
    employer_is_job = bool(employer_rest) and is_job_title(employer_rest)
    employer_is_company = (
        bool(employer_rest)
        and not employer_is_job
        and (level in COMPANY_LEVELS or (level == "unknown" and is_real_employer(employer_rest)))
    )
    occupation_level = company_evidence_level(occupation_rest) if occupation_rest else ""
    occupation_is_company = occupation_level in ("legal_form", "reviewed")
    # An employer box without the marker: a job word is the profession (PHYSICIAN / RETIRED);
    # a company stays for the donor step, which moves it to previous_employer.
    unmarked = bool(employer) and not employer_retired
    unmarked_job = unmarked and is_job_title(employer)
    # The former profession: the occupation's own words, else a job from the employer box.
    profession = (
        ("" if occupation_is_company else occupation_rest)
        or ("" if occupation_retired else occupation)
        or (employer_rest if employer_is_job else "")
        or (employer if unmarked_job else "")
    )
    if employer_is_company:
        company = employer_rest
    elif occupation_is_company:
        company = occupation_rest
    else:
        company = ""
    keeps_employer = unmarked and not unmarked_job and is_real_employer(employer)
    return (employer if keeps_employer else "RETIRED"), profession or "RETIRED", company, True
