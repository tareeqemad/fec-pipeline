"""Providers must agree with the filed state and ZIP; geocoding never guesses alternatives."""
import pytest
from geo_patch import patch_geo, point

from fec.env import GEOCODE_CACHE_JSON
from fec.geocoding import validation as accepted
from fec.geocoding import providers
from test_geo_town_level_search import _Response
from fec.geocoding import pipeline as geo
from fec.geocoding.cache import GeoCache


@pytest.fixture
def real_centroids():
    return True


@pytest.mark.parametrize("lat, lng, zipcode", [
    (41.686495, -73.7640282, "10023"),    # '3 RD FLOOR', Dutchess County
    (40.6145717, -73.6452839, "10036"),   # 1 BRYANT PARK on Long Island
    (41.7978, -87.6297, "60602"),         # 2 N LA SALLE ST on the South Side
    (33.9276013, -117.9724031, "90024"),  # 281 WILSHIRE AVE LA in Fullerton
    (40.647524, -73.918924, "10028"),     # 201 E 86TH ST in Canarsie
])
def test_provider_zip_disagreement_is_rejected(monkeypatch, real_centroids, lat, lng, zipcode):
    answer = [{"lat": str(lat), "lon": str(lng), "category": "building", "type": "yes",
               "address": {"house_number": "10", "postcode": "00000", "country_code": "us"}}]
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(providers.requests, "get", lambda *_args, **_kwargs: _Response(answer))
    assert providers.nominatim("10 MAIN ST", "FILED CITY", "", zipcode) is None


@pytest.mark.parametrize("lat, lng, state, zipcode, city", [
    (40.670464, -111.407427, "UT", "84036", "KAMAS"),
    (39.189424, -106.816967, "CO", "81611", "ASPEN"),
    (42.817407, -106.359202, "WY", "82604", "CASPER"),
    (40.7547795, -73.9844937, "NY", "10036", "NEW YORK"),
    (40.749146, -73.991886, "NY", "10001", "NEW YORK"),
])
def test_provider_matching_zip_keeps_the_address(monkeypatch, real_centroids, lat, lng, state, zipcode, city):
    answer = [{"lat": str(lat), "lon": str(lng), "category": "building", "type": "yes",
               "display_name": f"10 Main Street, {city}, {state}, {zipcode}",
               "address": {"house_number": "10", "road": "Main Street", "city": city,
                           "ISO3166-2-lvl4": f"US-{state}", "postcode": zipcode + "-1234",
                           "country_code": "us"}}]
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(providers.requests, "get", lambda *_args, **_kwargs: _Response(answer))
    result = providers.nominatim("10 MAIN ST", "FILED CITY", state, zipcode)
    assert (result["lat"], result["lng"], result["country"]) == (lat, lng, "US")
    matched = result["matched_address"]
    assert matched["house_number"] == "10"
    assert matched["street"].upper() == "MAIN STREET"
    assert matched["city"].upper() == city
    assert matched["state"] == state and matched["zip"].startswith(zipcode)
    assert matched["source_url"].startswith(providers.NOMINATIM_URL)
    key = f"10 MAIN ST|FILED CITY|{state}|{zipcode}"
    entry = {**result, "source": "nominatim"}
    assert accepted.confirmed_city(key, entry) == city
    for field in ("house_number", "street", "state", "zip", "city"):
        incomplete = {**matched, field: ""}
        assert accepted.confirmed_city(key, {**entry, "matched_address": incomplete}) is None
    for field, value in (("house_number", "11"), ("house_number", "10A"),
                         ("house_number", "010"), ("street", "MAIN RD"),
                         ("street", "N MAIN ST"), ("street", "MAIN ST W"),
                         ("state", "FL"), ("zip", "00000")):
        different = {**matched, field: value}
        assert accepted.confirmed_city(key, {**entry, "matched_address": different}) is None
    assert accepted.confirmed_city(f"10 MAIN ST|FILED CITY||{zipcode}", entry) is None
    assert accepted.confirmed_city(f"10 MAIN ST|FILED CITY|{state}|", entry) is None
    ambiguous = {**entry, "matched_address": {**matched, "street": "N ST"}}
    assert accepted.confirmed_city(f"10 NORTH ST|FILED CITY|{state}|{zipcode}", ambiguous) is None


def test_state_disagreement_is_rejected(monkeypatch, real_centroids):
    answer = [{"lat": "40.75", "lon": "-73.99", "category": "building", "type": "yes",
               "address": {"house_number": "10", "ISO3166-2-lvl4": "US-NJ", "postcode": "10001", "country_code": "us"}}]
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(providers.requests, "get", lambda *_args, **_kwargs: _Response(answer))
    assert providers.nominatim("10 MAIN ST", "NEW YORK", "NY", "10001") is None
    key = "10 MAIN ST|SAIPAN|MP|96950"
    assert accepted.accepted_coordinates(key, {"lat": 15.2, "lng": 145.75, "source": "nominatim", "country": "MP"})[:2] == (15.2, 145.75)
    assert accepted.accepted_coordinates(key, {"lat": 40.75, "lng": -73.99, "source": "nominatim", "country": "MP"})[:2] == (None, None)


def test_validated_wrong_state_result_is_looked_up_again(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "3 RD FLOOR|NEW YORK|NY|10023"
    cache.data[key] = {"lat": 30.2672, "lng": -97.7431, "source": "nominatim", "validated": True, "zip_checked": True}
    assert geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)


def test_rural_result_is_not_looked_up_again(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "13316 SLALOM RUN WAY|KAMAS|UT|84036"
    cache.put(key, 40.670464, -111.407427, "google")
    assert not geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key))[2] == "google"


def _synthetic(monkeypatch, centroids=None, size_km=1.0):
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    patch_geo(monkeypatch, "census", lambda *_args: None)


def test_wrong_state_street_result_leaves_coordinates_blank(monkeypatch):
    _synthetic(monkeypatch)
    patch_geo(monkeypatch, "nominatim", lambda *_args: point(30.2672, -97.7431, "US"))
    assert geo._geocode_one("10 MAIN ST", "NEW YORK", "NY", "10023") is None


def test_provider_zip_conflict_is_not_reinterpreted_as_a_zip_typo(monkeypatch):
    answer = [{"lat": "32.411453", "lon": "-96.842919", "category": "building", "type": "yes",
               "address": {"house_number": "791", "postcode": "75165", "country_code": "us", "state_code": "TX"}}]
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(providers.requests, "get", lambda *_args, **_kwargs: _Response(answer))
    assert providers.nominatim("791 HWY 77 N", "WAXAHACHIE", "TX", "75164") is None


def test_openstreetmap_answer_prevents_a_census_request(monkeypatch):
    _synthetic(monkeypatch)
    calls = []
    patch_geo(monkeypatch, "census", lambda *_args: calls.append("census") or point(35.00681, -85.25003, "US"))
    patch_geo(monkeypatch, "nominatim", lambda *_args: calls.append("osm") or point(35.0466, -85.3072, "US"))
    assert geo._geocode_one("1 FOUNTAIN SQUARE", "CHATTANOOGA", "TN", "37402") == point(35.0466, -85.3072, "US", source="nominatim")
    assert calls == ["osm"]


def test_rejected_openstreetmap_answer_tries_census_with_same_address(monkeypatch):
    address = ("10 S LA SALLE ST", "CHICAGO", "IL", "60603")
    calls = []
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(("osm", args)) or point(30.2672, -97.7431, "US"))
    patch_geo(monkeypatch, "census", lambda *args: calls.append(("census", args)) or point(41.8814, -87.6324, "US"))
    assert geo._geocode_one(*address) == point(41.8814, -87.6324, "US", source="census")
    assert calls == [("osm", address), ("census", address)]


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
def test_provider_match_preserves_the_filed_address(
        monkeypatch, street, city, state, zipcode, zip_point, found, city_point):
    calls = []
    patch_geo(monkeypatch, "nominatim", lambda *args: calls.append(args) or point(*found, "US"))
    patch_geo(monkeypatch, "census", lambda *_args: (_ for _ in ()).throw(AssertionError("not needed")))
    assert geo._geocode_one(street, city, state, zipcode) == point(*found, "US", source="nominatim")
    assert calls == [(street, city, state, zipcode)]


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
def test_census_mismatching_zip_is_not_published(
        monkeypatch, street, city, state, zipcode, zip_point, census, nominatim, city_point):
    coordinates = census or nominatim
    answer = {"result": {"addressMatches": [{"matchedAddress": street,
              "addressComponents": {"state": state, "zip": "00000"},
              "coordinates": {"y": coordinates[0], "x": coordinates[1]}}]}}
    monkeypatch.setattr(providers.requests, "get", lambda *_args, **_kwargs: _Response(answer))
    assert providers.census(street, city, state, zipcode) is None
    match = answer["result"]["addressMatches"][0]
    match["matchedAddress"] = f"{street}, {city}, {state}, {zipcode}"
    match["addressComponents"] = {
        "state": state, "zip": zipcode, "city": city,
        "fromAddress": "2", "toAddress": "9998",
    }
    result = providers.census(street, city, state, zipcode)
    assert result["matched_address"]["house_number"] == street.split()[0]
    assert result["matched_address"]["city"] == city
    assert result["matched_address"]["source_url"].startswith(providers.CENSUS_URL)


def test_agreeing_engines_in_another_state_are_not_kept(monkeypatch):
    far = (30.2672, -97.7431)
    patch_geo(monkeypatch, "census", lambda *_args: point(*far, "US"))
    patch_geo(monkeypatch, "nominatim", lambda *_args: point(far[0] + 0.001, far[1], "US"))
    assert geo._geocode_one("10 MAIN ST", "NEW YORK", "NY", "10023") is None


def test_existing_street_point_is_kept_without_changing_filed_zip(tmp_path, real_centroids):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "3750 S DIXIE HWY|MIAMI|FL|33154"
    cache.put(key, 25.7317566, -80.254089, "nominatim")
    before = dict(cache.get(key))
    assert not geo._needs_lookup(key, cache)
    assert geo.accepted_coordinates(key, cache.get(key)) == (25.7317566, -80.254089, "nominatim")
    assert cache.get(key) == before
    assert accepted.confirmed_city(key, cache.get(key)) is None


def test_every_reviewed_wrong_point_is_withheld(real_centroids):
    for key, (lat, lng, _note) in accepted.REVIEWED_WRONG_POINTS.items():
        entry = {"lat": lat, "lng": lng, "source": "nominatim"}
        assert accepted.accepted_coordinates(key, entry)[:2] == (None, None), key


def test_every_new_result_is_saved_with_its_provider(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    key = "7 PENN PLZ|NEW YORK|NY|10001"
    matched = {
        "text": "7 PENN PLZ, NEW YORK, NY, 10001", "house_number": "7",
        "street": "PENN PLZ", "city": "NEW YORK", "state": "NY", "zip": "10001",
        "source_url": providers.CENSUS_URL + "?address=7+PENN+PLZ%2C+NEW+YORK%2C+NY%2C+10001",
    }
    result = point(40.749146, -73.991886, source="census", matched_address=matched)
    patch_geo(monkeypatch, "_geocode_one", lambda *_args: result)
    geo._geocode_todo([key], cache, 50)
    assert geo.accepted_coordinates(key, cache.get(key)) == (40.749146, -73.991886, "census")
    assert not geo._needs_lookup(key, cache)

    assert cache.get(key)["matched_address"] == matched
    reloaded = GeoCache(cache.path)
    assert reloaded.get(key) == cache.get(key)
    assert not geo._needs_lookup(key, reloaded)


def test_address_query_is_exact_and_has_no_bounded_retry(monkeypatch):
    sent = []
    monkeypatch.setattr(providers, "_wait_for_nominatim", lambda _url: None)
    monkeypatch.setattr(providers.requests, "get", lambda _url, params, **kwargs: sent.append(params) or _Response([]))
    assert providers.nominatim("10 S LA SALLE ST", "CHICAGO", "IL", "60603") is None
    assert len(sent) == 1
    assert sent[0]["q"] == "10 S LA SALLE ST, CHICAGO, IL, 60603"
    assert sent[0]["countrycodes"] == "us"
    assert "viewbox" not in sent[0] and "bounded" not in sent[0]
