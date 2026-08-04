"""Employer AI lookups use cleaned names, safe context, and validated evidence."""

import json

import pandas as pd

from fec.resolve.pipeline.constants import EMPLOYER_PROMPT_VERSION
from fec.resolve.pipeline.steps.ai_employer import (
    EmployerLookup,
    _is_explicit_unknown,
    _needs_ai,
    _resolved_cache_entry,
    build_employer_prompt,
    collect_employer_lookups,
    step_ai_lookup,
)


class _Cache:
    def __init__(self, data=None):
        self.data = data or {}
        self.saves = 0

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1

    def __len__(self):
        return len(self.data)


def test_retry_policy_preserves_good_cache_and_retries_stale_misses():
    resolver = "openai+search"

    assert not _needs_ai({
        "employer_address": "1 Main St",
        "method": "ai_openai",
    }, resolver)
    assert not _needs_ai({
        "employer_address": "",
        "employer_city": "New York",
        "employer_state": "NY",
        "method": "manual_override",
    }, resolver)
    assert _needs_ai({
        "employer_address": "",
        "method": "ai_openai",
    }, resolver)
    assert _needs_ai({
        "employer_address": "",
        "method": "ai_not_found",
        "provider": resolver,
    }, resolver)
    assert not _needs_ai({
        "employer_address": "",
        "method": "ai_not_found",
        "resolver": resolver,
        "prompt_version": EMPLOYER_PROMPT_VERSION,
    }, resolver)


def test_prompt_keeps_context_for_identity_not_branch_selection():
    lookup = EmployerLookup(
        "ACME LLC",
        donor_locations=("NEW YORK, NY",),
        donor_occupations=("ATTORNEY",),
    )

    prompt = build_employer_prompt(lookup)
    instruction, payload_text = prompt.split("\n\n", 1)
    payload = json.loads(payload_text)

    assert "must not be used to choose a nearby branch" in instruction
    assert payload == {
        "employer_name": "ACME LLC",
        "donor_locations_for_identity_only": ["NEW YORK, NY"],
        "donor_occupations_for_identity_only": ["ATTORNEY"],
    }


def test_cache_entry_requires_complete_us_address_and_source():
    lookup = EmployerLookup("ACME LLC")
    result = {
        "name": "ACME LLC",
        "matched_company_name": "Acme LLC",
        "address": "1 Main St",
        "city": "New York",
        "state": "ny",
        "zip": "10001",
        "address_type": "HEADQUARTERS",
        "source_name": "Acme",
        "source_url": "https://acme.example/contact",
        "confidence": "high",
    }

    entry = _resolved_cache_entry(
        lookup, result, provider="openai", resolver_tag="openai+search"
    )

    assert entry["employer_state"] == "NY"
    assert entry["employer_zip"] == "10001"
    assert entry["source_url"] == "https://acme.example/contact"
    assert entry["prompt_version"] == EMPLOYER_PROMPT_VERSION

    for field, invalid_value in (
        ("state", "LONDON"),
        ("zip", "NW1 5DX"),
        ("source_url", ""),
        ("address_type", "BRANCH"),
    ):
        invalid = {**result, field: invalid_value}
        assert _resolved_cache_entry(
            lookup, invalid, provider="openai",
            resolver_tag="openai+search",
        ) is None


def test_only_an_explicit_matching_unknown_is_cacheable_as_not_found():
    lookup = EmployerLookup("ACME LLC")

    assert _is_explicit_unknown(
        lookup, {"name": "ACME LLC", "confidence": "UNKNOWN"}
    )
    assert not _is_explicit_unknown(
        lookup, {"name": "OTHER LLC", "confidence": "UNKNOWN"}
    )
    assert not _is_explicit_unknown(lookup, None)


def test_lookup_collection_aggregates_limited_public_context():
    df = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D1",
            "contributor_name": "ONE, DONOR",
            "contributor_employer": "ACME LLC",
            "contributor_city": "NEW YORK",
            "contributor_state": "NY",
            "contributor_occupation": "ATTORNEY",
        },
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D2",
            "contributor_name": "TWO, DONOR",
            "contributor_employer": "ACME LLC",
            "contributor_city": "NEW YORK",
            "contributor_state": "NY",
            "contributor_occupation": "ATTORNEY",
        },
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D3",
            "contributor_name": "THREE, DONOR",
            "contributor_employer": "ACME LLC",
            "contributor_city": "BOSTON",
            "contributor_state": "MA",
            "contributor_occupation": "PARTNER",
        },
    ])
    donor_totals = pd.DataFrame({"donor_key": ["D1", "D2", "D3"]})

    lookups = collect_employer_lookups(
        df,
        prev_cache=_Cache(),
        addr_cache=_Cache(),
        donor_totals=donor_totals,
        resolver_tag="openai+search",
    )

    assert lookups == [
        EmployerLookup(
            "ACME LLC",
            donor_locations=("NEW YORK, NY", "BOSTON, MA"),
            donor_occupations=("ATTORNEY", "PARTNER"),
        )
    ]


def test_lookup_collection_skips_self_employed_work_history():
    df = pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "donor_key": "D1",
        "contributor_name": "ONE, DONOR",
        "contributor_employer": "RETIRED",
        "contributor_city": "NEW YORK",
        "contributor_state": "NY",
        "contributor_occupation": "RETIRED",
    }])
    previous_employers = _Cache({
        "ONE, DONOR|NY": {
            "employer": "SELF-EMPLOYED",
            "employer_normalized": "SELF-EMPLOYED",
            "method": "manual_override",
        }
    })

    assert collect_employer_lookups(
        df,
        prev_cache=previous_employers,
        addr_cache=_Cache(),
        donor_totals=pd.DataFrame({"donor_key": ["D1"]}),
        resolver_tag="openai+search",
    ) == []


def test_invalid_model_response_is_not_cached(monkeypatch):
    df = pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "donor_key": "D1",
        "contributor_name": "ONE, DONOR",
        "contributor_employer": "ACME LLC",
        "contributor_city": "NEW YORK",
        "contributor_state": "NY",
        "contributor_occupation": "ATTORNEY",
    }])
    donor_totals = pd.DataFrame({"donor_key": ["D1"]})
    invalid_response = json.dumps({"results": [{
        "name": "ACME LLC",
        "address": "10 Downing St",
        "city": "London",
        "state": "",
        "zip": "SW1A 2AA",
        "address_type": "HEADQUARTERS",
        "source_url": "",
        "confidence": "HIGH",
    }]})
    address_cache = _Cache()

    monkeypatch.setattr(
        "fec.resolve.pipeline.steps.ai_employer.get_ai_client",
        lambda: (object(), "model", "openai"),
    )
    monkeypatch.setattr(
        "fec.resolve.pipeline.steps.ai_employer.ai_web_search_call",
        lambda *args: (invalid_response, 0.01),
    )

    found = step_ai_lookup(
        df, _Cache(), address_cache, donor_totals
    )

    assert found == 0
    assert address_cache.data == {}


