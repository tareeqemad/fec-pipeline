"""Build employer locations after employer geocoding."""

import json
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.addresses import _extract_units, _normalize_street, _normalize_unit, clean_cities
from fec.cleaning.previous_employer import current_employer_name, referenced_employers
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.config.streets import HASH_EXTRACT, UNIT_EXTRACT, usps_unit_designators
from fec.env import CLEANED_CSV, DATA_DIR, EMPLOYER_LOCATIONS_CSV
from fec.geocoding.pipeline import (
    accepted_coordinates,
    is_foreign_address,
    is_po_box,
    numbered_street,
)
from fec.log import get_logger
from fec.resolve.pipeline.constants import EMPLOYER_ADDR_CACHE
from fec.resolve.pipeline.locations import (
    ADDRESS_FIELDS,
    PUBLISHABLE_ADDRESS_TRUST,
    address_cache_lookup,
    location_candidates,
    publishable_first_entry,
    resolve_cache_entry,
    zip_centroid,
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
# addresses held back from publishing, one row each, for a person to decide
REVIEW_CSV = "employer_address_review.csv"
REVIEW_COLUMNS = [
    "employer_name",
    "reason",
    *ADDRESS_FIELDS,
    "is_primary",
    "address_source",
    "method",
    "donor_states",
    "employer_donors",
    "filers_at_address",
]
# a grounded address the full lookup did not choose (a canonical sibling's) in no donor's state
REVIEW_ALIAS_OUTSIDE_DONOR_STATES = "grounded_alias_outside_donor_states"
# a row kept from the previous employer_locations.csv whose cache or manual source is gone
REVIEW_NO_CURRENT_SOURCE = "carried_over_without_source"
# a home-based business: the office is an employee's own filing address
REVIEW_HOME_OFFICE = "home_office_street_withheld"
# a home-based business: at most this many donors file at the address, and the
# employer has at most this many donors (owner decision 2026-09-24)
HOME_OFFICE_MAX_FILERS = 2
HOME_OFFICE_MAX_DONORS = 3
# how precise a cached point is: a street point beats a ZIP centroid, which beats a town's point
_LEVEL_RANK = {
    "manual_census": 4,
    "census": 3,
    "google": 3,
    "nominatim": 3,
    "nominatim_intl": 3,
    "zip_centroid": 2,
    "nominatim_city": 1,
    "nominatim_intl_city": 1,
}
# a researcher's note inside the street text: "200 Liberty Street, 6th Floor (Brookfield Place)"
_EDITORIAL_NOTE_RE = re.compile(r"\s*\([^()]*\)")
_ZIP_PLUS_FOUR_RE = re.compile(r"^(\d{5})-?\d{4}$")
# a unit number: a single letter, or a code with at least one digit (700, 2A, E-100, 12/B)
_UNIT_CODE_RE = re.compile(r"[A-Z]|(?=[A-Z0-9\-/.]*\d)[A-Z0-9][A-Z0-9\-/.]*", re.IGNORECASE)
# a unit anywhere on the line (the donor split moves only the last one to street_2)
_ANY_UNIT_RE = re.compile(
    r"#|\b(?:STE|SUITE|FL|FLR|FLOOR|APT|APARTMENT|UNIT|RM|ROOM|BLDG|BUILDING|PH|PENTHOUSE"
    r"|PMB|LOT|SPC|SPACE|DEPT|OFC)\b"
)
_TRAILING_UNIT_WORD_RE = re.compile(r"\s+(?:APT|UNIT|STE|SUITE)\s*$")


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


def _geocodes() -> dict[tuple[str, ...], tuple[float, float, str]]:
    """Cached coordinates the key's own address accepts, with their level (a foreign office never takes a US match)."""
    cache = _read_json(DATA_DIR / "geocode_cache.json")
    coordinates = {}
    for key, result in cache.items():
        parts = tuple(part.strip().upper() for part in key.split("|"))
        if len(parts) != 4:
            continue
        latitude, longitude, level = accepted_coordinates("|".join(parts), result)
        if latitude is None:
            continue
        known = coordinates.get(parts)
        if known is None or _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(known[2], 0):
            coordinates[parts] = (latitude, longitude, level)
    return coordinates


def normalize_us_streets(streets: pd.Series) -> pd.Series:
    """Employer street text in the donor-address form: notes in parentheses dropped, then the donor street normaliser (uppercase, USPS street types and directions) and USPS unit designators ('10TH FLOOR' -> 'FL 10', 'SUITE 200' -> 'STE 200'). The suite/floor stays on the line: employer addresses have no street_2, and the donor unit split would drop a 'BUILDING 600' in front of the suite."""
    text = streets.fillna("").astype(str).str.replace(_EDITORIAL_NOTE_RE, " ", regex=True)
    text = text.str.split().str.join(" ")
    normalized = text.map(_normalize_street)
    # a text the street normaliser reads as junk (a bare number) is kept, uppercased
    keep = normalized.notna() & normalized.astype(str).ne("")
    streets = normalized.where(keep, text.str.upper()).astype(str).str.strip()
    return streets.map(_abbreviate_trailing_unit).map(usps_unit_designators)


def _abbreviate_trailing_unit(street: str) -> str:
    """'... SUITE 700' -> '... STE 700' with the donor unit normaliser; only the last unit, so nothing before it is lost.

    Only a real unit code counts: digits, a single letter, or letters and digits
    (700, C, 2A, E-100, PH1). '700 OFFICE PKWY', '1 POST OFFICE SQ' and 'RAYBURN HOUSE
    OFFICE BUILDING' end in a street or building name, not a unit, and stay as they are.
    Unit designators elsewhere on the line are rewritten by usps_unit_designators.
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


def _bare_street(street: str) -> str:
    """The building's street with every trailing unit removed ('399 PARK AVE FL 25 STE 2502' -> '399 PARK AVE')."""
    street = str(street or "").strip()
    while True:
        match = UNIT_EXTRACT.search(street) or HASH_EXTRACT.search(street)
        if not match:
            return street
        unit_text = match.group(1).strip()
        if not unit_text.startswith("#") and not _UNIT_CODE_RE.fullmatch(unit_text.split()[-1]):
            return street
        rest = street[:match.start()].rstrip(" ,-")
        if not rest:
            return street
        street = rest


def _donor_form_streets(streets: pd.Series) -> pd.Series:
    """The street_1 a donor filing of the same text gets: the last unit moved to street_2, as the donor cleaning does."""
    street_1, _street_2, _count = _extract_units(streets, pd.Series("", index=streets.index))
    street_1 = street_1.fillna("").astype(str).str.replace(_TRAILING_UNIT_WORD_RE, "", regex=True)
    return street_1.str.strip()


def normalize_us_cities(cities: pd.Series, states: pd.Series) -> pd.Series:
    """Employer city in the donor-address form (uppercase, FOXBOROUGH -> FOXBORO, ST. LOUIS -> SAINT LOUIS)."""
    frame = pd.DataFrame({
        "contributor_city": cities.fillna("").astype(str).str.strip(),
        "contributor_state": states.fillna("").astype(str).str.strip().str.upper(),
    })
    cleaned, _counts = clean_cities(frame, fuzzy=False)
    return cleaned["contributor_city"].fillna("").astype(str)


def _foreign_mask(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [is_foreign_address(state, zipcode)
         for state, zipcode in zip(frame["employer_state"], frame["employer_zip"])],
        index=frame.index, dtype=bool,
    )


def normalize_location_addresses(frame: pd.DataFrame) -> pd.DataFrame:
    """US employer addresses in the uppercase USPS form donor addresses use; foreign ones stay exactly as given."""
    frame = frame.copy()
    for field in ADDRESS_FIELDS:
        frame[field] = frame[field].fillna("").astype(str).str.strip()
    foreign = _foreign_mask(frame)
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


def _review_row(employer: str, location: dict, reason: str, **extra) -> dict:
    row = {
        "employer_name": employer,
        "reason": reason,
        **{field: location.get(field, "") for field in ADDRESS_FIELDS},
        "is_primary": bool(location.get("is_primary")),
        "address_source": location.get("address_source", ""),
        "method": location.get("method", ""),
    }
    row.update(extra)
    return row


def _outside_donor_states(location: dict, donor_states: set[str]) -> bool:
    """An AI-grounded address (not a curated one) in a state where none of the employer's donors files."""
    method = str(location.get("method") or "")
    state = str(location.get("employer_state") or "").strip().upper()
    return method.endswith("_search") and state not in donor_states


def _cache_rows(
    df: pd.DataFrame,
    employers: set[str],
    donor_states: dict[str, set[str]] | None = None,
) -> tuple[list[dict], set[str], list[dict]]:
    """Rows from the resolve cache, with the address resolve's apply step uses (publishable lookup first).

    When that lookup finds a grounded address the full lookup does not choose (a
    canonical sibling behind a closed-book exact answer), an address of it in no
    donor's state goes to the review list instead of being published: a grounded
    address can be a stale or namesake office (WILLIAMS & CONNOLLY moved in 2022).
    Each row carries the geocode key of its text as resolved in '_raw_key'.
    """
    cache = _read_json(DATA_DIR / EMPLOYER_ADDR_CACHE)
    trusted_lookup = address_cache_lookup(cache, publishable_only=True)
    lookup = address_cache_lookup(cache)
    if donor_states is None:
        donor_states = _donor_states(df)
    rows = []
    managed = set()
    review = []

    for employer in sorted(employers):
        states = donor_states.get(employer, set())
        entry, changed = publishable_first_entry(trusted_lookup, lookup, employer)
        candidates = location_candidates(entry)
        held = []
        if changed:
            held = [index for index, location in enumerate(candidates)
                    if _outside_donor_states(location, states)]
            review.extend(
                _review_row(employer, candidates[index], REVIEW_ALIAS_OUTSIDE_DONOR_STATES,
                            donor_states=";".join(sorted(states)))
                for index in held
            )
            if len(held) == len(candidates):
                # nothing of it can be published: keep the full lookup's answer
                entry, held = resolve_cache_entry(lookup, employer), []
                candidates = location_candidates(entry)
        if entry is None:
            continue
        managed.add(employer)
        for index, location in enumerate(candidates):
            source, trust = _trust(
                str(location.get("method") or ""),
                str(location.get("employer_state") or ""),
                states,
            )
            if index in held:
                trust = "uncorroborated"
            rows.append({
                "employer_name": employer,
                **{field: location.get(field, "") for field in ADDRESS_FIELDS},
                "employer_latitude": None,
                "employer_longitude": None,
                "is_primary": bool(location.get("is_primary")),
                "address_source": source,
                "address_trust": trust,
                # the geocode cache is keyed on the text as resolved, before normalising
                "_raw_key": _address_key(location),
            })
    return rows, managed, review


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


def _without_trust(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Rows kept from the previous CSV for employers the cache no longer knows: kept for reference, never published.

    Their source (a manual row, a cache entry) is gone, so a 'verified'/'grounded'
    label is no longer backed by anything (30 rows deleted from the manual CSV kept
    publishing, at least 9 of them wrong). 'uncorroborated' because the loader
    rejects an address row with a blank trust. Every such row with an address is
    listed for review on every build, so the list does not empty itself once the
    labels are gone; 'method' keeps the label the row came with.
    """
    kept, review = [], []
    for row in rows:
        if str(row.get("employer_address") or "").strip():
            review.append(_review_row(
                row["employer_name"], {**row, "is_primary": str(row.get("is_primary")).lower() == "true"},
                REVIEW_NO_CURRENT_SOURCE, method=f"previous:{row.get('address_trust') or ''}",
            ))
            if row.get("address_trust") in PUBLISHABLE_ADDRESS_TRUST:
                row = {**row, "address_trust": "uncorroborated"}
        kept.append(row)
    return kept, review


def _attach_coordinates(frame: pd.DataFrame) -> pd.DataFrame:
    """Coordinates for rows from the cache: the best accepted point among the keys one office is cached under.

    The geocode cache holds an office under the text as resolved ('20900 N.E. 30TH
    AVENUE, SUITE 203'), its normalised form ('20900 NE 30TH AVE STE 203') and, through
    the donors, the street without its unit ('20900 NE 30TH AVE'). A street point beats
    a ZIP centroid, which beats a town's point; on a tie the text as resolved wins. One
    normalised address takes the best point any of its spellings has. Rows without a
    '_raw_key' (kept from the previous CSV) keep their coordinates.
    """
    frame = frame.copy()
    from_cache = frame["_raw_key"].notna()
    if not from_cache.any():
        return frame
    geocodes = _geocodes()
    rows = frame.loc[from_cache]
    streets = rows["employer_address"].fillna("").astype(str)
    foreign = _foreign_mask(rows)
    donor_form = _donor_form_streets(streets).where(~foreign, streets)
    bare = streets.map(_bare_street).where(~foreign, streets)

    best: dict[object, tuple] = {}
    for index, row in rows.iterrows():
        normalized = _address_key(row)
        place = normalized[1:]
        keys = [row["_raw_key"], normalized,
                (numbered_street(donor_form[index]), *place),
                (numbered_street(bare[index]), *place)]
        choice = None
        for order, key in enumerate(keys):
            point = geocodes.get(tuple(key))
            if point is None:
                continue
            rank = (_LEVEL_RANK.get(point[2], 0), -order)
            if choice is None or rank > choice[0]:
                choice = (rank, point)
        best[index] = (normalized, choice)

    by_address: dict[tuple, tuple] = {}
    for normalized, choice in best.values():
        if choice is None:
            continue
        known = by_address.get(normalized)
        if known is None or choice[0][0] > known[0][0]:
            by_address[normalized] = choice
    for index, (normalized, choice) in best.items():
        shared = by_address.get(normalized)
        if shared is not None and (choice is None or shared[0][0] > choice[0][0]):
            choice = shared
        latitude, longitude = (choice[1][0], choice[1][1]) if choice else (None, None)
        frame.at[index, "employer_latitude"] = latitude
        frame.at[index, "employer_longitude"] = longitude
    return frame


def _deduplicate(rows: list[dict], employers: set[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=[*OUTPUT_COLUMNS, "_raw_key"]).astype(object)
    # one spelling per building, so variants of one address collapse below
    frame = normalize_location_addresses(frame)
    frame = _attach_coordinates(frame)
    frame["is_primary"] = frame["is_primary"].map(
        lambda value: value is True or str(value).strip().lower() == "true"
    )
    frame["_trust_rank"] = frame["address_trust"].map(TRUST_RANK).fillna(0)
    key_columns = ["employer_name", *ADDRESS_FIELDS]
    frame = (frame.sort_values(
        ["_trust_rank", "is_primary"], ascending=False, kind="stable"
    ).drop_duplicates(key_columns).drop(columns=["_trust_rank", "_raw_key"]))

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


def _street_match_key(street) -> str:
    """One building's street for comparing an office with a filing address: units, punctuation and ordinal spelling ignored."""
    street = numbered_street(str(street or "").strip().upper())
    street = _bare_street(street)
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", street).split())


def _employee_addresses(df: pd.DataFrame) -> tuple[dict, dict, dict, set]:
    """Who works where and who files where, by building (street key, ZIP5).

    Returns (employer -> {building: employee donors filing there}, employer -> employee
    donors, building -> donors filing there, buildings some filing gives a unit).
    An employee is a donor whose filing names the employer the way referenced_employers
    reads it: the current employer of an active or self-employed filing, the previous
    employer of a retired one. A building with a unit in any filing is not a house: a
    donor who files '55 HUDSON YARDS' / 'FL 50' files an office floor, 'APT 7E' is one
    flat of an apartment building.
    """
    work = df.reindex(columns=[
        "entity_type", "employer_status", "contributor_employer", "previous_employer",
        "donor_key", "contributor_street_1", "contributor_street_2", "contributor_zip",
    ]).fillna("").astype(str)
    work["_street"] = work["contributor_street_1"].map(_street_match_key)
    work["_zip5"] = work["contributor_zip"].str.strip().str[:5]
    filed = work[work["_street"].ne("") & work["donor_key"].ne("")]
    filers = filed.groupby(["_street", "_zip5"])["donor_key"].agg(set).to_dict()
    # the street_1 itself may still hold a unit the donor split could not move
    has_unit = (filed["contributor_street_2"].str.strip().ne("")
                | filed["contributor_street_1"].str.upper().str.contains(_ANY_UNIT_RE))
    with_units = set(zip(filed.loc[has_unit, "_street"], filed.loc[has_unit, "_zip5"]))

    individuals = work[work["entity_type"].eq("INDIVIDUAL")] if "entity_type" in df.columns else work
    pairs = individuals[["employer_status", "contributor_employer"]].drop_duplicates()
    current = {
        (status, employer): current_employer_name(status, employer)
        for status, employer in pairs.itertuples(index=False)
    }
    names = [current[(status, employer)] or (previous.strip() if status == "retired" else "")
             for status, employer, previous in individuals[
                 ["employer_status", "contributor_employer", "previous_employer"]
             ].itertuples(index=False)]
    individuals = individuals.assign(_employer=names)
    individuals = individuals[individuals["_employer"].ne("") & individuals["donor_key"].ne("")]

    donors = individuals.groupby("_employer")["donor_key"].agg(set).to_dict()
    addresses: dict[str, dict] = defaultdict(dict)
    for employer, street, zip5, donor in individuals[
        ["_employer", "_street", "_zip5", "donor_key"]
    ].itertuples(index=False):
        if street:
            addresses[employer].setdefault((street, zip5), set()).add(donor)
    return addresses, donors, filers, with_units


def _city_level_point(city: str, state: str, zipcode: str) -> tuple[object, object]:
    """The ZIP's centroid for an office published without its street, when it lies in the office's state."""
    point = zip_centroid(zipcode)
    if point is None:
        return None, None
    key = "|".join(("", city, state, zipcode)).upper()
    latitude, longitude, _level = accepted_coordinates(
        key, {"lat": point[0], "lng": point[1], "source": "zip_centroid"},
    )
    return latitude, longitude


def withhold_home_streets(frame: pd.DataFrame, df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """A home-based business is published with its city, state and ZIP only (owner decision 2026-09-24).

    A published US office is a home when it is the filing address (street without
    unit + ZIP5) of a donor who works there, has no suite/unit, at most
    HOME_OFFICE_MAX_FILERS donors file at that address and the employer has at most
    HOME_OFFICE_MAX_DONORS donors. Whatever the source (AI or manual), the street and
    its pin are withheld; the ZIP's centroid stands in. A PO box is a mailbox, not a
    home, and a storefront with a suite or several employees keeps its street.
    """
    frame = frame.copy()
    addresses, donors, filers, with_units = _employee_addresses(df)
    streets = frame["employer_address"].fillna("").astype(str)
    candidates = (
        streets.str.strip().ne("")
        & frame["address_trust"].isin(PUBLISHABLE_ADDRESS_TRUST)
        & ~_foreign_mask(frame)
        & ~streets.map(is_po_box)
        & ~streets.str.contains(_ANY_UNIT_RE)
    )
    review = []
    for index in frame.index[candidates]:
        row = frame.loc[index]
        employer = row["employer_name"]
        employer_donors = donors.get(employer, set())
        if not employer_donors or len(employer_donors) > HOME_OFFICE_MAX_DONORS:
            continue
        place = (_street_match_key(row["employer_address"]), str(row["employer_zip"]).strip()[:5])
        if not place[0] or place in with_units or place not in addresses.get(employer, {}):
            continue
        filing_donors = filers.get(place, set())
        if len(filing_donors) > HOME_OFFICE_MAX_FILERS:
            continue
        review.append(_review_row(
            employer, row.to_dict(), REVIEW_HOME_OFFICE,
            employer_donors=len(employer_donors), filers_at_address=len(filing_donors),
        ))
        latitude, longitude = _city_level_point(
            str(row["employer_city"]), str(row["employer_state"]), str(row["employer_zip"]),
        )
        frame.at[index, "employer_address"] = ""
        frame.at[index, "employer_latitude"] = latitude
        frame.at[index, "employer_longitude"] = longitude

    if review:
        # an office that is now only a town may repeat another row of the employer
        frame = (frame.sort_values(["employer_name", "is_primary"], ascending=[True, False], kind="stable")
                 .drop_duplicates(["employer_name", *ADDRESS_FIELDS])
                 .reset_index(drop=True))
    return frame, review


def build_locations(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(employer_locations rows, review rows) from the cleaned filings, the caches and the previous CSV."""
    employers = referenced_employers(df)
    donor_states = _donor_states(df)
    existing = _existing_rows(employers)
    cache_rows, managed, review = _cache_rows(df, employers, donor_states)
    preserved, unsourced = _without_trust([
        row for row in existing
        if row["employer_name"] not in managed
    ])
    locations = _deduplicate(cache_rows + preserved, employers)
    locations, home_offices = withhold_home_streets(locations, df)
    review_frame = pd.DataFrame(review + unsourced + home_offices, columns=REVIEW_COLUMNS)
    review_frame = review_frame.sort_values(["reason", "employer_name"], kind="stable").reset_index(drop=True)
    return locations, review_frame


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
    locations, review = build_locations(df)

    locations.to_csv(EMPLOYER_LOCATIONS_CSV, index=False, na_rep="")
    review.to_csv(DATA_DIR / REVIEW_CSV, index=False, na_rep="")
    has_address = locations["employer_address"].fillna("").ne("")
    publishable = (
        has_address
        & locations["address_trust"].isin(PUBLISHABLE_ADDRESS_TRUST)
    ).sum()
    logger.info(
        f"  employer_locations.csv: {len(locations):,} locations, "
        f"{len(set(locations['employer_name'])):,} companies, {publishable:,} publishable"
    )
    if len(review):
        counts = ", ".join(f"{count:,} {reason}" for reason, count in review["reason"].value_counts().items())
        logger.info(f"  {REVIEW_CSV}: {counts}")

    slim = df.drop(columns=[column for column in ADDRESS_COLUMNS if column in df.columns])
    slim.to_csv(CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  contributions_cleaned.csv: {len(slim.columns)} columns")
    return len(locations), len(slim.columns)
