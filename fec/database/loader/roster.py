"""The two rosters: leaders.csv and key_accomplices.csv.

Each row names a donor by key. The roster is read once into people, and
the people join the ordinary loads: a missing donor is created when the
row allows it, its address joins the address dimension, its job becomes
an employment when the filings gave the donor none, and its card, image
and committees are loaded at the end.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.normalize import categorize_final
from fec.cleaning.work.status import classify_employer_status, is_real_employer
from fec.committees import load_committees
from fec.database.loader.addresses import DONOR_ADDRESS_COLUMNS
from fec.database.loader.employers import insert_employments
from fec.database.loader.locations import employment_address_id, location_index
from fec.donor_match.rules import resolve_donor_key
from fec.env import REFERENCE_DIR
from fec.log import get_logger

logger = get_logger(__name__)

ROSTERS = (("leaders.csv", "leader"), ("key_accomplices.csv", "accomplice"))


# Read both rosters; every row is checked before anything is written.
def read_roster() -> list[dict]:
    committees = {row["committee_short"] for row in load_committees() if row.get("committee_short")}
    people = []
    for csv_filename, prefix in ROSTERS:
        path = REFERENCE_DIR / csv_filename
        if not path.exists():
            logger.info(f"  {path.name} not found -- skipping")
            continue
        rows = pd.read_csv(path, dtype=str, keep_default_na=False)
        skipped = 0
        for row in rows.to_dict("records"):
            person = _person(row, prefix, csv_filename, committees)
            if person is None:
                skipped += 1
            else:
                people.append(person)
        if skipped:
            logger.warning("WARNING: %s has %s rows without a name; skipped", path.name, skipped)
    return people


def _value(row: dict, field: str) -> str:
    return (row.get(field) or "").strip()


# One roster row as a person, or None without a name.
def _person(row: dict, prefix: str, csv_filename: str, committees: set) -> dict | None:
    name = _value(row, f"{prefix}_name")
    if not name:
        return None
    first = _value(row, f"{prefix}_first_name")
    last = _value(row, f"{prefix}_last_name")
    if (not first or not last) and "," in name:
        last_part, _, first_part = name.partition(",")
        last = last or last_part.strip()
        first = first or first_part.strip()

    donor_key = _value(row, "donor_key")
    if not donor_key:
        raise ValueError(f"Missing donor_key for {name!r}")
    street_1, city = _value(row, f"{prefix}_street_1"), _value(row, f"{prefix}_city")
    address = (street_1, _value(row, f"{prefix}_street_2"), city,
               _value(row, f"{prefix}_state"), _value(row, f"{prefix}_zip"))
    person = {
        "kind": prefix,
        "name": name,
        "first": first,
        "last": last,
        "donor_key": resolve_donor_key(donor_key),
        "create_if_missing": _boolean(row, "create_if_missing", csv_filename, name),
        "address": address if (street_1 or city) and any(address) else None,
        "latitude": _coordinate(row, "address_lat", csv_filename, name),
        "longitude": _coordinate(row, "address_lng", csv_filename, name),
        "employer": _value(row, f"{prefix}_employer"),
        "occupation": _value(row, f"{prefix}_occupation"),
        "image_path": _value(row, "image_path"),
    }
    if prefix == "leader":
        person["committees"] = _leader_committees(row.get("committee_ids"), committees, name)
    else:
        short = _value(row, "committee_id")
        person["committee"] = _committee(short, committees, csv_filename, name) if short else None
        person["sign"] = _value(row, "sign") or None
        person["subtitle"] = _value(row, "subtitle") or None
        person["body_text"] = _value(row, "body_text") or None
        person["display_order"] = _display_order(row.get("display_order"), name)
    return person


# Accept true or false; reject missing or invalid values.
def _boolean(row: dict, field: str, csv_filename: str, name: str) -> bool:
    value = _value(row, field).lower()
    if value in ("true", "false"):
        return value == "true"
    raise ValueError(f"{csv_filename}: {name!r} needs {field}=true or false")


# Read an optional coordinate and warn about invalid text.
def _coordinate(row: dict, field: str, csv_filename: str, name: str) -> float | None:
    raw = _value(row, field)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("  %s: %s -- unparseable %s %r, storing NULL", csv_filename, name, field, raw)
        return None


# A committee short name, or reject an unknown one.
def _committee(short: str, committees: set, csv_filename: str, who: str) -> str:
    if short not in committees:
        raise ValueError(
            f"{csv_filename}: {who!r} names unknown committee {short!r}; "
            f"use one of {sorted(committees)} (committee_short in committees.csv)"
        )
    return short


# The committees listed for one leader: "{AIPAC,AIEF}" or "AIPAC".
def _leader_committees(raw: str | None, committees: set, who: str) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text.strip():
        return []
    shorts = []
    for token in text.split(","):
        short = token.strip()
        if not short:
            raise ValueError(f"leaders.csv: {who!r} has an empty entry in committee_ids {raw!r}")
        if short in shorts:
            raise ValueError(f"leaders.csv: {who!r} lists committee {short!r} twice")
        shorts.append(_committee(short, committees, "leaders.csv", who))
    return shorts


# Read display order, using zero for missing or invalid text.
def _display_order(raw, who: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    if text.lstrip("-").isdigit():
        return int(text)
    logger.warning("  key_accomplices.csv: %s -- unparseable display_order %r, using 0", who, text)
    return 0


# Create the roster donors the filings do not hold, where the row allows.
def create_missing_donors(cur: Any, roster: list[dict], donor_ids: dict) -> None:
    rows = {}
    for person in roster:
        if person["donor_key"] in donor_ids or person["donor_key"] in rows:
            continue
        if not person["create_if_missing"]:
            raise ValueError(
                f"Unknown donor_key {person['donor_key']!r} for {person['name']!r}; "
                "update the editorial CSV"
            )
        rows[person["donor_key"]] = (
            person["donor_key"], "INDIVIDUAL",
            person["first"].upper() or None, person["last"].upper() or None,
        )
    if rows:
        execute_values(cur,
            "INSERT INTO donors (donor_key, entity_type, first_name, last_name) VALUES %s",
            list(rows.values()))
        cur.execute("SELECT donor_id, donor_key FROM donors")
        donor_ids.update({donor_key: donor_id for donor_id, donor_key in cur.fetchall()})


# Roster people whose donor has no filing of their own, so the roster job
# becomes their employment.
def roster_jobs(roster: list[dict], df: pd.DataFrame) -> list[dict]:
    filed = set(df.loc[df["entity_type"].eq("INDIVIDUAL"), "donor_key"])
    seen = set()
    jobs = []
    for person in roster:
        key = person["donor_key"]
        if key in filed or key in seen or not (person["employer"] or person["occupation"]):
            continue
        seen.add(key)
        jobs.append(person)
    return jobs


# The companies the roster jobs name.
def roster_companies(jobs: list[dict]) -> set[str]:
    return {person["employer"] for person in jobs if is_real_employer(person["employer"])}


# Load the roster jobs as employments.
def load_roster_employments(cur: Any, jobs: list[dict], donor_ids: dict, employer_ids: dict,
                            occupation_category_ids: dict, address_ids: dict,
                            employer_locations: list[dict]) -> None:
    offices = location_index(employer_locations)
    rows = []
    for person in jobs:
        employer, occupation = person["employer"], person["occupation"] or None
        if is_real_employer(employer):
            employer_id, status = employer_ids[employer], "active"
        else:
            employer_id = None
            status = classify_employer_status(employer) if employer else "missing"
        category_id = None
        if occupation:
            category_id = occupation_category_ids.get(categorize_final(pd.Series([occupation])).iloc[0])
        address = person["address"] or ("",) * len(DONOR_ADDRESS_COLUMNS)
        address_id = employment_address_id(
            status, employer, address, address[4], address[3], address[2],
            offices, address_ids, person["donor_key"],
        ) if status == "active" else None
        rows.append((donor_ids[person["donor_key"]], employer_id, occupation, category_id,
                     status, None, False, address_id))
    insert_employments(cur, rows)


# Cards, images and committee memberships.
def load_roster_tables(cur: Any, roster: list[dict], donor_ids: dict, committee_ids: dict) -> None:
    images = {donor_ids[p["donor_key"]]: p["image_path"] for p in roster if p["image_path"]}
    if images:
        execute_values(cur, "INSERT INTO donor_images (donor_id, image_path) VALUES %s", list(images.items()))

    leaders = {donor_ids[p["donor_key"]]: p["committees"] for p in roster if p["kind"] == "leader"}
    for donor_id, committees in leaders.items():
        cur.execute("INSERT INTO leaders (donor_id) VALUES (%s) RETURNING leader_id", (donor_id,))
        leader_id = cur.fetchone()[0]
        if committees:
            execute_values(cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, committee_ids[short]) for short in committees])

    cards = {donor_ids[p["donor_key"]]: p for p in roster if p["kind"] == "accomplice"}
    if cards:
        execute_values(cur,
            """INSERT INTO key_accomplices
               (donor_id, sign, subtitle, body_text, committee_id, display_order) VALUES %s""",
            [(donor_id, p["sign"], p["subtitle"], p["body_text"],
              committee_ids.get(p["committee"]) if p["committee"] else None, p["display_order"])
             for donor_id, p in cards.items()])
