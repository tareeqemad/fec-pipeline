"""Step 3: web-grounded employer address resolution."""

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd

from fec.config.geography import US_STATES
from fec.log import get_logger
from fec.resolve.pipeline.ai_client import ai_method, get_ai_client, resolver_id
from fec.resolve.pipeline.constants import AI_SYSTEM_PROMPT, EMPLOYER_PROMPT_VERSION
from fec.resolve.pipeline.helpers import _s
from fec.resolve.pipeline.steps.employer_lookups import (
    EmployerLookup,
    build_employer_prompt,
    collect_employer_lookups,
)
from fec.resolve.pipeline.steps.web_search import run_web_search

logger = get_logger(__name__)

AI_LOOKUP_BATCH_SIZE = 200
_ADDRESS_TYPES = frozenset(
    {
        "HEADQUARTERS",
        "PRINCIPAL_US_OFFICE",
        "PRIMARY_LOCATION",
    }
)
_OFFICE_TYPES = frozenset({"OFFICE"})
_CONFIDENCE_LEVELS = frozenset({"HIGH", "MEDIUM"})
# a full-match ZIP5, digits only: "10001"
_ZIP_RE = re.compile(r"^\d{5}$")


# true if value is a valid http(s) url
def _valid_source_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


# validate and normalize one sourced us employer location
def _validated_location(result: dict, address_types: frozenset[str]) -> dict | None:
    """Validate one sourced US employer location."""
    address = _s(result.get("address")).strip()
    city = _s(result.get("city")).strip()
    state = _s(result.get("state")).strip().upper()
    zip_code = _s(result.get("zip")).strip()
    address_type = _s(result.get("address_type")).strip().upper()
    confidence = _s(result.get("confidence")).strip().upper()
    source_url = _s(result.get("source_url")).strip()

    if not (
        address
        and city
        and state in US_STATES
        and _ZIP_RE.fullmatch(zip_code)
        and address_type in address_types
        and confidence in _CONFIDENCE_LEVELS
        and _valid_source_url(source_url)
    ):
        return None

    return {
        "employer_address": address,
        "employer_city": city,
        "employer_state": state,
        "employer_zip": zip_code,
        "confidence": confidence,
        "address_type": address_type,
        "source_name": _s(result.get("source_name")).strip(),
        "source_url": source_url,
    }


# validate an ai result into a cacheable location entry
def _resolved_cache_entry(
    lookup: EmployerLookup,
    result: dict | None,
    resolver_tag: str,
) -> dict | None:
    """Validate an AI result before it can become a trusted search entry."""
    if not isinstance(result, dict):
        return None
    if _s(result.get("name")).strip().casefold() != lookup.name.casefold():
        return None

    primary = _validated_location(result, _ADDRESS_TYPES)
    if primary is None:
        return None

    shared = {
        "method": ai_method(),
        "resolver": resolver_tag,
        "prompt_version": EMPLOYER_PROMPT_VERSION,
        "resolved_on": datetime.now(timezone.utc).date().isoformat(),
    }
    entry = {
        **primary,
        **shared,
        "matched_company_name": _s(result.get("matched_company_name")).strip(),
    }

    primary_key = tuple(
        primary[field]
        for field in (
            "employer_address",
            "employer_city",
            "employer_state",
            "employer_zip",
        )
    )
    locations = []
    seen = {primary_key}
    for raw_location in result.get("locations", []):
        if not isinstance(raw_location, dict):
            continue
        location = _validated_location(raw_location, _OFFICE_TYPES)
        if location is None:
            continue
        key = tuple(
            location[field]
            for field in (
                "employer_address",
                "employer_city",
                "employer_state",
                "employer_zip",
            )
        )
        if key in seen:
            continue
        seen.add(key)
        locations.append({**location, **shared})
    if locations:
        entry["locations"] = locations
    return entry


# true if the ai explicitly said it doesn't know
def _is_explicit_unknown(lookup: EmployerLookup, result: dict | None) -> bool:
    return (
        isinstance(result, dict)
        and _s(result.get("name")).strip().casefold() == lookup.name.casefold()
        and _s(result.get("confidence")).strip().upper() == "UNKNOWN"
    )


# cache entry recording an ai lookup with no result
def _not_found_entry(resolver_tag: str) -> dict:
    return {
        "employer_address": "",
        "method": "ai_not_found",
        "resolver": resolver_tag,
        "prompt_version": EMPLOYER_PROMPT_VERSION,
        "resolved_on": datetime.now(timezone.utc).date().isoformat(),
        "confidence": "UNKNOWN",
    }


# resolve pending employer addresses via grounded ai web search
def step_ai_lookup(
    df: pd.DataFrame,
    prev_cache,
    addr_cache,
    donor_totals: pd.DataFrame,
) -> int:
    """Resolve current employer cache misses with one grounded search each."""
    resolver_tag = resolver_id()
    lookups = collect_employer_lookups(
        df, prev_cache, addr_cache, donor_totals, resolver_tag
    )
    pending = len(lookups)
    batch = lookups[:AI_LOOKUP_BATCH_SIZE]
    logger.info(
        f"    AI Lookup [{EMPLOYER_PROMPT_VERSION}]: {pending:,} employers pending "
        f"({len(addr_cache):,} total cache entries)"
    )
    if len(batch) < pending:
        logger.info(
            f"    Processing {len(batch):,} highest-priority employers this run; "
            "rerun to continue"
        )

    if not batch:
        return 0

    try:
        client, model = get_ai_client()
    except ImportError:
        logger.info("    openai package not installed - run: pip install openai")
        return 0
    if client is None:
        return 0

    # cache a validated result, or an explicit not-found
    def store_result(lookup: EmployerLookup, result: dict | None) -> bool:
        entry = _resolved_cache_entry(lookup, result, resolver_tag)
        if entry:
            addr_cache.put(lookup.key, entry)
            return True
        if _is_explicit_unknown(lookup, result):
            addr_cache.put(lookup.key, _not_found_entry(resolver_tag))
        else:
            logger.warning(f"      {lookup.name}: invalid AI response was not cached")
        return False

    return run_web_search(
        client,
        model,
        AI_SYSTEM_PROMPT,
        batch,
        build_employer_prompt,
        store_result,
        addr_cache,
        "AI Lookup",
    )


