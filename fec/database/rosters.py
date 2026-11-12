"""The leader and key-accomplice rosters.

Each person is read as a row with the columns of a filing, so that the donor,
address and employment loaders treat a person like a filing without a contribution.
The roster tables themselves (leaders, key accomplices, images) are loaded last.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.employer_status import classify_employer_status, is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.log import get_logger
from fec.common.settings import REFERENCE_DIR
from fec.database.connection import missing_as_none, to_rows
from fec.database.reference_tables import committee_ids_by_short_name
from fec.matching.identity_rules import resolve_donor_key

logger = get_logger(__name__)

# Roster file and the prefix of its name, address and job columns.
ROSTERS = {"leaders": "leader_", "key_accomplices": "accomplice_"}

# Roster column and the filing column it fills.
FILING_COLUMNS = {
    "name": "contributor_name",
    "street_1": "contributor_street_1",
    "street_2": "contributor_street_2",
    "city": "contributor_city",
    "state": "contributor_state",
    "zip": "contributor_zip",
    "address_lat": "latitude",
    "address_lng": "longitude",
    "committee_id": "card_committee",
}


def _read_roster(roster: str, column_prefix: str) -> pd.DataFrame:
    people = pd.read_csv(REFERENCE_DIR / f"{roster}.csv", dtype=str, keep_default_na=False)
    people.columns = people.columns.str.removeprefix(column_prefix)
    people = people.apply(lambda column: column.str.strip())
    unnamed = people["name"].eq("")
    if unnamed.any():
        logger.warning("WARNING: %s.csv has %s rows without a name; skipped", roster, int(unnamed.sum()))
    return people[~unnamed].assign(roster=roster)


def _check_donor_keys(people: pd.DataFrame, filings: pd.DataFrame) -> None:
    create = people["create_if_missing"].str.lower()
    undecided = people[~create.isin(["true", "false"])]
    if len(undecided):
        raise ValueError(f"{undecided['name'].tolist()} need create_if_missing=true or false in the roster CSV")
    keyless = people[people["donor_key"].eq("")]
    if len(keyless):
        raise ValueError(f"Missing donor_key for {keyless['name'].tolist()}")
    unknown = people[create.eq("false") & ~people["donor_key"].isin(filings["donor_key"])]
    if len(unknown):
        raise ValueError(f"Unknown donor_key for {unknown['name'].tolist()}; update the roster CSV")


def read_roster_people(filings: pd.DataFrame) -> pd.DataFrame:
    """Return the people of both rosters as rows with the columns of the filings.

    A person the filings do not know becomes a new donor. A person gets the roster's job
    only when the filings give them none.
    """
    people = pd.concat([_read_roster(roster, prefix) for roster, prefix in ROSTERS.items()], ignore_index=True)
    people = people.fillna("")
    # Follow merged keys, so that an old roster key creates no second donor.
    people["donor_key"] = people["donor_key"].map(lambda key: resolve_donor_key(key) if key else "")
    _check_donor_keys(people, filings)

    # leaders.csv has no name columns; its names are written LAST, FIRST.
    last_part, comma, first_part = (people["name"].str.partition(",")[part].str.strip() for part in range(3))
    first = people["first_name"].where(people["first_name"].ne("") | comma.eq(""), first_part)
    last = people["last_name"].where(people["last_name"].ne("") | comma.eq(""), last_part)

    files_as_individual = set(filings.loc[filings["entity_type"].eq("INDIVIDUAL"), "donor_key"])
    has_job = (
        (people["employer"].ne("") | people["occupation"].ne(""))
        & ~people["donor_key"].isin(files_as_individual)
        & ~people["donor_key"].duplicated()
    )
    status = people["employer"].map(lambda name: "active" if is_real_employer(name) else classify_employer_status(name))

    rows = people.rename(columns=FILING_COLUMNS).assign(
        entity_type="INDIVIDUAL",
        identity_status="confirmed",
        contributor_first_name=first.str.upper(),
        contributor_last_name=last.str.upper(),
        contributor_employer=people["employer"].where(has_job, ""),
        contributor_occupation=people["occupation"].where(has_job, ""),
        employer_status=status.where(has_job, ""),
    )
    rows = rows.replace("", None)
    rows["occupation_category"] = categorize_occupations(rows["contributor_occupation"]).where(has_job)
    for column in ("latitude", "longitude"):
        # A coordinate that is not a number stops the load, so that a typing slip is seen.
        rows[column] = pd.to_numeric(rows[column])
    return rows


def load_roster_tables(cur: Any, people: pd.DataFrame) -> None:
    """Load the images, the leaders with their committees, and the key accomplices' cards."""
    people = missing_as_none(people)
    committee_ids = committee_ids_by_short_name(cur)
    unknown = sorted(set(_committee_names(people)) - set(committee_ids))
    if unknown:
        raise ValueError(f"The rosters name unknown committees {unknown}; use one of {sorted(committee_ids)}")

    # A person on both rosters keeps the image of the roster loaded last.
    images = people.dropna(subset="image_path").drop_duplicates("donor_id", keep="last")
    image_rows = to_rows(images[["donor_id", "image_path"]])
    execute_values(cur, "INSERT INTO donor_images (donor_id, image_path) VALUES %s", image_rows)

    leaders = people[people["roster"].eq("leaders")].to_dict("records")
    sql = "INSERT INTO leaders (donor_id) VALUES %s RETURNING donor_id, leader_id"
    leader_ids = dict(execute_values(cur, sql, [(leader["donor_id"],) for leader in leaders], fetch=True))
    memberships = [
        (leader_ids[leader["donor_id"]], committee_ids[short_name])
        for leader in leaders
        for short_name in _split_committee_names(leader["committee_ids"])
    ]
    execute_values(cur, "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s", memberships)

    cards = people[people["roster"].eq("key_accomplices")].copy()
    cards["committee_id"] = cards["card_committee"].map(committee_ids).astype("Int64")
    # A display order that is not a number stops the load.
    cards["display_order"] = pd.to_numeric(cards["display_order"]).fillna(0).astype(int)
    execute_values(
        cur,
        "INSERT INTO key_accomplices (donor_id, sign, subtitle, body_text, committee_id, display_order) VALUES %s",
        to_rows(cards[["donor_id", "sign", "subtitle", "body_text", "committee_id", "display_order"]]),
    )


def _split_committee_names(text: str | None) -> list[str]:
    # A leader's committees are written as {AIPAC,DMFI}; blank for a leader of no committee.
    names = (text or "").strip("{}").split(",")
    return [name.strip() for name in names if name.strip()]


def _committee_names(people: pd.DataFrame) -> list[str]:
    names = [name for text in people["committee_ids"] for name in _split_committee_names(text)]
    return names + people["card_committee"].dropna().tolist()
