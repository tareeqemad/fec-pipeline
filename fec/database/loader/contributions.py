"""Load contribution records."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.status import current_employer_name
from fec.database.loader._base import to_float_or_none, to_int_or_none
from fec.database.loader.addresses import DONOR_ADDRESS_COLUMNS
from fec.database.loader.employers import filing_employment_key
from fec.env import CLEANED_CSV

_INSERT_SQL = """
    INSERT INTO contributions (
        sub_id, transaction_id, donor_id, committee_id, donor_address_id,
        donor_employment_id, amount, receipt_date, election_cycle
    ) VALUES %s
"""


def _map_required_ids(df, donor_ids, committee_ids):
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


def _map_address_ids(rows, address_ids):
    """Match each filing to its donor-address link."""
    addresses = rows[DONOR_ADDRESS_COLUMNS].fillna("").astype(str)
    rows["_address_id"] = [
        address_ids.get((int(donor_id), *address))
        for donor_id, address in zip(rows["_donor_id"], addresses.itertuples(index=False, name=None))
    ]


def _map_employment_ids(rows, employment_ids, get_employer_id, occupation_category_ids):
    """Match each filing to its own employment row."""
    employer_ids = [
        get_employer_id(current_employer_name(status, employer))
        for status, employer in rows[
            [
                "employer_status",
                "contributor_employer",
            ]
        ].itertuples(index=False)
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
        key = filing_employment_key(
            donor_id,
            to_int_or_none(employer_id),
            occupation,
            status,
            previous_employer,
            category_id,
            get_employer_id,
        )
        matched_ids.append(employment_ids.get(key))
    rows["_employment_id"] = matched_ids

    missing = rows["entity_type"].eq("INDIVIDUAL") & rows["_employment_id"].isna()
    if missing.any():
        sample = rows.loc[missing, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing.sum())} INDIVIDUAL contribution row(s) "
            "have no exact donor_employment row -- "
            f"sample sub_ids: {sample}"
        )


def _native_row(values):
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


def _build_rows(rows):
    """Prepare contribution rows for insertion."""
    receipt_dates = pd.to_datetime(rows["contribution_receipt_date"], errors="coerce")
    election_cycles = pd.to_numeric(rows["two_year_transaction_period"], errors="coerce")
    return list(
        map(
            _native_row,
            zip(
                rows["sub_id"],
                rows["transaction_id"],
                rows["_donor_id"],
                rows["_committee_id"],
                rows["_address_id"],
                rows["_employment_id"],
                rows["contribution_receipt_amount"],
                receipt_dates,
                election_cycles,
            ),
        )
    )


def load_contributions(
    cur: Any,
    df: pd.DataFrame,
    donor_ids: dict,
    committee_ids: dict,
    address_ids: dict,
    employment_ids: dict,
    get_employer_id,
    occupation_category_ids: dict,
) -> None:
    """Load contributions with their donor, address and employment links."""
    rows = _map_required_ids(df, donor_ids, committee_ids)
    _map_address_ids(rows, address_ids)
    _map_employment_ids(rows, employment_ids, get_employer_id, occupation_category_ids)

    execute_values(cur, _INSERT_SQL, _build_rows(rows), page_size=5000)
