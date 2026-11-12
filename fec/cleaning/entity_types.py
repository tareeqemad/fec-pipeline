"""Say what each contributor is: a person, an organisation or a committee."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.cleaning.work.company_evidence import COMPANY_LEVELS, ORGANISATION_WORDS, Filings, has_legal_form_token
from fec.cleaning.work.company_names import documented_employer_name, employer_rule_key, reviewed_employer_name
from fec.common.settings import FEC_SOURCE_CSV, RULES_DIR
from fec.vocabulary.geography import US_STATES
from fec.vocabulary.names import INDIV_NAME_RE
from fec.vocabulary.not_employers import LEGAL_SUFFIX_RE
from fec.vocabulary.tables import CONFIG_DIR, read_map, words

SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id", "fec_image_number")

# FEC entity codes; a candidate (CAN) giving is a person.
_FEC_ENTITY_TYPES = read_map(CONFIG_DIR / "fec_entity_types.csv")
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


def is_committee_name(name: str) -> bool:
    """Tell whether a name holds a committee word, a word ending in PAC, or a campaign phrase."""
    name = name.upper()
    return bool(_CAMPAIGN_RE.search(name)) or any(
        word in _COMMITTEE_WORDS or word.endswith("PAC") for word in re.findall(r"[A-Z]+", name)
    )


def _find_company_evidence(df: pd.DataFrame, names: pd.Series, flagged: pd.Series) -> tuple[pd.Series, pd.Series]:
    filings = Filings(names[flagged], df.loc[flagged, "contributor_employer"].fillna(""))
    level = pd.Series([filings.level(name, 1) for name in names], index=names.index)
    legal_form = has_legal_form_token(names)
    # KAHAN TRUST, DAVID names a trust; TRUST, MARK names a person.
    head = names.str.split(",").str[0].str.upper()
    trust_head = head.str.split().map(lambda words: len(words) >= 2 and words[-1] in ORGANISATION_WORDS)
    certain = legal_form | level.isin(("legal_form", "reviewed", "frequent")) | (level.eq("named_org") & trust_head)
    company = legal_form | level.isin(COMPANY_LEVELS)
    return certain, company


def _apply_fec_types(df: pd.DataFrame, names: pd.Series, kind: pd.Series) -> pd.Series:
    if "fec_entity_type" not in df.columns:
        return kind
    filed = df["fec_entity_type"].map(_FEC_ENTITY_TYPES).dropna()
    if not len(filed):
        return kind
    by_name = filed.groupby(names[filed.index]).agg(lambda kinds: kinds.mode().iat[0])
    return names.map(by_name).fillna(kind)


def classify_entities(df: pd.DataFrame) -> None:
    """Set entity_type from the contributor name (committee phrase, company evidence); FEC's own type wins.

    A name written LAST, FIRST is a person unless the evidence is certain: a surname can be BANK.
    """
    names = df["contributor_name"].fillna("").astype(str)
    flagged = df["is_individual"].astype(bool)
    certain, company = _find_company_evidence(df, names, flagged)
    committee = names.map(is_committee_name)
    # The filer's own word that this is a person holds when the name boxes are filled.
    name_boxes = df[["contributor_first_name", "contributor_last_name"]].fillna("")
    trusted = flagged & name_boxes.ne("").all(axis=1)
    written_as_person = names.str.match(INDIV_NAME_RE)
    person = ~committee & np.where(written_as_person, trusted | ~certain, trusted & ~company)
    kind = pd.Series(
        np.select([person, ~committee & company], ["INDIVIDUAL", "ORGANIZATION"], "COMMITTEE/PAC"),
        index=df.index,
    )
    _set_types(df, _apply_fec_types(df, names, kind))


def _set_types(df: pd.DataFrame, kind: pd.Series) -> None:
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
