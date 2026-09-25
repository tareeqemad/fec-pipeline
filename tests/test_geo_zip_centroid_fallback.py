"""When street-level geocoding fails, the filed ZIP's centroid beats the city centroid.

Before: 42 BOCA RATON addresses in 6 ZIPs shared the one Boca Raton city pin, 11
HOUSTON addresses in 10 ZIPs the downtown Houston pin.

A PO box is different: it sits at the post office in the named town, so the town's
pin stays unless it lies outside the filed ZIP (a rural ZIP's centroid can be 28 km
out of San Angelo).
"""
import pytest

from fec.geocoding import GeoCache
from fec.geocoding import pipeline as geo
from geo_patch import patch_geo
from fec.geocoding import zip_checks

BOCA_CITY = (26.3586885, -80.0830984)
Z33496 = (26.4033, -80.1639)       # west Boca Raton, 9 km from the city pin
Z33434 = (26.3806, -80.1683)


@pytest.fixture
def boca(monkeypatch):
    patch_geo(monkeypatch, "_zip_centroids", lambda: {"33496": Z33496, "33434": Z33434})
    patch_geo(monkeypatch, "_zip_neighbour_km", lambda _zip: 3.0)
    monkeypatch.setattr(geo.time, "sleep", lambda _delay: None)


def test_cached_city_pin_becomes_the_zip_centroid(tmp_path, boca):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    street = "17104 NORTHWAY CIR|BOCA RATON|FL|33496"
    po_box = "PO BOX 5|BOCA RATON|FL|33434"
    no_centroid = "PO BOX 6|BOCA RATON|FL|33429"          # PO-box-only ZIP: no ZCTA
    conflict = "1 MAIN ST|BOCA RATON|FL|33496"
    for key in (street, po_box, no_centroid):
        cache.put(key, *BOCA_CITY, "nominatim_city", validated=True)
    cache.put(conflict, 30.33, -81.65, "nominatim_city")   # Jacksonville: contradicts the ZIP

    changed = geo.prefer_zip_centroids([street, po_box, no_centroid, conflict], cache)

    assert changed == 2
    assert (cache.get(street)["lat"], cache.get(street)["lng"]) == Z33496
    assert cache.get(street)["source"] == "zip_centroid"
    assert (cache.get(po_box)["lat"], cache.get(po_box)["lng"]) == Z33434
    assert cache.get(no_centroid)["source"] == "nominatim_city"
    assert cache.get(conflict)["lat"] == 30.33
    # stable: nothing to look up again, a second pass changes nothing
    assert not geo._needs_lookup(street, cache)
    assert geo.prefer_zip_centroids([street, po_box], cache) == 0


def test_failed_street_lookup_falls_back_to_the_zip_centroid(boca, monkeypatch):
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "city_level", lambda *_args: (*BOCA_CITY, "US"))

    result = geo._geocode_one("17104 NORTHWAY CIR", "BOCA RATON", "FL", "33496")

    assert result == (*Z33496, "US", "zip_centroid")


def test_po_box_uses_the_zip_centroid_when_the_town_pin_is_outside_the_zip(boca, monkeypatch):
    # the Boca Raton pin is 8.8 km from 33434's centroid, beyond its 7.5 km limit here
    patch_geo(monkeypatch, "city_level", lambda *_args: (*BOCA_CITY, "US"))

    assert geo._geocode_one("PO BOX 5", "BOCA RATON", "FL", "33434") == (*Z33434, "US", "zip_centroid")


def test_po_box_keeps_the_town_pin_inside_its_zip(boca, monkeypatch):
    patch_geo(monkeypatch, "_zip_neighbour_km", lambda _zip: 10.0)   # a rural-sized ZIP
    patch_geo(monkeypatch, "city_level", lambda *_args: (*BOCA_CITY, "US"))

    assert geo._geocode_one("PO BOX 5", "BOCA RATON", "FL", "33434") == (*BOCA_CITY, "US", "nominatim_city")


@pytest.mark.parametrize("key, town_pin, replaced", [
    # rural ZIPs: the ZCTA centroid lies 16-30 km out of town, the box is in town
    ("PO BOX 1|SAN ANGELO|TX|76902", (31.4649685, -100.4405094), False),
    ("PO BOX 1|ASPEN|CO|81612", (39.1911128, -106.82356), False),
    ("PO BOX 1|PIERRE|SD|57501", (44.3683644, -100.351136), False),
    ("PO BOX 1|FOND DU LAC|WI|54964", (43.7533414, -88.4493796), False),
    # the downtown St. Louis pin is outside Webster Groves' 63119: the ZIP is closer
    ("PO BOX 1|SAINT LOUIS|MO|63119", (38.6254063, -90.190009), True),
])
def test_po_box_town_pins_on_the_real_zip_reference(tmp_path, key, town_pin, replaced):
    if not zip_checks._ZIP_CENTROIDS.exists():
        pytest.skip("zip_centroids.csv not present")
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.put(key, *town_pin, "nominatim_city", validated=True)

    assert geo.prefer_zip_centroids([key], cache) == int(replaced)
    expected = zip_checks._zip_centroids()[key.split("|")[3]] if replaced else town_pin
    assert (cache.get(key)["lat"], cache.get(key)["lng"]) == expected


def test_city_pin_is_kept_when_the_zip_has_no_centroid(boca, monkeypatch):
    patch_geo(monkeypatch, "city_level", lambda *_args: (*BOCA_CITY, "US"))

    assert geo._geocode_one("PO BOX 6", "BOCA RATON", "FL", "33429") == (*BOCA_CITY, "US", "nominatim_city")


def test_city_zip_conflict_gives_no_coordinate_for_a_street(boca, monkeypatch):
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "city_level", lambda *_args: (30.33, -81.65, "US"))
    patch_geo(monkeypatch, "nominatim_international", lambda *_args: (None, None, None))

    assert geo._geocode_one("1 MAIN ST", "BOCA RATON", "FL", "33496") == (None, None, None, "not_found")


def test_unknown_city_falls_back_to_the_filed_zip(boca, monkeypatch):
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "city_level", lambda *_args: (None, None, None))

    assert geo._geocode_one("1 MAIN ST", "BOCA RATN", "FL", "33496") == (*Z33496, "US", "zip_centroid")


def test_zip_centroid_in_another_state_is_not_used(monkeypatch):
    # 33496 filed with state NY: the Florida centroid is outside the filed state
    patch_geo(monkeypatch, "_zip_centroids", lambda: {"33496": Z33496})
    assert geo._zip_point("33496", "NY") is None
    assert geo._zip_point("33496", "FL") == Z33496
