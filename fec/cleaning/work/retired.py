"""RETIRED in the work fields, in any spelling or place: RETIREE, TETIRED, RETIRED TEACHER, UCLA RETIRED.

The employer becomes RETIRED, the occupation the former profession, previous_employer the former company.
RETIRED after a current role (OFFICE MANAGER, RETIRED NURSE) qualifies only its own part: the filer still works.
"""

import re

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, company_evidence_level
from fec.cleaning.work.employer_status import is_job_title, is_real_employer
from fec.cleaning.work.status_spellings import status_word
from fec.vocabulary.statuses import STATUS_WORDS
from fec.vocabulary.tables import words

_RETIRED_WORDS = words("retired_words")
_WORD_RE = re.compile(r"[A-Z][A-Z']*")
# RETIREDPHYSICIAN: the space was dropped.
_GLUED_RE = re.compile(r"^RETIRED([A-Z]{4,})$")

# SEMI-RETIRED, MOSTLY RETIRED, NOW RETIRED: an optional word before the marker.
_PARTLY = r"(?:(?:SEMI|MOSTLY|PARTIALLY|PARTLY|NOW)[\s-]?)?"

# The marker at the start: "RETIRED FROM ACME", "(RETIRED) ACME", "RETIRED - ACME".
_PREFIX_RE = re.compile(
    rf"""
    ^ \(?               # an optional opening bracket
    {_PARTLY}
    (?P<word>[A-Z]+)    # the word tested: RETIRED, a variant or a slip
    \)?                 # an optional closing bracket
    (?:\s+FROM\b)?      # RETIRED FROM
    [\s,;:/()-]*        # the separators before the rest
    """,
    re.VERBOSE,
)

# The marker at the end: "ACME RETIRED", "PHYSICIAN, RETIRED", "DENTIST (RETIRED)".
_SUFFIX_RE = re.compile(
    rf"""
    [\s,;:/(-]*         # the separators after the rest
    \(? \s*             # an optional opening bracket
    {_PARTLY}
    (?P<word>[A-Z]+)    # the word tested: RETIRED, a variant or a slip
    \s* \)?             # an optional closing bracket
    \.? $               # an optional final period
    """,
    re.VERBOSE,
)
# Keep RETIRED when it ends a truncated organisation name.
_NAME_BEFORE_RE = re.compile(r"\b(?:OF|FOR|THE|AND|&)$")
# Parts of one occupation text: "OFFICE MANAGER, RETIRED NURSE", "VOLUNTEER (RETIRED ATTORNEY)"
_PART_RE = re.compile(r"\s*(?:[,;:()]|/|\s-\s)\s*")
# "I'M RETIRED", "SELF RETIRED": the rest names nothing.
_NO_ROLE = words("no_role")
_NOTHING = _NO_ROLE | STATUS_WORDS


def _is_retired_word(word: str) -> bool:
    # RETIRED, a known variant, or a slip away from it.
    return word in _RETIRED_WORDS or (word.isalpha() and status_word(word) == "RETIRED")


def split_retired(value: object) -> tuple[bool, str]:
    """(retired, rest): whether the text says retired (at its start, end, or inside), and what else it names."""
    upper = _to_plain_text(value)
    if not upper:
        return False, ""
    upper = _GLUED_RE.sub(r"RETIRED \1", upper)
    return _retired_at_start(upper) or _retired_at_end(upper) or _retired_inside(upper)


def _has_retired_word(text: str) -> bool:
    return any(_is_retired_word(word) for word in _WORD_RE.findall(text))


def _letter_count(text: str) -> int:
    return sum("A" <= character <= "Z" for character in text)


def _retired_at_start(upper: str) -> tuple[bool, str] | None:
    match = _PREFIX_RE.match(upper)
    if match and _is_retired_word(match.group("word")):
        return True, _tidy(upper[match.end() :])
    return None


def _retired_at_end(upper: str) -> tuple[bool, str] | None:
    match = _SUFFIX_RE.search(upper)
    if not match or match.start() == 0 or not _is_retired_word(match.group("word")):
        return None
    head = _tidy(upper[: match.start()])
    if _NAME_BEFORE_RE.search(head):
        return False, upper  # "BOARD OF RETIRED ...": a truncated name
    return True, head


def _retired_inside(upper: str) -> tuple[bool, str]:
    if not _has_retired_word(upper):
        return False, upper
    # RETIRED inside one part of the text: "OFFICE MANAGER, RETIRED NURSE".
    # The parts without it name the current role; the retired part is a former one and goes.
    parts = [part for part in _PART_RE.split(upper) if part.strip()]
    current = [part for part in parts if not _has_retired_word(part)]
    if current and len(current) < len(parts):
        role = _tidy(" ".join(current))
        # A status word (HOMEMAKER) is a current role too.
        if role not in _NO_ROLE and _letter_count(role) >= 3:
            return False, role
    kept = re.sub(r"\b[A-Z']+\b", _drop_retired_word, upper)
    return True, _tidy(kept)


def _drop_retired_word(match: re.Match) -> str:
    return "" if _is_retired_word(match.group()) else match.group()


def _to_plain_text(value: object) -> str:
    return " ".join(value.upper().replace(".", " ").split()) if isinstance(value, str) else ""


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    text = re.sub(r"\s*([,;:])\s*", r"\1 ", text)  # "A ,B" becomes "A, B"
    text = re.sub(r"\s*([/-])\s*", r"\1", text)  # "A / B" becomes "A/B"
    text = re.sub(r"\(\s+", "(", text).replace("()", "")  # "( A" becomes "(A"; empty brackets go
    text = re.sub(r"\s+\)", ")", text)  # "A )" becomes "A)"
    text = text.strip(" ,;:/-")
    # A bracket left without its pair.
    if text.startswith("(") and ")" not in text:
        text = text[1:]
    if text.endswith(")") and "(" not in text:
        text = text[:-1]
    return text.strip()


def _names_nothing(rest: str) -> bool:
    return not rest or rest in _NOTHING or _letter_count(rest) < 3


def read_retired(employer: str, occupation: str) -> tuple[str, str, str, bool]:
    """(employer, occupation, former company, said retired) of one filed pair; a box without the marker stays."""
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
