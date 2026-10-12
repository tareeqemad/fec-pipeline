"""Fix entity types the filer got wrong."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import upper_text
from fec.cleaning.entity_source import deciding_source_type, source_entity_type
from fec.config.data import INDIV_NAME_RE, ORG_KEYWORDS

# business tokens split organizations from committees
ORG_BUSINESS_RE = re.compile(
    r"\b(?:LLC|INC|CORP|CORPORATION|LLP|LP|HOLDINGS?|ENTERPRISES?"
    r"|VENTURES?|PROPERTIES|PROPERTY|REALTY|REAL ESTATE|MANAGEMENT|PARTNERS"
    r"|PARTNERSHIP|CAPITAL|GROUP|ASSOCIATES|COMPANY|INDUSTRIES|INVESTMENTS?"
    r"|BANK|INSURANCE|FOUNDATION|TRUST|MEDIA|DEVELOPMENT|LENDING|ACQUISITIONS?"
    r"|EQUITIES|TELECOM\w*|CONGREGATION|UNION|COUNCIL|BROTHERS|STEEL"
    r"|CONSTRUCTION|CONSULTANTS?)\b",
    re.IGNORECASE,
)

# committee words, party acronyms, FOR-office tails
_COMMITTEE_SIGNAL_RE = re.compile(
    r"\b(?:PAC|COMMITTEE|VICTORY|FRIENDS|CITIZENS|FUND|DEMOCRAT"
    r"|REPUBLICAN|SENATE|CONGRESS|ACTION|CAMPAIGN"
    r"|NRSC|NRCC|DCCC|DSCC)\b|FOR (?:CONGRESS|SENATE|AMERICA|GOVERNOR|MAYOR)",
    re.IGNORECASE,
)

# committee_type labels of rows entering as committees
_POLITICAL_COMMITTEE_TYPES = frozenset(
    {
        "CONGRESSIONAL CAMPAIGN",
        "SENATE CAMPAIGN",
        "POLITICAL COMMITTEE",
        "POLITICAL ACTION COMMITTEE",
        "PARTY ORGANIZATION",
    }
)

# commercial names outrank the committee label
_STRONG_BUSINESS_RE = re.compile(
    r"\b(?:BANK|TRUST|INVESTMENTS?|CAPITAL|INSURANCE|REALTY|REAL ESTATE"
    r"|HOLDINGS?|EQUITIES|CORPORATION|ENTERPRISES?"
    r"|FAMILY OFFICE|FAMILY TRUST|FAMILY (?:LIMITED )?PARTNERSHIP)\b",
    re.IGNORECASE,
)

# always committees, whatever the filer typed
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

# org-like names on nameless individual rows
_NAMELESS_ORG_RE = re.compile(
    r" (?:FOR |PAC|LLC|LP\b|INC|COMMITTEE|COUNCIL|PARTNERS"
    r"|HOLDINGS|INVESTMENT|COMPANY|SERVICES|MEDIA|PROPERTY"
    r"|TRADES|VENTURES|CORPORATION|GROUP|ASSOCIATION)"
    r"|^(?:DEMOCRATIC|REPUBLICAN) ",
    re.IGNORECASE,
)


# true where missing or whitespace-only
def _blank(s: pd.Series) -> pd.Series:
    return upper_text(s) == ""


# fix mistyped entity types, assign 3-way entity_type
def reclassify_entities(df: pd.DataFrame) -> tuple[int, int]:
    name_str = df["contributor_name"].astype(str)
    has_org_keywords = name_str.str.contains(ORG_KEYWORDS, na=False)
    looks_like_person = name_str.str.match(INDIV_NAME_RE, na=False)

    df["_reclass_reason"] = pd.Series(pd.NA, index=df.index, dtype="object")

    n_to_indiv = _reclass_committee_to_individual(
        df, has_org_keywords, looks_like_person
    )
    n_to_comm = (
        _reclass_individual_to_committee_org(df, has_org_keywords, looks_like_person)
        + _reclass_individual_nameless_org(df, name_str)
        + _reclass_individual_ghost(df)
        + _reclass_definite_committees(df, name_str)
    )

    # ORGANIZATION needs business signal, no committee signal
    indiv = df["is_individual"]
    names = df["contributor_name"].fillna("").astype(str)
    # labeled committees need strongly commercial names
    entered_as_committee = df["committee_type"].isin(_POLITICAL_COMMITTEE_TYPES)
    has_committee_signal = names.str.contains(_COMMITTEE_SIGNAL_RE, na=False)
    strong_business = (
        names.str.contains(_STRONG_BUSINESS_RE, na=False) & ~has_committee_signal
    )
    is_org = (
        ~indiv
        & ~has_committee_signal
        & names.str.contains(ORG_BUSINESS_RE, na=False)
        & (~entered_as_committee | strong_business)
    )
    df["entity_type"] = np.select(
        [indiv, is_org], ["INDIVIDUAL", "ORGANIZATION"], default="COMMITTEE/PAC"
    )
    _apply_source_entity_type(df)

    # ORGANIZATION anywhere wins; COMMITTEE/PAC is default
    enforce_entity_name_consistency(df)

    df.loc[df["entity_type"] == "ORGANIZATION", "occupation_category"] = "ORGANIZATION"
    df.loc[df["entity_type"] == "COMMITTEE/PAC", "occupation_category"] = (
        "POLITICAL COMMITTEE"
    )
    return n_to_indiv, n_to_comm


# FEC's own entity type beats the guess
def _apply_source_entity_type(df: pd.DataFrame) -> int:
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


# set class and reason on masked rows
def _reclassify(df: pd.DataFrame, mask: pd.Series, reason: str, individual: bool) -> int:
    count = int(mask.sum())
    if count:
        df.loc[mask, "_reclass_reason"] = reason
        df.loc[mask, "is_individual"] = individual
    return count


# committees named like LASTNAME, FIRST become individuals
def _reclass_committee_to_individual(
    df: pd.DataFrame,
    has_org_keywords: pd.Series,
    looks_like_person: pd.Series,
) -> int:
    should = ~df["is_individual"] & ~has_org_keywords & looks_like_person
    return _reclassify(df, should, "committee_to_individual_last_first", individual=True)


# org-keyword non-person names become committees
def _reclass_individual_to_committee_org(
    df: pd.DataFrame,
    has_org_keywords: pd.Series,
    looks_like_person: pd.Series,
) -> int:
    mask = df["is_individual"] & has_org_keywords & ~looks_like_person
    return _reclassify(df, mask, "individual_to_committee_org_keywords", individual=False)


# nameless individuals with org-like names become committees
def _reclass_individual_nameless_org(df: pd.DataFrame, name_str: pd.Series) -> int:
    no_names = _blank(df["contributor_first_name"]) & _blank(
        df["contributor_last_name"]
    )
    mask = (
        df["is_individual"]
        & no_names
        & name_str.str.contains(_NAMELESS_ORG_RE, na=False)
    )
    return _reclassify(df, mask, "individual_to_committee_no_name_org_pattern", individual=False)


# individuals with no identity become committees
def _reclass_individual_ghost(df: pd.DataFrame) -> int:
    mask = (
        df["is_individual"]
        & _blank(df["contributor_first_name"])
        & _blank(df["contributor_last_name"])
        & _blank(df["contributor_employer"])
        & _blank(df["contributor_occupation"])
    )
    return _reclassify(df, mask, "individual_to_committee_no_identity", individual=False)


# definite committee names become committees
def _reclass_definite_committees(df: pd.DataFrame, name_str: pd.Series) -> int:
    mask = df["is_individual"] & name_str.str.contains(_DEFINITE_COMM_RE, na=False)
    n_changed = _reclassify(df, mask, "individual_to_committee_definite_prefix", individual=False)
    if n_changed:
        for idx in df.loc[mask].index:
            name = df.at[idx, "contributor_name"]
            if "," in name:
                df.at[idx, "contributor_name"] = name.split(",")[0].strip()
            df.at[idx, "contributor_first_name"] = np.nan
            df.at[idx, "contributor_last_name"] = np.nan
    return n_changed


# same name, same entity_type; ORGANIZATION wins
def enforce_entity_name_consistency(df: pd.DataFrame) -> int:
    org_names = set(
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"].dropna()
    )
    if not org_names:
        return 0
    # rows FEC typed keep that type
    fix = (df["entity_type"] == "COMMITTEE/PAC") & df["contributor_name"].isin(
        org_names
    ) & source_entity_type(df).eq("")
    n_changed = int(fix.sum())
    if n_changed:
        df.loc[fix, "entity_type"] = "ORGANIZATION"
    return n_changed
