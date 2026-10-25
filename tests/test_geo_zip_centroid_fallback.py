"""Failed address lookups never publish a city or ZIP approximation."""
import pytest
from geo_patch import patch_geo, point

from fec.geocoding import pipeline as geo
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import NominatimUnavailable

BOCA_CITY = (26.3586885, -80.0830984)
Z33496 = (26.4033, -80.1639)
Z33434 = (26.3806, -80.1683)


@pytest.fixture
def boca(monkeypatch):
    patch_geo(monkeypatch, "census", lambda *_args: None)
    patch_geo(monkeypatch, "nominatim", lambda *_args: None)


def test_cached_city_pins_are_withheld_without_rewriting_cache(tmp_path, boca):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    keys = [
        "17104 NORTHWAY CIR|BOCA RATON|FL|33496",
        "PO BOX 5|BOCA RATON|FL|33434",
        "PO BOX 6|BOCA RATON|FL|33429",
        "1 MAIN ST|BOCA RATON|FL|33496",
    ]
    for key in keys:
        cache.put(key, *BOCA_CITY, "nominatim_city")
    before = {key: dict(entry) for key, entry in cache.data.items()}

    for key in keys:
        assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)
        assert geo._needs_lookup(key, cache)
    assert cache.data == before


def test_failed_street_lookup_leaves_coordinates_blank(boca, monkeypatch):
    assert geo._geocode_one("17104 NORTHWAY CIR", "BOCA RATON", "FL", "33496") is None


def test_po_box_does_not_use_the_zip_centroid(boca, monkeypatch):
    assert geo._geocode_one("PO BOX 5", "BOCA RATON", "FL", "33434") is None


def test_outage_followed_by_other_provider_miss_stays_retryable(tmp_path, boca, monkeypatch):
    def outage(*args):
        raise NominatimUnavailable("temporary failure")
    patch_geo(monkeypatch, "nominatim", outage)
    address = ("17104 NORTHWAY CIR", "BOCA RATON", "FL", "33496")
    with pytest.raises(NominatimUnavailable):
        geo._geocode_one(*address)
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "|".join(address)
    geo._geocode_todo([key], cache, 50)
    assert cache.get(key)["source"] == "transient_fail"
    assert geo._needs_lookup(key, cache)


@pytest.mark.parametrize("key, town_pin", [
    ("PO BOX 1|SAN ANGELO|TX|76902", (31.4649685, -100.4405094)),
    ("PO BOX 1|ASPEN|CO|81612", (39.1911128, -106.82356)),
    ("PO BOX 1|PIERRE|SD|57501", (44.3683644, -100.351136)),
    ("PO BOX 1|FOND DU LAC|WI|54964", (43.7533414, -88.4493796)),
    ("PO BOX 1|SAINT LOUIS|MO|63119", (38.6254063, -90.190009)),
])
def test_po_box_town_pins_are_not_published(tmp_path, key, town_pin):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.put(key, *town_pin, "nominatim_city")
    assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)
    assert geo._needs_lookup(key, cache)
    assert (cache.get(key)["lat"], cache.get(key)["lng"]) == town_pin


def test_unknown_po_box_zip_does_not_get_a_city_pin(boca, monkeypatch):
    assert geo._geocode_one("PO BOX 6", "BOCA RATON", "FL", "33429") is None


def test_city_zip_conflict_gives_no_coordinate_for_a_street(boca, monkeypatch):
    assert geo._geocode_one("1 MAIN ST", "BOCA RATON", "FL", "33496") is None


def test_osm_outage_still_tries_census_with_the_same_address(boca, monkeypatch):
    calls = []
    def outage(*args):
        calls.append(("osm", args))
        raise NominatimUnavailable("temporary failure")
    patch_geo(monkeypatch, "nominatim", outage)
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or point(*Z33496, "US"))
    address = ("17104 NORTHWAY CIR", "BOCA RATON", "FL", "33496")
    assert geo._geocode_one(*address) == point(*Z33496, "US", source="census")
    assert calls == [("osm", address), ("census", address)]


def test_cached_zip_centroid_is_not_used(tmp_path):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    for state in ("NY", "FL"):
        key = f"1 MAIN ST|BOCA RATON|{state}|33496"
        cache.put(key, *Z33496, "zip_centroid")
        assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)
        assert geo._needs_lookup(key, cache)
