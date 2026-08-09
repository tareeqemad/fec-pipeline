"""Employer geocoding covers every published location."""

import json

import pandas as pd

import geocode
from fec.geocoding import GeoCache
from fec.geocoding.engines import NominatimUnavailable
from fec.geocoding import pipeline as geocoding_pipeline


def test_unassigned_office_is_included(tmp_path):
    rows = pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "contributor_employer": "BIG FIRM",
        "previous_employer": "",
        "employer_status": "active",
        "employer_address": "1 MAIN ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
    }])
    cache = {
        "BIG FIRM": {
            "employer_address": "1 MAIN ST",
            "employer_city": "NEW YORK",
            "employer_state": "NY",
            "employer_zip": "10001",
            "method": "manual_override",
            "locations": [{
                "employer_address": "2 BRANCH ST",
                "employer_city": "BOSTON",
                "employer_state": "MA",
                "employer_zip": "02110",
                "method": "manual_override",
            }],
        },
    }
    (tmp_path / "resolve_employer_addr.json").write_text(
        json.dumps(cache), encoding="utf-8",
    )

    result = geocode._all_employer_addresses(rows, str(tmp_path))

    assert "2 BRANCH ST" in set(result["employer_address"])


def test_network_failure_stays_retryable(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "1 MAIN ST|NEW YORK|NY|10001"

    def unavailable(*_args, **_kwargs):
        raise NominatimUnavailable("TLS failure")

    monkeypatch.setattr(geocoding_pipeline, "_geocode_one", unavailable)

    geocoding_pipeline._geocode_todo([key], cache, 50)

    assert cache.needs_retry(key)
    assert cache.get(key)["source"] == "transient_fail"


def test_employer_geocode_can_be_applied_twice(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "1 MAIN ST|NEW YORK|NY|10001"
    cache.put(key, 40.1, -73.9, "nominatim")
    rows = pd.DataFrame([{
        "employer_address": "1 MAIN ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
    }])

    result = geocoding_pipeline.apply_employer_to_dataframe(rows, cache)

    assert result.loc[0, "employer_latitude"] == 40.1
    assert result.loc[0, "employer_geocode_level"] == "nominatim"


def test_manual_coordinate_survives_rerun(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "1 MAIN ST|NEW YORK|NY|10001"
    cache.put(key, 40.1, -73.9, "manual_census")

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("manual coordinate was retried")

    monkeypatch.setattr(geocoding_pipeline, "_geocode_one", unexpected_lookup)
    rows = pd.DataFrame([{
        "employer_address": "1 MAIN ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
    }])

    geocoding_pipeline.geocode_employer_addresses(rows, cache)

    assert cache.get(key)["source"] == "manual_census"


def test_city_fallback_retries_without_zip(monkeypatch):
    calls = []

    def city(_city, _state, zipcode):
        calls.append(zipcode)
        if zipcode:
            return None, None, None
        return 37.54, -77.43, "US"

    monkeypatch.setattr(geocoding_pipeline, "city_level", city)
    monkeypatch.setattr(geocoding_pipeline.time, "sleep", lambda _delay: None)

    result = geocoding_pipeline._geocode_one(
        "PO BOX 396", "Richmond", "VA", "23218",
    )

    assert calls == ["23218", ""]
    assert result == (37.54, -77.43, "US", "nominatim_city")
