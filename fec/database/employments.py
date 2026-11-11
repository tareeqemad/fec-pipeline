"""Load employers and donor employments."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.employer_status import current_employer_name, referenced_employers
from fec.database.connection import to_native
from fec.database.offices import filing_office_address_id, offices_by_company
from fec.database.previous_employers import previous_employer_of_filing
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


def employment_key(fields: tuple) -> tuple:
    """Build the seven-field employment key with None for missing values."""
    (
        donor_id,
        employer_id,
        occupation,
        employer_status,
        previous_employer_id,
        previous_self_employed,
        occupation_category_id,
    ) = fields
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


def filing_employment_key(filing: tuple, get_employer_id: Callable[[str], int | None]) -> tuple:
    """Build an employment key from this filing only.

    filing holds donor_id, employer_id, occupation, employer_status,
    previous_employer and occupation_category_id.
    """
    (
        donor_id,
        employer_id,
        occupation,
        employer_status,
        previous_employer,
        occupation_category_id,
    ) = filing
    previous_employer_id, previous_self_employed = previous_employer_of_filing(
        employer_status,
        previous_employer,
        get_employer_id,
    )
    return employment_key(
        (
            donor_id,
            employer_id,
            occupation,
            employer_status,
            previous_employer_id,
            previous_self_employed,
            occupation_category_id,
        )
    )


def _keep_latest_filings(individuals: pd.DataFrame) -> pd.DataFrame:
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


def _find_exact_employer_id(employer_ids: dict, name: Any) -> int | None:
    if pd.isna(name):
        return None
    employer = str(name).strip()
    if employer.upper() in NOT_REAL_EMPLOYER:
        return None
    return employer_ids.get(employer)


def make_employer_lookup(employer_ids: dict) -> Callable[[Any], int | None]:
    """Create an exact company-name lookup."""
    return partial(_find_exact_employer_id, employer_ids)


def load_employers(cur: Any, df: pd.DataFrame) -> dict:
    """Load referenced companies and return their IDs."""
    employer_rows = [(name,) for name in sorted(referenced_employers(df))]

    execute_values(
        cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows,
        page_size=5000,
    )

    cur.execute("SELECT employer_id, name FROM employers")
    return {row[1]: row[0] for row in cur.fetchall()}


def load_employments(
    cur: Any,
    df: pd.DataFrame,
    id_maps: dict[str, dict],
    get_employer_id: Callable[[str], int | None],
    employer_locations: list[dict] | None = None,
) -> dict:
    """Load employment rows and return their IDs.

    id_maps holds the "donor" and "occupation_category" ID maps, and
    optionally the "address" IDs used to link each company office.
    """
    rows = _build_employment_rows(df, id_maps, get_employer_id, employer_locations)
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
    return _read_employment_ids(cur)


def _build_employment_rows(
    df: pd.DataFrame,
    id_maps: dict[str, dict],
    get_employer_id: Callable[[str], int | None],
    employer_locations: list[dict] | None,
) -> list[tuple]:
    donor_ids = id_maps["donor"]
    occupation_category_ids = id_maps["occupation_category"]
    address_ids = id_maps.get("address")

    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    employment_rows = []
    seen = set()

    locations = offices_by_company(employer_locations) if address_ids else {}
    latest_filings = _keep_latest_filings(individuals)

    for _, row in latest_filings.iterrows():
        donor_key = row["donor_key"]

        donor_id = donor_ids.get(donor_key)
        if not donor_id:
            continue

        status = to_native(row.get("employer_status"))
        employer_name, employer_id = _find_current_employer(
            status,
            row["contributor_employer"],
            donor_key,
            get_employer_id,
        )
        category_id = occupation_category_ids.get(to_native(row.get("occupation_category")))
        filing = (
            donor_id,
            employer_id,
            row["contributor_occupation"],
            status,
            row.get("previous_employer"),
            category_id,
        )
        key = filing_employment_key(filing, get_employer_id)
        if key in seen:
            continue
        seen.add(key)

        office_employer = _pick_office_employer(row, status, employer_name, employer_id)
        office_address_id = filing_office_address_id(
            row,
            office_employer,
            locations,
            address_ids or {},
        )
        employment_rows.append(_build_employment_insert_values(key, office_address_id))

    return employment_rows


def _pick_office_employer(row: pd.Series, status: Any, employer_name: str, employer_id: int | None) -> Any:
    if employer_id:
        return employer_name
    if status == "retired":
        return row.get("previous_employer")
    return None


def _build_employment_insert_values(key: tuple, address_id: int | None) -> tuple:
    (
        donor_id,
        employer_id,
        occupation,
        status,
        previous_employer_id,
        previous_self_employed,
        category_id,
    ) = key
    return (
        donor_id,
        employer_id,
        occupation,
        category_id,
        status,
        previous_employer_id,
        previous_self_employed,
        address_id,
    )


def _find_current_employer(
    status: str | None, employer: Any, donor_key: str, get_employer_id: Callable[[str], int | None]
) -> tuple[str, int | None]:
    employer_name = current_employer_name(status, employer)
    if status == "active" and not employer_name:
        raise RuntimeError(f"active employment has no employer: donor_key={donor_key}")
    employer_id = get_employer_id(employer_name)
    if employer_name and employer_id is None:
        raise RuntimeError(f"cleaned employer has no exact database match: {employer_name!r}")
    return employer_name, employer_id


def _read_employment_ids(cur: Any) -> dict:
    cur.execute("SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS) + " FROM donor_employments")
    return {employment_key(row[1:]): row[0] for row in cur.fetchall()}
