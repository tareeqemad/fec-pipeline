"""Street-level geocodes must lie inside the filed ZIP (a same-named street in another town is rejected).

Real cases from the 2026-09-23 audit: '3 RD FLOOR' NEW YORK 10023 was pinned in
Dutchess County (103 km away, cached as validated), 1 BRYANT PARK NY on Long Island
(33 km), N LA SALLE ST Chicago on the South Side. Large rural ZIPs (Kamas UT 84036)
legitimately hold addresses 30+ km from their centroid and must pass.

Being outside the filed ZIP is not enough to reject a point: when the filed ZIP has
no street of that name, the ZIP is the typo (3750 S DIXIE HWY MIAMI filed with Bal
Harbour's 33154) and a street point in the filed city is kept.
"""
import pytest

from fec.geocoding import GeoCache
from fec.geocoding import pipeline as geo
from fec.geocoding import accepted
from geo_patch import patch_geo
from fec.geocoding import zip_checks


@pytest.fixture
def real_centroids():
    if not zip_checks._ZIP_CENTROIDS.exists():
        pytest.skip("zip_centroids.csv not present")
    return zip_checks._zip_centroids()


@pytest.mark.parametrize("lat, lng, zipcode", [
    (41.686495, -73.7640282, "10023"),    # '3 RD FLOOR', Dutchess County
    (40.6145717, -73.6452839, "10036"),   # 1 BRYANT PARK on Long Island
    (41.7978, -87.6297, "60602"),         # 2 N LA SALLE ST on the South Side
    (33.9276013, -117.9724031, "90024"),  # 281 WILSHIRE AVE LA in Fullerton
    (40.647524, -73.918924, "10028"),     # 201 E 86TH ST in Canarsie
])
def test_wrong_town_matches_are_outside_their_zip(real_centroids, lat, lng, zipcode):
    assert accepted._street_far_from_zip(lat, lng, zipcode)


@pytest.mark.parametrize("lat, lng, zipcode", [
    (40.670464, -111.407427, "84036"),    # Kamas UT: 33 km from a huge rural ZIP's centroid
    (39.189424, -106.816967, "81611"),    # Aspen: 13 km out
    (42.817407, -106.359202, "82604"),    # Casper WY: 30 km out
    (40.7547795, -73.9844937, "10036"),   # 1 Bryant Park itself
    (40.749146, -73.991886, "10001"),     # 7 Penn Plaza
])
def test_real_addresses_stay_inside_their_zip(real_centroids, lat, lng, zipcode):
    assert not accepted._street_far_from_zip(lat, lng, zipcode)


def test_limit_has_a_floor_for_dense_downtown_zips(real_centroids):
    assert zip_checks.street_zip_limit_km("10036") == zip_checks._STREET_ZIP_FLOOR_KM
    assert zip_checks.street_zip_limit_km("84036") > 30
    assert zip_checks.street_zip_limit_km("00000") is None
    assert zip_checks.street_zip_limit_km("") is None


def test_validated_far_result_is_looked_up_again_once(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "3 RD FLOOR|NEW YORK|NY|10023"
    # the cached entry exactly as the audit found it: validated, 103 km away
    cache.put(key, 41.686495, -73.7640282, "nominatim", "US", validated=True)
    assert geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key))[2] == "rejected_far_from_zip"

    # the re-lookup's answer is final, even when it kept a far street (ZIP typo case)
    cache.put(key, 41.686495, -73.7640282, "nominatim", "US", validated=True, zip_checked=True)
    assert not geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key))[:2] == (41.686495, -73.7640282)


def test_rural_result_is_not_looked_up_again(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "13316 SLALOM RUN WAY|KAMAS|UT|84036"
    cache.put(key, 40.670464, -111.407427, "google")
    assert not geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key))[2] == "google"


def _synthetic(monkeypatch, centroids, size_km=1.0):
    """Tiny ZIP reference so the decision is exact; every ZIP 'size' is size_km."""
    patch_geo(monkeypatch, "_zip_centroids", lambda: centroids)
    patch_geo(monkeypatch, "_zip_neighbour_km", lambda _zip: size_km)
    monkeypatch.setattr(geo.time, "sleep", lambda _delay: None)
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    # by default the filed ZIP has no street of that name
    patch_geo(monkeypatch, "nominatim_within", lambda *_args: (None, None, None))


def test_far_street_result_falls_back_to_the_filed_zip(monkeypatch):
    # the filed ZIP has no '3 RD FLOOR' street and the Dutchess County match is
    # 100 km from New York: neither a same-named street nor a ZIP typo
    zip_point = (40.7769, -73.9813)                     # 10023, Upper West Side
    _synthetic(monkeypatch, {"10023": zip_point})
    patch_geo(monkeypatch, "nominatim", lambda *_args: (41.686495, -73.7640282, "US"))
    # the filed city agrees with the filed ZIP (NYC City Hall, 8 km away)
    patch_geo(monkeypatch, "city_level", lambda *_args: (40.7127, -74.0060, "US"))
    patch_geo(monkeypatch, "nominatim_international",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("not needed")))

    result = geo._geocode_one("3 RD FLOOR", "NEW YORK", "NY", "10023")

    assert result == (zip_point[0], zip_point[1], "US", "zip_centroid")


def test_far_street_result_is_kept_when_the_zip_contradicts_the_city(monkeypatch):
    # 791 HWY 77 N, WAXAHACHIE TX, filed with 75164 (Josephine, 89 km away):
    # the street match sits in Waxahachie, so the ZIP is the typo
    _synthetic(monkeypatch, {"75164": (33.06, -96.31)})
    patch_geo(monkeypatch, "nominatim", lambda *_args: (32.411453, -96.842919, "US"))
    patch_geo(monkeypatch, "city_level", lambda *_args: (32.3866, -96.8483, "US"))

    result = geo._geocode_one("791 HWY 77 N", "WAXAHACHIE", "TX", "75164")

    assert result == (32.411453, -96.842919, "US", "nominatim")


def test_census_result_outside_the_zip_yields_to_the_street_inside_it(monkeypatch):
    zip_point = (35.0456, -85.3097)                     # downtown Chattanooga
    _synthetic(monkeypatch, {"37402": zip_point})
    patch_geo(monkeypatch, "census", lambda *_args: (35.00681, -85.25003, "US"))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "city_level", lambda *_args: (35.0457, -85.3094, "US"))
    # 1 Fountain Square exists inside 37402: the census hit 7 km east was another place
    patch_geo(monkeypatch, "nominatim_within", lambda *_args: (35.0466, -85.3072, "US"))

    result = geo._geocode_one("1 FOUNTAIN SQUARE", "CHATTANOOGA", "TN", "37402")

    assert result == (35.0466, -85.3072, "US", "nominatim")


def test_same_named_street_in_another_part_of_town_is_replaced(monkeypatch):
    # 10 S LA SALLE ST, CHICAGO 60603 was matched on the South Side (9 km, still Chicago)
    zip_point = (41.8801, -87.6258)
    _synthetic(monkeypatch, {"60603": zip_point})
    patch_geo(monkeypatch, "nominatim", lambda *_args: (41.797837, -87.629691, "US"))
    patch_geo(monkeypatch, "city_level", lambda *_args: (41.8756, -87.6244, "US"))
    searched = []

    def within(street, city, state, box):
        searched.append((street, city, state, box))
        return 41.8814, -87.6324, "US"

    patch_geo(monkeypatch, "nominatim_within", within)

    result = geo._geocode_one("10 S LA SALLE ST", "CHICAGO", "IL", "60603")

    assert result == (41.8814, -87.6324, "US", "nominatim")
    street, city, state, (south, west, north, east) = searched[0]
    assert (street, city, state) == ("10 S LA SALLE ST", "CHICAGO", "IL")
    # the search box is the filed ZIP's accept area (5 km floor here)
    assert south < zip_point[0] < north and west < zip_point[1] < east
    assert 0.08 < north - south < 0.1


@pytest.mark.parametrize("street, city, state, zipcode, zip_point, found, city_point", [
    # audit verdict: ZIP typos whose street point is right
    ("3750 S DIXIE HWY", "MIAMI", "FL", "33154", (25.8859, -80.1322),
     (25.731757, -80.254089), (25.7743, -80.1937)),
    ("201 ROUSE BLVD", "PHILADELPHIA", "PA", "19122", (39.9781, -75.1427),
     (39.894862, -75.171003), (39.9527, -75.1635)),
    # no city filed: the filed ZIP's area (North Hollywood, 16 km) stands in for it
    ("1875 CENTURY PARK EAST", "", "CA", "91605", (34.2066, -118.4003),
     (34.061841, -118.415054), None),
])
def test_zip_typo_in_the_filed_city_keeps_its_street_point(
        monkeypatch, street, city, state, zipcode, zip_point, found, city_point):
    _synthetic(monkeypatch, {zipcode: zip_point})
    patch_geo(monkeypatch, "census", lambda *_args: (*found, "US"))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (*found, "US"))
    city_answer = (*city_point, "US") if city_point else (None, None, None)
    patch_geo(monkeypatch, "city_level", lambda *_args: city_answer)

    result = geo._geocode_one(street, city, state, zipcode)

    assert result == (*found, "US", "census")


@pytest.mark.parametrize("street, city, state, zipcode, zip_point, census, nominatim, city_point", [
    # live 2026-09-23 answers: Nominatim alone put 1 BRYANT PARK in Yonkers (24 km
    # from City Hall) and Census alone put 1 FOUNTAIN SQUARE in East Ridge (6 km)
    ("1 BRYANT PARK", "NEW YORK", "NY", "10036", (40.759792, -73.990685),
     None, (40.925288, -73.889775), (40.7127281, -74.0060152)),
    ("1 FOUNTAIN SQUARE", "CHATTANOOGA", "TN", "37402", (35.046805, -85.316289),
     (35.00681156287, -85.250028273462), None, (35.0457219, -85.3094883)),
    # the engines disagree (Houston MLK: 20 km apart)
    ("3500 MARTIN LUTHER KING JR. DRIVE", "HOUSTON", "TX", "77004", (29.724272, -95.364242),
     (29.817446, -95.335692), (29.9987805, -95.2880093), (29.7589382, -95.3676974)),
])
def test_one_engine_outside_the_zip_is_not_enough(
        monkeypatch, street, city, state, zipcode, zip_point, census, nominatim, city_point):
    _synthetic(monkeypatch, {zipcode: zip_point})
    patch_geo(monkeypatch, "census", lambda *_args: (*census, "US") if census else (None, None, None))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (*nominatim, "US") if nominatim else (None, None, None))
    patch_geo(monkeypatch, "city_level", lambda *_args: (*city_point, "US"))

    result = geo._geocode_one(street, city, state, zipcode)

    assert result == (*zip_point, "US", "zip_centroid")


def test_agreeing_engines_far_from_the_filed_city_are_not_kept(monkeypatch):
    zip_point = (40.7769, -73.9813)
    _synthetic(monkeypatch, {"10023": zip_point})
    far = (41.686495, -73.7640282)                      # 100 km away, both engines
    patch_geo(monkeypatch, "census", lambda *_args: (*far, "US"))
    patch_geo(monkeypatch, "nominatim", lambda *_args: (far[0] + 0.001, far[1], "US"))
    patch_geo(monkeypatch, "city_level", lambda *_args: (40.7127, -74.0060, "US"))

    assert geo._geocode_one("3 RD FLOOR", "NEW YORK", "NY", "10023")[3] == "zip_centroid"


def test_reviewed_zip_typo_keeps_its_cached_point(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "3750 S DIXIE HWY|MIAMI|FL|33154"
    cache.put(key, 25.7317566, -80.254089, "nominatim")
    assert accepted._street_far_from_zip(25.7317566, -80.254089, "33154")
    assert not geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key)) == (25.7317566, -80.254089, "nominatim")
    # the same point under a key nobody reviewed is still withheld until re-checked
    other = "3750 S DIXIE HWY|MIAMI|FL|33160"
    cache.put(other, 25.7317566, -80.254089, "nominatim")
    assert geo._needs_lookup(other, cache)


def test_every_reviewed_key_still_needs_its_exemption(real_centroids):
    """A reviewed key must be a real, cached street point outside its filed ZIP; otherwise the entry is stale."""
    import json
    from fec.geocoding.reviewed_points import REVIEWED_ZIP_TYPO_KEYS

    path = zip_checks._ZIP_CENTROIDS.parents[1] / "geocode_cache.json"
    if not path.exists():
        pytest.skip("geocode_cache.json not present")
    cache = json.loads(path.read_text(encoding="utf-8"))
    present = [key for key in REVIEWED_ZIP_TYPO_KEYS if key in cache]
    if not present:
        pytest.skip("no reviewed key in this cache")
    for key in present:
        entry = cache[key]
        assert entry.get("source") in geo.STREET_LEVEL_SOURCES, key
        assert accepted._street_far_from_zip(entry["lat"], entry["lng"], key.split("|")[3]), key


def test_every_new_result_is_marked_zip_checked(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "7 PENN PLZ|NEW YORK|NY|10001"
    patch_geo(monkeypatch, "_geocode_one", lambda *_args: (40.749146, -73.991886, "US", "census"))

    geo._geocode_todo([key], cache, 50)

    assert cache.get(key)["zip_checked"] is True
    assert cache.get(key)["validated"] is True


def test_zip_search_is_bounded_to_the_filed_zip(monkeypatch):
    from fec.geocoding import engines

    sent = {}

    def request(params):
        sent.update(params)
        return None, None, None

    monkeypatch.setattr(engines, "_nominatim_request", request)

    engines.nominatim_within("10 S LA SALLE ST", "CHICAGO", "IL", (41.83, -87.69, 41.93, -87.56))

    assert sent["q"] == "10 S LA SALLE ST, CHICAGO, IL"
    assert sent["bounded"] == 1 and sent["countrycodes"] == "us"
    assert sent["viewbox"] == "-87.69,41.93,-87.56,41.83"
