from fec.geocoding.cache import GeoCache
from fec.geocoding import pipeline as geo


def point(lat, lng, country="US", source=None, matched_address=None):
    result = {"lat": lat, "lng": lng, "country": country,
              "matched_address": matched_address or {}}
    if source is not None:
        result["source"] = source
    return result


def _cache(entry):
    cache = GeoCache.__new__(GeoCache)
    cache.path, cache.data = None, {"10 MAIN ST|AUSTIN|TX|78701": entry}
    return cache


def test_an_accepted_census_point_is_not_looked_up_again():
    entry = {"lat": 30.2672, "lng": -97.7431, "source": "census", "validated": True, "zip_checked": True}

    assert not geo._needs_lookup("10 MAIN ST|AUSTIN|TX|78701", _cache(entry))


def test_openstreetmap_answer_prevents_a_census_request(monkeypatch):
    calls = []
    monkeypatch.setattr(geo, "census", lambda *_args: calls.append("census") or point(35.00681, -85.25003, "US"))
    monkeypatch.setattr(geo, "nominatim", lambda *_args: calls.append("osm") or point(35.0466, -85.3072, "US"))
    assert geo._geocode_one("1 FOUNTAIN SQUARE", "CHATTANOOGA", "TN", "37402") == point(35.0466, -85.3072, "US", source="nominatim")
    assert calls == ["osm"]


def test_rejected_openstreetmap_answer_tries_census_with_same_address(monkeypatch):
    address = ("10 S LA SALLE ST", "CHICAGO", "IL", "60603")
    calls = []
    monkeypatch.setattr(geo, "nominatim", lambda *args: calls.append(("osm", args)) or point(30.2672, -97.7431, "US"))
    monkeypatch.setattr(geo, "census", lambda *args: calls.append(("census", args)) or point(41.8814, -87.6324, "US"))
    assert geo._geocode_one(*address) == point(41.8814, -87.6324, "US", source="census")
    assert calls == [("osm", address), ("census", address)]
