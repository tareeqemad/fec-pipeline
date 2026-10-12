"""Post-apply quality fixes for resolved employer addresses."""

import re

import pandas as pd

from fec.config.geography import US_STATES
from fec.config.streets import POBOX_RE
from fec.resolve.pipeline.ai_address_checks import (
    AI_METHOD_RE,
    clear_ai_hallucinated_addresses,
)

# US ZIP5 or ZIP+4, whole value
_US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


# null employer fields for non-individual donors
def clear_nonindividual_employer(df: pd.DataFrame) -> None:
    non_individual = df["entity_type"] != "INDIVIDUAL"
    if not non_individual.any():
        return
    for col in ("employer_address", "employer_city", "employer_state", "employer_zip"):
        df.loc[non_individual, col] = pd.NA
    df.loc[non_individual, "resolve_method"] = "skip"
    df.loc[non_individual, "resolve_confidence"] = "NONE"


# fix known AI employer-resolution quality issues
def fix_employer_address_quality(df: pd.DataFrame) -> None:
    # partial foreign addresses geocode to US namesakes
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
        df.loc[foreign, "resolve_method"] = "non_us_cleared"
        df.loc[foreign, "resolve_confidence"] = "NONE"

    # a PO box is no corporate HQ
    emp_addr = df["employer_address"].fillna("")
    is_po_box = emp_addr.str.contains(POBOX_RE, na=False)
    # closed-book only; a searched PO box stays
    method_col = df["resolve_method"].fillna("").astype(str)
    is_ai = method_col.str.contains(AI_METHOD_RE, na=False) & ~method_col.str.contains(
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

    clear_ai_hallucinated_addresses(df)
