from fec.geocoding.cache import GeoCache
from fec.geocoding import pipeline as geo


def point(lat, lng, country="US", source=None):
    result = {"lat": lat, "lng": lng, "country": country}
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


def test_cache_is_keyed_by_the_cleaned_address(tmp_path):
    from fec.geocoding.cache import GeoCache
    cache = GeoCache(str(tmp_path / 'cache.json'))
    cache.put('2 N RIVERSIDE PLZ|CHICAGO|IL|60606', 41.88, -87.64, 'nominatim')
    assert cache.get('2 N RIVERSIDE PLZ|CHICAGO|IL|60606')['lat'] == 41.88
    assert cache.get('2 N RIVERSIDE PLZ|CHICAGO|IL|60601') is None
    assert not cache.needs_retry('2 N RIVERSIDE PLZ|CHICAGO|IL|60606')
    assert cache.needs_retry('2 N RIVERSIDE PLAZA|CHICAGO|IL|60606')      # an earlier spelling is another key
