"""One reading of a filing's work fields.

The spelling of each box is read first. Then the two boxes are settled
together, one rule after another: a status word, a job in the employer
box, a company in the occupation box, a box that names nothing, the
donor's own name. What a filing means depends on the filed pair and the
filer's name, so each distinct combination is read once.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import name_words
from fec.cleaning.work.company import COMPANY_LEVELS, _only_common_words, _text_evidence, company_key
from fec.cleaning.work.employer_aliases import documented_employer_name, reviewed_employer_name
from fec.cleaning.work.normalize import EMPLOYER_STATUS_TEXT, _categorize, _normalize_text
from fec.cleaning.work.previous import firm_after_self_marker, split_self_marker
from fec.cleaning.work.retired import settle_retired_fields
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.cleaning.work.status_words import status_word
from fec.cleaning.work.style import normalize_occupation_style
from fec.config.constants import JUNK_EMPLOYER_RE, OK_SHORT_EMPLOYERS, REFUSAL_EMPLOYERS, SKIP_EMPLOYERS, STATUS_WORDS
from fec.config.data import MISSING_VALUES
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER, LEGAL_SUFFIX_RE, OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER, SECTOR_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP, SELF_EMPLOYMENT_WORDS,
)
from fec.config.occupation_rules.rules import (
    CATEGORY_OVERRIDES, EMPLOYER_FROM_OCCUPATION, EMPLOYER_NORMALIZE, FINAL_JUNK_EMPLOYERS, FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS, HOMEMAKER_EMPLOYER_VALUES, OCCUPATION_KEYWORDS, OCCUPATION_NORMALIZE, SWAP_JOB_TITLES,
    TEXT_CORRECTIONS, WEB_ARTIFACT_OCCUPATIONS,
)
from fec.config.tables import words

_JOBS = OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
_SWAP_JOBS = SWAP_JOB_TITLES | OCCUPATION_KEYWORDS | _JOBS
_JOB_WORDS = words("job_words")
_GENERIC_SELF_TAILS = words("generic_self_tails")
_PLACEHOLDER_WORDS = FINAL_JUNK_EMPLOYERS | FINAL_NULL_EMPLOYERS | REFUSAL_EMPLOYERS
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
# A role, then the company: "PRESIDENT, ACME INC", "CEO AT ACME LLC".
_ROLE_THEN_COMPANY_RE = re.compile(r'^(?P<role>[A-Z][A-Z .&-]*?)\s*(?:,|/|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$')


class Filings:
    """What the filings say together: who names which text as their employer."""

    def __init__(self, names: pd.Series, employers: pd.Series):
        keys = employers.map(company_key)
        filed = keys.ne("")
        self.filers = names[filed].groupby(keys[filed].to_numpy()).nunique().to_dict()
        self.own = set(zip(names[filed], keys[filed]))

    def level(self, text: str, min_filers: int) -> str:
        """How surely a text names a company (see fec.cleaning.work.company)."""
        key = company_key(text)
        level = _text_evidence(key, "&" in text)
        if (level in ("unknown", "named_org") and self.filers.get(key, 0) >= min_filers
                and len(re.sub(r"[^A-Z]", "", key)) >= 3 and is_real_employer(key) and not _only_common_words(key)):
            return "frequent"
        return level


def names_no_employer(text: str, *, also_words: bool = True) -> bool:
    """A number, an email, X's, symbols, a date or a short code; with words, also a placeholder or refusal."""
    return bool(text) and (
        "@" in text or bool(re.fullmatch(r"X+", text)) or bool(_JUNK_RE.match(text))
        or (len(text) <= 2 and text not in OK_SHORT_EMPLOYERS and text not in MISSING_VALUES)
        or (also_words and " ".join(text.split()) in _PLACEHOLDER_WORDS))


def settle(e: str, o: str, name: str, first: str, last: str, filings: Filings) -> tuple[str, str]:
    """The employer and the occupation of one filing, read together."""
    legal_form = bool(LEGAL_SUFFIX_RE.search(e))
    e = e.removeprefix("--CHOOSE--").strip()

    # Status words.
    if status_word(e) == "SELF-EMPLOYED":
        e = "SELF-EMPLOYED"
    if e in HOMEMAKER_EMPLOYER_VALUES:
        e, o = "NOT EMPLOYED", ("HOMEMAKER" if o in ("", "NOT DISCLOSED", "NOT EMPLOYED") else o)
    e = reviewed_employer_name(e) if e else ""

    # A box that names nothing.
    if len(o) <= 2 and o not in FINAL_SHORT_OCCUPATIONS or _JUNK_RE.match(o) or o in WEB_ARTIFACT_OCCUPATIONS or "@" in o:
        o = ""
    o = re.sub(r"^([A-Z ]+?)\s+\d+$", r"\1", o)
    if names_no_employer(e):
        if company_key(o) == company_key(e) and o not in CATEGORY_OVERRIDES:
            o = ""
        e = documented_employer_name(e) or ""

    # SELF - ACME LLC: the firm stays; a trade beside the marker fills an empty occupation.
    marked, rest = split_self_marker(e) if e != "SELF-EMPLOYED" else (False, "")
    if marked:
        firm = "" if rest in _GENERIC_SELF_TAILS else firm_after_self_marker(rest)
        e, o = firm or "SELF-EMPLOYED", o or (rest if not firm and is_job_title(rest) else "")

    # One text in both boxes: a job leaves the employer unknown, a company the occupation.
    if e and e not in SKIP_EMPLOYERS and e != "EMPLOYED" and company_key(e) == company_key(o):
        if o in _JOB_WORDS or is_job_title(o):
            e = ""
        else:
            o = "SELF-EMPLOYED" if first and last and (first in o or last in o) else ""

    # A job in the employer box.
    says_self = o == "SELF-EMPLOYED" or bool({e, o} & SELF_EMPLOYMENT_WORDS) or CATEGORY_OVERRIDES.get(e) == "SELF-EMPLOYED"
    if e in _SWAP_JOBS and (o == "SELF-EMPLOYED" or (o not in _SWAP_JOBS and filings.level(o, 1) in COMPANY_LEVELS)):
        e, o = o, TEXT_CORRECTIONS.get(e, e)                      # ATTORNEY | WOLF RIFKIN LLP
    elif e in _JOBS or (e in SECTOR_AS_EMPLOYER and e == "EXECUTIVE"):
        if o in ("", "SELF-EMPLOYED", "RETIRED", "NOT EMPLOYED") or (o in SKIP_EMPLOYERS and o not in STATUS_WORDS):
            status = o if o in ("RETIRED", "NOT EMPLOYED") else "SELF-EMPLOYED" if says_self else ""
            e, o = status, e                                      # ATTORNEY | (empty)
        else:
            e = "SELF-EMPLOYED" if says_self else ""              # CONSULTANT | INVESTOR
    elif e in SECTOR_AS_EMPLOYER:
        e = ""                                                    # HEALTHCARE | NURSE
    # The donor's own name is no employer, unless it was filed with a legal form.
    own = name_words(first) | name_words(last) if name_words(first) and name_words(last) else None
    if e not in SKIP_EMPLOYERS and name_words(e) == own and not legal_form:
        e = "SELF-EMPLOYED"

    # A company in the occupation box: the role stays, the company moves to an open employer.
    level = filings.level(o, 2) if o else ""
    own_employer = (name, company_key(o)) in filings.own and company_key(o) != company_key(e) and not is_job_title(o) and o not in _JOBS
    if level in ("legal_form", "reviewed", "frequent") or (level == "named_org" and (own_employer or not e)):
        parts = _ROLE_THEN_COMPANY_RE.match(o)
        role = parts["role"].strip() if parts else ""
        role, company = (role, parts["company"].strip()) if role in _JOBS or is_job_title(role) else ("", o)
        e, o = (company if e in ("", "SELF-EMPLOYED") else e), role

    # A status in one box fills the other.
    if e and e != "SELF-EMPLOYED":
        e = reviewed_employer_name(e)
    if not o and e in ("RETIRED", "SELF-EMPLOYED", "NOT EMPLOYED"):
        o = e
    if not e:
        e = EMPLOYER_FROM_OCCUPATION.get(o, "")
    if o and _text_evidence(company_key(o), "&" in o) not in ("legal_form", "reviewed"):
        o = normalize_occupation_style(o)
    return e, o


def read_work(df: pd.DataFrame, people: pd.Series, filed_employers: pd.Series, filed_occupations: pd.Series) -> None:
    """Set the work fields of these people from the employer and occupation they filed."""
    rows = df.index[people]
    e = _normalize_text(filed_employers.loc[rows], EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT)
    o = _normalize_text(filed_occupations.loc[rows], OCCUPATION_NORMALIZE)
    # RETIRED, read in both boxes (see fec.cleaning.work.retired).
    work = pd.DataFrame({"contributor_employer": e, "contributor_occupation": o, "is_individual": True,
                         "previous_employer": pd.Series(np.nan, index=rows, dtype=object), "_retired_marker": False})
    settle_retired_fields(work)
    work = work.fillna({"contributor_employer": "", "contributor_occupation": ""})

    def text(column: str) -> pd.Series:
        return df.loc[rows, column].fillna("").astype(str).str.strip().str.upper()

    names = text("contributor_name")
    filings = Filings(names, work["contributor_employer"])
    keys = list(zip(work["contributor_employer"], work["contributor_occupation"], names,
                    text("contributor_first_name"), text("contributor_last_name")))
    read = {key: settle(*key, filings) for key in set(keys)}
    df.loc[rows, "contributor_employer"] = pd.Series([read[k][0] or np.nan for k in keys], index=rows, dtype=object)
    occupation = pd.Series([read[k][1] or np.nan for k in keys], index=rows, dtype=object)
    df.loc[rows, "contributor_occupation"] = occupation
    df.loc[rows, "occupation_category"] = _categorize(occupation)
    df.loc[rows, "previous_employer"] = work["previous_employer"]
    df["_retired_marker"] = work["_retired_marker"].reindex(df.index, fill_value=False)
