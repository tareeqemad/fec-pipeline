"""Say what each contributor is: a person, an organisation or a committee."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.cleaning.work.company import COMPANY_LEVELS, ORGANISATION_WORDS, Filings, has_legal_form_token
from fec.cleaning.work.employer_aliases import documented_employer_name, employer_rule_key, reviewed_employer_name
from fec.config.data import INDIV_NAME_RE
from fec.config.geography import US_STATES
from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.config.tables import words
from fec.env import FEC_SOURCE_CSV, RULES_DIR

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
CATEGORY_OF_TYPE = {"ORGANIZATION": "ORGANIZATION", "COMMITTEE/PAC": "POLITICAL COMMITTEE"}

_COMMITTEE_WORDS = words("committee_words")
# A campaign before the comma or an office after it: TENNEY FOR CONGRESS, CLAUDIA REP.
_CAMPAIGN_RE = re.compile(r" FOR [^,]+,|,.* (?:REP|SEN)\.?(?: |$)")
# Keep campaign phrases such as ROB FOR PA intact.
_FOR_STATE_RE = re.compile(r"\bFOR (?:" + "|".join(sorted(US_STATES)) + r")$")


def attach_source_fields(df: pd.DataFrame, path: Path = FEC_SOURCE_CSV) -> None:
    """Attach FEC source fields by sub_id; leave missing values blank."""
    source = pd.DataFrame(columns=["sub_id", *SOURCE_COLUMNS])
    if Path(path).exists():
        source = pd.read_csv(path, dtype=str, keep_default_na=False).drop_duplicates("sub_id", keep="last")
    source = source.set_index("sub_id").reindex(df["sub_id"].astype(str).str.strip())
    for column in SOURCE_COLUMNS:
        values = source[column] if column in source.columns else pd.Series("", index=source.index)
        df[column] = values.fillna("").astype(str).str.strip().str.upper().to_numpy()


def names_committee(name: str) -> bool:
    """Tell whether a name holds a committee word, a word ending in PAC, or a campaign phrase."""
    name = name.upper()
    return bool(_CAMPAIGN_RE.search(name)) or any(
        word in _COMMITTEE_WORDS or word.endswith("PAC") for word in re.findall(r"[A-Z]+", name)
    )


def classify_entities(df: pd.DataFrame) -> None:
    """Set entity_type from the contributor name; FEC's own type wins where it is filed.

    A committee phrase names a committee and company evidence names an
    organisation. A name written LAST, FIRST is a person unless that evidence
    is certain, as a surname can be an organisation word (BANK, ELLYN).
    FEC's individual flag is filed for a third of the people only.
    """
    names = df["contributor_name"].fillna("").astype(str)
    flagged = df["is_individual"].astype(bool)
    filings = Filings(names[flagged], df.loc[flagged, "contributor_employer"].fillna(""))
    level = names.map(lambda name: filings.level(name, 1))
    legal_form = has_legal_form_token(names)
    # KAHAN TRUST, DAVID names a trust; TRUST, MARK names a person.
    trust_head = (
        names.str.split(",")
        .str[0]
        .str.upper()
        .str.split()
        .map(lambda head: len(head) >= 2 and head[-1] in ORGANISATION_WORDS)
    )
    certain = legal_form | level.isin(("legal_form", "reviewed", "frequent")) | (level.eq("named_org") & trust_head)
    company = legal_form | level.isin(COMPANY_LEVELS)
    committee = names.map(names_committee)
    # The filer's own word that this is a person holds when the name boxes are filled.
    trusted = flagged & df[["contributor_first_name", "contributor_last_name"]].fillna("").ne("").all(axis=1)
    person = ~committee & np.where(names.str.match(INDIV_NAME_RE), trusted | ~certain, trusted & ~company)

    kind = pd.Series(
        np.select([person, ~committee & company], ["INDIVIDUAL", "ORGANIZATION"], "COMMITTEE/PAC"), index=df.index
    )
    # FEC's type for a name holds for every filing of that name.
    filed = df["fec_entity_type"].map(FEC_ENTITY_TYPES) if "fec_entity_type" in df.columns else pd.Series(dtype=object)
    filed = filed.dropna()
    if len(filed):
        by_name = filed.groupby(names[filed.index]).agg(lambda kinds: kinds.mode().iat[0])
        kind = names.map(by_name).fillna(kind)
    _set_types(df, kind)


def _set_types(df: pd.DataFrame, kind: pd.Series) -> None:
    """Write the type with the flag and category that follow from it."""
    df.loc[kind.index, "entity_type"] = kind
    df.loc[kind.index, "is_individual"] = kind.eq("INDIVIDUAL")
    other = kind[kind.ne("INDIVIDUAL")]
    df.loc[other.index, "occupation_category"] = other.map(CATEGORY_OF_TYPE)


def read_entity_overrides() -> dict[str, str]:
    """Reviewed contributor name -> entity type (entity_overrides.csv)."""
    path = RULES_DIR / "entity_overrides.csv"
    if not path.exists():
        return {}
    rows = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(rows["contributor_name"].str.strip().str.upper(), rows["entity_type"].str.strip().str.upper()))


def apply_entity_overrides(df: pd.DataFrame) -> None:
    """Apply the reviewed types by name; a person gets the LAST, FIRST name back."""
    reviewed = {employer_rule_key(name): kind for name, kind in read_entity_overrides().items()}
    kind = df["contributor_name"].map(employer_rule_key).map(reviewed).dropna()
    _set_types(df, kind)
    other = kind.index[kind.ne("INDIVIDUAL")]
    df.loc[other, ["contributor_employer", "contributor_occupation"]] = np.nan
    people = kind.index[kind.eq("INDIVIDUAL")]
    parts = df.loc[people, "contributor_name"].fillna("").str.split(",", n=1)
    df.loc[people, "contributor_last_name"] = parts.str[0].str.strip()
    df.loc[people, "contributor_first_name"] = parts.str[1].fillna("").str.strip().str.lstrip(". ")
    df.loc[people, "occupation_category"] = "OTHER"


def normalize_business_names(df: pd.DataFrame) -> None:
    """Give organisations their reviewed name and committees a name without a legal form."""
    names = df.loc[df["entity_type"].ne("INDIVIDUAL"), "contributor_name"].fillna("")
    kinds = df.loc[names.index, "entity_type"]
    keys = names.map(employer_rule_key)
    known = set(keys[kinds.eq("ORGANIZATION")])
    known.update(employer_rule_key(name) for name, kind in read_entity_overrides().items() if kind == "ORGANIZATION")
    organisation = keys.isin(known)
    cleaned = names.where(~organisation, names.map(reviewed_employer_name))
    committee = kinds.eq("COMMITTEE/PAC") & ~organisation & ~names.str.contains(_FOR_STATE_RE)
    cleaned = cleaned.where(~committee, names.str.replace(LEGAL_SUFFIX_RE, "", regex=True).str.strip())
    # A documented company alias can also fix committee names.
    cleaned = names.map(documented_employer_name).fillna(cleaned)
    changed = cleaned.ne(names) & cleaned.ne("")
    df.loc[changed[changed].index, "contributor_name"] = cleaned[changed]
