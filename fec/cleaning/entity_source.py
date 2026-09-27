"""What FEC itself says a contributor is, before any guess from the name.

data/fec_source_fields.csv keeps FEC's entity_type (fec_entity_type) and
contributor id (fec_contributor_id) per raw sub_id; the raw file itself is
never rewritten (pull.py writes the file, --backfill-source fills it for
rows pulled before it existed). Where FEC gave a type, it decides the
contributor's entity_type over every name-based guess; a documented
correction in data/database/entity_overrides.csv can still overrule it
when the filing itself is wrong. Where FEC gave none (rows pulled before
these fields existed, not yet backfilled), the name-based rules decide.

A held filing (a hold rule on its sub_id) or an unresolved one keeps its
classification: FEC's type is shown beside it, never used to settle who
gave. contributor_id is not used to type a row either: a C id can name the
committee the money went through, not the contributor.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fec.donor_match.rules import HELD_FILINGS
from fec.env import FEC_SOURCE_CSV

SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id", "fec_image_number")

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


# each row's FEC source fields from the source file, '' where it has none
def attach_source_fields(df: pd.DataFrame, path: Path = FEC_SOURCE_CSV) -> None:
    source = (
        pd.read_csv(path, dtype=str, keep_default_na=False).drop_duplicates("sub_id", keep="last").set_index("sub_id")
        if Path(path).exists() else pd.DataFrame(columns=list(SOURCE_COLUMNS))
    )
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in SOURCE_COLUMNS:
        values = source[column] if column in source.columns else pd.Series(dtype=object)
        df[column] = sub_ids.map(values).fillna("").astype(str).str.strip().str.upper().to_numpy()


# each row's entity_type as FEC gives it, '' where FEC gave none
def source_entity_type(df: pd.DataFrame) -> pd.Series:
    if "fec_entity_type" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    codes = df["fec_entity_type"].astype("string").fillna("").str.strip().str.upper()
    return codes.map(FEC_ENTITY_TYPES).fillna("").astype(object)


# FEC's type where it may decide: a confirmed filing, never a held or unresolved one
def deciding_source_type(df: pd.DataFrame) -> pd.Series:
    source = source_entity_type(df)
    if not source.ne("").any():
        return source
    undecided = df["sub_id"].astype(str).str.strip().isin(HELD_FILINGS)
    if "identity_status" in df.columns:
        undecided |= df["identity_status"].fillna("confirmed").ne("confirmed")
    return source.where(~undecided, "")
