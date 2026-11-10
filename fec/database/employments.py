"""Load employers and donor employments."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.employer_status import current_employer_name, referenced_employers
from fec.database.connection import to_native
from fec.database.offices import filing_office_address_id, offices_by_company
from fec.database.previous_employers import filing_previous
from fec.vocabulary.not_employers import NOT_REAL_EMPLOYER

# Match the seven-field UNIQUE NULLS NOT DISTINCT key in schema.sql.
EMPLOYMENT_KEY_COLUMNS = (
    "donor_id",
    "employer_id",
    "occupation",
    "employer_status",
    "previous_employer_id",
    "previous_self_employed",
    "occupation_category_id",
)


def employment_key(
    donor_id: Any,
    employer_id: Any,
    occupation: Any,
    employer_status: Any,
    previous_employer_id: Any = None,
    previous_self_employed: bool = False,
    occupation_category_id: Any = None,
) -> tuple:
    """Build the seven-field employment key with None for missing values."""
    employer_id = to_native(employer_id)
    previous_employer_id = to_native(previous_employer_id)
    occupation_category_id = to_native(occupation_category_id)
    return (
        int(donor_id),
        None if employer_id is None else int(employer_id),
        to_native(occupation),
        to_native(employer_status),
        None if previous_employer_id is None else int(previous_employer_id),
        bool(previous_self_employed),
        None if occupation_category_id is None else int(occupation_category_id),
    )


def filing_employment_key(
    donor_id: Any,
    employer_id: int | None,
    occupation: Any,
    employer_status: Any,
    previous_employer: Any,
    occupation_category_id: Any,
    get_employer_id: Callable[[str], int | None],
) -> tuple:
    """Build an employment key from this filing only."""
    previous_employer_id, previous_self_employed = filing_previous(
        employer_status,
        previous_employer,
        get_employer_id,
    )
    return employment_key(
        donor_id,
        employer_id,
        occupation,
        employer_status,
        previous_employer_id,
        previous_self_employed,
        occupation_category_id,
    )


def _latest_employment_rows(individuals: pd.DataFrame) -> pd.DataFrame:
    """Keep the newest filing for each distinct employment."""
    keys = [
        "donor_key",
        "contributor_employer",
        "contributor_occupation",
        "employer_status",
        "previous_employer",
        "occupation_category",
    ]
    return individuals.sort_values(
        "contribution_receipt_date",
        ascending=False,
    ).drop_duplicates(keys, keep="first")


def make_employer_lookup(employer_ids: dict) -> Callable[[Any], int | None]:
    """Create an exact company-name lookup."""

    def get_employer_id(name: Any) -> int | None:
        """Reject non-company values and find the exact company name."""
        if pd.isna(name):
            return None
        employer = str(name).strip()
        if employer.upper() in NOT_REAL_EMPLOYER:
            return None
        return employer_ids.get(employer)

    return get_employer_id


def load_employers(cur: Any, df: pd.DataFrame) -> dict:
    """Load referenced companies and return their IDs."""
    employer_rows = [(name,) for name in sorted(referenced_employers(df))]

    execute_values(
        cur, "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING", employer_rows, page_size=5000
    )

    cur.execute("SELECT employer_id, name FROM employers")
    employer_ids = {row[1]: row[0] for row in cur.fetchall()}
    return employer_ids


def load_employments(
    cur: Any,
    df: pd.DataFrame,
    donor_ids: dict,
    occupation_category_ids: dict,
    get_employer_id: Callable[[str], int | None],
    address_ids: dict | None = None,
    employer_locations: list[dict] | None = None,
) -> dict:
    """Load employment rows and return their IDs."""
    rows = _employment_rows(
        df,
        donor_ids,
        occupation_category_ids,
        get_employer_id,
        address_ids,
        employer_locations,
    )
    execute_values(
        cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, previous_self_employed,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows,
        page_size=5000,
    )
    employment_ids = _employment_ids(cur)
    return employment_ids


def _employment_rows(
    df: pd.DataFrame,
    donor_ids: dict,
    occupation_category_ids: dict,
    get_employer_id: Callable[[str], int | None],
    address_ids: dict | None,
    employer_locations: list[dict] | None,
) -> list[tuple]:
    """Build each employment from its newest filing."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    employment_rows = []
    seen = set()

    locations = offices_by_company(employer_locations) if address_ids else {}
    latest_filings = _latest_employment_rows(individuals)

    for _, row in latest_filings.iterrows():
        donor_key = row["donor_key"]

        donor_id = donor_ids.get(donor_key)
        if not donor_id:
            continue

        status = to_native(row.get("employer_status"))
        employer_name, employer_id = _current_employer(
            status,
            row["contributor_employer"],
            donor_key,
            get_employer_id,
        )
        category_id = occupation_category_ids.get(to_native(row.get("occupation_category")))
        key = filing_employment_key(
            donor_id,
            employer_id,
            row["contributor_occupation"],
            status,
            row.get("previous_employer"),
            category_id,
            get_employer_id,
        )
        if key in seen:
            continue
        seen.add(key)

        location_employer = employer_name
        if not employer_id:
            location_employer = row.get("previous_employer") if status == "retired" else None
        location_address_id = filing_office_address_id(
            row,
            location_employer,
            locations,
            address_ids or {},
        )

        (donor_id, employer_id, occupation, status, previous_employer_id, previous_self_employed, category_id) = key
        employment_rows.append(
            (
                donor_id,
                employer_id,
                occupation,
                category_id,
                status,
                previous_employer_id,
                previous_self_employed,
                location_address_id,
            )
        )

    return employment_rows


def _current_employer(
    status: str | None, employer: Any, donor_key: str, get_employer_id: Callable[[str], int | None]
) -> tuple[str, int | None]:
    """Find the filing company and require its database ID."""
    employer_name = current_employer_name(status, employer)
    if status == "active" and not employer_name:
        raise RuntimeError(f"active employment has no employer: donor_key={donor_key}")
    employer_id = get_employer_id(employer_name)
    if employer_name and employer_id is None:
        raise RuntimeError(f"cleaned employer has no exact database match: {employer_name!r}")
    return employer_name, employer_id


def _employment_ids(cur: Any) -> dict:
    """Map each employment key to its database ID."""
    cur.execute("SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS) + " FROM donor_employments")
    return {employment_key(*row[1:]): row[0] for row in cur.fetchall()}
