"""Apply resolved addresses to DataFrame columns."""

import pandas as pd

from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.previous_employer import (
    classify_employer_status,
    preserve_own_named_legal_employer,
)

from .helpers import _prev_key, _previous_employer_identity, _s
from .locations import address_cache_lookup, select_location
from .quality_fixes import (
    _clear_nonindividual_employer,
    _fix_employer_address_quality,
)


def _address_aliases(addr_cache) -> dict:
    """Build exact and canonical cache aliases."""
    entries = getattr(addr_cache, "data", addr_cache)
    return address_cache_lookup(entries)


def apply_results(df: pd.DataFrame, prev_cache, addr_cache,
                  comm_cache) -> pd.DataFrame:
    """Write resolved addresses to DataFrame columns."""
    prior_previous = df.get("previous_employer")
    if prior_previous is not None:
        prior_previous = prior_previous.copy()

    cols = {
        "employer_address": [], "employer_city": [],
        "employer_state": [], "employer_zip": [],
        "resolve_method": [], "resolve_confidence": [],
        "employer_status": [], "previous_employer": [],
    }

    address_lookup = _address_aliases(addr_cache)
    for _, row in df.iterrows():
        result = _resolve_row(row, prev_cache, address_lookup, comm_cache)
        for column in cols:
            cols[column].append(result.get(column, ""))

    for column, values in cols.items():
        df[column] = values

    _fix_employer_address_quality(df)
    _preserve_previous_employer_display(df, prior_previous)
    # A committee/org IS the entity - its address is already the donor address,
    # so don't duplicate it into employer_*.
    _clear_nonindividual_employer(df)
    return df


def _preserve_previous_employer_display(
    df: pd.DataFrame, prior_previous: pd.Series | None,
) -> None:
    """Keep build_employers' spelling when resolve found the same company.

    Resolve may replace the identity; build_employers owns its display spelling.
    This boundary prevents suffix/style churn on pipeline-tail reruns.
    """
    if prior_previous is None or "previous_employer" not in df.columns:
        return

    prior = prior_previous.fillna("").astype(str).str.strip()
    current = df["previous_employer"].fillna("").astype(str).str.strip()
    prior_key = prior.map(canonical_key)
    same_company = prior_key.ne("") & current.map(canonical_key).eq(prior_key)
    if same_company.any():
        df.loc[same_company, "previous_employer"] = prior[same_company]


def _result(
    status: str, entry: dict | None = None, *, method: str = "skip",
    confidence: str = "NONE", address: str = "", city: str = "",
    state: str = "", zip_code: str = "", previous: str = "",
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


def _own_address(
    row: pd.Series, state: str, status: str, method: str, confidence: str,
) -> dict | None:
    """Return the donor/entity address in employer-result form, when present."""
    street = _s(row.get("contributor_street_1"))
    if not street:
        return None
    return _result(
        status, method=method, confidence=confidence, address=street,
        city=_s(row.get("contributor_city")), state=state,
        zip_code=_s(row.get("contributor_zip")),
    )


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


def _resolve_row(row: pd.Series, prev_cache, addr_cache, comm_cache) -> dict:
    """Resolve one row."""
    entity = row.get("entity_type", "")
    emp = _s(row.get("contributor_employer")).strip()
    emp_upper = emp.upper()
    state = _s(row.get("contributor_state")).strip()
    zip_code = _s(row.get("contributor_zip")).strip()

    if entity == "COMMITTEE/PAC":
        name = str(row.get("contributor_name", ""))
        cached = comm_cache.get(f"{name}|{state}")
        if cached and cached.get("employer_address"):
            return _result("committee", cached, method="fec_api", state=state)
        return (
            _own_address(
                row, state, "committee", "committee_own_address", "LOW",
            )
            or _result("committee")
        )

    # An org IS the entity (its address is the donor address) - no employer lookup.
    if entity == "ORGANIZATION":
        return _result("organization")

    status = classify_employer_status(
        emp,
        row.get("contributor_occupation"),
        row.get("occupation_category"),
    )

    if status == "active":
        cached = _cached_address(
            addr_cache, (emp_upper,), zip_code, state,
        )
        if cached:
            prefix = "" if cached.get("is_primary") else "nearest_"
            return _result(
                "active", cached, method="ai_openai", method_prefix=prefix,
            )
        return _result("active", method="pending")

    if status == "retired":
        prev_key = _prev_key(row.get("contributor_name", ""), state)
        prev_entry = prev_cache.get(prev_key)
        prev_name, address_keys = _previous_employer_identity(prev_entry)
        if not prev_name:
            return _result("retired")

        # Normal matching strips legal suffixes. For an own-named practice,
        # the source suffix is what proves this is a company, not the donor.
        donor_name = row.get("contributor_name", "")
        for source in (
            prev_entry.get("employer_source", ""),
            prev_entry.get("employer", ""),
            prev_entry.get("employer_normalized", ""),
        ):
            displayed = preserve_own_named_legal_employer(
                prev_name, source, donor_name,
            )
            if displayed:
                prev_name = displayed
                break

        # An own-named legal company must prefer its suffix-bearing cache key.
        # The bare personal-name key may point to a residence or legacy guess.
        legal_key = prev_name.strip().upper()
        address_keys = (
            legal_key,
            *(key for key in address_keys if key != legal_key),
        )

        cached = _cached_address(
            addr_cache, address_keys, zip_code, state,
        )
        if cached:
            location_prefix = "" if cached.get("is_primary") else "nearest_"
            return _result(
                "retired", cached, method="ai_openai",
                method_prefix=(
                    f"{prev_entry.get('method', 'cross_record')}+"
                    f"{location_prefix}"
                ),
                previous=prev_name,
            )
        return _result(
            "retired", method="pending_address", previous=prev_name,
        )

    if status == "self_employed":
        return (
            _own_address(
                row, state, "self_employed",
                "self_employed_own_address", "HIGH",
            )
            or _result("self_employed")
        )

    return _result(status)
