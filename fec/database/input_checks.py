"""Read the loader's input files and check them before the database is touched."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.quality_gates import run_quality_gates
from fec.cleaning.work.employer_status import referenced_employers
from fec.common.columns import check_loadable
from fec.common.committees import read_committees
from fec.common.files import read_pipeline_csv
from fec.common.settings import CLEANED_CSV, EMPLOYER_LOCATIONS_CSV
from fec.database.offices import ADDRESS_TRUST_VALUES, publishable_offices


def _check_employer_locations(locations: pd.DataFrame) -> None:
    is_primary = locations["is_primary"].str.lower()
    primary_count = is_primary.eq("true").groupby(locations["employer_name"]).sum()
    if not primary_count.eq(1).all():
        raise ValueError("each employer must have exactly one primary location row")
    if not is_primary.isin(["true", "false"]).all():
        raise ValueError("employer_locations.csv contains an invalid is_primary value")

    has_address = locations["employer_address"].str.strip().ne("")
    known_trust = locations["address_trust"].str.strip().isin(ADDRESS_TRUST_VALUES)
    if (has_address & ~known_trust).any():
        raise ValueError("employer_locations.csv contains an invalid address_trust value")


def _check_contribution_fields(df: pd.DataFrame) -> None:
    if df["donor_key"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("donor_key contains blank values")
    if not df["sub_id"].fillna("").astype(str).str.strip().str.fullmatch(r"\d+").all():
        raise ValueError("sub_id contains invalid values")
    if pd.to_datetime(df["contribution_receipt_date"], errors="coerce").isna().any():
        raise ValueError("contribution_receipt_date contains invalid values")
    if pd.to_numeric(df["two_year_transaction_period"], errors="coerce").isna().any():
        raise ValueError("two_year_transaction_period contains invalid values")


def _check_committees(df: pd.DataFrame) -> None:
    known = {(committee["committee_short"] or "").strip() for committee in read_committees()}
    unknown = sorted(set(df["recipient_committee"].dropna().astype(str).str.strip()) - known)
    if unknown:
        raise ValueError("recipient_committee is missing from committees.csv: " + ", ".join(unknown))


def read_load_input() -> tuple[pd.DataFrame, list[dict]]:
    """Read and check the cleaned filings and the company offices."""
    if not CLEANED_CSV.exists():
        raise FileNotFoundError(f"{CLEANED_CSV} not found; run the pipeline first")
    df = read_pipeline_csv(CLEANED_CSV)
    locations = pd.read_csv(EMPLOYER_LOCATIONS_CSV, dtype=str, keep_default_na=False)

    check_loadable(df.columns)
    quality = run_quality_gates(df)
    if not quality["passed"]:
        raise ValueError("cleaned data failed quality gates: " + "; ".join(quality["issues"]))
    # Only the offices of companies in this data are checked.
    _check_employer_locations(locations[locations["employer_name"].isin(referenced_employers(df))])
    _check_contribution_fields(df)
    _check_committees(df)

    df["contribution_receipt_amount"] = pd.to_numeric(df["contribution_receipt_amount"])
    return df, publishable_offices(locations)
