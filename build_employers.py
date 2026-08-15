"""Build employer locations after employer geocoding."""

import json
from collections import defaultdict

import pandas as pd

from fec.cleaning.previous_employer import referenced_employers
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.env import CLEANED_CSV, DATA_DIR, EMPLOYER_LOCATIONS_CSV
from fec.log import get_logger
from fec.resolve.pipeline.constants import EMPLOYER_ADDR_CACHE
from fec.resolve.pipeline.locations import (
    ADDRESS_FIELDS,
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


def _read_json(path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _address_key(values) -> tuple[str, ...]:
    return tuple(str(values.get(field) or "").strip().upper()
                 for field in ADDRESS_FIELDS)


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
    cache = _read_json(DATA_DIR / "geocode_cache.json")
    coordinates = {}
    for key, result in cache.items():
        parts = tuple(part.strip().upper() for part in key.split("|"))
        if len(parts) == 4 and result.get("lat") is not None:
            coordinates[parts] = (result["lat"], result["lng"])
    return coordinates


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
    resolved = locations["employer_address"].fillna("").ne("").sum()
    logger.info(
        f"  employer_locations.csv: {len(locations):,} locations, "
        f"{len(employers):,} companies, {resolved:,} resolved"
    )

    slim = df.drop(columns=[column for column in ADDRESS_COLUMNS if column in df.columns])
    slim.to_csv(CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  contributions_cleaned.csv: {len(slim.columns)} columns")
    return len(locations), len(slim.columns)
