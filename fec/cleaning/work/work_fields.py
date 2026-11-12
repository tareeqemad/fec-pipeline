"""A filing's employer and occupation, spelled first, then settled together one rule after another.

Each distinct (employer, occupation, filer name) is read once.
"""

import re

import numpy as np
import pandas as pd

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, Filings, company_evidence_level
from fec.cleaning.work.company_names import documented_employer_name, employer_rule_key, reviewed_employer_name
from fec.cleaning.work.employer_status import is_job_title, is_real_employer
from fec.cleaning.work.occupation_categories import lookup_categories
from fec.cleaning.work.occupation_style import normalize_occupation_style
from fec.cleaning.work.retired import read_retired
from fec.cleaning.work.self_employed import firm_after_self_marker, split_self_marker
from fec.cleaning.work.status_spellings import status_word
from fec.cleaning.work.text_cleanup import clean_box_text
from fec.common.text import name_words_without_initials, upper_text
from fec.vocabulary.names import MISSING_VALUES
from fec.vocabulary.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    LEGAL_SUFFIX_RE,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP,
    SELF_EMPLOYMENT_WORDS,
)
from fec.vocabulary.occupations import (
    EMPLOYER_FROM_OCCUPATION,
    FINAL_SHORT_OCCUPATIONS,
    HOMEMAKER_EMPLOYER_VALUES,
    OCCUPATION_CATEGORIES,
    PLACEHOLDER_EMPLOYERS,
    SWAP_JOB_TITLES,
    TEXT_CORRECTIONS,
    WEB_ARTIFACT_OCCUPATIONS,
)
from fec.vocabulary.statuses import JUNK_EMPLOYER_RE, OK_SHORT_EMPLOYERS, SKIP_EMPLOYERS, STATUS_WORDS
from fec.vocabulary.tables import words

_JOBS = OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
_SWAP_JOBS = SWAP_JOB_TITLES | _JOBS
_JOB_WORDS = words("job_words")
_GENERIC_SELF_TAILS = words("generic_self_tails")
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
# A role, then the company: "PRESIDENT, ACME INC", "CEO AT ACME LLC".
_ROLE_THEN_COMPANY_RE = re.compile(r"^(?P<role>[A-Z][A-Z .&-]*?)\s*(?:,|/|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$")


def names_no_employer(text: str) -> bool:
    """Tell whether a box names nothing: a number, an email, X's, symbols, a date, a short code or a refusal."""
    return bool(text) and (
        "@" in text
        or bool(re.fullmatch(r"X+", text))
        or bool(_JUNK_RE.match(text))
        or (len(text) <= 2 and text not in OK_SHORT_EMPLOYERS and text not in MISSING_VALUES)
        or " ".join(text.split()) in PLACEHOLDER_EMPLOYERS
    )


def _read_status_words(employer: str, occupation: str) -> tuple[str, str]:
    if status_word(employer) == "SELF-EMPLOYED":
        employer = "SELF-EMPLOYED"
    if employer in HOMEMAKER_EMPLOYER_VALUES:
        employer = "NOT EMPLOYED"
        if occupation in ("", "NOT DISCLOSED", "NOT EMPLOYED"):
            occupation = "HOMEMAKER"
    employer = reviewed_employer_name(employer) if employer else ""
    return employer, occupation


def _is_empty_occupation(occupation: str) -> bool:
    return (
        (len(occupation) <= 2 and occupation not in FINAL_SHORT_OCCUPATIONS)
        or bool(_JUNK_RE.match(occupation))
        or occupation in WEB_ARTIFACT_OCCUPATIONS
        or "@" in occupation
    )


def _clear_boxes_naming_nothing(employer: str, occupation: str) -> tuple[str, str]:
    if _is_empty_occupation(occupation):
        occupation = ""
    occupation = re.sub(r"^([A-Z ]+?)\s+\d+$", r"\1", occupation)
    if names_no_employer(employer):
        if employer_rule_key(occupation) == employer_rule_key(employer) and occupation not in OCCUPATION_CATEGORIES:
            occupation = ""
        employer = documented_employer_name(employer) or ""
    return employer, occupation


def _split_self_employed(employer: str, occupation: str) -> tuple[str, str]:
    marked, rest = split_self_marker(employer)
    if not marked or employer == "SELF-EMPLOYED":
        return employer, occupation
    firm = "" if rest in _GENERIC_SELF_TAILS else firm_after_self_marker(rest)
    if not occupation:
        occupation = rest if not firm and is_job_title(rest) else ""
    return firm or "SELF-EMPLOYED", occupation


def _settle_same_text(employer: str, occupation: str, first: str, last: str) -> tuple[str, str]:
    same_text = employer_rule_key(employer) == employer_rule_key(occupation)
    if not employer or employer in SKIP_EMPLOYERS or employer == "EMPLOYED" or not same_text:
        return employer, occupation
    if occupation in _JOB_WORDS or is_job_title(occupation):
        return "", occupation
    if first and last and (first in occupation or last in occupation):
        return employer, "SELF-EMPLOYED"
    return employer, ""


def _move_job_as_employer(employer: str, occupation: str) -> tuple[str, str]:
    says_self = (
        occupation == "SELF-EMPLOYED"
        or bool({employer, occupation} & SELF_EMPLOYMENT_WORDS)
        or OCCUPATION_CATEGORIES.get(employer) == "SELF-EMPLOYED"
    )
    self_employed = "SELF-EMPLOYED" if says_self else ""
    occupation_open = occupation in ("", "SELF-EMPLOYED", "RETIRED", "NOT EMPLOYED") or (
        occupation in SKIP_EMPLOYERS and occupation not in STATUS_WORDS
    )
    if not occupation_open:
        return self_employed, occupation  # CONSULTANT | INVESTOR
    if occupation in ("RETIRED", "NOT EMPLOYED"):
        return occupation, employer
    return self_employed, employer  # ATTORNEY | (empty)


def _move_job_out_of_employer(employer: str, occupation: str, filings: Filings) -> tuple[str, str]:
    if employer in _SWAP_JOBS and (
        occupation == "SELF-EMPLOYED"
        or (occupation not in _SWAP_JOBS and filings.level(occupation, 1) in COMPANY_LEVELS)
    ):
        return occupation, TEXT_CORRECTIONS.get(employer, employer)  # ATTORNEY | WOLF RIFKIN LLP
    if employer in _JOBS or (employer in SECTOR_AS_EMPLOYER and employer == "EXECUTIVE"):
        return _move_job_as_employer(employer, occupation)
    if employer in SECTOR_AS_EMPLOYER:
        return "", occupation  # HEALTHCARE | NURSE
    return employer, occupation


def _replace_own_name(employer: str, first: str, last: str, legal_form: bool) -> str:
    first_words = name_words_without_initials(first)
    last_words = name_words_without_initials(last)
    own = first_words | last_words if first_words and last_words else None
    if employer not in SKIP_EMPLOYERS and name_words_without_initials(employer) == own and not legal_form:
        return "SELF-EMPLOYED"
    return employer


def _move_company_out_of_occupation(employer: str, occupation: str, name: str, filings: Filings) -> tuple[str, str]:
    level = filings.level(occupation, 2) if occupation else ""
    own_employer = (
        (name, employer_rule_key(occupation)) in filings.own
        and employer_rule_key(occupation) != employer_rule_key(employer)
        and not is_job_title(occupation)
        and occupation not in _JOBS
    )
    names_company = level in ("legal_form", "reviewed", "frequent") or (
        level == "named_org" and (own_employer or not employer)
    )
    if not names_company:
        return employer, occupation
    parts = _ROLE_THEN_COMPANY_RE.match(occupation)
    role = parts["role"].strip() if parts else ""
    if role in _JOBS or is_job_title(role):
        company = parts["company"].strip()
    else:
        role, company = "", occupation
    if employer in ("", "SELF-EMPLOYED"):
        employer = company
    elif role and is_real_employer(employer) and employer_rule_key(company) != employer_rule_key(employer):
        # The employer box names another company: the occupation keeps both the role and its company.
        return employer, occupation
    return employer, role


def _fill_from_status(employer: str, occupation: str) -> tuple[str, str]:
    if employer and employer != "SELF-EMPLOYED":
        employer = reviewed_employer_name(employer)
    # SELF-EMPLOYED says how a donor works, not what they do: it never fills an empty occupation.
    if not occupation and employer in ("RETIRED", "NOT EMPLOYED"):
        occupation = employer
    if not employer:
        employer = EMPLOYER_FROM_OCCUPATION.get(occupation, "")
    if occupation and company_evidence_level(occupation) not in ("legal_form", "reviewed"):
        occupation = normalize_occupation_style(occupation)
    return employer, occupation


def settle_work_pair(
    employer: str, occupation: str, name: str, first: str, last: str, filings: Filings
) -> tuple[str, str]:
    """Read the employer and the occupation of one filing together."""
    legal_form = bool(LEGAL_SUFFIX_RE.search(employer))
    employer = employer.removeprefix("--CHOOSE--").strip()
    employer, occupation = _read_status_words(employer, occupation)
    employer, occupation = _clear_boxes_naming_nothing(employer, occupation)
    employer, occupation = _split_self_employed(employer, occupation)
    employer, occupation = _settle_same_text(employer, occupation, first, last)
    employer, occupation = _move_job_out_of_employer(employer, occupation, filings)
    employer = _replace_own_name(employer, first, last, legal_form)
    employer, occupation = _move_company_out_of_occupation(employer, occupation, name, filings)
    return _fill_from_status(employer, occupation)


def companies_named(texts: pd.Series) -> pd.Series:
    """Return the company each employer text names, read as the employer box is; '' for a status or a job."""
    filed = pd.Series(texts.dropna().astype(str).unique())
    spelled = clean_box_text(filed, employer=True).fillna("")
    alone = Filings(pd.Series(dtype=str), pd.Series(dtype=str))
    read = (settle_work_pair(text, "", "", "", "", alone)[0] for text in spelled)
    named = {text: (company if is_real_employer(company) else "") for text, company in zip(filed, read)}
    return texts.map(named).fillna("")


def company_named(text: str) -> str:
    """Return the company one employer text names, or '' for a status, a job or nothing."""
    return companies_named(pd.Series([text])).iloc[0]


def read_work(df: pd.DataFrame, people: pd.Series, filed_employers: pd.Series, filed_occupations: pd.Series) -> None:
    """Set the work fields of these people from the employer and occupation they filed."""
    rows = df.index[people]
    employers = clean_box_text(filed_employers.loc[rows], employer=True)
    occupations = clean_box_text(filed_occupations.loc[rows], employer=False)
    # RETIRED, read in both boxes (see fec.cleaning.work.retired).
    filed = list(zip(employers.fillna("").astype(str), occupations.fillna("").astype(str)))
    retired = {pair: read_retired(*pair) for pair in set(filed)}
    work = pd.DataFrame([retired[pair] for pair in filed], index=rows, columns=["e", "o", "previous", "said_retired"])
    names = upper_text(df.loc[rows, "contributor_name"])
    filings = Filings(names, work["e"])
    first = upper_text(df.loc[rows, "contributor_first_name"])
    last = upper_text(df.loc[rows, "contributor_last_name"])
    keys = list(zip(work["e"], work["o"], names, first, last))
    read = {key: settle_work_pair(*key, filings) for key in set(keys)}
    df.loc[rows, "contributor_employer"] = pd.Series([read[key][0] or np.nan for key in keys], index=rows, dtype=object)
    occupation = pd.Series([read[key][1] or np.nan for key in keys], index=rows, dtype=object)
    df.loc[rows, "contributor_occupation"] = occupation
    df.loc[rows, "occupation_category"] = lookup_categories(occupation)
    df.loc[rows, "previous_employer"] = work["previous"].replace("", np.nan).astype(object)
    df["_retired_marker"] = work["said_retired"].reindex(df.index, fill_value=False)
