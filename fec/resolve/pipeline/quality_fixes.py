"""Post-apply quality fixes for resolved employer addresses."""

import re

import pandas as pd

from fec.cleaning.previous_employer import normalize_previous_employer_column
from fec.config.geography import US_STATES
from fec.config.streets import POBOX_RE
from fec.resolve.pipeline.ai_address_checks import (
    _AI_METHOD_RE,
    _clear_ai_hallucinated_addresses,
)

_US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _clear_nonindividual_employer(df: pd.DataFrame) -> None:
    """Null employer_* for non-individuals in-place - only individuals have an employer."""
    if "entity_type" not in df.columns:
        return
    non_individual = df["entity_type"] != "INDIVIDUAL"
    if not non_individual.any():
        return
    for col in (
        "employer_address",
        "employer_city",
        "employer_state",
        "employer_zip",
        "employer_latitude",
        "employer_longitude",
    ):
        if col in df.columns:
            df.loc[non_individual, col] = pd.NA
    if "employer_geocode_level" in df.columns:
        df.loc[non_individual, "employer_geocode_level"] = "no_address"
    df.loc[non_individual, "resolve_method"] = "skip"
    df.loc[non_individual, "resolve_confidence"] = "NONE"


def _fix_employer_address_quality(df: pd.DataFrame) -> None:
    """Fix known AI resolution quality issues in-place."""
    # 1. Foreign addresses cleared ENTIRELY - a kept foreign city/zip would
    # geocode to a US namesake (London->CT, Paris->KY). Foreign = non-US state
    # code, or non-US ZIP when the state isn't a confirmed US state; the US
    # territories in US_STATES count as US.
    emp_state = df["employer_state"].fillna("").astype(str).str.strip().str.upper()
    emp_zip = df["employer_zip"].fillna("").astype(str).str.strip()
    us_state_ok = emp_state.isin(US_STATES)
    non_us_state = (emp_state != "") & ~us_state_ok
    non_us_zip = (emp_zip != "") & ~emp_zip.str.match(_US_ZIP_RE, na=False)
    foreign = non_us_state | (non_us_zip & ~us_state_ok)
    if foreign.any():
        for col in (
            "employer_address",
            "employer_city",
            "employer_state",
            "employer_zip",
        ):
            df.loc[foreign, col] = ""
        for col in (
            "employer_latitude",
            "employer_longitude",
            "employer_geocode_level",
        ):
            if col in df.columns:
                df.loc[foreign, col] = pd.NA
        df.loc[foreign, "resolve_method"] = "non_us_cleared"
        df.loc[foreign, "resolve_confidence"] = "NONE"

    # 2. PO Box is wrong for a corporate HQ.
    emp_addr = df["employer_address"].fillna("")
    is_po_box = emp_addr.str.contains(POBOX_RE, na=False)
    # Closed-book AI only - a web-search-grounded (_search) PO box is the
    # firm's verified public address; keep it (still geocodes at ZIP level).
    method_col = df["resolve_method"].fillna("").astype(str)
    is_ai = method_col.str.contains(_AI_METHOD_RE, na=False) & ~method_col.str.contains(
        "_search", na=False, regex=False
    )
    ai_po_box = is_po_box & is_ai
    if ai_po_box.any():
        df.loc[ai_po_box, "employer_address"] = ""
        df.loc[ai_po_box, "employer_city"] = ""
        df.loc[ai_po_box, "employer_state"] = ""
        df.loc[ai_po_box, "employer_zip"] = ""
        df.loc[ai_po_box, "resolve_method"] = "ai_po_box_cleared"
        df.loc[ai_po_box, "resolve_confidence"] = "NONE"

    # 3. The resolve cache's previous_employer values are uncleaned, and older
    # CSVs may carry stale un-normalized entries.
    _normalize_previous_employer_column(df)

    # 4. AI-hallucinated addresses - patterns documented in the function.
    _clear_ai_hallucinated_addresses(df)


def _normalize_previous_employer_column(df: pd.DataFrame) -> None:
    """Apply the shared previous_employer contract (fec/cleaning/previous_employer.py) so every writer uses the same rules."""
    normalize_previous_employer_column(df)


