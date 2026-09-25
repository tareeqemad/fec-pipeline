"""Apply resolved addresses to DataFrame columns."""

from dataclasses import dataclass, field

import pandas as pd

from fec.cleaning.donor_consistency.retired import dated_previous_employers
from fec.cleaning.employer_status import classify_employer_status
from fec.cleaning.employer_synonyms import _recanonicalize_employers, canonical_key
from fec.cleaning.previous_employer import preserve_own_named_legal_employer
from fec.resolve.pipeline.helpers import _prev_key, _previous_employer_identity, _s
from fec.resolve.pipeline.location_choice import select_location
from fec.resolve.pipeline.locations import address_cache_lookup
from fec.resolve.pipeline.quality_fixes import (
    _clear_nonindividual_employer,
    _fix_employer_address_quality,
)
from fec.resolve.pipeline.steps.previous_employer import PROTECTED_METHODS


@dataclass(frozen=True)
class ResolveContext:
    previous_cache: object
    address_lookup: dict
    # row index -> the donor's own latest employer on or before that retired filing
    dated_previous: dict = field(default_factory=dict)


# build employer-name aliases from publishable cache entries
def _address_aliases(addr_cache) -> dict:
    """Build aliases from publishable cache entries."""
    entries = getattr(addr_cache, "data", addr_cache)
    return address_cache_lookup(entries, publishable_only=True)


# resolve every row and write results into DataFrame columns
def apply_results(df: pd.DataFrame, prev_cache, addr_cache) -> pd.DataFrame:
    """Write resolved addresses to DataFrame columns."""
    prior_previous = df.get("previous_employer")
    if prior_previous is not None:
        prior_previous = prior_previous.copy()

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

    dated = {}
    if {"entity_type", "donor_key", "contribution_receipt_date"}.issubset(df.columns):
        retired_rows = df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].eq("RETIRED")
        dated = dated_previous_employers(df, retired_rows)
    context = ResolveContext(
        previous_cache=prev_cache,
        address_lookup=_address_aliases(addr_cache),
        dated_previous=dated,
    )
    for index, row in df.iterrows():
        result = _resolve_row(row, context, index)
        for column in cols:
            cols[column].append(result.get(column, ""))

    for column, values in cols.items():
        df[column] = values

    _fix_employer_address_quality(df)
    _preserve_previous_employer_display(df, prior_previous)
    _recanonicalize_employers(df)
    # A committee/org IS the entity - its address is already the donor address,
    # so don't duplicate it into employer_*.
    _clear_nonindividual_employer(df)
    return df


# keep build_employers' spelling when resolve found the same company
def _preserve_previous_employer_display(
    df: pd.DataFrame,
    prior_previous: pd.Series | None,
) -> None:
    """Keep build_employers' spelling when resolve found the same company.

    Resolve may replace the identity; build_employers owns its display spelling.
    This boundary prevents suffix/style churn on pipeline-tail reruns.
    """
    if prior_previous is None or "previous_employer" not in df.columns:
        return

    # Employer names are uppercase: a tail rerun must not bring back a
    # mixed-case spelling (the old "WhatsApp LLC") that the contract fixed.
    prior = prior_previous.fillna("").astype(str).str.strip().str.upper()
    current = df["previous_employer"].fillna("").astype(str).str.strip()
    prior_key = prior.map(canonical_key)
    same_company = prior_key.ne("") & current.map(canonical_key).eq(prior_key)
    if same_company.any():
        df.loc[same_company, "previous_employer"] = prior[same_company]


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
    """Return the complete, shared result shape."""
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


# return the donor's own address in employer-result form
def _own_address(
    row: pd.Series,
    state: str,
    status: str,
    method: str,
    confidence: str,
) -> dict | None:
    """Return the donor/entity address in employer-result form, when present."""
    street = _s(row.get("contributor_street_1"))
    if not street:
        return None
    return _result(
        status,
        method=method,
        confidence=confidence,
        address=street,
        city=_s(row.get("contributor_city")),
        state=state,
        zip_code=_s(row.get("contributor_zip")),
    )


# find the first usable cached address for given keys
def _cached_address(
    addr_cache,
    keys: tuple[str, ...],
    donor_zip: str = "",
    donor_state: str = "",
) -> dict | None:
    """Return the first usable address under an exact or canonical cache key."""
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


# resolve an active employer's address from the cache
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


# choose the best display spelling for a previous employer
def _display_previous_employer(
    prev_name: str, prev_entry: dict, donor_name: str
) -> str:
    sources = (
        prev_entry.get("employer_source", ""),
        prev_entry.get("employer", ""),
        prev_entry.get("employer_normalized", ""),
    )
    for source in sources:
        displayed = preserve_own_named_legal_employer(prev_name, source, donor_name)
        if displayed:
            return displayed
    return prev_name


# resolve a retired filer's previous employer and its office
def _resolve_retired(
    row: pd.Series,
    context: ResolveContext,
    state: str,
    zip_code: str,
    index=None,
) -> dict:
    """A retired filing's previous employer and its office.

    The cache holds one previous employer per donor (their latest). A donor who
    retired, went back to work and retired again (RUDY, RICHARD: BASCO INC, then
    KINZIE HOUSE DESIGNS in 2026) needs each filing's own: the latest employer
    the donor filed on or before that date. A hand-set cache entry still wins.
    """
    prev_entry = context.previous_cache.get(_prev_key(row.get("donor_key", "")))
    dated = context.dated_previous.get(index)
    if dated and not (prev_entry and prev_entry.get("method") in PROTECTED_METHODS):
        prev_entry = {"employer": dated, "method": "cross_record"}
    prev_name, address_keys = _previous_employer_identity(prev_entry)
    if not prev_name:
        return _result("retired")

    prev_name = _display_previous_employer(
        prev_name,
        prev_entry,
        row.get("contributor_name", ""),
    )
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


# dispatch one row to the resolver matching its employer status
def _resolve_row(row: pd.Series, context: ResolveContext, index=None) -> dict:
    """Resolve one row."""
    entity = row.get("entity_type", "")
    state = _s(row.get("contributor_state")).strip()
    if entity == "COMMITTEE/PAC":
        return _result("committee")
    if entity == "ORGANIZATION":
        return _result("organization")

    employer = _s(row.get("contributor_employer")).strip()
    zip_code = _s(row.get("contributor_zip")).strip()
    status = classify_employer_status(
        employer,
        row.get("contributor_occupation"),
        row.get("occupation_category"),
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
