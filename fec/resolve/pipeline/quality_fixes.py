"""Post-apply quality fixes for resolved employer addresses."""

import re

import pandas as pd

from fec.cleaning.previous_employer import normalize_previous_employer_column
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.geography import US_STATES
from fec.config.streets import POBOX_RE

_US_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# ai_xai: legacy tags still in the cache
_AI_METHOD_RE = re.compile(r"ai_(?:openai|xai)")

_AI_PLACEHOLDER_NUMBERS = frozenset(
    {
        "100",
        "101",
        "123",
        "200",
        "234",
        "300",
        "345",
        "400",
        "456",
        "500",
        "567",
        "600",
        "678",
        "700",
        "789",
        "800",
        "890",
        "900",
        "999",
        "1000",
        "1234",
        "2345",
        "3456",
        "4567",
        "5678",
        "9999",
    }
)

# Sequential / keyboard-walk / repeated-digit numbers are almost never real
# street numbers - diagnostic on their own, no token overlap needed.
_AI_UNAMBIGUOUS_FAKE_NUMBERS = frozenset(
    {
        "1234",
        "2345",
        "3456",
        "4567",
        "5678",
        "6789",
        "7890",
        "4321",
        "8765",
        "9876",
        "5432",
        "6543",
        "7654",
        "12345",
        "23456",
        "34567",
        "45678",
        "56789",
        "54321",
        "65432",
        "76543",
        "87654",
        "98765",
        "1111",
        "2222",
        "3333",
        "4444",
        "5555",
        "6666",
        "7777",
        "8888",
        "9999",
        "9101",  # seen in samples: '9101 E 22nd St'
    }
)

_AI_STREET_STOPWORDS = frozenset(
    {
        "STREET",
        "ST",
        "AVENUE",
        "AVE",
        "ROAD",
        "RD",
        "DRIVE",
        "DR",
        "LANE",
        "LN",
        "COURT",
        "CT",
        "PLACE",
        "PL",
        "BOULEVARD",
        "BLVD",
        "PLAZA",
        "CENTER",
        "CENTRE",
        "PARK",
        "PARKWAY",
        "PKWY",
        "NORTH",
        "SOUTH",
        "EAST",
        "WEST",
        "N",
        "S",
        "E",
        "W",
        "SUITE",
        "STE",
        "FLOOR",
        "FL",
        "BUILDING",
        "BLDG",
        "CORPORATION",
        "CORP",
        "COMPANY",
        "CO",
        "INC",
        "LLC",
        "LLP",
        "LTD",
        "GROUP",
        "ENTERPRISES",
        "PARTNERS",
        "THE",
        "OF",
        "AND",
        "OR",
        "A",
        "AN",
    }
)

_AI_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")
_AI_NUM_RE = re.compile(r"^\s*(\d+)")


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


def _ai_words(value) -> set[str]:
    if pd.isna(value):
        return set()
    return {
        token
        for token in _AI_WORD_RE.findall(str(value).upper())
        if len(token) >= 4 and token not in _AI_STREET_STOPWORDS
    }


def _street_number(value) -> str:
    if pd.isna(value):
        return ""
    match = _AI_NUM_RE.match(str(value))
    return match.group(1) if match else ""


def _clear_ai_hallucinated_addresses(df: pd.DataFrame) -> None:
    """Clear AI-fabricated employer_address rows in-place (method 'ai_hallucination_cleared')."""
    if "resolve_method" not in df.columns or "employer_address" not in df.columns:
        return

    method = df["resolve_method"].fillna("").astype(str)
    ai = method.str.contains(_AI_METHOD_RE, na=False) & ~method.str.contains(
        "_search", na=False, regex=False
    )
    if not ai.any():
        return

    street_number = df["employer_address"].map(_street_number)
    address_words = df["employer_address"].map(_ai_words)
    employer_words = df.apply(
        lambda row: (
            _ai_words(row.get("contributor_employer"))
            | _ai_words(row.get("previous_employer"))
        ),
        axis=1,
    )
    placeholder_num = street_number.isin(_AI_PLACEHOLDER_NUMBERS)
    word_overlap = pd.Series(
        [
            bool(employer_set & address_set)
            for employer_set, address_set in zip(employer_words, address_words)
        ],
        index=df.index,
    )

    employer_col = df["contributor_employer"].fillna("")
    prev_empty = (
        df.get("previous_employer", pd.Series("", index=df.index)).fillna("").eq("")
    )
    status_no_prev = employer_col.isin(EMPLOYER_STATUS_VALUES) & prev_empty
    unambiguous_fake_num = street_number.isin(_AI_UNAMBIGUOUS_FAKE_NUMBERS)

    hallucinated = ai & (
        (placeholder_num & word_overlap)
        | status_no_prev
        | unambiguous_fake_num
    )
    n_cleared = int(hallucinated.sum())
    if not n_cleared:
        return

    address_fields = (
        "employer_address",
        "employer_city",
        "employer_state",
        "employer_zip",
        "employer_latitude",
        "employer_longitude",
        "employer_geocode_level",
    )
    for col in address_fields:
        if col in df.columns:
            df.loc[hallucinated, col] = pd.NA
    df.loc[hallucinated, "resolve_method"] = "ai_hallucination_cleared"
    df.loc[hallucinated, "resolve_confidence"] = "NONE"
