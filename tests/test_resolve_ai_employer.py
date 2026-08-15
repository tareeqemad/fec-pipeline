"""Employer AI lookups use cleaned names, safe context, and validated evidence."""

import json

import pandas as pd

from fec.resolve.pipeline.cli import _deduplicate_address_cache
from fec.resolve.pipeline.constants import EMPLOYER_PROMPT_VERSION
from fec.resolve.pipeline.dedup import dedup_by_resolved_address
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


def _address(city):
    return {
        "employer_address": "1 MAIN ST",
        "employer_city": city,
        "employer_state": "NY",
        "employer_zip": "10001",
        "method": "ai_openai_search",
        "confidence": "HIGH",
    }


def test_address_aliases_never_rewrite_clean_employer_names():
    df = pd.DataFrame({
        "contributor_employer": ["ACME", "ACME LLC"],
    })
    original = df.copy()
    cache = _Cache({"ACME": _address("NEW YORK"), "ACME LLC": _address("NEW YORK")})

    _deduplicate_address_cache(df, cache)

    pd.testing.assert_frame_equal(df, original)
    assert cache.data["ACME"]["alias_of"] == "ACME LLC"


def test_address_aliases_require_the_complete_address():
    cache = _Cache({
        "ACME": _address("NEW YORK"),
        "ACME LLC": _address("ALBANY"),
    })

    aliases, groups = dedup_by_resolved_address(cache)

    assert (aliases, groups) == (0, 0)


def test_address_aliases_prefer_frequency_but_keep_best_evidence():
    common = _address("NEW YORK")
    common["method"] = "ai_openai"
    common["confidence"] = "MEDIUM"
    searched = _address("NEW YORK")
    cache = _Cache({"ACME": common, "ACME LLC": searched})

    aliases, groups = dedup_by_resolved_address(
        cache, freq={"ACME": 20, "ACME LLC": 1}
    )

    assert (aliases, groups) == (1, 1)
    assert cache.data["ACME"]["method"] == "ai_openai_search"
    assert cache.data["ACME LLC"]["alias_of"] == "ACME"


def test_address_aliases_skip_manual_low_confidence_and_existing_aliases():
    manual = _address("NEW YORK")
    manual["method"] = "manual_override"
    low = _address("NEW YORK")
    low["confidence"] = "LOW"
    alias = _address("NEW YORK")
    alias["alias_of"] = "ACME"
    cache = _Cache({
        "ACME": _address("NEW YORK"),
        "ACME LLC": manual,
        "ACME CORP": low,
        "ACME COMPANY": alias,
    })

    assert dedup_by_resolved_address(cache) == (0, 0)
    assert cache.saves == 0


def test_address_aliases_are_idempotent():
    cache = _Cache({"ACME": _address("NEW YORK"), "ACME LLC": _address("NEW YORK")})

    assert dedup_by_resolved_address(cache) == (1, 1)
    snapshot = {key: value.copy() for key, value in cache.data.items()}
    assert dedup_by_resolved_address(cache) == (0, 0)

    assert cache.data == snapshot
    assert cache.saves == 1


def test_retry_policy_retries_legacy_and_stale_cache_entries():
    resolver = "openai+search"

    assert _needs_ai({
        "employer_address": "1 Main St",
        "method": "ai_openai",
    }, resolver)
    assert _needs_ai({
        "employer_address": "1 Main St",
        "method": "ai_openai_search",
    }, resolver)
    assert not _needs_ai({
        "employer_address": "1 Main St",
        "method": "ai_openai_search",
        "resolver": resolver,
        "prompt_version": EMPLOYER_PROMPT_VERSION,
    }, resolver)
    assert not _needs_ai({
        "employer_address": "",
        "employer_city": "New York",
        "employer_state": "NY",
        "method": "manual_override",
    }, resolver)
    assert _needs_ai({
        "employer_address": "1 Possible St",
        "method": "manual_review",
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


def test_prompt_uses_context_to_find_only_confirmed_locations():
    lookup = EmployerLookup(
        "ACME LLC",
        donor_locations=("NEW YORK, NY",),
        donor_occupations=("ATTORNEY",),
    )

    prompt = build_employer_prompt(lookup)
    instruction, payload_text = prompt.split("\n\n", 1)
    payload = json.loads(payload_text)

    assert "only when an authoritative source confirms" in instruction
    assert payload == {
        "employer_name": "ACME LLC",
        "donor_locations": ["NEW YORK, NY"],
        "donor_occupations": ["ATTORNEY"],
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
        "locations": [{
            "address": "555 California St",
            "city": "San Francisco",
            "state": "CA",
            "zip": "94104",
            "address_type": "OFFICE",
            "source_name": "Acme offices",
            "source_url": "https://acme.example/offices",
            "confidence": "HIGH",
        }],
    }

    entry = _resolved_cache_entry(
        lookup, result, provider="openai", resolver_tag="openai+search"
    )

    assert entry["employer_state"] == "NY"
    assert entry["employer_zip"] == "10001"
    assert entry["source_url"] == "https://acme.example/contact"
    assert entry["prompt_version"] == EMPLOYER_PROMPT_VERSION
    assert entry["locations"][0]["employer_zip"] == "94104"

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
            "contributor_zip": "10001",
        },
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D2",
            "contributor_name": "TWO, DONOR",
            "contributor_employer": "ACME LLC",
            "contributor_city": "NEW YORK",
            "contributor_state": "NY",
            "contributor_occupation": "ATTORNEY",
            "contributor_zip": "10001",
        },
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D3",
            "contributor_name": "THREE, DONOR",
            "contributor_employer": "ACME LLC",
            "contributor_city": "BOSTON",
            "contributor_state": "MA",
            "contributor_occupation": "PARTNER",
            "contributor_zip": "02108",
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
            donor_locations=("NEW YORK, NY 10001", "BOSTON, MA 02108"),
            donor_occupations=("ATTORNEY", "PARTNER"),
        )
    ]


def test_lookup_collection_prioritizes_high_value_donors():
    df = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D1",
            "contributor_employer": "SMALL CO",
            "contributor_occupation": "OWNER",
        },
        {
            "entity_type": "INDIVIDUAL",
            "donor_key": "D2",
            "contributor_employer": "BIG CO",
            "contributor_occupation": "OWNER",
        },
    ])
    donor_totals = pd.DataFrame({
        "donor_key": ["D1", "D2"],
        "donor_total": [1_000, 500_000],
    })

    lookups = collect_employer_lookups(
        df, _Cache(), _Cache(), donor_totals, "openai+search"
    )

    assert [lookup.name for lookup in lookups] == ["BIG CO", "SMALL CO"]


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
        "donor:D1": {
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


def test_lookup_collection_skips_non_working_occupation():
    df = pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "donor_key": "D1",
        "contributor_name": "STUDENT, ONE",
        "contributor_employer": "NYU",
        "contributor_city": "NEW YORK",
        "contributor_state": "NY",
        "contributor_occupation": "STUDENT",
        "occupation_category": "STUDENT",
    }])

    assert collect_employer_lookups(
        df,
        prev_cache=_Cache(),
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


def test_ai_lookup_uses_safe_batch(monkeypatch):
    lookups = [EmployerLookup(str(number)) for number in range(201)]
    searched = []

    monkeypatch.setattr(
        "fec.resolve.pipeline.steps.ai_employer.collect_employer_lookups",
        lambda *args: lookups,
    )
    monkeypatch.setattr(
        "fec.resolve.pipeline.steps.ai_employer.get_ai_client",
        lambda: (object(), "model", "openai"),
    )

    def fake_search(*args):
        searched.extend(args[3])
        return 0

    monkeypatch.setattr(
        "fec.resolve.pipeline.steps.ai_employer.run_web_search",
        fake_search,
    )

    step_ai_lookup(pd.DataFrame(), _Cache(), _Cache(), pd.DataFrame())

    assert searched == lookups[:200]


