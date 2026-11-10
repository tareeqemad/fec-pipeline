"""Load contribution records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.employer_status import current_employer_name
from fec.common.settings import CLEANED_CSV
from fec.database.addresses import DONOR_ADDRESS_COLUMNS
from fec.database.connection import to_float_or_none, to_int_or_none
from fec.database.employments import filing_employment_key

_INSERT_SQL = """
    INSERT INTO contributions (
        sub_id, transaction_id, donor_id, committee_id, donor_address_id,
        donor_employment_id, amount, receipt_date, election_cycle
    ) VALUES %s
"""


def _map_required_ids(df: pd.DataFrame, donor_ids: dict, committee_ids: dict) -> pd.DataFrame:
    """Require matching donor and committee IDs for every filing."""
    rows = df.assign(
        _donor_id=df["donor_key"].map(donor_ids),
        _committee_id=df["recipient_committee"].map(committee_ids),
    )
    missing_donor = rows["_donor_id"].isna()

    if missing_donor.any():
        sample = rows.loc[missing_donor, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing_donor.sum())} contribution row(s) have a donor_key "
            f"that mapped to no donors row -- sample sub_ids: {sample}"
        )
    return rows


def _map_address_ids(rows: pd.DataFrame, address_ids: dict) -> None:
    """Match each filing to its donor-address link."""
    addresses = rows[DONOR_ADDRESS_COLUMNS].fillna("").astype(str)
    rows["_address_id"] = [
        address_ids.get((int(donor_id), *address))
        for donor_id, address in zip(rows["_donor_id"], addresses.itertuples(index=False, name=None))
    ]


def map_employment_ids(
    rows: pd.DataFrame,
    employment_ids: dict,
    get_employer_id: Callable[[str], int | None],
    occupation_category_ids: dict,
) -> None:
    """Match each filing to its own employment row."""
    rows["_employment_id"] = _find_filing_employment_ids(rows, employment_ids, get_employer_id, occupation_category_ids)
    _require_individual_employments(rows)


def _find_filing_employment_ids(
    rows: pd.DataFrame,
    employment_ids: dict,
    get_employer_id: Callable[[str], int | None],
    occupation_category_ids: dict,
) -> list:
    """Look up the employment ID of each filing, None when unmatched."""
    statuses_and_employers = rows[["employer_status", "contributor_employer"]].itertuples(index=False)
    employer_ids = [
        get_employer_id(current_employer_name(status, employer)) for status, employer in statuses_and_employers
    ]
    previous_names = rows["previous_employer"] if "previous_employer" in rows else [None] * len(rows)
    filings = zip(
        rows["_donor_id"],
        employer_ids,
        rows["contributor_occupation"],
        rows["employer_status"],
        previous_names,
        rows["occupation_category"].map(occupation_category_ids),
    )
    matched_ids = []
    for donor_id, employer_id, occupation, status, previous_employer, category_id in filings:
        filing = (
            donor_id,
            to_int_or_none(employer_id),
            occupation,
            status,
            previous_employer,
            category_id,
        )
        key = filing_employment_key(filing, get_employer_id)
        matched_ids.append(employment_ids.get(key))
    return matched_ids


def _require_individual_employments(rows: pd.DataFrame) -> None:
    """Reject individual filings that matched no employment row."""
    missing = rows["entity_type"].eq("INDIVIDUAL") & rows["_employment_id"].isna()
    if missing.any():
        sample = rows.loc[missing, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing.sum())} INDIVIDUAL contribution row(s) "
            "have no exact donor_employment row -- "
            f"sample sub_ids: {sample}"
        )


def _build_native_row(values: tuple) -> tuple:
    """Convert one contribution to PostgreSQL values."""
    (
        sub_id,
        transaction_id,
        donor_id,
        committee_id,
        address_id,
        employment_id,
        amount,
        receipt_date,
        election_cycle,
    ) = values
    return (
        int(sub_id),
        str(transaction_id),
        to_int_or_none(donor_id),
        to_int_or_none(committee_id),
        to_int_or_none(address_id),
        to_int_or_none(employment_id),
        to_float_or_none(amount),
        receipt_date.date() if pd.notna(receipt_date) else None,
        to_int_or_none(election_cycle),
    )


def _build_rows(rows: pd.DataFrame) -> list[tuple]:
    """Prepare contribution rows for insertion."""
    receipt_dates = pd.to_datetime(rows["contribution_receipt_date"], errors="coerce")
    election_cycles = pd.to_numeric(rows["two_year_transaction_period"], errors="coerce")
    contribution_values = zip(
        rows["sub_id"],
        rows["transaction_id"],
        rows["_donor_id"],
        rows["_committee_id"],
        rows["_address_id"],
        rows["_employment_id"],
        rows["contribution_receipt_amount"],
        receipt_dates,
        election_cycles,
    )
    return [_build_native_row(values) for values in contribution_values]


def load_contributions(
    cur: Any,
    df: pd.DataFrame,
    id_maps: dict[str, dict],
    get_employer_id: Callable[[str], int | None],
) -> None:
    """Load contributions with their donor, address and employment links.

    id_maps holds the "donor", "committee", "donor_address", "employment"
    and "occupation_category" ID maps.
    """
    rows = _map_required_ids(df, id_maps["donor"], id_maps["committee"])
    _map_address_ids(rows, id_maps["donor_address"])
    map_employment_ids(rows, id_maps["employment"], get_employer_id, id_maps["occupation_category"])

    execute_values(cur, _INSERT_SQL, _build_rows(rows), page_size=5000)
