"""Employer geocoding covers every published location."""

import json
import sys

import pandas as pd
import pytest
from geo_patch import patch_geo, point

from fec.resolve import employer_locations
import geocode
from fec import io
from fec.geocoding import pipeline as employers
from fec.geocoding import pipeline as geocoding_pipeline
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import NominatimUnavailable


def test_contributor_geocode_keeps_cleaned_address(tmp_path, monkeypatch):
    import clean
    from fec.cleaning.addresses.fixes import verified

    rules_path = tmp_path / "address_rules.csv"
    rules_path.write_text(
        "match_street,match_state,match_zip,street,unit,city,state,zip,source,match_unit,match_sub_id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verified, "ADDRESS_RULES_CSV", rules_path)
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    matched = {
        "text": "1 MAIN STREET, NEW YORK, NY, 10001", "house_number": "1",
        "street": "MAIN STREET", "city": "NEW YORK", "state": "NY", "zip": "10001",
        "source_url": "https://nominatim.openstreetmap.org/search?q=1+MAIN+ST%2C+NEW+YROK%2C+NY%2C+10001",
    }
    cache.put("1 MAIN ST|NEW YROK|NY|10001", 40.7500, -73.9900, "nominatim",
              matched_address=matched)
    cache.put("1 MAIN STREET|NEW YROK|NY|10001", 40.7501, -73.9901, "nominatim")
    rows = pd.DataFrame([
        {
            "sub_id": "101", "donor_key": "same-donor", "contribution_receipt_amount": 750.00,
            "contributor_street_1": "1 MAIN ST",
            "contributor_street_2": "APT 7",
            "contributor_city": "NEW YROK",
            "contributor_state": "NY",
            "contributor_zip": "10001",
        },
        {
            "sub_id": "102", "donor_key": "same-donor", "contribution_receipt_amount": 125.50,
            "contributor_street_1": "1 MAIN STREET",
            "contributor_street_2": "STE F3",
            "contributor_city": "NEW YROK",
            "contributor_state": "NY",
            "contributor_zip": "10001",
        },
    ])
    original = rows.copy(deep=True)
    expected = rows.copy(deep=True)
    expected.loc[0, "contributor_city"] = "NEW YORK"
    clean_calls = []

    def reclean(*, expected):
        clean_calls.append(expected.copy(deep=True))
        result = original.copy(deep=True)
        verified.apply_verified_address_fixes(result, city_only=True)
        return result.drop(columns="_address_rule")

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("city correction must reuse the cached coordinates")

    monkeypatch.setattr(clean, "main", reclean)
    patch_geo(monkeypatch, "_geocode_one", unexpected_lookup)
    old_entries = json.loads(json.dumps(cache.data))

    result = geocode._geocode_contributors(rows, cache)

    pd.testing.assert_frame_equal(result[expected.columns], expected, check_dtype=False)
    assert len(clean_calls) == 1
    pd.testing.assert_frame_equal(clean_calls[0][expected.columns], expected, check_dtype=False)
    pd.testing.assert_frame_equal(rows, original)
    assert result["latitude"].notna().all()
    assert result.loc[0, "latitude"] == 40.7500
    assert result.loc[0, "longitude"] == -73.9900
    assert cache.get("1 MAIN ST|NEW YORK|NY|10001") == old_entries["1 MAIN ST|NEW YROK|NY|10001"]
    assert all(cache.get(key) == value for key, value in old_entries.items())
    assert GeoCache(cache.path).get("1 MAIN ST|NEW YORK|NY|10001") == cache.get("1 MAIN ST|NEW YORK|NY|10001")
    saved = pd.read_csv(rules_path, dtype=str, keep_default_na=False)
    assert len(saved) == 1
    rule = saved.iloc[0]
    assert rule["match_sub_id"] == "101"
    assert rule["match_street"] == rule["street"] == "1 MAIN ST"
    assert rule["match_state"] == "NY" and rule["match_zip"] == "10001"
    assert rule["city"] == "NEW YORK"
    assert rule["source"] == matched["source_url"]
    assert all(rule[field] == "" for field in ("unit", "state", "zip", "match_unit"))
    before = rules_path.read_bytes()
    again = geocode._geocode_contributors(result, cache)
    pd.testing.assert_frame_equal(again[expected.columns], expected, check_dtype=False)
    assert len(clean_calls) == 1
    assert geocoding_pipeline.save_city_corrections(again, cache) == 0
    assert rules_path.read_bytes() == before

    # A saved rule still applies when a previous run stopped before rewriting the CSV.
    resumed = geocode._geocode_contributors(original.copy(), cache)
    pd.testing.assert_frame_equal(resumed[expected.columns], expected, check_dtype=False)
    assert len(clean_calls) == 2
    assert rules_path.read_bytes() == before

    cache.put("1 MAIN ST|NEW YORK|NY|10001", 40.7502, -73.9902, "manual_verified")
    existing_target = cache.get("1 MAIN ST|NEW YORK|NY|10001").copy()
    preserved = geocode._geocode_contributors(original.copy(), cache)
    assert cache.get("1 MAIN ST|NEW YORK|NY|10001") == existing_target
    assert preserved.loc[0, "latitude"] == 40.7502
    assert len(clean_calls) == 3

    resolved = original.assign(employer_status="active")
    with pytest.raises(ValueError):
        geocode._geocode_contributors(resolved, cache)
    assert len(clean_calls) == 3
    assert resolved["contributor_city"].tolist() == ["NEW YROK", "NEW YROK"]


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
    import clean

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
    monkeypatch.setattr(clean, "main", lambda **_args: pytest.fail("employer geocoding must not rerun clean"))

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
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(("osm", args)) or None)
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or None)
    assert geocoding_pipeline._geocode_one("PO BOX 396", "Richmond", "VA", "23219") is None
    assert not calls


def test_openstreetmap_is_preferred_for_us_street(monkeypatch):
    patch_geo(
        monkeypatch, "nominatim",
        lambda *_args: point(40.749146, -73.991886, "US"),
    )
    patch_geo(
        monkeypatch, "census",
        lambda *_args: (_ for _ in ()).throw(AssertionError("not needed")),
    )

    result = geocoding_pipeline._geocode_one(
        "7 PENN PLZ", "NEW YORK", "NY", "10001",
    )

    assert result == point(40.749146, -73.991886, "US", source="nominatim")


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
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(("osm", args)) or None)
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or None)
    address = ("268 CHESTNUT ST", "ENGLEWOOD", "NY", "11963")
    assert geocoding_pipeline._geocode_one(*address) is None
    assert calls == [("osm", address), ("census", address)]
