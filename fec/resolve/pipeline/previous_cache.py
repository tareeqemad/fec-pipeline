"""The previous-employer cache: keys, entries, readers."""

import pandas as pd

from fec.cleaning.employer_status import is_real_employer
from fec.cleaning.previous_employer import (
    explicitly_self_employed,
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.io import cell_text
from fec.resolve.pipeline.constants import RETIRED

# built from the cleaned CSV each run
LOCAL_METHODS = frozenset({"cross_record", "cleaned_previous"})

PROTECTED_METHODS = {
    "manual_clear",
    "manual_override",
    "cleared_swapped_record",
}


# cache key of one donor
def previous_cache_key(donor_key) -> str:
    return f"donor:{cell_text(donor_key)}"


# a real company or SELF-EMPLOYED, else blank
def clean_previous_employer(value) -> str:
    cleaned = normalize_previous_employer_value(value)
    return cleaned if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned) else ""


# clean name; unclean spellings stay lookup keys
def previous_employer_identity(entry: dict | None) -> tuple[str, tuple[str, ...]]:
    if not entry:
        return "", ()

    raw_name = cell_text(entry.get("employer"))
    normalized_name = cell_text(entry.get("employer_normalized"))
    source_name = cell_text(entry.get("employer_source"))

    clean_name = ""
    for candidate in (raw_name, normalized_name, source_name):
        clean_name = clean_previous_employer(candidate)
        if clean_name:
            break
    if not clean_name:
        return "", ()

    keys = []
    for name in (clean_name, normalized_name, raw_name, source_name):
        key = name.strip().upper()
        if key and key not in keys:
            keys.append(key)
    return clean_name, tuple(keys)


# individuals, and each retired donor's latest row
def retired_donors(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    retired_keys = set(
        individuals.loc[
            individuals["contributor_employer"] == RETIRED, "donor_key"
        ].unique()
    )
    latest = (
        individuals[individuals["donor_key"].isin(retired_keys)]
        .sort_values("contribution_receipt_date", ascending=False)
        .drop_duplicates("donor_key", keep="first")
    )
    return individuals, latest


# one clean cache entry, raw spelling kept
def cache_entry(
    raw_employer,
    *,
    state,
    method,
    source_date="",
    source_name="",
    source_city="",
    source_zip="",
) -> dict:
    raw_name = cell_text(raw_employer)
    employer = normalize_previous_employer_value(raw_name)
    if employer == "SELF-EMPLOYED" and not explicitly_self_employed(raw_name):
        employer = ""
    if source_name:
        employer = preserve_own_named_legal_employer(
            employer,
            raw_name,
            source_name,
        )
    if not employer:
        return {"employer": "", "method": f"{method}_not_found"}

    entry = {
        "employer": employer,
        "employer_normalized": employer.upper(),
        "state": cell_text(state),
        "method": method,
    }
    if raw_name.upper() != employer.upper():
        entry["employer_source"] = raw_name
    provenance = {
        "source_date": source_date,
        "source_name": source_name,
        "source_city": source_city,
        "source_zip": source_zip,
    }
    entry.update({key: cell_text(value) for key, value in provenance.items() if value})
    return entry
