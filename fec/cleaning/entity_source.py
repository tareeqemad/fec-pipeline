"""What FEC itself says a contributor is."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fec.donor_match.rules import held_groups
from fec.env import FEC_SOURCE_CSV

SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id", "fec_image_number")

# a candidate (CAN) giving is a person
FEC_ENTITY_TYPES = {
    "IND": "INDIVIDUAL",
    "CAN": "INDIVIDUAL",
    "ORG": "ORGANIZATION",
    "COM": "COMMITTEE/PAC",
    "PAC": "COMMITTEE/PAC",
    "PTY": "COMMITTEE/PAC",
    "CCM": "COMMITTEE/PAC",
}


# attach each row's FEC source fields
def attach_source_fields(df: pd.DataFrame, path: Path = FEC_SOURCE_CSV) -> None:
    source = (
        pd.read_csv(path, dtype=str, keep_default_na=False).drop_duplicates("sub_id", keep="last").set_index("sub_id")
        if Path(path).exists() else pd.DataFrame(columns=list(SOURCE_COLUMNS))
    )
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in SOURCE_COLUMNS:
        values = source[column] if column in source.columns else pd.Series(dtype=object)
        df[column] = sub_ids.map(values).fillna("").astype(str).str.strip().str.upper().to_numpy()


# each row's FEC entity_type, else ''
def source_entity_type(df: pd.DataFrame) -> pd.Series:
    if "fec_entity_type" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    codes = df["fec_entity_type"].astype("string").fillna("").str.strip().str.upper()
    return codes.map(FEC_ENTITY_TYPES).fillna("").astype(object)


# FEC's type, only for confirmed unheld filings
def deciding_source_type(df: pd.DataFrame) -> pd.Series:
    source = source_entity_type(df)
    if not source.ne("").any():
        return source
    undecided = held_groups(df) != ""
    if "identity_status" in df.columns:
        undecided |= df["identity_status"].fillna("confirmed").ne("confirmed")
    return source.where(~undecided, "")
