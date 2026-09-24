"""Hand-checked points (2026-09-24 audit, area street-wrong-town).

REVIEWED_POINTS: a right point copied from a paid Google result under another
spelling of the address, published instead of the wrong cached one.
REVIEWED_WRONG_POINTS: a wrong cached street point that is withheld and looked up
again with Census only, falling back to the filed ZIP's centroid.
"""
import json
import re

import pytest

from fec.geocoding import GeoCache
from fec.geocoding import pipeline as geo
from fec.geocoding.engines import CensusUnavailable
from fec.geocoding.places import distance_km
from fec.geocoding.reviewed_points import (
    REVIEWED_POINTS,
    REVIEWED_WRONG_POINTS,
    REVIEWED_ZIP_TYPO_KEYS,
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
    monkeypatch.setattr(geo.time, "sleep", lambda _delay: None)


@pytest.fixture
def palm_beach_zip(monkeypatch, no_sleep):
    monkeypatch.setattr(geo, "_zip_centroids", lambda: {"33480": ZIP_33480})
    monkeypatch.setattr(geo, "_zip_neighbour_km", lambda _zip: 5.9)   # a 14.8 km limit, as for 33480


# ---- REVIEWED_POINTS

def test_a_reviewed_point_is_published_instead_of_the_cached_one(tmp_path):
    cache = _cache(tmp_path, FORT_APACHE, *FORT_APACHE_NOMINATIM, "nominatim")

    assert geo.accepted_coordinates(FORT_APACHE, cache.get(FORT_APACHE)) == (*FORT_APACHE_GOOGLE, "google")
    # also when the key has no cache entry at all
    assert geo.accepted_coordinates(FORT_APACHE, None) == (*FORT_APACHE_GOOGLE, "google")


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
        assert key.count("|") == 3 and geo._valid_for_state(lat, lng, key.split("|")[2]), key
        assert _source_key(note).count("|") == 3, key
    # the lists never contradict each other
    assert not set(REVIEWED_POINTS) & set(REVIEWED_WRONG_POINTS)
    assert not set(REVIEWED_POINTS) & set(REVIEWED_ZIP_TYPO_KEYS)


def _real_cache() -> dict:
    path = geo._ZIP_CENTROIDS.parents[1] / "geocode_cache.json"
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


def test_reviewed_points_replace_a_different_cached_point():
    """A reviewed key whose cached point already is the reviewed one needs no entry here."""
    cache = _real_cache()
    for key, (lat, lng, _note) in REVIEWED_POINTS.items():
        entry = cache.get(key)
        if entry and entry.get("lat") is not None:
            assert distance_km((entry["lat"], entry["lng"]), (lat, lng)) > 1, key


# ---- REVIEWED_WRONG_POINTS

def test_a_wrong_street_point_is_withheld_and_looked_up_again(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")
    # inside the 33480 circle: no ZIP rule catches it
    assert not geo._street_far_from_zip(*MANALAPAN, "33480")

    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH)) == (None, None, "rejected_reviewed_wrong")
    assert geo._needs_lookup(PALM_BEACH, cache)
    # a run's own zip_checked mark does not make it right
    cache.data[PALM_BEACH].update(validated=True, zip_checked=True)
    assert geo._needs_lookup(PALM_BEACH, cache)


def test_a_new_point_away_from_the_wrong_one_stands(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *PALM_BEACH_2660, "census", validated=True, zip_checked=True)

    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH))[:2] == PALM_BEACH_2660
    assert not geo._needs_lookup(PALM_BEACH, cache)


def test_a_centroid_on_the_wrong_spot_is_honest_and_stands(tmp_path, palm_beach_zip):
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "zip_centroid", validated=True)

    assert geo.accepted_coordinates(PALM_BEACH, cache.get(PALM_BEACH))[:2] == MANALAPAN
    assert not geo._needs_lookup(PALM_BEACH, cache)


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
    nominatim = _Engine((*MANALAPAN, "US"))
    monkeypatch.setattr(geo, "census", census)
    monkeypatch.setattr(geo, "nominatim", nominatim)
    monkeypatch.setattr(geo, "nominatim_within", nominatim)
    monkeypatch.setattr(geo, "city_level", _Engine((26.7056, -80.0364, "US")))
    return census, nominatim


def test_census_answering_the_wrong_point_again_falls_back_to_the_zip(monkeypatch, palm_beach_zip):
    census, nominatim = _engines(monkeypatch, (26.5570, -80.0420, "US"))   # 30 m from the rejected point

    assert geo._geocode_one(*PALM_BEACH.split("|")) == (*ZIP_33480, "US", "zip_centroid")
    assert len(census.calls) == 1 and nominatim.calls == []   # Nominatim gave the wrong point


def test_a_census_miss_falls_back_to_the_zip(monkeypatch, palm_beach_zip):
    _census, nominatim = _engines(monkeypatch, (None, None, None))

    assert geo._geocode_one(*PALM_BEACH.split("|")) == (*ZIP_33480, "US", "zip_centroid")
    assert nominatim.calls == []


def test_a_census_point_away_from_the_wrong_one_is_taken(monkeypatch, palm_beach_zip):
    _engines(monkeypatch, (*PALM_BEACH_2660, "US"))

    assert geo._geocode_one(*PALM_BEACH.split("|")) == (*PALM_BEACH_2660, "US", "census")


def test_a_census_outage_retries_next_run(tmp_path, monkeypatch, palm_beach_zip):
    _engines(monkeypatch, CensusUnavailable("down"))
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")

    geo._geocode_todo([PALM_BEACH], cache, 50)

    assert cache.get(PALM_BEACH)["source"] == "transient_fail"
    assert geo._needs_lookup(PALM_BEACH, cache)


def test_the_run_replaces_the_wrong_point(tmp_path, monkeypatch, palm_beach_zip):
    _engines(monkeypatch, (None, None, None))
    cache = _cache(tmp_path, PALM_BEACH, *MANALAPAN, "nominatim")

    geo._geocode_todo([PALM_BEACH], cache, 50)

    entry = cache.get(PALM_BEACH)
    assert (entry["lat"], entry["lng"], entry["source"]) == (*ZIP_33480, "zip_centroid")
    assert geo.accepted_coordinates(PALM_BEACH, entry)[:2] == ZIP_33480
    assert not geo._needs_lookup(PALM_BEACH, cache)


def test_other_keys_keep_the_whole_chain(monkeypatch, palm_beach_zip):
    census, nominatim = _engines(monkeypatch, (None, None, None))

    result = geo._geocode_one("2500 S OCEAN BLVD", "PALM BEACH", "FL", "33480")

    assert result[3] == "nominatim" and nominatim.calls


def test_rejected_points_are_the_cached_points():
    """Each rejected point is what the cache held when it was reviewed, until a run re-geocodes it."""
    cache = _real_cache()
    checked = 0
    for key, (lat, lng, _note) in REVIEWED_WRONG_POINTS.items():
        entry = cache.get(key)
        if not entry or entry.get("lat") is None or entry.get("source") not in geo._REVIEWED_STREET_SOURCES:
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
def test_a_leading_building_name_is_not_sent(filed, sent):
    assert geo._clean_street_for_geocoding(filed) == sent


def test_census_gets_the_street_without_the_building_name(monkeypatch, no_sleep):
    census = _Engine((36.1563, -95.9928, "US"))
    monkeypatch.setattr(geo, "census", census)

    geo._geocode_one("ONE WILLIAMS CENTER 101 E 2ND ST", "TULSA", "OK", "74172")

    assert census.calls == [("101 E 2ND ST", "TULSA", "OK", "74172")]
