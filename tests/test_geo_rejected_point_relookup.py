"""A cached point its own address rules out is looked up again, whatever engine gave it."""
from fec.geocoding import pipeline as gp
from fec.geocoding.cache import GeoCache


def _cache(entry):
    cache = GeoCache.__new__(GeoCache)
    cache.path, cache.data = None, {"10 MAIN ST|AUSTIN|TX|78701": entry}
    return cache


def test_a_census_point_in_another_state_is_looked_up_again():
    # a Census answer in Maine for an Austin, TX address: never published, so it must be retried
    entry = {"lat": 44.3, "lng": -69.8, "source": "census", "validated": True, "zip_checked": True}

    assert gp.accepted_coordinates("10 MAIN ST|AUSTIN|TX|78701", entry)[0] is None
    assert gp._needs_lookup("10 MAIN ST|AUSTIN|TX|78701", _cache(entry))


def test_an_accepted_census_point_is_not_looked_up_again():
    entry = {"lat": 30.2672, "lng": -97.7431, "source": "census", "validated": True, "zip_checked": True}

    assert not gp._needs_lookup("10 MAIN ST|AUSTIN|TX|78701", _cache(entry))
