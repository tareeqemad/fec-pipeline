"""Check the cleaned CSV and its companions before anything is loaded."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.employer_status import referenced_employers
from fec.cleaning.quality import run_quality_gates
from fec.config.data import FINAL_OUTPUT_COLUMNS
from fec.database.loader.addresses import (
    load_employer_locations,
)
from fec.env import (
    CLEANED_CSV,
    COMMITTEES_CSV,
    EMPLOYER_LOCATIONS_CSV,
)
from fec.io import read_pipeline_csv
from fec.resolve.pipeline.locations import ADDRESS_TRUST_VALUES

_REQUIRED_COLUMNS = set(FINAL_OUTPUT_COLUMNS)


_LOCATION_COLUMNS = {
    "employer_name",
    "employer_address",
    "employer_city",
    "employer_state",
    "employer_zip",
    "employer_latitude",
    "employer_longitude",
    "is_primary",
    "address_source",
    "address_trust",
}


def _validate_cleaned_data(df: pd.DataFrame) -> None:
    missing = sorted(_REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"{CLEANED_CSV.name}: missing columns: {', '.join(missing)}")

    quality = run_quality_gates(df)
    if not quality["passed"]:
        raise ValueError(
            "cleaned data failed quality gates: " + "; ".join(quality["issues"])
        )


def _validate_employer_locations(
    df: pd.DataFrame,
    locations: pd.DataFrame,
) -> None:
    if not _LOCATION_COLUMNS.issubset(locations.columns):
        raise ValueError(
            "employer_locations.csv is invalid; run geocode.py --employer-only"
        )

    expected = referenced_employers(df)
    actual = set(locations["employer_name"].dropna().astype(str).str.strip())
    if locations["employer_name"].isna().any() or expected != actual:
        raise ValueError(
            "employer_locations.csv does not match cleaned employers; "
            "run geocode.py --employer-only"
        )

    primary = locations["is_primary"].astype(str).str.lower().eq("true")
    primary_counts = primary.groupby(locations["employer_name"]).sum()
    if not primary_counts.eq(1).all():
        raise ValueError("each employer must have exactly one primary location row")

    valid_primary = (
        locations["is_primary"].astype(str).str.lower().isin({"true", "false"})
    )
    if not valid_primary.all():
        raise ValueError("employer_locations.csv contains an invalid is_primary value")

    trust = locations["address_trust"].fillna("").astype(str).str.strip()
    has_address = locations["employer_address"].fillna("").astype(str).str.strip().ne("")
    if (has_address & ~trust.isin(ADDRESS_TRUST_VALUES)).any():
        raise ValueError("employer_locations.csv contains an invalid address_trust value")


def _validate_contribution_fields(df: pd.DataFrame) -> None:
    donor_keys = df["donor_key"].fillna("").astype(str).str.strip()
    if donor_keys.eq("").any():
        raise ValueError("donor_key contains blank values")

    sub_ids = df["sub_id"].fillna("").astype(str).str.strip()
    if not sub_ids.str.fullmatch(r"\d+").all():
        raise ValueError("sub_id contains invalid values")

    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("contribution_receipt_date contains invalid values")

    cycles = pd.to_numeric(df["two_year_transaction_period"], errors="coerce")
    if cycles.isna().any():
        raise ValueError("two_year_transaction_period contains invalid values")


def _validate_committees(df: pd.DataFrame) -> None:
    committees = pd.read_csv(COMMITTEES_CSV, dtype=str, keep_default_na=False)
    known = set(committees["committee_short"].str.strip())
    received = set(df["recipient_committee"].dropna().astype(str).str.strip())
    unknown = sorted(received - known)
    if unknown:
        raise ValueError(
            "recipient_committee is missing from committees.csv: " + ", ".join(unknown)
        )


def _validate_input(df: pd.DataFrame, locations: pd.DataFrame) -> None:
    """Reject unfinished pipeline output."""
    _validate_cleaned_data(df)
    _validate_employer_locations(df, locations)
    _validate_contribution_fields(df)
    _validate_committees(df)


def _read_input() -> tuple[pd.DataFrame, list[dict]]:
    if not CLEANED_CSV.exists():
        raise FileNotFoundError(f"{CLEANED_CSV} not found; run the pipeline first")
    if not EMPLOYER_LOCATIONS_CSV.exists():
        raise FileNotFoundError(
            f"{EMPLOYER_LOCATIONS_CSV} not found; "
            "run geocode.py --employer-only"
        )

    df = read_pipeline_csv(CLEANED_CSV)
    locations = pd.read_csv(
        EMPLOYER_LOCATIONS_CSV,
        dtype=str,
        keep_default_na=False,
    )
    _validate_input(df, locations)

    amounts = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce")
    if amounts.isna().any():
        raise ValueError("contribution_receipt_amount contains invalid values")
    df["contribution_receipt_amount"] = amounts
    return df, load_employer_locations(locations)
