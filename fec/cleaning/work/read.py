"""One reading of a filing's work fields.

Each field is read on its own first: spelling, status words (retired,
self-employed, not employed, homemaker), placeholders and reviewed company
names. Then the two are settled together: a job in the employer box, a
company in the occupation box, a status in one box that fills the other.
The reading depends on the filed pair only, so each distinct pair is read
once.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import name_words
from fec.cleaning.work.company import COMPANY_LEVELS, _only_common_words, _text_evidence, company_key
from fec.cleaning.work.employer_aliases import documented_employer_name, employer_rule_key, reviewed_employer_name
from fec.cleaning.work.normalize import EMPLOYER_STATUS_TEXT, _categorize, _classify_committee_names, _normalize_text
from fec.cleaning.work.previous import firm_after_self_marker, split_self_marker
from fec.cleaning.work.retired import settle_retired_fields
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.cleaning.work.status_words import status_word
from fec.cleaning.work.style import normalize_occupation_style
from fec.config.constants import (
    JUNK_EMPLOYER_RE, OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS, REFUSAL_EMPLOYERS, SKIP_EMPLOYERS, STATUS_CATEGORIES, STATUS_SLIPS_BEYOND_RULE, STATUS_WORDS,
)
from fec.config.data import MISSING_VALUES
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER, LEGAL_SUFFIX_RE, NOT_REAL_EMPLOYER, OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER, SELF_EMPLOYED_OCC_AS_EMP, SELF_EMPLOYMENT_WORDS,
)
from fec.config.occupation_rules.rules import (
    CATEGORY_OVERRIDES, EMPLOYER_FROM_OCCUPATION, EMPLOYER_NORMALIZE, FINAL_JUNK_EMPLOYERS, FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS,
    HOMEMAKER_EMPLOYER_VALUES, OCCUPATION_CORRECTIONS, OCCUPATION_KEYWORDS, OCCUPATION_NORMALIZE,
    SWAP_JOB_TITLES, TEXT_CORRECTIONS, WEB_ARTIFACT_OCCUPATIONS,
)
from fec.config.tables import words

_SWAP_JOBS = SWAP_JOB_TITLES | OCCUPATION_KEYWORDS
_JOBS_IN_EMPLOYER = OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
_KNOWN_OCCUPATIONS = _JOBS_IN_EMPLOYER | {"ADVOCATE", "SCULPTOR"}
_NOT_WORK = STATUS_CATEGORIES | SKIP_EMPLOYERS | {"EMPLOYED"}
_JOB_WORDS = words("job_words")
_GENERIC_SELF_TAILS = words("generic_self_tails")
_PLACEHOLDER_WORDS = FINAL_JUNK_EMPLOYERS | FINAL_NULL_EMPLOYERS | REFUSAL_EMPLOYERS
ORG_TITLE_ROLES = words("org_title_roles")
_NO_INFO_OCCUPATIONS = {"EMPLOYED", "NOT DISCLOSED", "OTHER"}
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
_ROLE_THEN_COMPANY_RE = re.compile(r'^(?P<role>[A-Z][A-Z .&-]*?)\s*(?:,|/|\bAT\b|\bOF\b| - )\s*(?P<company>.+)$')
_BARE_LEGAL_FORM_RE = re.compile(r'(?:COMPANY|CORPORATION|CORP|INC|LLC|LLP|LTD|PLLC)\.?')
STATUS_FILL = {"RETIRED", "SELF-EMPLOYED", "NOT EMPLOYED"}


class Filings:
    """What the other filings say: how many filers name a text as their employer, and who."""

    def __init__(self, names: pd.Series, employers: pd.Series, occupations: pd.Series):
        keys = employers.map(company_key)
        filed = keys.ne("")
        self.filers = names[filed].groupby(keys[filed].to_numpy()).nunique().to_dict()
        self.occupation_filers = names.groupby(occupations.to_numpy()).nunique().to_dict()
        self.own = set(zip(names[filed], keys[filed]))

    def level(self, text: str, min_filers: int = 3) -> str:
        """The company evidence of a text (see fec.cleaning.work.company)."""
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


def _blank_junk_employer(employer: str, occupation: str, *, also_words: bool) -> tuple[str, str]:
    """An employer that names none goes; the same text in the occupation goes with it."""
    if not names_no_employer(employer, also_words=also_words):
        return employer, occupation
    if employer_rule_key(occupation) == employer_rule_key(employer) and occupation not in CATEGORY_OVERRIDES:
        occupation = ""
    return documented_employer_name(employer) or "", occupation


def settle(employer: str, occupation: str, filings) -> tuple[str, str]:
    """The first half: statuses, swapped boxes, placeholders and what one box says about the other."""
    e, o = employer, occupation

    def company_in_occupation(jobs, min_filers=1) -> bool:
        return filings.level(o, min_filers) in COMPANY_LEVELS and o not in jobs

    # A job in the employer box and a company (or SELF-EMPLOYED) in the occupation box: swapped.
    if e in _SWAP_JOBS and (company_in_occupation(_SWAP_JOBS) or o == "SELF-EMPLOYED"):
        e, o = o, e
    if (status_word(e) == "SELF-EMPLOYED") or e in STATUS_SLIPS_BEYOND_RULE - {"RETIREFT", "RETIREF"}:
        e = "SELF-EMPLOYED"
    if e in HOMEMAKER_EMPLOYER_VALUES:
        e = "NOT EMPLOYED"
        if o in ("", "NOT DISCLOSED", "NOT EMPLOYED"):
            o = "HOMEMAKER"
    if e:
        e = reviewed_employer_name(e)
    e, o = _blank_junk_employer(e, o, also_words=False)
    if o and len(o) <= 2 and o not in OK_SHORT_OCCUPATIONS and o not in MISSING_VALUES and o not in OCCUPATION_CORRECTIONS:
        o = ""
    # A status in one box fills the other when it is empty.
    if not o and e in STATUS_FILL:
        o = e
    if o and not e:
        e = EMPLOYER_FROM_OCCUPATION.get(o, "")
    if e and len(e) <= 2 and e not in OK_SHORT_EMPLOYERS:
        e = ""
    if o and len(o) <= 2 and o not in OK_SHORT_OCCUPATIONS:
        o = ""
    return e, o


def settle_late(e: str, o: str, own_name: bool, legal_suffix: bool, name_parts: tuple[str, str], own_employer: bool,
                filings: Filings) -> tuple[str, str]:
    """The second half: a job in the employer box, a company in the occupation box, the donor's own name."""
    def company_in_occupation(jobs, min_filers=1) -> bool:
        return filings.level(o, min_filers) in COMPANY_LEVELS and o not in jobs

    if e in _SWAP_JOBS and (company_in_occupation(_SWAP_JOBS) or o == "SELF-EMPLOYED"):
        e, o = o, e
    e, o = _blank_junk_employer(e, o, also_words=True)
    if o and ((len(o) <= 2 and o not in FINAL_SHORT_OCCUPATIONS) or _JUNK_RE.match(o) or o in ("LLP", "NIST")):
        o = ""
    if re.match(r"^[A-Z ]+\s+\d+$", o):
        o = re.sub(r"\s+\d+$", "", o)
    # SELF - ACME LLC: the firm stays; a trade beside the marker fills an empty occupation.
    if e and e != "SELF-EMPLOYED":
        marked, rest = split_self_marker(e)
        if marked:
            firm = "" if rest in _GENERIC_SELF_TAILS else firm_after_self_marker(rest)
            e = firm or "SELF-EMPLOYED"
            if rest and not firm and is_job_title(rest) and not o:
                o = rest
    # One value in both boxes: a job leaves the employer unknown, a company the occupation.
    if e and e not in _NOT_WORK and employer_rule_key(e) == employer_rule_key(o):
        if o in _JOB_WORDS or is_job_title(o):
            e = ""
        elif all(name_parts) and (name_parts[0] in o or name_parts[1] in o):
            o = "SELF-EMPLOYED"
        else:
            o = ""
    # A job word in the employer box.
    if e in _JOBS_IN_EMPLOYER:
        says_self = (o == "SELF-EMPLOYED" or o in SELF_EMPLOYMENT_WORDS or e in SELF_EMPLOYMENT_WORDS
                     or CATEGORY_OVERRIDES.get(e) == "SELF-EMPLOYED")
        # CEO beside a text only this filer writes, and no known job: the organisation they lead
        leads = (e in ORG_TITLE_ROLES and o and CATEGORY_OVERRIDES.get(o, "OTHER") == "OTHER"
                 and filings.occupation_filers.get(o) == 1 and o not in _KNOWN_OCCUPATIONS
                 and o not in NOT_REAL_EMPLOYER and o not in _NO_INFO_OCCUPATIONS)
        if company_in_occupation(_JOBS_IN_EMPLOYER) or leads:
            e, o = o, TEXT_CORRECTIONS.get(e, e)
        elif o in ("", "SELF-EMPLOYED") or (o in SKIP_EMPLOYERS and o not in STATUS_WORDS) or o in ("RETIRED", "NOT EMPLOYED"):
            status = o if o in ("RETIRED", "NOT EMPLOYED") else ""
            e, o = (status or ("SELF-EMPLOYED" if says_self else "")), e
        else:
            e = "SELF-EMPLOYED" if says_self else ""
    if e.startswith("--CHOOSE--"):
        e = e.removeprefix("--CHOOSE--").strip()
    if e in SECTOR_AS_EMPLOYER:
        if e == "EXECUTIVE" and o and filings.level(o) in COMPANY_LEVELS:
            e, o = o, TEXT_CORRECTIONS.get(e, e)
        else:
            if e == "EXECUTIVE" and not o:
                o = e
            e = ""
    if own_name and e and e not in SKIP_EMPLOYERS and not legal_suffix:
        e = "SELF-EMPLOYED"
    if o in WEB_ARTIFACT_OCCUPATIONS:
        o = ""
    # An email or a company in the occupation box is no occupation.
    if o:
        level = filings.level(o, 2)
        is_company = level in ("legal_form", "reviewed", "frequent") or (level == "named_org" and (own_employer or not e))
        if "@" in o:
            o = ""
        elif is_company:
            company, role = o, ""
            parts = _ROLE_THEN_COMPANY_RE.match(o)
            if parts and (parts["role"].strip() in _KNOWN_OCCUPATIONS or is_job_title(parts["role"].strip())):
                role, company = parts["role"].strip(), parts["company"].strip()
            if e in ("", "SELF-EMPLOYED"):
                e = company
            o = role
        elif _BARE_LEGAL_FORM_RE.fullmatch(o):
            o = ""
    return e, o


def read_fields(employers: pd.Series, occupations: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Each field read on its own: spelling, then nothing else."""
    e = _normalize_text(employers, EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT)
    o = _normalize_text(occupations, OCCUPATION_NORMALIZE)
    return e.where(e.notna(), "").astype(str), o.where(o.notna(), "").astype(str)


def _retired(e: pd.Series, o: pd.Series) -> pd.DataFrame:
    """RETIRED read in both fields of each pair (see fec.cleaning.work.retired)."""
    frame = pd.DataFrame({"contributor_employer": e.replace("", np.nan), "contributor_occupation": o.replace("", np.nan),
                          "is_individual": True, "previous_employer": pd.Series(np.nan, index=e.index, dtype=object),
                          "_retired_marker": False})
    settle_retired_fields(frame)
    return frame.fillna({"contributor_employer": "", "contributor_occupation": "", "previous_employer": ""})


class _NoFilings:
    """Before the employers are read, nothing is known about the other filings."""
    level = staticmethod(lambda text, min_filers=3: _text_evidence(company_key(text), "&" in text))


def _first_half(raw_employers: pd.Series, raw_occupations: pd.Series, names: pd.Series) -> tuple[pd.DataFrame, Filings]:
    """Each filing's e, o, previous and marker after the first half, and what the filings say together."""
    raw = pd.DataFrame({"re": raw_employers.fillna("").astype(str), "ro": raw_occupations.fillna("").astype(str)})
    pairs = raw.drop_duplicates().reset_index(drop=True)
    first = _retired(*read_fields(pairs["re"], pairs["ro"]))
    filed = list(zip(first["contributor_employer"], first["contributor_occupation"]))

    def settled(filings) -> pd.DataFrame:
        done = [settle(e, o, filings) for e, o in filed]
        return pairs.assign(e=[d[0] for d in done], o=[d[1] for d in done])

    # the swap needs to know which texts other filers name as their employer: read once to learn them
    early = raw.merge(settled(_NoFilings), on=["re", "ro"], how="left")
    pairs = settled(Filings(names, early["e"].set_axis(raw.index), early["o"].set_axis(raw.index)))
    second = _retired(pairs["e"], pairs["o"])
    pairs["e"], pairs["o"] = second["contributor_employer"], second["contributor_occupation"]
    pairs["previous"] = first["previous_employer"].where(first["previous_employer"].ne(""), second["previous_employer"])
    pairs["marker"] = first["_retired_marker"] | second["_retired_marker"]
    rows = raw.merge(pairs, on=["re", "ro"], how="left").set_axis(raw.index)
    return rows, Filings(names, rows["e"], rows["o"])


def _write(df: pd.DataFrame, people, employers, occupations) -> None:
    df.loc[people, "contributor_employer"] = pd.Series(employers, index=people, dtype=object).replace("", np.nan)
    occupation = pd.Series(occupations, index=people, dtype=object).replace("", np.nan)
    df.loc[people, "contributor_occupation"] = occupation
    df.loc[people, "occupation_category"] = _categorize(occupation)


def _names(df: pd.DataFrame, people) -> pd.Series:
    return df.loc[people, "contributor_name"].fillna("").astype(str).str.strip().str.upper()


def first_reading(df: pd.DataFrame) -> None:
    """The work fields as far as the filing alone tells, for deciding who is a person."""
    people = df.index[df["is_individual"].astype(bool)]
    rows, _ = _first_half(df.loc[people, "contributor_employer"], df.loc[people, "contributor_occupation"], _names(df, people))
    _write(df, people, rows["e"].to_numpy(), rows["o"].to_numpy())
    # Committees: the kind from the name, no occupation.
    committee = ~df["is_individual"].astype(bool)
    df["committee_type"] = pd.Series(dtype="object", index=df.index)
    df.loc[committee, "committee_type"] = _classify_committee_names(df.loc[committee, "contributor_name"])
    df.loc[committee, "occupation_category"] = "POLITICAL COMMITTEE"
    df.loc[committee, "contributor_occupation"] = np.nan


def read_work(df: pd.DataFrame, raw_employers: pd.Series, raw_occupations: pd.Series) -> None:
    """Set every individual's work fields from the filed employer and occupation."""
    people = df.index[df["entity_type"].eq("INDIVIDUAL")]
    names = _names(df, people)
    rows, filings = _first_half(raw_employers.loc[people], raw_occupations.loc[people], names)

    first_name = df.loc[people, "contributor_first_name"].fillna("").astype(str).str.upper()
    last_name = df.loc[people, "contributor_last_name"].fillna("").astype(str).str.upper()
    own_words = [name_words(a) | name_words(b) if name_words(a) and name_words(b) else None for a, b in zip(first_name, last_name)]
    rows["own"] = [w is not None and name_words(t) == w for t, w in zip(rows["e"], own_words)]
    rows["legal"] = rows["re"].str.strip().str.upper().map(lambda t: bool(LEGAL_SUFFIX_RE.search(t)))
    okey, ekey = rows["o"].map(company_key), rows["e"].map(company_key)
    # the occupation text is a name this donor files as their employer elsewhere
    rows["own_emp"] = [(n, k) in filings.own and k != ek and is_real_employer(k) and t not in _KNOWN_OCCUPATIONS and not is_job_title(t)
                       for n, k, ek, t in zip(names, okey, ekey, rows["o"])]
    same = okey.eq(ekey) & rows["e"].ne("")
    rows["first"], rows["last"] = first_name.where(same, ""), last_name.where(same, "")
    key = ["e", "o", "own", "legal", "own_emp", "first", "last"]
    done = {k: settle_late(k[0], k[1], k[2], k[3], (k[5], k[6]), k[4], filings)
            for k in rows[key].drop_duplicates().itertuples(index=False, name=None)}
    out = [done[k] for k in rows[key].itertuples(index=False, name=None)]
    employers = {e for e, _ in out}
    # Style last: a company name standing in the occupation box keeps its spelling.
    styled = {o: normalize_occupation_style(o) for o in {o for _, o in out}
              if o and o not in employers and _text_evidence(company_key(o), "&" in o) not in ("legal_form", "reviewed")}
    _write(df, people, [e for e, _ in out], [styled.get(o, o) for _, o in out])
    df.loc[people, "previous_employer"] = rows["previous"].replace("", np.nan).astype(object)
    df["_retired_marker"] = False
    df.loc[people, "_retired_marker"] = rows["marker"].astype(bool)
