"""Foreign employer offices are geocoded internationally only and never keep a US match.

KIFO (York Gate, 100 Marylebone Road, London NW1 5DX) was stored in Norwich CT and
SHOPPERAI (65 Yigal Alon Street, Tel Aviv 6744316) in Providence RI, because the
US-only engines appended ', USA' / countrycodes=us to their queries.
"""
import json

import numpy as np
import pandas as pd
import pytest
from geo_patch import patch_geo, point

from fec.resolve import addresses, employer_locations
from fec.geocoding import validation as accepted, pipeline as employers, providers as engines
from fec.geocoding import pipeline as geo
from fec.geocoding.cache import GeoCache

KIFO_KEY = "YORK GATE, 100 MARYLEBONE ROAD|LONDON||NW1 5DX"
SHOPPERAI_KEY = "65 YIGAL ALON STREET|TEL AVIV||6744316"
STANTON_KEY = "KM 25 VIA A SIBATE|SIBATE|CUNDINAMARCA|"


def _no_us_engine(*_args, **_kwargs):
    raise AssertionError("a US-restricted engine was called for a foreign address")


@pytest.mark.parametrize("state, zipcode, foreign", [
    ("", "NW1 5DX", True),          # London postcode, no state
    ("", "6744316", True),          # Israeli 7-digit postcode
    ("CUNDINAMARCA", "", True),     # Colombian department in the state field
    ("ON", "M5V 2T6", True),        # Canadian province
    ("", "", True),                 # no state and no ZIP: nothing makes it American
    ("NY", "10001", False),
    ("NY", "10001-1234", False),
    ("", "10001", False),           # US ZIP with the state missing
    ("NY", "10007.0", False),       # ZIP read as a float in an old cache key
    ("NJ", "NAN", False),
    ("MA", "2138", False),          # a US state with a lost leading zero stays US
    ("NY", "UNKNOWN", False),       # junk in the ZIP field proves nothing
    ("NY", "100011", False),        # an extra digit is a US typo
    ("WA", "V6B 1A1", True),        # a Canadian postcode is never a US ZIP typo
    ("NY", "NW1 5DX", True),        # nor is a UK one
])
def test_foreign_address_is_recognised_from_state_and_postcode(state, zipcode, foreign):
    assert accepted.is_foreign_address(state, zipcode) is foreign


def test_foreign_office_uses_only_the_international_engine(monkeypatch):
    calls = []

    def international(street, city, state, zipcode):
        calls.append((street, city, state, zipcode))
        return point(51.5234, -0.1530, "GB")

    for name in ("census",):
        patch_geo(monkeypatch, name, _no_us_engine)
    patch_geo(monkeypatch, "nominatim", international)

    result = geo._geocode_one("YORK GATE, 100 MARYLEBONE ROAD", "LONDON", "", "NW1 5DX")

    assert result == point(51.5234, -0.1530, "GB", source="nominatim")
    assert calls == [("YORK GATE, 100 MARYLEBONE ROAD", "LONDON", "", "NW1 5DX")]


def test_foreign_office_rejects_a_us_answer_from_the_international_engine(monkeypatch):
    monkeypatch.setattr(engines.requests, "get", _no_us_engine)
    # street query and city query both come back in the US (Providence RI)
    patch_geo(monkeypatch, "nominatim",
                        lambda *_args: point(41.8174424, -71.4018816, "US"))

    result = geo._geocode_one("65 YIGAL ALON STREET", "TEL AVIV", "", "6744316")

    assert result is None


def test_foreign_office_does_not_fall_back_to_its_city_abroad(monkeypatch):
    answers = iter([None, point(32.0853, 34.7818, "IL")])
    patch_geo(monkeypatch, "nominatim", lambda *_args: next(answers))

    result = geo._geocode_one("65 YIGAL ALON STREET", "TEL AVIV", "", "6744316")

    assert result is None
    assert next(answers) == point(32.0853, 34.7818, "IL")


def test_cached_us_match_for_a_foreign_office_is_looked_up_again(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    # the exact wrong entry that put KIFO in Connecticut
    cache.put(KIFO_KEY, 41.5300962, -72.0896919, "nominatim_city", "US")
    cache.put(STANTON_KEY, 4.4256051, -74.2930194, "nominatim_intl", "CO")

    assert geo._needs_lookup(KIFO_KEY, cache)
    assert not geo._needs_lookup(STANTON_KEY, cache)

    cache.put(KIFO_KEY, 51.5234, -0.1530, "nominatim_intl", "GB")
    assert not geo._needs_lookup(KIFO_KEY, cache)


def test_us_match_for_a_foreign_office_is_never_published(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.put(SHOPPERAI_KEY, 41.8174424, -71.4018816, "nominatim_city", "US")
    rows = pd.DataFrame([{
        "employer_address": "65 Yigal Alon Street",
        "employer_city": "Tel Aviv",
        "employer_state": "",
        "employer_zip": "6744316",
    }])

    result = employers.apply_employer_to_dataframe(rows, cache)

    assert np.isnan(result.loc[0, "employer_latitude"])
    assert result.loc[0, "employer_geocode_level"] == "not_found"
    assert geo.accepted_coordinates(STANTON_KEY, {
        "lat": 4.4256051, "lng": -74.2930194, "source": "nominatim_intl", "country": "CO",
    })[:2] == (4.4256051, -74.2930194)


def test_build_keeps_the_foreign_office_text_and_drops_its_us_coordinates(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "contributor_employer": name,
        "previous_employer": "", "contributor_state": "NY", "employer_status": "active",
    } for name in ("KIFO", "STANTON SAS")]).to_csv(cleaned, index=False)
    resolve_cache = {
        "KIFO": {
            "employer_address": "York Gate, 100 Marylebone Road", "employer_city": "London",
            "employer_state": "", "employer_zip": "NW1 5DX", "method": "manual_override",
        },
        "STANTON SAS": {
            "employer_address": "Km 25 Via a Sibate", "employer_city": "Sibate",
            "employer_state": "CUNDINAMARCA", "employer_zip": "", "method": "manual_override",
        },
    }
    geocodes = {
        KIFO_KEY: {"lat": 41.5300962, "lng": -72.0896919, "source": "nominatim_city", "country": "US"},
        STANTON_KEY: {"lat": 4.4256051, "lng": -74.2930194, "source": "nominatim_intl",
                      "country": "CO", "validated": True},
    }
    (tmp_path / "resolve_employer_addr.json").write_text(json.dumps(resolve_cache), encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text(json.dumps(geocodes), encoding="utf-8")
    monkeypatch.setattr(employer_locations, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(addresses, "CACHE_DIR", tmp_path)
    for folder in ("CACHE_DIR", "REPORTS_DIR"):
        monkeypatch.setattr(employer_locations, folder, tmp_path)
    monkeypatch.setattr(employer_locations, "EMPLOYER_LOCATIONS_CSV", output)

    employer_locations.build()

    locations = pd.read_csv(output, dtype=str, keep_default_na=False).set_index("employer_name")
    kifo, stanton = locations.loc["KIFO"], locations.loc["STANTON SAS"]
    assert kifo["employer_address"] == "York Gate, 100 Marylebone Road"   # kept as given
    assert kifo["employer_city"] == "London"
    assert kifo["employer_latitude"] == "" and kifo["employer_longitude"] == ""
    assert stanton["employer_address"] == "Km 25 Via a Sibate"
    assert (stanton["employer_latitude"], stanton["employer_longitude"]) == ("4.4256051", "-74.2930194")


def test_international_query_skips_empty_parts(monkeypatch):
    seen = []
    from test_geo_town_level_search import _Response
    monkeypatch.setattr(engines, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(engines.requests, "get", lambda _url, params, **kwargs: seen.append(params) or _Response([]))

    engines.nominatim("100 Marylebone Road", "London", "", "NW1 5DX")
    engines.nominatim("Km 25 Via a Sibate", "Sibate", "CUNDINAMARCA", "")

    assert [entry["q"] for entry in seen] == [
        "100 Marylebone Road, London, NW1 5DX", "Km 25 Via a Sibate, Sibate, CUNDINAMARCA",
    ]
    assert all("countrycodes" not in entry for entry in seen)
