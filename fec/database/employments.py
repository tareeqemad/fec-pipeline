"""Load the companies the filings name and each donor's employments."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.employer_status import current_employer_name, referenced_employers
from fec.database.addresses import ADDRESS_COLUMNS, address_key
from fec.database.connection import missing_as_none, to_rows
from fec.database.offices import choose_office, office_address, workplaces_by_company

# Stored as previous_self_employed, never as a company in employers.
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"

# The df columns of the UNIQUE key of donor_employments in schema.sql, in the order they are read back.
EMPLOYMENT_KEY = [
    "donor_id",
    "employer_id",
    "contributor_occupation",
    "employer_status",
    "previous_employer_id",
    "previous_self_employed",
    "occupation_category_id",
]


def load_employers(cur: Any, df: pd.DataFrame) -> dict:
    """Load the companies, add each filing's employer columns to df and return the company IDs by name.

    The columns are employer_id, previous_employer_id and previous_self_employed.
    """
    names = sorted(referenced_employers(df))
    execute_values(cur, "INSERT INTO employers (name) VALUES %s", [(name,) for name in names], page_size=5000)
    cur.execute("SELECT name, employer_id FROM employers")
    employer_ids = dict(cur.fetchall())

    current_names = [
        current_employer_name(status, employer)
        for status, employer in zip(df["employer_status"], df["contributor_employer"])
    ]
    df["employer_id"] = pd.Series(current_names, index=df.index).map(employer_ids).astype("Int64")
    # The quality gates allow a previous employer only on a retired filing.
    # The column is text even when it is empty and was read as numbers.
    previous_names = df["previous_employer"].astype("str").str.strip()
    df["previous_employer_id"] = previous_names.map(employer_ids).astype("Int64")
    df["previous_self_employed"] = previous_names.str.upper().eq(PREVIOUS_SELF_EMPLOYED)
    return employer_ids


def load_employments(cur: Any, df: pd.DataFrame, offices: list[dict], address_ids: dict) -> None:
    """Load each donor's distinct employments with a workplace address and add donor_employment_id to df."""
    # A roster person whose job the filings already give has no employer status of their own.
    individuals = df[df["entity_type"].eq("INDIVIDUAL") & df["employer_status"].notna()]
    # The workplace comes from the latest filing that reports the employment.
    latest = individuals.sort_values("contribution_receipt_date", ascending=False).drop_duplicates(EMPLOYMENT_KEY)
    workplaces = workplaces_by_company(offices)
    rows = []
    for filing in missing_as_none(latest).to_dict("records"):
        workplace_address_id = _find_workplace_address_id(filing, workplaces, address_ids)
        key = [filing[column] for column in EMPLOYMENT_KEY]
        rows.append((*key, workplace_address_id))

    execute_values(
        cur,
        """
        INSERT INTO donor_employments (
            donor_id, employer_id, occupation, employer_status, previous_employer_id,
            previous_self_employed, occupation_category_id, address_id
        ) VALUES %s
        """,
        rows,
        page_size=5000,
    )
    cur.execute("""
        SELECT donor_employment_id, donor_id, employer_id, occupation, employer_status, previous_employer_id,
               previous_self_employed, occupation_category_id
        FROM donor_employments
    """)
    employment_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    # Committees and organizations have no employment.
    filing_ids = [employment_ids.get(key) for key in to_rows(df[EMPLOYMENT_KEY])]
    df["donor_employment_id"] = pd.array(filing_ids, dtype="Int64")


def _find_workplace_address_id(filing: dict, workplaces: dict, address_ids: dict) -> int | None:
    status = filing["employer_status"]
    if status == "self_employed":
        # A self-employed donor works at the address on the filing, when it has one.
        has_address = any(filing[column] for column in ADDRESS_COLUMNS)
        return filing["address_id"] if has_address else None
    if status == "active":
        company = filing["contributor_employer"].strip()
    elif status == "retired" and filing["previous_employer_id"] is not None:
        company = filing["previous_employer"].strip()
    else:
        return None

    office = choose_office(
        workplaces.get(company, []),
        filing["contributor_zip"] or "",
        filing["contributor_state"] or "",
        filing["contributor_city"] or "",
    )
    if office is None:
        return None
    return address_ids[address_key(office_address(office))]
