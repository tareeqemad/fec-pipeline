"""Apply resolved addresses to DataFrame columns."""

import pandas as pd

from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.previous_employer import (
    is_real_employer,
    preserve_own_named_legal_employer,
)

from .constants import RETIRED, SELF_EMPLOYED
from .helpers import _prev_key, _previous_employer_identity, _s
from .quality_fixes import (
    _clear_nonindividual_employer,
    _fix_employer_address_quality,
)


def _branch_aliases(branch_cache) -> dict:
    """Add unique canonical-name aliases to the small curated branch cache."""
    if not branch_cache:
        return {}

    entries = getattr(branch_cache, "data", branch_cache)
    lookup = {str(key).upper(): value for key, value in entries.items()}
    aliases, ambiguous = {}, set()
    for key, value in entries.items():
        if "|" not in str(key):
            continue
        employer, state = str(key).rsplit("|", 1)
        alias = f"{canonical_key(employer)}|{state.strip().upper()}"
        if not alias or alias.startswith("|"):
            continue
        prior = aliases.get(alias)
        if prior is not None and prior != value:
            ambiguous.add(alias)
        else:
            aliases[alias] = value

    for alias in ambiguous:
        aliases.pop(alias, None)
    for alias, value in aliases.items():
        lookup.setdefault(alias, value)
    return lookup


def _address_aliases(addr_cache) -> dict:
    """Add unambiguous canonical-name aliases to the employer address cache."""
    entries = getattr(addr_cache, "data", addr_cache)
    lookup = {str(key).upper(): value for key, value in entries.items()}
    grouped = {}

    for key, entry in entries.items():
        alias = canonical_key(str(key))
        if not alias:
            continue
        grouped.setdefault(alias, []).append(entry)

    for alias, candidates in grouped.items():
        invalid = [item for item in candidates
                   if item.get("method") == "manual_invalid"]
        if invalid:
            lookup.setdefault(alias, invalid[0])
            continue

        usable = [item for item in candidates if _usable_address(item)]
        manual = [item for item in usable
                  if item.get("method") == "manual_override"]
        preferred = manual or usable
        signatures = {
            tuple(item.get(field, "") for field in (
                "employer_address", "employer_city",
                "employer_state", "employer_zip",
            ))
            for item in preferred
        }
        if len(signatures) == 1:
            lookup.setdefault(alias, preferred[0])

    return lookup


def apply_results(df: pd.DataFrame, prev_cache, addr_cache, comm_cache,
                  branch_cache=None) -> pd.DataFrame:
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
    branch_lookup = _branch_aliases(branch_cache)
    for _, row in df.iterrows():
        result = _resolve_row(
            row, prev_cache, address_lookup, comm_cache, branch_lookup,
        )
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


def _usable_address(entry: dict | None) -> bool:
    """An address may be street-level or an intentional manual city fallback."""
    return bool(entry and (
        entry.get("employer_address")
        or (entry.get("method") == "manual_override"
            and entry.get("employer_city"))
    ))


def _cached_address(addr_cache, keys: tuple[str, ...]) -> dict | None:
    """Return the first usable address under an exact or canonical cache key."""
    for key in keys:
        exact_key = str(key).upper()
        exact = addr_cache.get(exact_key)
        if _usable_address(exact):
            return exact
        if exact is not None:
            continue

        alias = addr_cache.get(canonical_key(exact_key))
        if _usable_address(alias):
            return alias
    return None


def _resolve_row(row: pd.Series, prev_cache, addr_cache, comm_cache,
                 branch_cache=None) -> dict:
    """Resolve one row."""
    entity = row.get("entity_type", "")
    emp = _s(row.get("contributor_employer")).strip()
    emp_upper = emp.upper()
    state = _s(row.get("contributor_state")).strip()

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

    # A donor's branch office wins over the corporate HQ.
    if is_real_employer(emp):
        branch = None
        if branch_cache and state:
            branch_key = f"{emp_upper}|{state.upper()}"
            branch = branch_cache.get(branch_key)
            if not branch:
                branch = branch_cache.get(f"{canonical_key(emp_upper)}|{state.upper()}")
        if branch and branch.get("employer_address"):
            return _result(
                "active", branch, method="manual_override", state=state,
                method_prefix="branch_",
            )

        cached = _cached_address(addr_cache, (emp_upper,))
        if cached:
            return _result("active", cached, method="ai_openai")
        return _result("active", method="pending")

    if emp_upper == RETIRED:
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

        cached = _cached_address(addr_cache, address_keys)
        if cached:
            return _result(
                "retired", cached, method="ai_openai",
                method_prefix=f"{prev_entry.get('method', 'cross_record')}+",
                previous=prev_name,
            )
        return _result(
            "retired", method="pending_address", previous=prev_name,
        )

    if emp_upper == SELF_EMPLOYED:
        return (
            _own_address(
                row, state, "self_employed",
                "self_employed_own_address", "HIGH",
            )
            or _result("self_employed")
        )

    status = (
        "not_employed"
        if emp_upper in ("NOT EMPLOYED", "STUDENT", "HOMEMAKER", "UNEMPLOYED")
        else "missing"
    )
    return _result(status)
