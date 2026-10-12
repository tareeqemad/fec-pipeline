"""Apply resolved addresses to DataFrame columns."""

from dataclasses import dataclass, field

import pandas as pd

from fec.cleaning.donor_consistency.retired import dated_previous_employers
from fec.cleaning.employer_status import classify_employer_status
from fec.cleaning.employer_synonyms.canonical import canonical_key, recanonicalize_employers
from fec.cleaning.previous_employer import normalize_previous_employer_column
from fec.io import cell_text
from fec.resolve.pipeline.location_choice import select_location
from fec.resolve.pipeline.locations import address_cache_lookup
from fec.resolve.pipeline.previous_cache import previous_employer_identity
from fec.resolve.pipeline.previous_choice import (
    choose_previous_entry,
    display_previous_employer,
    expand_previous_employer_abbreviations,
    first_filed_employers,
    preserve_previous_employer_display,
)
from fec.resolve.pipeline.quality_fixes import (
    clear_nonindividual_employer,
    fix_employer_address_quality,
)


@dataclass(frozen=True)
class ResolveContext:
    previous_cache: object
    address_lookup: dict
    # row index to donor's latest earlier employer
    dated_previous: dict = field(default_factory=dict)
    # sub_id to the employer as filed
    filed_employers: dict = field(default_factory=dict)
    # (donor_key, employer) to first filed date
    first_filed: dict = field(default_factory=dict)


# build employer-name aliases from publishable cache entries
def _address_aliases(addr_cache) -> dict:
    entries = getattr(addr_cache, "data", addr_cache)
    return address_cache_lookup(entries, publishable_only=True)


# resolve every row into DataFrame columns
def apply_results(df: pd.DataFrame, prev_cache, addr_cache, filed_employers: dict | None = None) -> pd.DataFrame:
    prior_previous = df["previous_employer"].copy()

    cols = {
        "employer_address": [],
        "employer_city": [],
        "employer_state": [],
        "employer_zip": [],
        "resolve_method": [],
        "resolve_confidence": [],
        "employer_status": [],
        "previous_employer": [],
    }

    retired_rows = df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].eq("RETIRED")
    context = ResolveContext(
        previous_cache=prev_cache,
        address_lookup=_address_aliases(addr_cache),
        dated_previous=dated_previous_employers(df, retired_rows),
        first_filed=first_filed_employers(df),
        filed_employers=filed_employers or {},
    )
    for index, row in df.iterrows():
        result = _resolve_row(row, context, index)
        for column in cols:
            cols[column].append(result.get(column, ""))

    for column, values in cols.items():
        df[column] = values

    normalize_previous_employer_column(df)
    fix_employer_address_quality(df)
    preserve_previous_employer_display(df, prior_previous)
    expand_previous_employer_abbreviations(df)
    recanonicalize_employers(df)
    # an org's address is already the donor's
    clear_nonindividual_employer(df)
    return df


# build the shared per-row resolve result dict
def _result(
    status: str,
    entry: dict | None = None,
    *,
    method: str = "skip",
    confidence: str = "NONE",
    address: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    previous: str = "",
    method_prefix: str = "",
) -> dict:
    source = entry or {}
    return {
        "employer_address": source.get("employer_address", address),
        "employer_city": source.get("employer_city", city),
        "employer_state": source.get("employer_state", state),
        "employer_zip": source.get("employer_zip", zip_code),
        "resolve_method": f"{method_prefix}{source.get('method', method)}",
        "resolve_confidence": source.get("confidence", confidence),
        "employer_status": status,
        "previous_employer": previous,
    }


# donor's own address as an employer result
def _own_address(
    row: pd.Series,
    state: str,
    status: str,
    method: str,
    confidence: str,
) -> dict | None:
    street = cell_text(row["contributor_street_1"])
    if not street:
        return None
    return _result(
        status,
        method=method,
        confidence=confidence,
        address=street,
        city=cell_text(row["contributor_city"]),
        state=state,
        zip_code=cell_text(row["contributor_zip"]),
    )


# first usable cached address for the keys
def _cached_address(
    addr_cache,
    keys: tuple[str, ...],
    donor_zip: str = "",
    donor_state: str = "",
) -> dict | None:
    for key in keys:
        exact_key = str(key).upper()
        exact = addr_cache.get(exact_key)
        selected = select_location(exact, donor_zip, donor_state)
        if selected:
            return selected
        if exact is not None:
            continue

        alias = addr_cache.get(canonical_key(exact_key))
        selected = select_location(alias, donor_zip, donor_state)
        if selected:
            return selected
    return None


# active employer's address from the cache
def _resolve_active(
    employer: str,
    state: str,
    zip_code: str,
    context: ResolveContext,
) -> dict:
    cached = _cached_address(
        context.address_lookup,
        (employer.upper(),),
        zip_code,
        state,
    )
    if not cached:
        return _result("active", method="pending")
    prefix = "" if cached.get("is_primary") else "nearest_"
    return _result("active", cached, method="ai_openai", method_prefix=prefix)


# retired filer's previous employer and its office
def _resolve_retired(
    row: pd.Series,
    context: ResolveContext,
    state: str,
    zip_code: str,
    index=None,
) -> dict:
    prev_entry = choose_previous_entry(row, context, index)
    prev_name, address_keys = previous_employer_identity(prev_entry)
    if not prev_name:
        return _result("retired")

    prev_name = display_previous_employer(prev_name, prev_entry, row["contributor_name"])
    legal_key = prev_name.strip().upper()
    address_keys = (legal_key, *(key for key in address_keys if key != legal_key))
    cached = _cached_address(context.address_lookup, address_keys, zip_code, state)
    if not cached:
        return _result("retired", method="pending_address", previous=prev_name)

    location_prefix = "" if cached.get("is_primary") else "nearest_"
    method_prefix = f"{prev_entry.get('method', 'cross_record')}+{location_prefix}"
    return _result(
        "retired",
        cached,
        method="ai_openai",
        method_prefix=method_prefix,
        previous=prev_name,
    )


# resolve one row by its employer status
def _resolve_row(row: pd.Series, context: ResolveContext, index=None) -> dict:
    entity = row["entity_type"]
    state = cell_text(row["contributor_state"])
    if entity == "COMMITTEE/PAC":
        return _result("committee")
    if entity == "ORGANIZATION":
        return _result("organization")

    employer = cell_text(row["contributor_employer"])
    zip_code = cell_text(row["contributor_zip"])
    status = classify_employer_status(
        employer,
        row["contributor_occupation"],
        row["occupation_category"],
    )
    if status == "active":
        return _resolve_active(employer, state, zip_code, context)
    if status == "retired":
        return _resolve_retired(row, context, state, zip_code, index)
    if status == "self_employed":
        return _own_address(
            row,
            state,
            "self_employed",
            "self_employed_own_address",
            "HIGH",
        ) or _result("self_employed")
    return _result(status)
