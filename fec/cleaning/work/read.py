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
from fec.cleaning.work.company import COMPANY_LEVELS, Filings, company_key, evidence
from fec.cleaning.work.employer_aliases import documented_employer_name, reviewed_employer_name
from fec.cleaning.work.normalize import EMPLOYER_STATUS_TEXT, _categorize, _normalize_text
from fec.cleaning.work.retired import read_retired
from fec.cleaning.work.self_employed import firm_after_self_marker, split_self_marker
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.cleaning.work.status_words import status_word
from fec.cleaning.work.style import normalize_occupation_style
from fec.config.constants import JUNK_EMPLOYER_RE, OK_SHORT_EMPLOYERS, REFUSAL_EMPLOYERS, SKIP_EMPLOYERS, STATUS_WORDS
from fec.config.data import MISSING_VALUES
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    LEGAL_SUFFIX_RE,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP,
    SELF_EMPLOYMENT_WORDS,
)
from fec.config.occupation_rules.rules import (
    CATEGORY_OVERRIDES,
    EMPLOYER_FROM_OCCUPATION,
    EMPLOYER_NORMALIZE,
    FINAL_JUNK_EMPLOYERS,
    FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS,
    HOMEMAKER_EMPLOYER_VALUES,
    OCCUPATION_KEYWORDS,
    OCCUPATION_NORMALIZE,
    SWAP_JOB_TITLES,
    TEXT_CORRECTIONS,
    WEB_ARTIFACT_OCCUPATIONS,
)
from fec.config.tables import words

_JOBS = OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
_SWAP_JOBS = SWAP_JOB_TITLES | OCCUPATION_KEYWORDS | _JOBS
_JOB_WORDS = words("job_words")
_GENERIC_SELF_TAILS = words("generic_self_tails")
_PLACEHOLDER_WORDS = FINAL_JUNK_EMPLOYERS | FINAL_NULL_EMPLOYERS | REFUSAL_EMPLOYERS
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
# A role, then the company: "PRESIDENT, ACME INC", "CEO AT ACME LLC".
_ROLE_THEN_COMPANY_RE = re.compile(r"^(?P<role>[A-Z][A-Z .&-]*?)\s*(?:,|/|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$")


def names_no_employer(text: str, *, also_words: bool = True) -> bool:
    """Tell whether a box names nothing.

    That is a number, an email, X's, symbols, a date or a short code; with
    words, also a placeholder or a refusal.
    """
    return bool(text) and (
        "@" in text
        or bool(re.fullmatch(r"X+", text))
        or bool(_JUNK_RE.match(text))
        or (len(text) <= 2 and text not in OK_SHORT_EMPLOYERS and text not in MISSING_VALUES)
        or (also_words and " ".join(text.split()) in _PLACEHOLDER_WORDS)
    )


def settle(e: str, o: str, name: str, first: str, last: str, filings: Filings) -> tuple[str, str]:
    """Read the employer and the occupation of one filing together."""
    legal_form = bool(LEGAL_SUFFIX_RE.search(e))
    e = e.removeprefix("--CHOOSE--").strip()

    # Status words.
    if status_word(e) == "SELF-EMPLOYED":
        e = "SELF-EMPLOYED"
    if e in HOMEMAKER_EMPLOYER_VALUES:
        e, o = "NOT EMPLOYED", ("HOMEMAKER" if o in ("", "NOT DISCLOSED", "NOT EMPLOYED") else o)
    e = reviewed_employer_name(e) if e else ""

    # A box that names nothing.
    if (
        len(o) <= 2
        and o not in FINAL_SHORT_OCCUPATIONS
        or _JUNK_RE.match(o)
        or o in WEB_ARTIFACT_OCCUPATIONS
        or "@" in o
    ):
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
    says_self = (
        o == "SELF-EMPLOYED" or bool({e, o} & SELF_EMPLOYMENT_WORDS) or CATEGORY_OVERRIDES.get(e) == "SELF-EMPLOYED"
    )
    if e in _SWAP_JOBS and (o == "SELF-EMPLOYED" or (o not in _SWAP_JOBS and filings.level(o, 1) in COMPANY_LEVELS)):
        e, o = o, TEXT_CORRECTIONS.get(e, e)  # ATTORNEY | WOLF RIFKIN LLP
    elif e in _JOBS or (e in SECTOR_AS_EMPLOYER and e == "EXECUTIVE"):
        if o in ("", "SELF-EMPLOYED", "RETIRED", "NOT EMPLOYED") or (o in SKIP_EMPLOYERS and o not in STATUS_WORDS):
            status = o if o in ("RETIRED", "NOT EMPLOYED") else "SELF-EMPLOYED" if says_self else ""
            e, o = status, e  # ATTORNEY | (empty)
        else:
            e = "SELF-EMPLOYED" if says_self else ""  # CONSULTANT | INVESTOR
    elif e in SECTOR_AS_EMPLOYER:
        e = ""  # HEALTHCARE | NURSE
    # The donor's own name is no employer, unless it was filed with a legal form.
    own = name_words(first) | name_words(last) if name_words(first) and name_words(last) else None
    if e not in SKIP_EMPLOYERS and name_words(e) == own and not legal_form:
        e = "SELF-EMPLOYED"

    # A company in the occupation box: the role stays, the company moves to an open employer.
    level = filings.level(o, 2) if o else ""
    own_employer = (
        (name, company_key(o)) in filings.own
        and company_key(o) != company_key(e)
        and not is_job_title(o)
        and o not in _JOBS
    )
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
    if o and evidence(o) not in ("legal_form", "reviewed"):
        o = normalize_occupation_style(o)
    return e, o


def companies_named(texts: pd.Series) -> pd.Series:
    """Return the company each employer text names, read as the employer box is; '' for a status, a job or nothing."""
    filed = pd.Series(texts.dropna().astype(str).unique())
    spelled = _normalize_text(filed, EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT).fillna("")
    alone = Filings(pd.Series(dtype=str), pd.Series(dtype=str))
    read = (settle(text, "", "", "", "", alone)[0] for text in spelled)
    named = {text: (company if is_real_employer(company) else "") for text, company in zip(filed, read)}
    return texts.map(named).fillna("")


def company_named(text: str) -> str:
    return companies_named(pd.Series([text])).iloc[0]


def read_work(df: pd.DataFrame, people: pd.Series, filed_employers: pd.Series, filed_occupations: pd.Series) -> None:
    """Set the work fields of these people from the employer and occupation they filed."""
    rows = df.index[people]
    e = _normalize_text(filed_employers.loc[rows], EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT)
    o = _normalize_text(filed_occupations.loc[rows], OCCUPATION_NORMALIZE)
    # RETIRED, read in both boxes (see fec.cleaning.work.retired).
    filed = list(zip(e.fillna("").astype(str), o.fillna("").astype(str)))
    retired = {pair: read_retired(*pair) for pair in set(filed)}
    work = pd.DataFrame([retired[pair] for pair in filed], index=rows, columns=["e", "o", "previous", "said_retired"])

    def text(column: str) -> pd.Series:
        return df.loc[rows, column].fillna("").astype(str).str.strip().str.upper()

    names = text("contributor_name")
    filings = Filings(names, work["e"])
    keys = list(zip(work["e"], work["o"], names, text("contributor_first_name"), text("contributor_last_name")))
    read = {key: settle(*key, filings) for key in set(keys)}
    df.loc[rows, "contributor_employer"] = pd.Series([read[k][0] or np.nan for k in keys], index=rows, dtype=object)
    occupation = pd.Series([read[k][1] or np.nan for k in keys], index=rows, dtype=object)
    df.loc[rows, "contributor_occupation"] = occupation
    df.loc[rows, "occupation_category"] = _categorize(occupation)
    df.loc[rows, "previous_employer"] = work["previous"].replace("", np.nan).astype(object)
    df["_retired_marker"] = work["said_retired"].reindex(df.index, fill_value=False)
