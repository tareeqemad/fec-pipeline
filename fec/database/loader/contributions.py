"""Load the contributions fact table."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.employer_status import current_employer_name
from fec.env import CLEANED_CSV
from fec.log import get_logger

from ._base import _count, to_float_or_none, to_int_or_none
from .employers import employment_key

logger = get_logger(__name__)

_ADDRESS_COLUMNS = [
    "contributor_street_1", "contributor_street_2", "contributor_city",
    "contributor_state", "contributor_zip",
]
_INSERT_SQL = """
    INSERT INTO contributions (
        sub_id, transaction_id, donor_id, committee_id, donor_address_id,
        donor_employment_id, amount, receipt_date, election_cycle,
        employment_source
    ) VALUES %s
"""


# map donor and committee ids, raising when any don't resolve
def _map_required_ids(df, donor_ids, committee_ids):
    """Return a private working frame and reject unmapped required keys."""
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
            "add them to data/database/committees.csv"
        )

    if missing_donor.any():
        sample = rows.loc[missing_donor, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing_donor.sum())} contribution row(s) have a donor_key "
            f"that mapped to no donors row -- sample sub_ids: {sample}"
        )
    return rows


# map each row's donor address to its loaded address id
def _map_address_ids(rows, address_ids):
    """Match the key produced by load_donor_addresses."""
    addresses = rows[_ADDRESS_COLUMNS].fillna("").astype(str)
    rows["_address_id"] = [
        address_ids.get((int(donor_id), *address))
        for donor_id, address in zip(
            rows["_donor_id"], addresses.itertuples(index=False, name=None)
        )
    ]


# map each row's employment to its loaded id, or raise
def _map_employment_ids(rows, employment_ids, get_employer_id):
    """Match the (donor, employer, occupation, status) key produced by load_employments."""
    employer_ids = [
        get_employer_id(current_employer_name(status, employer))
        for status, employer in rows[[
            "employer_status", "contributor_employer",
        ]].itertuples(index=False)
    ]
    rows["_employment_id"] = [
        employment_ids.get(employment_key(
            donor_id, to_int_or_none(employer_id), occupation, status,
        ))
        for donor_id, employer_id, occupation, status in zip(
            rows["_donor_id"], employer_ids,
            rows["contributor_occupation"], rows["employer_status"],
        )
    ]

    missing = rows["entity_type"].eq("INDIVIDUAL") & rows["_employment_id"].isna()
    if missing.any():
        sample = rows.loc[missing, "sub_id"].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(missing.sum())} INDIVIDUAL contribution row(s) "
            "have no exact donor_employment row -- "
            f"sample sub_ids: {sample}"
        )


# convert one row's values to native psycopg2-insertable types
def _native_row(values):
    (sub_id, transaction_id, donor_id, committee_id, address_id, employment_id,
     amount, receipt_date, election_cycle, employment_source) = values
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
        employment_source,
    )


# convert the working frame to native values accepted by psycopg2
def _build_rows(rows):
    """Convert the working frame to native values accepted by psycopg2."""
    missing = rows["contribution_receipt_amount"].isna()
    if missing.any():
        sample = rows.loc[missing, "sub_id"].head(10).tolist()
        raise ValueError(
            f"{CLEANED_CSV.name}: {int(missing.sum())} row(s) with missing/unparseable "
            f"contribution_receipt_amount -- sample sub_ids: {sample}"
        )

    receipt_dates = pd.to_datetime(
        rows["contribution_receipt_date"], errors="coerce"
    )
    election_cycles = pd.to_numeric(
        rows["two_year_transaction_period"], errors="coerce"
    )
    # a CSV from before the column existed reads as all filed
    sources = rows.get("employment_source", pd.Series("filed", index=rows.index))
    sources = sources.fillna("").replace("", "filed")
    return list(map(_native_row, zip(
        rows["sub_id"], rows["transaction_id"], rows["_donor_id"],
        rows["_committee_id"], rows["_address_id"], rows["_employment_id"],
        rows["contribution_receipt_amount"], receipt_dates, election_cycles,
        sources,
    )))


# step 7: load the contributions fact table
def load_contributions(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                       comm_map: dict, addr_key_to_id: dict,
                       empl_donor_emp_to_id: dict, get_employer_id) -> None:
    """Step 7: load the contributions fact table."""
    logger.info("\n-- 7/8 Loading contributions --")
    start = time.time()

    rows = _map_required_ids(df, donor_key_to_id, comm_map)
    _map_address_ids(rows, addr_key_to_id)
    _map_employment_ids(rows, empl_donor_emp_to_id, get_employer_id)

    execute_values(cur, _INSERT_SQL, _build_rows(rows), page_size=5000)
    conn.commit()

    total = _count(cur, "contributions")
    logger.info("  contributions: %s (%0.1fs)", f"{total:,}", time.time() - start)
