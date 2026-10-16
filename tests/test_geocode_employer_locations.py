"""Employer geocoding covers every published location."""

import json
import sys

import pandas as pd
from geo_patch import patch_geo

from fec.resolve import employer_locations
import geocode
from fec import io
from fec.geocoding import pipeline as employers
from fec.geocoding import pipeline as geocoding_pipeline
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import NominatimUnavailable


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


def test_unassigned_office_is_included(tmp_path, monkeypatch):
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

    monkeypatch.setattr(geocode, "CACHE_DIR", tmp_path)
    result = geocode._all_employer_addresses(rows)

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
        lambda df, _cache: (df, True),
    )
    monkeypatch.setattr(geocode, "_write_output", lambda *_args: calls.append("write"))
    monkeypatch.setattr(employer_locations, "build", lambda: calls.append("build"))
    # Column checks have their own tests.
    monkeypatch.setattr(geocode, "check_input", lambda *_args: None)

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


def test_po_box_does_not_trigger_a_city_fallback(monkeypatch):
    calls = []
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(("osm", args)) or (None, None, None))
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or (None, None, None))
    assert geocoding_pipeline._geocode_one("PO BOX 396", "Richmond", "VA", "23219") == (
        None, None, None, "not_found",
    )
    assert not calls


def test_openstreetmap_is_preferred_for_us_street(monkeypatch):
    patch_geo(
        monkeypatch, "nominatim",
        lambda *_args: (40.749146, -73.991886, "US"),
    )
    patch_geo(
        monkeypatch, "census",
        lambda *_args: (_ for _ in ()).throw(AssertionError("not needed")),
    )

    result = geocoding_pipeline._geocode_one(
        "7 PENN PLZ", "NEW YORK", "NY", "10001",
    )

    assert result == (40.749146, -73.991886, "US", "nominatim")


def test_far_legacy_coordinate_is_rechecked_once(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "7 PENN PLZ|NEW YORK|NY|10001"
    cache.put(key, 30.2672, -97.7431, "nominatim")

    assert geocoding_pipeline._needs_lookup(key, cache)

    cache.put(key, 40.749146, -73.991886, "census")
    assert not geocoding_pipeline._needs_lookup(key, cache)


def test_old_not_found_remains_cached(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "822 KUMHO DR|FAIRLAWN|OH|44333"
    cache.data[key] = {"lat": None, "lng": None, "source": "not_found"}

    assert not geocoding_pipeline._needs_lookup(key, cache)

    cache.put_failed(key)
    assert not geocoding_pipeline._needs_lookup(key, cache)


def test_street_miss_leaves_coordinates_blank(monkeypatch):
    calls = []
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(("osm", args)) or (None, None, None))
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or (None, None, None))
    address = ("268 CHESTNUT ST", "ENGLEWOOD", "NY", "11963")
    assert geocoding_pipeline._geocode_one(*address) == (None, None, None, "not_found")
    assert calls == [("osm", address), ("census", address)]
