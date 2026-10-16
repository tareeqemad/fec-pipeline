"""Build employer locations after employer geocoding."""

from collections import defaultdict

import pandas as pd

from fec.cleaning.work.status import referenced_employers
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.contract import check_output
from fec.env import CACHE_DIR, CLEANED_CSV, EMPLOYER_LOCATIONS_CSV, REPORTS_DIR
from fec.io import write_csv_atomic
from fec.log import get_logger
from fec.resolve.addresses import (
    _address_key,
    _attach_coordinates,
    normalize_location_addresses,
)
from fec.resolve.cache import EMPLOYER_ADDR_CACHE, _read_json
from fec.resolve.locations import (
    ADDRESS_FIELDS,
    PUBLISHABLE_ADDRESS_TRUST,
    address_cache_lookup,
    location_candidates,
    publishable_first_entry,
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
REVIEW_CSV = "employer_address_review.csv"
REVIEW_COLUMNS = [
    "employer_name",
    "reason",
    *ADDRESS_FIELDS,
    "is_primary",
    "address_source",
    "method",
    "donor_states",
]
REVIEW_ALIAS_OUTSIDE_DONOR_STATES = "grounded_alias_outside_donor_states"
REVIEW_NO_CURRENT_SOURCE = "carried_over_without_source"


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
    """Check whether a search result is outside all donor states."""
    method = str(location.get("method") or "")
    state = str(location.get("employer_state") or "").strip().upper()
    return method.endswith("_search") and state not in donor_states


def _cache_rows(
    df: pd.DataFrame,
    employers: set[str],
    donor_states: dict[str, set[str]] | None = None,
) -> tuple[list[dict], set[str], list[dict]]:
    """Build cache rows and flag alternate addresses outside donor states."""
    cache = _read_json(CACHE_DIR / EMPLOYER_ADDR_CACHE)
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
    """Keep unsourced addresses for review without publishing them."""
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
    review_frame = pd.DataFrame(review + unsourced, columns=REVIEW_COLUMNS)
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

    write_csv_atomic(locations, EMPLOYER_LOCATIONS_CSV, index=False, na_rep="")
    write_csv_atomic(review, REPORTS_DIR / REVIEW_CSV, index=False, na_rep="")
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
    check_output("employers", df.columns, slim.columns)
    write_csv_atomic(slim, CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  contributions_cleaned.csv: {len(slim.columns)} columns")
    return len(locations), len(slim.columns)
