"""Load contribution records."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.loader._base import to_float_or_none, to_int_or_none
from fec.database.loader.addresses import DONOR_ADDRESS_COLUMNS
from fec.database.loader.employers import filing_employment_keys
from fec.env import CLEANED_CSV

_INSERT_SQL = """
    INSERT INTO contributions (
        sub_id, transaction_id, donor_id, committee_id, donor_address_id,
        donor_employment_id, amount, receipt_date, election_cycle
    ) VALUES %s
"""


# Require matching donor and committee IDs for every filing.
def _map_required_ids(df, donor_ids, committee_ids):
    rows = df.assign(
        _donor_id=df["donor_key"].map(donor_ids),
        _committee_id=df["recipient_committee"].map(committee_ids),
    )
    missing_donor = rows["_donor_id"].isna()
    missing_committee = ~missing_donor & rows["_committee_id"].isna()

    if missing_committee.any():
        values = sorted(set(
            rows.loc[missing_committee, "recipient_committee"].fillna("<blank>")
        ))
        shown = ", ".join(repr(value) for value in values[:10])
        more = f" (+{len(values) - 10} more)" if len(values) > 10 else ""
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing_committee.sum())} contribution row(s) reference "
            f"recipient_committee value(s) with no committees-table match: {shown}{more} -- "
            "add them to data/reference/committees.csv"
        )

    if missing_donor.any():
        sample = rows.loc[missing_donor, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing_donor.sum())} contribution row(s) have a donor_key "
            f"that mapped to no donors row -- sample sub_ids: {sample}"
        )
    return rows


# Match each filing to its donor-address link.
def _map_address_ids(rows, address_ids):
    addresses = rows[DONOR_ADDRESS_COLUMNS].fillna("").astype(str)
    rows["_address_id"] = [
        address_ids.get((int(donor_id), *address))
        for donor_id, address in zip(
            rows["_donor_id"], addresses.itertuples(index=False, name=None)
        )
    ]


# Match each filing to its own employment row.
def _map_employment_ids(rows, employment_ids, get_employer_id, occupation_category_ids):
    donor_ids = dict(zip(rows["donor_key"], rows["_donor_id"]))
    keys = filing_employment_keys(rows, donor_ids, occupation_category_ids, get_employer_id)
    rows["_employment_id"] = [employment_ids.get(key) if key else None for key in keys]

    missing = rows["entity_type"].eq("INDIVIDUAL") & rows["_employment_id"].isna()
    if missing.any():
        sample = rows.loc[missing, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing.sum())} INDIVIDUAL contribution row(s) "
            "have no exact donor_employment row -- "
            f"sample sub_ids: {sample}"
        )


# Convert one contribution to PostgreSQL values.
def _native_row(values):
    (sub_id, transaction_id, donor_id, committee_id, address_id, employment_id,
     amount, receipt_date, election_cycle) = values
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


# Load contributions with their donor, address and employment links.
def load_contributions(cur: Any, df: pd.DataFrame, donor_ids: dict,
                       committee_ids: dict, address_ids: dict,
                       employment_ids: dict, get_employer_id, occupation_category_ids: dict) -> None:
    rows = _map_required_ids(df, donor_ids, committee_ids)
    _map_address_ids(rows, address_ids)
    _map_employment_ids(rows, employment_ids, get_employer_id, occupation_category_ids)

    receipt_dates = pd.to_datetime(rows["contribution_receipt_date"], errors="coerce")
    election_cycles = pd.to_numeric(rows["two_year_transaction_period"], errors="coerce")
    values = list(map(_native_row, zip(
        rows["sub_id"], rows["transaction_id"], rows["_donor_id"],
        rows["_committee_id"], rows["_address_id"], rows["_employment_id"],
        rows["contribution_receipt_amount"], receipt_dates, election_cycles,
    )))
    execute_values(cur, _INSERT_SQL, values, page_size=5000)
