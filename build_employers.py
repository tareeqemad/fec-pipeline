"""Build employer locations after employer geocoding."""

import json
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.addresses import _normalize_street, _normalize_unit, clean_cities
from fec.cleaning.previous_employer import referenced_employers
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.config.streets import UNIT_EXTRACT
from fec.env import CLEANED_CSV, DATA_DIR, EMPLOYER_LOCATIONS_CSV
from fec.geocoding.pipeline import (
    accepted_coordinates,
    is_foreign_address,
    numbered_street,
)
from fec.log import get_logger
from fec.resolve.pipeline.constants import EMPLOYER_ADDR_CACHE
from fec.resolve.pipeline.locations import (
    ADDRESS_FIELDS,
    PUBLISHABLE_ADDRESS_TRUST,
    address_cache_lookup,
    location_candidates,
    resolve_cache_entry,
)

logger = get_logger(__name__)

ADDRESS_COLUMNS = [
    *ADDRESS_FIELDS,
    "employer_latitude",
    "employer_longitude",
]
NAME_COLUMNS = ["contributor_employer", "previous_employer"]
OUTPUT_COLUMNS = [
    "employer_name",
    *ADDRESS_COLUMNS,
    "is_primary",
    "address_source",
    "address_trust",
]
TRUST_RANK = {
    "verified": 4,
    "grounded": 3,
    "corroborated": 2,
    "uncorroborated": 1,
    "": 0,
}
# a researcher's note inside the street text: "200 Liberty Street, 6th Floor (Brookfield Place)"
_EDITORIAL_NOTE_RE = re.compile(r"\s*\([^()]*\)")
_ZIP_PLUS_FOUR_RE = re.compile(r"^(\d{5})-?\d{4}$")
# a unit number: a single letter, or a code with at least one digit (700, 2A, E-100, 12/B)
_UNIT_CODE_RE = re.compile(r"[A-Z]|(?=[A-Z0-9\-/.]*\d)[A-Z0-9][A-Z0-9\-/.]*", re.IGNORECASE)


def _read_json(path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _address_key(values) -> tuple[str, ...]:
    street, *rest = (str(values.get(field) or "").strip().upper() for field in ADDRESS_FIELDS)
    return (numbered_street(street), *rest)  # the street form geocode cache keys use


def _donor_states(df: pd.DataFrame) -> dict[str, set[str]]:
    states = defaultdict(set)
    for column in NAME_COLUMNS:
        pairs = df[[column, "contributor_state"]].fillna("")
        for name, state in pairs.itertuples(index=False):
            if name and state:
                states[name].add(str(state).strip().upper())
    return states


def _trust(method: str, state: str, donor_states: set[str]) -> tuple[str, str]:
    source = "manual" if method.startswith("manual_") else (
        "ai" if method.startswith("ai") else method
    )
    if method == "manual_override":
        return source, "verified"
    if method == "manual_review":
        return source, "uncorroborated"
    if method.endswith("_search"):
        return source, "grounded"
    if state.upper() in donor_states:
        return source, "corroborated"
    return source, "uncorroborated"


def _geocodes() -> dict[tuple[str, ...], tuple[object, object]]:
    """Cached coordinates the key's own address accepts (a foreign office never takes a US match)."""
    cache = _read_json(DATA_DIR / "geocode_cache.json")
    coordinates = {}
    for key, result in cache.items():
        parts = tuple(part.strip().upper() for part in key.split("|"))
        if len(parts) != 4:
            continue
        latitude, longitude, _level = accepted_coordinates("|".join(parts), result)
        if latitude is not None:
            coordinates[parts] = (latitude, longitude)
    return coordinates


def normalize_us_streets(streets: pd.Series) -> pd.Series:
    """Employer street text in the donor-address form: notes in parentheses dropped, then the donor street normaliser (uppercase, USPS street types and directions). The suite/floor stays on the line: employer addresses have no street_2, and the donor unit split would drop a 'BUILDING 600' in front of the suite."""
    text = streets.fillna("").astype(str).str.replace(_EDITORIAL_NOTE_RE, " ", regex=True)
    text = text.str.split().str.join(" ")
    normalized = text.map(_normalize_street)
    # a text the street normaliser reads as junk (a bare number) is kept, uppercased
    keep = normalized.notna() & normalized.astype(str).ne("")
    streets = normalized.where(keep, text.str.upper()).astype(str).str.strip()
    return streets.map(_abbreviate_trailing_unit)


def _abbreviate_trailing_unit(street: str) -> str:
    """'... SUITE 700' -> '... STE 700' with the donor unit normaliser; only the last unit, so nothing before it is lost.

    Only a real unit code counts: digits, a single letter, or letters and digits
    (700, C, 2A, E-100, PH1). '700 OFFICE PKWY', '1 POST OFFICE SQ' and 'RAYBURN HOUSE
    OFFICE BUILDING' end in a street or building name, not a unit, and stay as they are.
    """
    match = UNIT_EXTRACT.search(street)
    if not match:
        return street
    unit_text = match.group(1).strip()
    if not _UNIT_CODE_RE.fullmatch(unit_text.split()[-1]):
        return street
    unit = _normalize_unit(unit_text)
    if not isinstance(unit, str) or not unit:
        return street
    return f"{street[:match.start()].rstrip(' ,')} {unit}".strip()


def normalize_us_cities(cities: pd.Series, states: pd.Series) -> pd.Series:
    """Employer city in the donor-address form (uppercase, FOXBOROUGH -> FOXBORO, ST. LOUIS -> SAINT LOUIS)."""
    frame = pd.DataFrame({
        "contributor_city": cities.fillna("").astype(str).str.strip(),
        "contributor_state": states.fillna("").astype(str).str.strip().str.upper(),
    })
    cleaned, _counts = clean_cities(frame, fuzzy=False)
    return cleaned["contributor_city"].fillna("").astype(str)


def normalize_location_addresses(frame: pd.DataFrame) -> pd.DataFrame:
    """US employer addresses in the uppercase USPS form donor addresses use; foreign ones stay exactly as given."""
    frame = frame.copy()
    for field in ADDRESS_FIELDS:
        frame[field] = frame[field].fillna("").astype(str).str.strip()
    foreign = pd.Series(
        [is_foreign_address(state, zipcode)
         for state, zipcode in zip(frame["employer_state"], frame["employer_zip"])],
        index=frame.index, dtype=bool,
    )
    has_place = frame[list(ADDRESS_FIELDS)].ne("").any(axis=1)
    us = ~foreign & has_place
    if not us.any():
        return frame
    rows = frame.loc[us]
    frame.loc[us, "employer_address"] = normalize_us_streets(rows["employer_address"])
    frame.loc[us, "employer_city"] = normalize_us_cities(rows["employer_city"], rows["employer_state"])
    frame.loc[us, "employer_state"] = rows["employer_state"].str.upper()
    frame.loc[us, "employer_zip"] = rows["employer_zip"].str.replace(_ZIP_PLUS_FOUR_RE, r"\1", regex=True)
    return frame


def _cache_rows(
    df: pd.DataFrame,
    employers: set[str],
) -> tuple[list[dict], set[str]]:
    cache = _read_json(DATA_DIR / EMPLOYER_ADDR_CACHE)
    lookup = address_cache_lookup(cache)
    donor_states = _donor_states(df)
    coordinates = _geocodes()
    rows = []
    managed = set()

    for employer in sorted(employers):
        entry = resolve_cache_entry(lookup, employer)
        if entry is None:
            continue
        managed.add(employer)
        for location in location_candidates(entry):
            # the geocode cache is keyed on the text as resolved, before normalising
            key = _address_key(location)
            latitude, longitude = coordinates.get(key, (None, None))
            source, trust = _trust(
                str(location.get("method") or ""),
                str(location.get("employer_state") or ""),
                donor_states[employer],
            )
            rows.append({
                "employer_name": employer,
                **{field: location.get(field, "") for field in ADDRESS_FIELDS},
                "employer_latitude": latitude,
                "employer_longitude": longitude,
                "is_primary": bool(location.get("is_primary")),
                "address_source": source,
                "address_trust": trust,
            })
    return rows, managed


def _existing_rows(employers: set[str]) -> list[dict]:
    if not EMPLOYER_LOCATIONS_CSV.exists():
        return []

    frame = pd.read_csv(
        EMPLOYER_LOCATIONS_CSV,
        dtype=str,
        keep_default_na=False,
    )
    frame = frame[frame["employer_name"].isin(employers)]
    return frame.to_dict("records")


def _deduplicate(rows: list[dict], employers: set[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    # one spelling per building, so variants of one address collapse below
    frame = normalize_location_addresses(frame)
    frame["is_primary"] = frame["is_primary"].map(
        lambda value: value is True or str(value).strip().lower() == "true"
    )
    frame["_trust_rank"] = frame["address_trust"].map(TRUST_RANK).fillna(0)
    key_columns = ["employer_name", *ADDRESS_FIELDS]
    frame = (frame.sort_values(
        ["_trust_rank", "is_primary"], ascending=False, kind="stable"
    ).drop_duplicates(key_columns).drop(columns="_trust_rank"))

    present = set(frame["employer_name"])
    missing = sorted(employers - present)
    if missing:
        empty = pd.DataFrame([{"employer_name": name, "is_primary": True}
                              for name in missing])
        frame = pd.concat([frame, empty], ignore_index=True)

    for indexes in frame.groupby("employer_name").groups.values():
        primary = [index for index in indexes if frame.at[index, "is_primary"]]
        chosen = primary[0] if primary else min(indexes)
        frame.loc[indexes, "is_primary"] = False
        frame.at[chosen, "is_primary"] = True

    return (frame.reindex(columns=OUTPUT_COLUMNS)
            .sort_values(["employer_name", "is_primary"], ascending=[True, False])
            .reset_index(drop=True))


def build() -> tuple[int, int]:
    df = pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, na_values=[""])
    unfinished = sorted(set(INTERNAL_OUTPUT_COLUMNS) & set(df.columns))
    if unfinished:
        raise ValueError(
            "run geocode.py --employer-only after resolve.py --apply; "
            f"unfinished columns: {', '.join(unfinished)}"
        )
    required = {
        "entity_type", "contributor_employer", "previous_employer",
        "contributor_state", "employer_status",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            "run resolve.py --apply before geocode.py --employer-only; "
            f"missing columns: {', '.join(missing)}"
        )
    employers = referenced_employers(df)
    existing = _existing_rows(employers)
    cache_rows, managed = _cache_rows(df, employers)
    preserved = [
        row for row in existing
        if row["employer_name"] not in managed
    ]
    locations = _deduplicate(cache_rows + preserved, employers)

    locations.to_csv(EMPLOYER_LOCATIONS_CSV, index=False, na_rep="")
    has_address = locations["employer_address"].fillna("").ne("")
    publishable = (
        has_address
        & locations["address_trust"].isin(PUBLISHABLE_ADDRESS_TRUST)
    ).sum()
    logger.info(
        f"  employer_locations.csv: {len(locations):,} locations, "
        f"{len(employers):,} companies, {publishable:,} publishable"
    )

    slim = df.drop(columns=[column for column in ADDRESS_COLUMNS if column in df.columns])
    slim.to_csv(CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  contributions_cleaned.csv: {len(slim.columns)} columns")
    return len(locations), len(slim.columns)
