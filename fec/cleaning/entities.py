"""Classify contributors and apply entity corrections."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from fec.donor_match.rules import held_groups
from fec.env import FEC_SOURCE_CSV
import re
import numpy as np
from fec.cleaning._helpers import _norm, _indiv_idx
from fec.config.constants import STATUS_CATEGORIES
from fec.config.geography import US_STATES
from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.config.data import INDIV_NAME_RE
from fec.cleaning.work.normalize import (
    EMPLOYER_STATUS_TEXT,
    _categorize,
    _normalize_text,
    map_occupation_fixes,
)
from fec.cleaning.work.company import (
    COMPANY_LEVELS, ORGANISATION_WORDS, company_evidence, has_legal_form_token,
)
from fec.cleaning.work.employer_aliases import (
    documented_employer_name, employer_rule_key, reviewed_employer_name,
)
from fec.cleaning.work.status import is_job_title
from fec.config.constants import OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS, SKIP_EMPLOYERS
from fec.config.data import MISSING_VALUES
from fec.config.occupation_rules.rules import EMPLOYER_NORMALIZE, OCCUPATION_NORMALIZE
from fec.config.tables import words


SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id", "fec_image_number")

# FEC entity codes; a candidate (CAN) giving is a person.
FEC_ENTITY_TYPES = {
    "IND": "INDIVIDUAL",
    "CAN": "INDIVIDUAL",
    "ORG": "ORGANIZATION",
    "COM": "COMMITTEE/PAC",
    "PAC": "COMMITTEE/PAC",
    "PTY": "COMMITTEE/PAC",
    "CCM": "COMMITTEE/PAC",
}


def attach_source_fields(df: pd.DataFrame, path: Path = FEC_SOURCE_CSV) -> None:
    """Attach FEC source fields by sub_id; leave missing values blank."""
    source = (
        pd.read_csv(path, dtype=str, keep_default_na=False).drop_duplicates("sub_id", keep="last").set_index("sub_id")
        if Path(path).exists() else pd.DataFrame(columns=list(SOURCE_COLUMNS))
    )
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in SOURCE_COLUMNS:
        values = source[column] if column in source.columns else pd.Series(dtype=object)
        df[column] = sub_ids.map(values).fillna("").astype(str).str.strip().str.upper().to_numpy()


def source_entity_type(df: pd.DataFrame) -> pd.Series:
    """Map FEC codes to entity types, leaving unknown codes blank."""
    if "fec_entity_type" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    codes = df["fec_entity_type"].astype("string").fillna("").str.strip().str.upper()
    return codes.map(FEC_ENTITY_TYPES).fillna("").astype(object)


def deciding_source_type(df: pd.DataFrame) -> pd.Series:
    """Use FEC's type only for confirmed filings."""
    source = source_entity_type(df)
    if not source.ne("").any():
        return source
    undecided = held_groups(df) != ""
    if "identity_status" in df.columns:
        undecided |= df["identity_status"].fillna("confirmed").ne("confirmed")
    return source.where(~undecided, "")


# Placeholders and life statuses are left to their own rules.
_NOT_WORK = STATUS_CATEGORIES | SKIP_EMPLOYERS | frozenset({'EMPLOYED'})
# Job and field words that is_job_title does not recognise.
_JOB_WORDS = words("job_words")


# Keep campaign phrases such as ROB FOR PA intact.
_FOR_STATE_RE = re.compile(r'\bFOR (?:' + '|'.join(sorted(US_STATES)) + r')$')


def normalize_business_names(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Retain company legal forms and the existing committee-name format."""
    from fec.cleaning.donors.entity import read_entity_overrides

    names = df.loc[df['entity_type'].ne('INDIVIDUAL'), 'contributor_name'].fillna('')
    types = df.loc[names.index, 'entity_type']
    cleaned = names.copy()
    company_names = set(names[types.eq('ORGANIZATION')].map(employer_rule_key))
    company_names.update(employer_rule_key(name) for name, kind in read_entity_overrides().items()
                         if kind == 'ORGANIZATION')
    organizations = names.map(employer_rule_key).isin(company_names)
    cleaned.loc[organizations] = names[organizations].map(reviewed_employer_name)
    committees = types.eq('COMMITTEE/PAC') & ~organizations & ~names.str.contains(_FOR_STATE_RE)
    cleaned.loc[committees] = names[committees].str.replace(LEGAL_SUFFIX_RE, '', regex=True).str.strip()
    # A documented company alias can also fix committee names.
    cleaned = names.map(documented_employer_name).fillna(cleaned)
    changed = (cleaned != names) & (cleaned != '')
    n_fixed = changed.sum()
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, int(n_fixed)


def fix_employer_equals_occupation(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """One value in both fields: keep it where it belongs, never guess the other.

    A job leaves the employer unknown; the donor's own name means
    self-employed; a company leaves the occupation unknown.
    """
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])

    same = indiv_idx[
        emp.map(employer_rule_key).eq(occ.map(employer_rule_key))
        & emp.ne('') & ~emp.isin(_NOT_WORK)
    ]
    n_fixed = len(same)

    is_job = occ.loc[same].isin(_JOB_WORDS) | occ.loc[same].map(is_job_title)
    jobs = same[is_job]
    df.loc[jobs, 'contributor_employer'] = np.nan

    for idx in same.difference(jobs):
        val = df.at[idx, 'contributor_occupation']
        first_name = (df.at[idx, 'contributor_first_name'] or '').upper()
        last_name = (df.at[idx, 'contributor_last_name'] or '').upper()
        val_upper = (val or '').upper()

        if first_name and last_name and (first_name in val_upper or last_name in val_upper):
            # Their own name in both fields: self-employed.
            df.at[idx, 'contributor_occupation'] = 'SELF-EMPLOYED'
            df.at[idx, 'occupation_category'] = 'SELF-EMPLOYED'
        else:
            # A company in both fields: occupation unknown.
            df.at[idx, 'contributor_occupation'] = np.nan
            df.at[idx, 'occupation_category'] = pd.NA

    return df, n_fixed


# Committee names and campaign phrases: "X FOR CONGRESS", "FRIENDS OF X",
# "BELL FOR MISSOURI, WESLEY REP.", "NRSC, DAN SEN.".
_COMMITTEE_RE = re.compile(
    r"\b(?:COMMITTEE|VICTORY|FRIENDS|CITIZENS|FUND|DEMOCRAT(?:IC|S)?"
    r"|REPUBLICANS?|SENATE|CONGRESS|ACTION|CAMPAIGN"
    r"|NRSC|NRCC|DCCC|DSCC|RNC|DNC)\b"
    r"|PAC\b"
    r"|\bFOR (?:CONGRESS|SENATE|AMERICA|GOVERNOR|MAYOR)\b"
    r"|,\s+[\w\s.]+\s+(?:REP|SEN)\.?(?:\s|$)"
    r"| FOR [A-Z]{2,}(?: [A-Z]{2,}){0,2}(?:,? INC\.?)?,\s",
    re.IGNORECASE,
)

# Committee prefixes, including the joined name BELLFORMISSOURI.
_DEFINITE_COMM_RE = re.compile(
    r"^(?:FRIENDS\s+(?:OF|FOR|TO\s+ELECT)\b"
    r"|BELLFORMISSOURI"
    r"|PEOPLE\s+FOR\b"
    r"|CITIZENS\s+FOR\b"
    r"|TEXANS\s+FOR\b"
    r"|NEVADANS\s+FOR\b"
    r"|ALASKANS\s+FOR\b"
    r"|MONTANANS\s+FOR\b"
    r"|KANSANS\s+FOR\b)",
    re.IGNORECASE,
)

# Company evidence for contributor names (fec.cleaning.work.company); a
# name other donors file as their employer counts as well.
def _company_levels(df: pd.DataFrame, names: pd.Series) -> pd.Series:
    people = df["is_individual"].astype(bool)
    return company_evidence(
        names, employers=df.loc[people, "contributor_employer"],
        filers=df.loc[people, "contributor_name"], min_filers=1,
    )


def _blank(s: pd.Series) -> pd.Series:
    """True where the value is missing or whitespace-only."""
    return _norm(s) == ""


def _reclassify_entities(df: pd.DataFrame) -> tuple[int, int]:
    """Correct entity types; return person and committee reclassification counts.

    A committee phrase or company evidence in the name marks an organisation
    or committee; a LAST, FIRST shape without either marks a person.
    """
    initial_names = df["contributor_name"].astype(str)
    committee = initial_names.str.contains(_COMMITTEE_RE, na=False)
    levels = _company_levels(df, initial_names)
    # A surname can be an organisation word (BANK, ELLYN), so a LAST, FIRST
    # name stays a person unless the evidence is certain.
    certain = has_legal_form_token(initial_names) | levels.isin(("legal_form", "reviewed", "frequent"))
    # KAHAN TRUST, DAVID names a trust; TRUST, MARK names a person.
    named_head = initial_names.str.split(",").str[0].str.upper().str.split().map(
        lambda words: len(words) >= 2 and words[-1] in ORGANISATION_WORDS
    )
    certain |= levels.eq("named_org") & named_head
    company = certain | levels.eq("named_org")
    looks_like_person = initial_names.str.match(INDIV_NAME_RE, na=False)

    df["_reclass_reason"] = pd.Series(pd.NA, index=df.index, dtype="object")

    to_individual = ~df["is_individual"] & ~committee & ~certain & looks_like_person
    n_to_indiv = _reclassify(
        df, to_individual, "committee_to_individual_last_first",
        individual=True,
    )
    to_committee = df["is_individual"] & (committee | company) & ~looks_like_person
    n_to_comm = _reclassify(
        df, to_committee, "individual_to_committee_org_keywords",
        individual=False,
    )
    n_to_comm += _reclass_individual_nameless_org(df, committee | company)
    n_to_comm += _reclass_individual_ghost(df)
    n_to_comm += _reclass_definite_committees(df, initial_names)

    individuals = df["is_individual"]
    names = df["contributor_name"].fillna("").astype(str)
    changed = names.ne(initial_names)
    if changed.any():
        committee = committee.where(~changed, names.str.contains(_COMMITTEE_RE, na=False))
        levels = levels.where(~changed, _company_levels(df, names))
        company = company.where(~changed, has_legal_form_token(names) | levels.isin(COMPANY_LEVELS))
    organizations = ~individuals & ~committee & company
    df["entity_type"] = np.select(
        [individuals, organizations], ["INDIVIDUAL", "ORGANIZATION"], default="COMMITTEE/PAC"
    )

    _apply_source_entity_type(df)
    _enforce_entity_name_consistency(df)

    df.loc[df["entity_type"] == "ORGANIZATION", "occupation_category"] = "ORGANIZATION"
    df.loc[df["entity_type"] == "COMMITTEE/PAC", "occupation_category"] = "POLITICAL COMMITTEE"
    return n_to_indiv, n_to_comm


def _apply_source_entity_type(df: pd.DataFrame) -> int:
    """Prefer FEC's type; mark new people for work restoration."""
    source = deciding_source_type(df)
    differs = source.ne("") & source.ne(df["entity_type"])
    if not differs.any():
        return 0
    to_person = differs & source.eq("INDIVIDUAL")
    df.loc[to_person, "_reclass_reason"] = "committee_to_individual_fec_source"
    df.loc[differs & ~to_person, "_reclass_reason"] = "fec_source_entity_type"
    df.loc[differs, "entity_type"] = source[differs]
    df.loc[differs, "is_individual"] = to_person[differs]
    return int(differs.sum())


def _reclassify(df: pd.DataFrame, mask: pd.Series, reason: str, individual: bool) -> int:
    count = int(mask.sum())
    if count:
        df.loc[mask, "_reclass_reason"] = reason
        df.loc[mask, "is_individual"] = individual
    return count


def _reclass_individual_nameless_org(df: pd.DataFrame, organisation: pd.Series) -> int:
    """Individuals with no first/last name and an organisation name."""
    no_names = _blank(df["contributor_first_name"]) & _blank(
        df["contributor_last_name"]
    )
    mask = df["is_individual"] & no_names & organisation
    return _reclassify(df, mask, "individual_to_committee_no_name_org_pattern", individual=False)


def _reclass_individual_ghost(df: pd.DataFrame) -> int:
    """Individuals without name, employer and occupation are committees."""
    mask = (
        df["is_individual"]
        & _blank(df["contributor_first_name"])
        & _blank(df["contributor_last_name"])
        & _blank(df["contributor_employer"])
        & _blank(df["contributor_occupation"])
    )
    return _reclassify(df, mask, "individual_to_committee_no_identity", individual=False)


def _reclass_definite_committees(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Names that are always committees (FRIENDS OF, PEOPLE FOR, ...)."""
    mask = df["is_individual"] & name_str.str.contains(_DEFINITE_COMM_RE, na=False)
    n_changed = _reclassify(df, mask, "individual_to_committee_definite_prefix", individual=False)
    for idx in df.loc[mask].index:
        name = df.at[idx, "contributor_name"]
        if "," in name:
            df.at[idx, "contributor_name"] = name.split(",")[0].strip()
        df.at[idx, "contributor_first_name"] = np.nan
        df.at[idx, "contributor_last_name"] = np.nan
    return n_changed


def _enforce_entity_name_consistency(df: pd.DataFrame) -> int:
    """Use ORGANIZATION for matching names unless FEC supplied a type."""
    org_names = set(
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"].dropna()
    )
    if not org_names:
        return 0
    fix = (
        (df["entity_type"] == "COMMITTEE/PAC")
        & df["contributor_name"].isin(org_names)
        & source_entity_type(df).eq("")
    )
    n_changed = int(fix.sum())
    if n_changed:
        df.loc[fix, "entity_type"] = "ORGANIZATION"
    return n_changed


def _restore_individual_occupations(df: pd.DataFrame, raw_occupations: pd.Series) -> None:
    """Restore filed occupations after reclassification as people."""
    has_occupation = raw_occupations.notna() & raw_occupations.astype(str).str.strip().ne("")
    if not has_occupation.any():
        return

    restored = _normalize_text(
        raw_occupations[has_occupation],
        OCCUPATION_NORMALIZE,
    )
    # Swapped fields are fixed later by swap_job_and_company.
    restored = restored[~restored.isin(MISSING_VALUES) & restored.notna()]
    if restored.empty:
        return

    df.loc[restored.index, "contributor_occupation"] = restored
    df.loc[restored.index, "occupation_category"] = _categorize(restored)
    map_occupation_fixes(df, restored.index)


def _restore_individual_employers(
    df: pd.DataFrame,
    reclassified: pd.Series,
    raw_employers: pd.Series,
) -> None:
    """Restore missing employers after reclassification."""
    has_employer = raw_employers.notna() & raw_employers.astype(str).str.strip().ne("")
    missing_employer = reclassified & (
        df["contributor_employer"].isna()
        | df["contributor_employer"].eq("NOT DISCLOSED")
    )
    restore = has_employer & missing_employer.loc[has_employer.index]
    if not restore.any():
        return

    restored = _normalize_text(
        raw_employers[restore],
        EMPLOYER_NORMALIZE,
        digits_only_before=EMPLOYER_STATUS_TEXT,
    )
    restored = restored[restored.notna() & ~restored.isin(MISSING_VALUES)]
    if not restored.empty:
        df.loc[restored.index, "contributor_employer"] = restored


def _clear_individual_residue(df: pd.DataFrame) -> None:
    """Fix missing-work status and clear short invalid values."""
    is_individual = df["is_individual"]

    missing_occupation = is_individual & df["contributor_occupation"].isna()
    if missing_occupation.any():
        df.loc[missing_occupation, "occupation_category"] = pd.NA

    short_fields = (
        ("contributor_employer", OK_SHORT_EMPLOYERS),
        ("contributor_occupation", OK_SHORT_OCCUPATIONS),
    )
    for column, whitelist in short_fields:
        values = df[column]
        junk = (
            is_individual
            & values.notna()
            & values.str.len().le(2)
            & ~values.isin(whitelist)
        )
        if junk.any():
            df.loc[junk, column] = np.nan


def _restore_reclassified_committees(
    df: pd.DataFrame,
    raw_occ_backup: pd.Series,
    raw_emp_backup: pd.Series,
) -> None:
    """Restore work fields for committee rows reclassified as individuals."""
    reclassified = df["is_individual"] & df["_reclass_reason"].str.startswith(
        "committee_to_individual", na=False
    )
    if reclassified.any():
        raw_occupations = raw_occ_backup.loc[reclassified]
        raw_employers = raw_emp_backup.loc[reclassified]
        _restore_individual_occupations(df, raw_occupations)
        _restore_individual_employers(df, reclassified, raw_employers)
