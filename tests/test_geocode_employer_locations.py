"""Employer geocoding covers every published location."""

import json
import sys

import pandas as pd

import build_employers
import geocode
from fec import io
from fec.geocoding import GeoCache
from fec.geocoding import pipeline as geocoding_pipeline
from fec.geocoding import employers
from geo_patch import patch_geo
from fec.geocoding.engines import NominatimUnavailable


def test_contributor_geocode_keeps_cleaned_address(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.put("1 MAIN ST|NEW YORK|NY|10001", 40.7500, -73.9900, "nominatim")
    cache.put("1 MAIN STREET|NEW YORK|NY|10001", 40.7501, -73.9901, "nominatim")
    rows = pd.DataFrame([
        {
            "donor_key": "same-donor",
            "contributor_street_1": "1 MAIN ST",
            "contributor_street_2": "",
            "contributor_city": "NEW YORK",
            "contributor_state": "NY",
            "contributor_zip": "10001",
        },
        {
            "donor_key": "same-donor",
            "contributor_street_1": "1 MAIN STREET",
            "contributor_street_2": "",
            "contributor_city": "NEW YORK",
            "contributor_state": "NY",
            "contributor_zip": "10001",
        },
    ])
    address_columns = [
        "contributor_street_1",
        "contributor_street_2",
        "contributor_city",
        "contributor_state",
        "contributor_zip",
    ]
    expected = rows[address_columns].copy()

    result = geocode._geocode_contributors(rows, cache)

    pd.testing.assert_frame_equal(result[address_columns], expected)
    assert result["latitude"].notna().all()


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

    patch_geo(monkeypatch, "_geocode_one", unavailable)

    geocoding_pipeline._geocode_todo([key], cache, 50)

    assert cache.needs_retry(key)
    assert cache.get(key)["source"] == "transient_fail"


def test_employer_geocode_can_be_applied_twice(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "1 MAIN ST|NEW YORK|NY|10001"
    # inside ZIP 10001 (a point 70 km away would be rejected as a wrong-town match)
    cache.put(key, 40.7506, -73.9971, "nominatim")
    rows = pd.DataFrame([{
        "employer_address": "1 MAIN ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
    }])

    result = employers.apply_employer_to_dataframe(rows, cache)

    assert result.loc[0, "employer_latitude"] == 40.7506
    assert result.loc[0, "employer_geocode_level"] == "nominatim"


def test_employer_mode_builds_location_file(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions_cleaned.csv"
    rows = pd.DataFrame([{"employer_address": "1 MAIN ST"}])
    calls = []

    monkeypatch.setattr(sys, "argv", ["geocode.py", "--employer-only"])
    monkeypatch.setattr(geocode, "_find_csv", lambda: str(csv_path))
    monkeypatch.setattr(io, "read_pipeline_csv", lambda _path: rows)
    monkeypatch.setattr(
        geocode,
        "_geocode_employers",
        lambda df, _cache, _data_dir: (df, True),
    )
    monkeypatch.setattr(geocode, "_write_output", lambda *_args: calls.append("write"))
    monkeypatch.setattr(build_employers, "build", lambda: calls.append("build"))

    geocode.main()

    assert calls == ["write", "build"]


def test_manual_coordinate_survives_rerun(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "1 MAIN ST|NEW YORK|NY|10001"
    cache.put(key, 40.1, -73.9, "manual_census")

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("manual coordinate was retried")

    patch_geo(monkeypatch, "_geocode_one", unexpected_lookup)
    rows = pd.DataFrame([{
        "employer_address": "1 MAIN ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
    }])

    employers.geocode_employer_addresses(rows, cache)

    assert cache.get(key)["source"] == "manual_census"


def test_city_fallback_retries_without_zip(monkeypatch):
    calls = []

    def city(_city, _state, zipcode, _near):
        calls.append(zipcode)
        if zipcode:
            return None, None, None
        return 37.54, -77.43, "US"

    patch_geo(monkeypatch, "city_level", city)
    monkeypatch.setattr(geocoding_pipeline.time, "sleep", lambda _delay: None)

    # 23219 (downtown Richmond) has a centroid: the town is searched with it first
    result = geocoding_pipeline._geocode_one(
        "PO BOX 396", "Richmond", "VA", "23219",
    )

    assert calls == ["23219", ""]
    assert result == (37.54, -77.43, "US", "nominatim_city")

    # 23218 is PO-box-only, unknown to OSM and without a centroid: never sent
    calls.clear()
    result = geocoding_pipeline._geocode_one(
        "PO BOX 396", "Richmond", "VA", "23218",
    )

    assert calls == [""]
    assert result == (37.54, -77.43, "US", "nominatim_city")


def test_census_is_preferred_for_us_street(monkeypatch):
    patch_geo(
        monkeypatch, "census",
        lambda *_args: (40.749146, -73.991886, "US"),
    )
    patch_geo(
        monkeypatch, "nominatim",
        lambda *_args: (_ for _ in ()).throw(AssertionError("not needed")),
    )

    result = geocoding_pipeline._geocode_one(
        "7 PENN PLZ", "NEW YORK", "NY", "10001",
    )

    assert result == (40.749146, -73.991886, "US", "census")


def test_far_legacy_coordinate_is_rechecked_once(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "7 PENN PLZ|NEW YORK|NY|10001"
    cache.put(key, 43.131885, -77.440208, "nominatim")

    assert geocoding_pipeline._needs_lookup(key, cache)

    cache.put(key, 40.749146, -73.991886, "census", validated=True)
    assert not geocoding_pipeline._needs_lookup(key, cache)


def test_old_not_found_gets_one_census_retry(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "822 KUMHO DR|FAIRLAWN|OH|44333"
    cache.put_failed(key)

    assert geocoding_pipeline._needs_lookup(key, cache)

    cache.put_failed(key, validated=True)
    assert not geocoding_pipeline._needs_lookup(key, cache)


def test_street_city_fallback_rejects_zip_conflict(monkeypatch):
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(
        monkeypatch, "city_level",
        lambda *_args: (41.3582, -73.7052, "US"),
    )
    patch_geo(
        monkeypatch, "nominatim_international",
        lambda *_args: (None, None, None),
    )
    patch_geo(
        monkeypatch, "_zip_centroids",
        lambda: {"11963": (40.9979, -72.2926)},
    )
    monkeypatch.setattr(geocoding_pipeline.time, "sleep", lambda _delay: None)

    result = geocoding_pipeline._geocode_one(
        "268 CHESTNUT ST", "ENGLEWOOD", "NY", "11963",
    )

    assert result == (None, None, None, "not_found")
