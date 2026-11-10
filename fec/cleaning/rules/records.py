"""Record rules that need the settled entity types."""

import pandas as pd

from fec.cleaning.entities import normalize_business_names
from fec.cleaning.work.normalize import categorize_final
from fec.cleaning.work.read import read_work
from fec.config.tables import words

_REAL_ESTATE_WORDS = words("real_estate_employer_words")


def apply_record_rules(df: pd.DataFrame, filed_employers: pd.Series, filed_occupations: pd.Series) -> pd.DataFrame:
    """Settle names, then read every individual's work fields from the filing."""
    df["is_individual"] = df["entity_type"].eq("INDIVIDUAL")
    normalize_business_names(df)
    read_work(df, df["is_individual"], filed_employers, filed_occupations)
    df.loc[df["entity_type"].eq("COMMITTEE/PAC") & df["occupation_category"].isna(), "occupation_category"] = "POLITICAL COMMITTEE"
    name_developer_field(df)
    return df


def name_developer_field(df: pd.DataFrame) -> None:
    """A bare DEVELOPER at an employer named for real estate is a REAL ESTATE DEVELOPER."""
    rows = df.index[df["is_individual"] & df["contributor_occupation"].fillna("").str.strip().str.upper().eq("DEVELOPER")]
    employer = " " + df.loc[rows, "contributor_employer"].fillna("").str.upper().str.replace(r"[^A-Z]+", " ", regex=True) + " "
    rows = rows[employer.map(lambda text: any(f" {word} " in text for word in _REAL_ESTATE_WORDS))]
    df.loc[rows, "contributor_occupation"] = "REAL ESTATE DEVELOPER"
    df.loc[rows, "occupation_category"] = categorize_final(df.loc[rows, "contributor_occupation"])
