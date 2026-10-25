"""Reviewed same-address points survive; known wrong points remain withheld."""
import json
import re

import pytest
from geo_patch import patch_geo, point

from fec.env import GEOCODE_CACHE_JSON
from fec.geocoding import validation as accepted
from fec.geocoding import pipeline as geo
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import CensusUnavailable
from fec.geocoding.validation import distance_km
from fec.geocoding.validation import (
    REVIEWED_POINTS,
    REVIEWED_WRONG_POINTS,
)

FORT_APACHE = "5130 S FORT APACHE RD|LAS VEGAS|NV|89148"
FORT_APACHE_NOMINATIM = (36.1300726, -115.2971675)   # Desert Inn Rd, 3.8 km north of the store
FORT_APACHE_GOOGLE = (36.0957486, -115.2964452)

PALM_BEACH = "2660 S OCEAN BLVD|PALM BEACH|FL|33480"
MANALAPAN = (26.5568015, -80.0418709)
ZIP_33480 = (26.68522, -80.037632)
PALM_BEACH_2660 = (26.6258, -80.0392)                # between 2500 (26.618) and 2778 (26.635)


def _cache(tmp_path, key, lat, lng, source, **flags):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[key] = {"lat": lat, "lng": lng, "source": source, **flags}
    return cache


@pytest.fixture
def no_sleep(monkeypatch):
    from fec.geocoding import providers
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)


@pytest.fixture
def palm_beach_zip(monkeypatch, no_sleep):
    assert accepted.valid_for_state(*PALM_BEACH_2660, "FL")


# ---- REVIEWED_POINTS

def test_a_reviewed_point_is_published_instead_of_the_cached_one(tmp_path):
    cache = _cache(tmp_path, FORT_APACHE, *FORT_APACHE_NOMINATIM, "nominatim")

    assert geo.accepted_coordinates(FORT_APACHE, cache.get(FORT_APACHE)) == (*FORT_APACHE_GOOGLE, "google")
    # also when the key has no cache entry at all
    assert geo.accepted_coordinates(FORT_APACHE, None) == (*FORT_APACHE_GOOGLE, "google")
    assert geo.accepted_coordinates(" | ".join(FORT_APACHE.lower().split("|")), None) == (*FORT_APACHE_GOOGLE, "google")


def test_a_reviewed_point_is_never_looked_up(tmp_path):
    cache = _cache(tmp_path, FORT_APACHE, *FORT_APACHE_NOMINATIM, "nominatim")
    assert not geo._needs_lookup(FORT_APACHE, cache)

    del cache.data[FORT_APACHE]
    assert not geo._needs_lookup(FORT_APACHE, cache)


def test_contributor_rows_get_the_reviewed_point(tmp_path):
    import pandas as pd

    cache = _cache(tmp_path, FORT_APACHE, *FORT_APACHE_NOMINATIM, "nominatim")
    rows = pd.DataFrame([{
        "contributor_street_1": "5130 S FORT APACHE RD", "contributor_street_2": "", "contributor_city": "LAS VEGAS",
        "contributor_state": "NV", "contributor_zip": "89148",
    }])

    out = geo.apply_to_dataframe(rows, cache)

    assert (out.at[0, "latitude"], out.at[0, "longitude"]) == FORT_APACHE_GOOGLE


def _source_key(note: str) -> str:
    return re.match(r"from '([^']+)'", note).group(1)


def test_every_reviewed_point_names_its_source_key():
    for key, (lat, lng, note) in REVIEWED_POINTS.items():
        assert key.count("|") == 3 and accepted.valid_for_state(lat, lng, key.split("|")[2]), key
        assert _source_key(note).count("|") == 3, key
    # the lists never contradict each other
    assert not set(REVIEWED_POINTS) & set(REVIEWED_WRONG_POINTS)


def _real_cache() -> dict:
    path = GEOCODE_CACHE_JSON
    if not path.exists():
        pytest.skip("geocode_cache.json not present")
    return json.loads(path.read_text(encoding="utf-8"))


def test_reviewed_points_are_the_paid_points_they_were_copied_from():
    """Each copied point equals its source entry while the cache still holds it (the source keys are pruned later)."""
    cache = _real_cache()
    checked = 0
    for key, (lat, lng, note) in REVIEWED_POINTS.items():
        source = cache.get(_source_key(note))
        if source is None:
            continue
        assert source["source"] == "google", key
        assert (source["lat"], source["lng"]) == pytest.approx((lat, lng), abs=1e-6), key
        checked += 1
    if not checked:
        pytest.skip("the source keys are pruned from this cache")


def test_reviewed_points_match_corrected_cache():
    """The shipped cache contains the reviewed same-address corrections."""
    cache = _real_cache()
    for key, (lat, lng, _note) in REVIEWED_POINTS.items():
        entry = cache.get(key)
        assert entry is not None, key
        assert (entry["lat"], entry["lng"]) == pytest.approx((lat, lng), abs=1e-6), key


# ---- REVIEWED_WRONG_POINTS

def test_a_wrong_street_point_is_withheld_and_looked_up_again(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")

    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH)) == (None, None, "rejected_reviewed_wrong")
    assert geo.accepted_coordinates(" | ".join(PALM_BEACH.lower().split("|")), cache.get(PALM_BEACH)) == (None, None, "rejected_reviewed_wrong")
    assert geo._needs_lookup(PALM_BEACH, cache)
    # a run's own zip_checked mark does not make it right
    cache.data[PALM_BEACH].update(validated=True, zip_checked=True)
    assert geo._needs_lookup(PALM_BEACH, cache)


def test_a_new_point_away_from_the_wrong_one_stands(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *PALM_BEACH_2660, "census", validated=True, zip_checked=True)

    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH))[:2] == PALM_BEACH_2660
    assert not geo._needs_lookup(PALM_BEACH, cache)


def test_a_centroid_on_the_wrong_spot_is_withheld(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "zip_centroid", validated=True)
    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH))[:2] == (None, None)
    assert geo._needs_lookup(PALM_BEACH, cache)


class _Engine:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _engines(monkeypatch, census_answer):
    census = _Engine(census_answer)
    nominatim = _Engine(point(*MANALAPAN, "US"))
    patch_geo(monkeypatch, "census", census)
    patch_geo(monkeypatch, "nominatim", nominatim)
    return census, nominatim


def test_both_providers_answering_the_wrong_point_leave_coordinates_blank(monkeypatch, palm_beach_zip):
    census, nominatim = _engines(monkeypatch, point(26.5570, -80.0420, "US"))
    assert geo._geocode_one(*PALM_BEACH.split("|")) is None
    assert len(census.calls) == len(nominatim.calls) == 1


def test_a_census_miss_after_a_wrong_osm_point_leaves_coordinates_blank(monkeypatch, palm_beach_zip):
    census, nominatim = _engines(monkeypatch, None)
    assert geo._geocode_one(*PALM_BEACH.split("|")) is None
    assert len(census.calls) == len(nominatim.calls) == 1


def test_a_census_point_away_from_the_wrong_one_is_taken(monkeypatch, palm_beach_zip):
    _engines(monkeypatch, point(*PALM_BEACH_2660, "US"))

    assert geo._geocode_one(*PALM_BEACH.split("|")) == point(*PALM_BEACH_2660, "US", source="census")


def test_a_census_outage_retries_next_run(tmp_path, monkeypatch, palm_beach_zip):
    _engines(monkeypatch, CensusUnavailable("down"))
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")
    geo._geocode_todo([PALM_BEACH], cache, 50)
    entry = cache.get(PALM_BEACH)
    assert entry["lookup_status"] == "transient_fail"
    assert (entry["lat"], entry["lng"]) == MANALAPAN
    assert geo._needs_lookup(PALM_BEACH, cache)
    assert geo.accepted_coordinates(PALM_BEACH, entry)[:2] == (None, None)


def test_the_run_withholds_the_wrong_point_without_deleting_cache_evidence(tmp_path, monkeypatch, palm_beach_zip):
    _engines(monkeypatch, None)
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")
    geo._geocode_todo([PALM_BEACH], cache, 50)
    entry = cache.get(PALM_BEACH)
    assert (entry["lat"], entry["lng"], entry["source"]) == (*MANALAPAN, "nominatim")
    assert entry["lookup_status"] == "not_found"
    assert geo.accepted_coordinates(PALM_BEACH, entry)[:2] == (None, None)
    assert not geo._needs_lookup(PALM_BEACH, cache)


def test_other_keys_keep_the_whole_chain(monkeypatch, palm_beach_zip):
    census, nominatim = _engines(monkeypatch, None)

    result = geo._geocode_one("2500 S OCEAN BLVD", "PALM BEACH", "FL", "33480")

    assert result["source"] == "nominatim" and nominatim.calls


def test_rejected_points_are_the_cached_points():
    """Each rejected point is what the cache held when it was reviewed, until a run re-geocodes it."""
    cache = _real_cache()
    checked = 0
    for key, (lat, lng, _note) in REVIEWED_WRONG_POINTS.items():
        entry = cache.get(key)
        if not entry or entry.get("lat") is None or entry.get("source") not in {"nominatim", "census", "google", "manual_census"}:
            continue
        if distance_km((entry["lat"], entry["lng"]), (lat, lng)) > 0.5:
            continue   # re-geocoded since
        assert (entry["lat"], entry["lng"]) == pytest.approx((lat, lng), abs=1e-6), key
        assert geo._needs_lookup(key, _as_cache(cache)), key
        checked += 1
    if not checked:
        pytest.skip("every reviewed wrong point is re-geocoded in this cache")


def _as_cache(data: dict) -> GeoCache:
    view = GeoCache.__new__(GeoCache)   # in memory only, never saved
    view.path, view.data = None, data
    return view


# ---- a building's name before the street

@pytest.mark.parametrize("filed, sent", [
    ("ONE WILLIAMS CENTER 101 E 2ND ST", "101 E 2ND ST"),              # MAGELLAN MIDSTREAM, Tulsa
    ("ONE WILLIAMS CENTER, 101 E 2ND STREET", "101 E 2ND STREET"),
    ("CIRA CENTRE, 2929 ARCH ST, SUITE 1700", "2929 ARCH ST"),
    ("ENCINO COMMONS BLDG, 16133 VENTURA BLVD", "16133 VENTURA BLVD"),  # not cut at 'BLDG' as a unit
    ("CITY HALL 1 CENTRE ST", "1 CENTRE ST"),
    # no building name, or no street after the number: unchanged
    ("ONE KENDALL SQ BUILDING 600 STE 380", "ONE KENDALL SQ BUILDING 600"),
    ("1 WORLD TRADE CENTER", "1 WORLD TRADE CENTER"),
    ("ONE MARKET PLAZA", "ONE MARKET PLAZA"),
    ("TOWER RD 5", "TOWER RD 5"),
    ("100 MAIN ST", "100 MAIN ST"),
])
def test_building_and_unit_text_is_sent_as_cleaned(monkeypatch, filed, sent):
    nominatim = _Engine(None)
    census = _Engine(None)
    patch_geo(monkeypatch, "nominatim", nominatim)
    patch_geo(monkeypatch, "census", census)
    geo._geocode_one(filed, "TULSA", "OK", "74172")
    assert nominatim.calls == census.calls == [(filed, "TULSA", "OK", "74172")]


def test_census_gets_the_same_cleaned_address_as_openstreetmap(monkeypatch, no_sleep):
    census = _Engine(point(36.1563, -95.9928, "US"))
    nominatim = _Engine(None)
    patch_geo(monkeypatch, "census", census)
    patch_geo(monkeypatch, "nominatim", nominatim)
    address = ("ONE WILLIAMS CENTER 101 E 2ND ST", "TULSA", "OK", "74172")
    assert geo._geocode_one(*address) == point(36.1563, -95.9928, "US", source="census")
    assert census.calls == nominatim.calls == [address]
