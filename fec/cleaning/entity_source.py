"""What FEC itself says a contributor is, before any guess from the name.

The raw file keeps FEC's entity_type (fec_entity_type) and contributor id
(fec_contributor_id) as pulled. Where FEC gave a type, it decides the
contributor's entity_type over every name-based guess; a documented
correction in data/database/entity_overrides.csv can still overrule it
when the filing itself is wrong. Where FEC gave none (rows pulled before
these fields existed, not yet backfilled), the name-based rules decide.
"""
from __future__ import annotations

import pandas as pd

SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id")

# FEC entity_type codes: a candidate (CAN) giving personally is a person
FEC_ENTITY_TYPES = {
    "IND": "INDIVIDUAL",
    "CAN": "INDIVIDUAL",
    "ORG": "ORGANIZATION",
    "COM": "COMMITTEE/PAC",
    "PAC": "COMMITTEE/PAC",
    "PTY": "COMMITTEE/PAC",
    "CCM": "COMMITTEE/PAC",
}


# add the source columns, empty, to a frame pulled before they existed
def ensure_source_columns(df: pd.DataFrame) -> None:
    for column in SOURCE_COLUMNS:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].astype("string").fillna("").str.strip().str.upper().astype(object)


# each row's entity_type as FEC gives it, '' where FEC gave none
def source_entity_type(df: pd.DataFrame) -> pd.Series:
    if "fec_entity_type" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    codes = df["fec_entity_type"].astype("string").fillna("").str.strip().str.upper()
    return codes.map(FEC_ENTITY_TYPES).fillna("").astype(object)
