"""Load the contributions fact table."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import CLEANED_CSV
from fec.log import get_logger

from ._base import _count, to_float_or_none, to_int_or_none

logger = get_logger(__name__)


def load_contributions(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                       comm_map: dict, addr_key_to_id: dict,
                       empl_donor_emp_to_id: dict, get_employer_id) -> None:
    """Step 7: the contributions fact table (vectorized)."""
    logger.info("\n-- 7/8 Loading contributions --")
    start = time.time()

    df['_donor_id'] = df['donor_key'].map(donor_key_to_id)
    df['_comm_id'] = df['recipient_committee'].map(comm_map)

    has_donor = df['_donor_id'].notna()
    has_both = has_donor & df['_comm_id'].notna()
    valid = df[has_both].copy()
    skipped_no_donor = int((~has_donor).sum())
    skipped_no_committee = int(has_donor.sum()) - len(valid)

    # A row mapping to no committee or no donor would silently vanish from the
    # fact table -- refuse to load instead.
    if skipped_no_committee > 0:
        unmapped = sorted(set(
            df.loc[has_donor & df['_comm_id'].isna(), 'recipient_committee'].fillna('<blank>')
        ))
        shown = ", ".join(repr(value) for value in unmapped[:10])
        more = f" (+{len(unmapped) - 10} more)" if len(unmapped) > 10 else ""
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {skipped_no_committee} contribution row(s) reference "
            f"recipient_committee value(s) with no committees-table match: {shown}{more} -- "
            f"add them to data/database/committees.csv")
    if skipped_no_donor > 0:
        sample = df.loc[~has_donor, 'sub_id'].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {skipped_no_donor} contribution row(s) have a donor_key "
            f"that mapped to no donors row -- sample sub_ids: {sample}")

    # Key shape must match what load_donor_addresses put into addr_key_to_id:
    # (donor_id, street_1, street_2, city, state, zip_code)
    valid['_st1'] = valid['contributor_street_1'].fillna('').astype(str)
    valid['_st2'] = valid['contributor_street_2'].fillna('').astype(str)
    valid['_city'] = valid['contributor_city'].fillna('').astype(str)
    valid['_state'] = valid['contributor_state'].fillna('').astype(str)
    valid['_z5'] = valid['contributor_zip'].fillna('').astype(str)
    valid['_addr_id'] = [
        addr_key_to_id.get((int(donor_id), street_1, street_2, city, state, zip_code))
        for donor_id, street_1, street_2, city, state, zip_code in zip(
            valid['_donor_id'], valid['_st1'], valid['_st2'],
            valid['_city'], valid['_state'], valid['_z5']
        )
    ]

    # Same resolver as load_employments (exact -> synonym -> canonical_key), so
    # the composite lookup key can never diverge from what was inserted.
    valid['_emp_id'] = valid['contributor_employer'].map(get_employer_id)
    valid['_occ_key'] = valid['contributor_occupation'].where(
        valid['contributor_occupation'].notna(), None,
    )
    valid['_empl_id'] = [
        empl_donor_emp_to_id.get((int(donor_id),
                                  None if pd.isna(employer_id) else int(employer_id),
                                  None if (occ is None or (isinstance(occ, float) and pd.isna(occ))) else occ))
        for donor_id, employer_id, occ in zip(
            valid['_donor_id'], valid['_emp_id'], valid['_occ_key']
        )
    ]

    # An INDIVIDUAL row with an employer but no employment row means the composite
    # keys diverged between build steps -- a silent NULL would detach the contribution.
    bad_empl = ((valid['entity_type'] == 'INDIVIDUAL')
                & valid['_emp_id'].notna() & valid['_empl_id'].isna())
    if bad_empl.any():
        sample = valid.loc[bad_empl, 'sub_id'].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(bad_empl.sum())} INDIVIDUAL contribution row(s) "
            f"resolved an employer_id but no donor_employment row -- "
            f"sample sub_ids: {sample}")

    # Amounts were coerced to numeric once at read time (loader/__init__); a NaN
    # here is unparseable CSV garbage -- refuse to load it as 0.
    amt_na = valid['contribution_receipt_amount'].isna()
    if amt_na.any():
        sample = valid.loc[amt_na, 'sub_id'].head(10).tolist()
        raise ValueError(
            f"{CLEANED_CSV.name}: {int(amt_na.sum())} row(s) with missing/unparseable "
            f"contribution_receipt_amount -- sample sub_ids: {sample}")

    # receipt_date is nullable -- coerce stands
    valid['_dt'] = pd.to_datetime(valid['contribution_receipt_date'], errors='coerce')
    valid['_cycle'] = pd.to_numeric(valid['two_year_transaction_period'], errors='coerce')

    # psycopg2 needs native Python types
    contribution_rows = list(zip(
        [int(sub_id) for sub_id in valid['sub_id']],  # BIGINT PK (read as string -> exact int)
        [str(transaction_id) for transaction_id in valid['transaction_id']],
        [to_int_or_none(donor_id) for donor_id in valid['_donor_id']],
        [to_int_or_none(committee_id) for committee_id in valid['_comm_id']],
        [to_int_or_none(address_id) for address_id in valid['_addr_id']],
        [to_int_or_none(employment_id) for employment_id in valid['_empl_id']],
        [to_float_or_none(amount) for amount in valid['contribution_receipt_amount']],
        [date.date() if pd.notna(date) else None for date in valid['_dt']],
        [to_int_or_none(cycle) for cycle in valid['_cycle']],
    ))

    execute_values(cur,
        """INSERT INTO contributions
           (sub_id, transaction_id, donor_id, committee_id, donor_address_id, donor_employment_id,
            amount, receipt_date, election_cycle)
           VALUES %s""",
        contribution_rows, page_size=5000)
    conn.commit()

    df.drop(columns=['_donor_id', '_comm_id'], inplace=True, errors='ignore')

    logger.info("  contributions: %s (%0.1fs)", f"{_count(cur, 'contributions'):,}", time.time()-start)
