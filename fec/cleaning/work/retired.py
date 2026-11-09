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

import numpy as np
import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.cleaning.work.company import COMPANY_LEVELS, company_evidence
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.config.constants import RETIRED_TYPO_EMPLOYERS, STATUS_WORDS

_RETIRED_WORDS = frozenset({"RETIRED", "RETIRE", "RETIREE", "RETIRD"}) | RETIRED_TYPO_EMPLOYERS
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
_NO_ROLE = frozenset({"I'M", "IM", "I AM", "SELF", "ME", "MILITARY", "FROM", "NOW", "FULLY", "HAPPILY"})
_NOTHING = _NO_ROLE | STATUS_WORDS


def is_retired_word(word: str) -> bool:
    """RETIRED, a known variant, or one letter away from it."""
    if word in _RETIRED_WORDS:
        return True
    return 6 <= len(word) <= 8 and word.isalpha() and levenshtein(word, "RETIRED") == 1


def split_retired(value) -> tuple[bool, str]:
    """(retired, rest): whether the text says retired, and what else it names."""
    if not isinstance(value, str):
        return False, ""
    upper = " ".join(value.upper().replace(".", " ").split())
    if not upper:
        return False, ""
    upper = _GLUED_RE.sub(r"RETIRED \1", upper)
    match = _PREFIX_RE.match(upper)
    if match and is_retired_word(match.group("word")):
        return True, _tidy(upper[match.end():])
    match = _SUFFIX_RE.search(upper)
    if match and match.start() > 0 and is_retired_word(match.group("word")):
        head = _tidy(upper[:match.start()])
        if _NAME_BEFORE_RE.search(head):
            return False, upper  # "BOARD OF RETIRED ...": a truncated name
        return True, head
    words = _WORD_RE.findall(upper)
    if not words or all(not is_retired_word(w) for w in words):
        return False, upper
    # RETIRED inside one part of the text: "OFFICE MANAGER, RETIRED NURSE".
    # The parts before it name the current role; the retired part is a
    # former one and goes.
    parts = [p for p in _PART_RE.split(upper) if p.strip()]
    current = [p for p in parts if not any(is_retired_word(w) for w in _WORD_RE.findall(p))]
    if current and len(current) < len(parts):
        role = _tidy(" ".join(current))
        # A status word (HOMEMAKER) is a current role too.
        if role not in _NO_ROLE and len(re.sub(r"[^A-Z]", "", role)) >= 3:
            return False, role
    kept = re.sub(r"\b[A-Z']+\b", lambda m: "" if is_retired_word(m.group()) else m.group(), upper)
    return True, _tidy(kept)


def _plain(value) -> str:
    """The text as split_retired reads it before any word is removed."""
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
    return not rest or rest in _NOTHING or len(re.sub(r"[^A-Z]", "", rest)) < 3


def settle_retired_fields(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Read RETIRED in both work fields; keep the former job and company.

    A field without the marker is left as filed: it already names the
    profession or the company.
    """
    people = df["is_individual"].astype(bool)
    rows = df.index[people]
    emp = df.loc[rows, "contributor_employer"]
    occ = df.loc[rows, "contributor_occupation"]
    emp_split = pd.DataFrame(emp.map(split_retired).tolist(), index=rows, columns=["retired", "rest"])
    occ_split = pd.DataFrame(occ.map(split_retired).tolist(), index=rows, columns=["retired", "rest"])
    retired = emp_split["retired"] | occ_split["retired"]
    # Not retired, but a former part was dropped: OFFICE MANAGER, RETIRED NURSE.
    current = ~retired & occ_split["rest"].ne(occ.map(_plain)) & occ_split["rest"].ne("")
    df.loc[rows[current], "contributor_occupation"] = occ_split.loc[current, "rest"]
    if not retired.any():
        return df, int(current.sum())
    hit = rows[retired]
    if "previous_employer" not in df.columns:
        df["previous_employer"] = pd.Series(dtype="object", index=df.index)
    # The donor step reads this flag once the donor's filings are matched.
    if "_retired_marker" not in df.columns:
        df["_retired_marker"] = False
    df.loc[hit, "_retired_marker"] = True

    # What a marker-bearing field names once the marker is removed.
    emp_rest = emp_split.loc[hit, "rest"].where(emp_split.loc[hit, "retired"], "")
    occ_rest = occ_split.loc[hit, "rest"].where(occ_split.loc[hit, "retired"], "")
    emp_rest = emp_rest.where(~emp_rest.map(_names_nothing), "")
    occ_rest = occ_rest.where(~occ_rest.map(_names_nothing), "")

    emp_is_job = emp_rest.ne("") & emp_rest.map(is_job_title)
    emp_level = company_evidence(emp_rest)
    emp_is_company = emp_rest.ne("") & ~emp_is_job & (
        emp_level.isin(COMPANY_LEVELS) | (emp_level.eq("unknown") & emp_rest.map(is_real_employer))
    )
    occ_is_company = occ_rest.ne("") & company_evidence(occ_rest).isin(("legal_form", "reviewed", "frequent"))

    # The former profession: the occupation's own words, else a job from the
    # employer box; a filed occupation without the marker stays as it is.
    occ_text = occ.loc[hit].fillna("").astype(str).str.strip()
    occ_marked = occ_split.loc[hit, "retired"]
    profession = occ_rest.where(~occ_is_company, "")
    profession = profession.where(profession.ne("") | occ_marked | occ_text.eq(""), occ_text)
    profession = profession.where(profession.ne(""), emp_rest.where(emp_is_job, ""))
    # An employer box without the marker: a job word is the profession
    # (PHYSICIAN / RETIRED); a company stays for the donor step, which moves
    # it to previous_employer once the donor's filings are matched.
    emp_text = emp.loc[hit].fillna("").astype(str).str.strip()
    unmarked = ~emp_split.loc[hit, "retired"] & emp_text.ne("")
    unmarked_job = unmarked & emp_text.str.upper().map(is_job_title)
    profession = profession.where(profession.ne("") | ~unmarked_job, emp_text.str.upper())
    company = emp_rest.where(emp_is_company, "")
    company = company.where(company.ne(""), occ_rest.where(occ_is_company, ""))
    keep_employer = unmarked & ~unmarked_job & emp_text.map(is_real_employer)

    df.loc[hit[~keep_employer], "contributor_employer"] = "RETIRED"
    df.loc[hit, "contributor_occupation"] = profession.where(profession.ne(""), "RETIRED")
    previous = df.loc[hit, "previous_employer"].fillna("").astype(str).str.strip()
    fill = company.ne("") & previous.eq("")
    df.loc[hit[fill], "previous_employer"] = company[fill]
    if "occupation_category" in df.columns:
        df.loc[hit, "occupation_category"] = np.where(profession.ne(""), df.loc[hit, "occupation_category"], "RETIRED")
    return df, len(hit) + int(current.sum())
