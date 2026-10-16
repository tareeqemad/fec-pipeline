"""Address queries reject city and ZIP approximations, including old cached pins."""
import pytest
from geo_patch import patch_geo

from fec.env import GEOCODE_CACHE_JSON
from fec.geocoding import providers as engines
from fec.geocoding import validation as places
from fec.geocoding import pipeline as geo
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import NominatimUnavailable


def _place(name, addresstype, lat, lng, state, category="boundary", kind="administrative", **names):
    """One Nominatim jsonv2 search result as the live service returns it."""
    return {
        "lat": str(lat), "lon": str(lng), "name": name,
        "addresstype": addresstype, "category": category, "type": kind,
        "namedetails": {"name": name, **names},
        "address": {"ISO3166-2-lvl4": f"US-{state}", "country_code": "us"},
    }


# the audit's wrong answers, at their cached coordinates
MURPHY_USA_SEALY = _place("Murphy USA", "amenity", 29.7569077, -96.1511306, "TX", "amenity", "fuel")
LEGO_MINILAND = _place("Lego San Francisco, Miniland USA", "tourism", 33.1282409, -117.3113959,
                       "CA", "tourism", "attraction")
JACKSON_COUNTY = _place("Jackson County", "county", 30.4899024, -88.6486325, "MS")
CROWN_POINT_ROAD = _place("Crown Point", "road", 39.9642293, -86.2745354, "IN", "highway", "residential")
POTOMAC_BUILDING = _place("Potomac", "building", 39.3954942, -76.5772786, "MD", "building", "yes")
NORTH_LITTLE_ROCK = _place("North Little Rock", "town", 34.769536, -92.2670941, "AR")
JAMAICA_STATION = _place("Jamaica", "railway", 40.69983, -73.8077, "NY", "railway", "station")
# the towns themselves (live structured answers, 2026-09-24)
LITTLE_ROCK = _place("Little Rock", "city", 34.7465071, -92.2896267, "AR")
HOUSTON_MO = _place("Houston", "village", 37.3261588, -91.955988, "MO")
HOUSTON_TX = _place("Houston", "city", 29.7589382, -95.3676974, "TX")
POTOMAC_MONTGOMERY = _place("Potomac", "town", 39.017936, -77.2094542, "MD", "place", "town")
POTOMAC_ALLEGANY = _place("Potomac", "hamlet", 39.5675895, -78.83891, "MD", "place", "hamlet")
FOREST_HILLS_QUEENS = _place("Forest Hills", "neighbourhood", 40.72141, -73.84382, "NY", "place", "neighbourhood")
CHERRY_HILL = _place("Cherry Hill Township", "city", 39.93483, -75.03072, "NJ", short_name="Cherry Hill")


class _Nominatim:
    """Stands in for requests.get: records each search and gives one answer to all of them."""

    def __init__(self, answer):
        self.answer = list(answer)
        self.sent = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.sent.append(dict(params))
        return _Response(self.answer)


class _Response:
    status_code = 200
    ok = True

    def __init__(self, answer):
        self._answer = answer

    def json(self):
        return self._answer


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(engines.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(engines, "_wait_for_nominatim", lambda _url: None)


def _serve(monkeypatch, answer):
    service = _Nominatim(answer)
    monkeypatch.setattr(engines.requests, "get", service)
    return service


@pytest.mark.parametrize("city, state, wrong", [
    ("AUSTIN", "TX", MURPHY_USA_SEALY),        # POI named '... USA', 164 km away
    ("SAN FRANCISCO", "CA", LEGO_MINILAND),    # POI named '... USA', 694 km away
    ("JACKSON", "MS", JACKSON_COUNTY),         # the county, 248 km from Jackson
    ("CROWN POINT", "IN", CROWN_POINT_ROAD),   # a road named like the town
    ("POTOMAC", "MD", POTOMAC_BUILDING),       # a building named like the town
    ("LITTLE ROCK", "AR", NORTH_LITTLE_ROCK),  # a real town, but the next city
    ("JAMAICA", "NY", JAMAICA_STATION),        # a station named like the town
])
def test_a_place_without_the_filed_house_number_is_rejected(monkeypatch, no_sleep, city, state, wrong):
    service = _serve(monkeypatch, [wrong])
    assert engines.nominatim("10 MAIN ST", city, state, "") == (None, None, None)
    [sent] = service.sent
    assert sent["q"] == f"10 MAIN ST, {city}, {state}"
    assert sent["countrycodes"] == "us" and sent["limit"] == 1
    assert "viewbox" not in sent and "city" not in sent


def test_neither_a_neighbouring_city_nor_the_town_is_an_address(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [NORTH_LITTLE_ROCK, LITTLE_ROCK])
    assert engines.nominatim("10 MAIN ST", "LITTLE ROCK", "AR", "72201") == (None, None, None)
    assert len(service.sent) == 1


def test_same_name_towns_in_either_state_are_not_an_address(monkeypatch, no_sleep):
    _serve(monkeypatch, [HOUSTON_MO, HOUSTON_TX])
    assert engines.nominatim("10 MAIN ST", "HOUSTON", "TX", "77004") == (None, None, None)


def test_state_is_sent_as_cleaned(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [])
    engines.nominatim("10 MAIN ST", "JEFFERSON", "LA", "")
    assert service.sent[0]["q"] == "10 MAIN ST, JEFFERSON, LA"
    engines.nominatim("10 MAIN ST", "SAIPAN", "MP", "96950")
    assert service.sent[1]["q"] == "10 MAIN ST, SAIPAN, MP, 96950"
    assert service.sent[1]["countrycodes"] == "us,mp"


def test_the_zip_is_sent_only_when_given(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [])
    engines.nominatim("10 MAIN ST", "POTOMAC", "MD", "20854")
    engines.nominatim("10 MAIN ST", "POTOMAC", "MD", "")
    assert [entry["q"] for entry in service.sent] == [
        "10 MAIN ST, POTOMAC, MD, 20854", "10 MAIN ST, POTOMAC, MD",
    ]


def test_same_name_towns_do_not_supply_address_coordinates(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [POTOMAC_ALLEGANY, POTOMAC_MONTGOMERY])
    assert engines.nominatim("10 MAIN ST", "POTOMAC", "MD", "20854") == (None, None, None)
    assert len(service.sent) == 1


def test_a_translated_town_name_is_not_an_address(monkeypatch, no_sleep):
    kaneohe = _place("Kaneohe", "city", 21.418555, -157.804184, "HI", "boundary", "census",
                     **{"name:en": "Kaneohe", "name:mg": "Honolulu"})
    _serve(monkeypatch, [kaneohe])
    assert engines.nominatim("10 MAIN ST", "HONOLULU", "HI", "96813") == (None, None, None)


def test_civil_town_and_city_centres_are_both_rejected(monkeypatch, no_sleep):
    town = _place("Town of Ithaca", "city", 42.4374175, -76.5483724, "NY")
    city = _place("City of Ithaca", "city", 42.4396039, -76.4968019, "NY")
    _serve(monkeypatch, [town, city])
    assert engines.nominatim("10 MAIN ST", "ITHACA", "NY", "14850") == (None, None, None)


def test_a_township_centre_is_not_kept(monkeypatch, no_sleep):
    _serve(monkeypatch, [CHERRY_HILL])
    assert engines.nominatim("10 MAIN ST", "CHERRY HILL", "NJ", "08003") == (None, None, None)


def test_a_neighbourhood_is_not_an_address(monkeypatch, no_sleep):
    _serve(monkeypatch, [FOREST_HILLS_QUEENS])
    assert engines.nominatim("10 MAIN ST", "FOREST HILLS", "NY", "11375") == (None, None, None)


def test_no_street_sends_no_query(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [LITTLE_ROCK])
    assert engines.nominatim("", "LITTLE ROCK", "AR", "72217") == (None, None, None)
    assert service.sent == []


def test_an_unexpected_answer_is_an_outage_not_a_miss(monkeypatch, no_sleep):
    monkeypatch.setattr(engines.requests, "get", lambda *_args, **_kwargs: _Response({"error": "busy"}))
    with pytest.raises(NominatimUnavailable):
        engines.nominatim("10 MAIN ST", "LITTLE ROCK", "AR", "72201")


@pytest.mark.parametrize("filed, osm", [
    ("SAINT LOUIS", "St. Louis"),
    ("ST LOUIS", "Saint Louis"),
    ("MC LEAN", "McLean"),
    ("WINSTON SALEM", "Winston-Salem"),
    ("COEUR D ALENE", "Coeur d'Alene"),
    ("CANON CITY", "Cañon City"),
    ("BOULDER", "City of Boulder"),
    ("N LITTLE ROCK", "North Little Rock"),
    ("FT LAUDERDALE", "Fort Lauderdale"),
    ("BRONX", "The Bronx"),
])
def test_town_name_forms_are_sent_without_rewriting(monkeypatch, filed, osm):
    monkeypatch.setattr(engines, "_wait_for_nominatim", lambda _url: None)
    service = _serve(monkeypatch, [])
    engines.nominatim("10 MAIN ST", filed, "NY", "")
    engines.nominatim("10 MAIN ST", osm, "NY", "")
    assert [entry["q"] for entry in service.sent] == [f"10 MAIN ST, {filed}, NY", f"10 MAIN ST, {osm}, NY"]


@pytest.mark.parametrize("filed, osm", [
    ("LITTLE ROCK", "North Little Rock"),
    ("ORANGE", "East Orange"),
    ("JAMAICA", "South Jamaica Houses"),
    ("JACKSON", "Jackson County"),
    ("UNION", "Union City"),
])
def test_different_towns_remain_different_queries(monkeypatch, filed, osm):
    monkeypatch.setattr(engines, "_wait_for_nominatim", lambda _url: None)
    service = _serve(monkeypatch, [])
    engines.nominatim("10 MAIN ST", filed, "NY", "")
    engines.nominatim("10 MAIN ST", osm, "NY", "")
    assert service.sent[0]["q"] != service.sent[1]["q"]


def test_an_alternative_town_name_does_not_make_an_address(monkeypatch, no_sleep):
    _serve(monkeypatch, [CHERRY_HILL])
    assert engines.nominatim("10 MAIN ST", "CHERRY HILL", "NJ", "08003") == (None, None, None)


# No city or ZIP fallback

# synthetic reference: Little Rock's delivery ZIPs, North Little Rock, and a far 7xx ZIP
LR_CENTROIDS = {
    "72201": (34.748, -92.276), "72202": (34.742, -92.232), "72204": (34.726, -92.354),
    "72205": (34.750, -92.346), "72207": (34.772, -92.357), "72114": (34.766, -92.264),
    "72701": (36.06, -94.16),
}


@pytest.fixture
def little_rock_reference(monkeypatch, no_sleep):
    patch_geo(monkeypatch, "nominatim", lambda *_args: (None, None, None))
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))


def test_a_zip_without_centroid_does_not_borrow_neighbours(little_rock_reference):
    assert geo._geocode_one("10 MAIN ST", "LITTLE ROCK", "AR", "72217") == (None, None, None, "not_found")


def test_po_box_without_zip_centroid_has_no_town_point(monkeypatch, little_rock_reference):
    assert geo._geocode_one("PO BOX 7839", "LITTLE ROCK", "AR", "72217") == (None, None, None, "not_found")


def test_po_box_whose_town_is_not_found_is_not_searched_abroad(monkeypatch, little_rock_reference):
    assert geo._geocode_one("PO BOX 7839", "LITTLE ROCK", "AR", "72217") == (None, None, None, "not_found")
    assert geo._geocode_one("PO BOX 7839", "KINGSTON", "AR", "") == (None, None, None, "not_found")


def test_zip_with_centroid_does_not_supply_a_po_box_point(monkeypatch, little_rock_reference):
    assert geo._geocode_one("PO BOX 1", "LITTLE ROCK", "AR", "72201") == (None, None, None, "not_found")


# Old cached approximations

SF_KEY = "PO BOX 411291|SAN FRANCISCO|CA|94141"


@pytest.mark.parametrize("key, entry, due", [
    # the audit's wrong pins: a ZIP with no centroid, or no ZIP at all
    (SF_KEY, {"lat": 33.1282409, "lng": -117.3113959, "source": "nominatim_city"}, True),
    ("|NAPA|CA|", {"lat": 38.4898675, "lng": -122.3218414, "source": "nominatim_city"}, True),
    ("PO BOX 7839|LITTLE ROCK|AR|72217",
     {"lat": 34.769536, "lng": -92.2670941, "source": "nominatim_city", "validated": True}, True),
    # A previous check does not turn a city pin into an address.
    (SF_KEY, {"lat": 37.78, "lng": -122.41, "source": "nominatim_city", "town_checked": True}, True),
    # not a town pin
    ("1 MARKET ST|SAN FRANCISCO|CA|94141", {"lat": 37.79, "lng": -122.39, "source": "census"}, False),
    ("PO BOX 1|SAN FRANCISCO|CA|94103", {"lat": 37.77, "lng": -122.41, "source": "zip_centroid"}, True),
    # City pins are rejected even where a ZIP reference exists.
    ("PO BOX 1|SAN FRANCISCO|CA|94103", {"lat": 37.7749, "lng": -122.4194, "source": "nominatim_city"}, True),
])
def test_cached_town_pins_are_withheld_and_rechecked(tmp_path, monkeypatch, key, entry, due):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[key] = entry
    assert geo._needs_lookup(key, cache) is due
    if entry["source"] != "census":
        assert geo.accepted_coordinates(key, entry)[:2] == (None, None)


def _recheck_cache(tmp_path, monkeypatch):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[SF_KEY] = {"lat": 33.1282409, "lng": -117.3113959, "source": "nominatim_city"}
    return cache


def test_recheck_saves_a_valid_street_point(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    key = "1 MAIN ST|SAN FRANCISCO|CA|94103"
    patch_geo(monkeypatch, "_geocode_one", lambda *_args: (37.7792588, -122.4193286, "US", "nominatim"))
    geo._geocode_todo([key], cache, 50)
    entry = cache.get(key)
    assert (entry["lat"], entry["lng"], entry["source"]) == (37.7792588, -122.4193286, "nominatim")
    assert not geo._needs_lookup(key, cache)


def test_recheck_that_finds_nothing_clears_the_old_approximation(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    patch_geo(monkeypatch, "_geocode_one", lambda *_args: (None, None, None, "not_found"))
    geo._geocode_todo([SF_KEY], cache, 50)
    entry = cache.get(SF_KEY)
    assert entry["lat"] == 33.1282409 and entry["source"] == "nominatim_city"
    assert entry["lookup_status"] == "not_found"
    assert geo.accepted_coordinates(SF_KEY, entry)[:2] == (None, None)
    assert not geo._needs_lookup(SF_KEY, cache)


def test_a_us_filing_never_publishes_a_foreign_point(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    cache.put(SF_KEY, 18.1096, -77.2975, "nominatim_intl", "JM")
    assert geo.accepted_coordinates(SF_KEY, cache.get(SF_KEY))[:2] == (None, None)


def test_recheck_during_an_outage_stays_retryable(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    def outage(*_args):
        raise NominatimUnavailable("down")
    patch_geo(monkeypatch, "_geocode_one", outage)
    geo._geocode_todo([SF_KEY], cache, 50)
    assert cache.get(SF_KEY)["lookup_status"] == "transient_fail"
    assert geo._needs_lookup(SF_KEY, cache)
    assert geo.accepted_coordinates(SF_KEY, cache.get(SF_KEY))[:2] == (None, None)


def test_a_new_lookup_that_finds_nothing_is_still_final(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    key = "PO BOX 5|SAN FRANCISCO|CA|94147"
    patch_geo(monkeypatch, "_geocode_one", lambda *_args: (None, None, None, "not_found"))

    geo._geocode_todo([key], cache, 50)

    assert cache.get(key)["source"] == "not_found"


# ---- the audit's wrong pins in the real cache

# key -> (the filed town's settlement point, the wrong cached point the audit found)
AUDIT_WRONG_TOWN_PINS = {
    "PO BOX 411291|SAN FRANCISCO|CA|94141": ((37.7879363, -122.4075201), (33.1282409, -117.3113959)),
    "PO BOX 470068|SAN FRANCISCO|CA|94147": ((37.7879363, -122.4075201), (33.1282409, -117.3113959)),
    "PO BOX 1087|MILL VALLEY|CA|94942": ((37.9060368, -122.5449763), (34.3015401, -116.8866208)),
    "PO BOX 907|NEW YORK|NY|10150": ((40.7127281, -74.0060152), (43.2169801, -76.9051618)),
    "PO BOX 2930|JACKSON|MS|39207": ((32.2998686, -90.1830408), (30.4899024, -88.6486325)),
    "PO BOX 55|CROWN POINT|IN|46308": ((41.4169806, -87.3653136), (39.9642293, -86.2745354)),
    "PO BOX 13026|AUSTIN|TX|78711": ((30.2711286, -97.7436995), (29.7569077, -96.1511306)),
    "PO BOX 163301|AUSTIN|TX|78716": ((30.2711286, -97.7436995), (29.7569077, -96.1511306)),
    "PO BOX 60587|POTOMAC|MD|20859": ((39.017936, -77.2094542), (39.3954942, -76.5772786)),
    "PO BOX 910409|SAN DIEGO|CA|92191": ((32.7157062, -117.1638284), (33.1278222, -117.3114108)),
    "PO BOX 7142|BOULDER|CO|80306": ((40.0149856, -105.270545), (40.1936183, -105.1051184)),
    "|NAPA|CA|": ((38.2971367, -122.2855293), (38.4898675, -122.3218414)),
    "PO BOX 23219|JEFFERSON|LA|70183": ((29.9665206, -90.1529006), (29.8502657, -90.1293894)),
    "PO BOX 7839|LITTLE ROCK|AR|72217": ((34.7465071, -92.2896267), (34.769536, -92.2670941)),
    "PO BOX 7841|LITTLE ROCK|AR|72217": ((34.7465071, -92.2896267), (34.769536, -92.2670941)),
    "PO BOX 47|RICHMOND|VA|23218": ((37.5385087, -77.43428), (37.528657, -77.3605306)),
}


def test_audit_wrong_town_pins_are_never_published():
    if not GEOCODE_CACHE_JSON.exists():
        pytest.skip("geocode_cache.json not present")
    cache = GeoCache(str(GEOCODE_CACHE_JSON))
    for key in AUDIT_WRONG_TOWN_PINS:
        entry = cache.get(key)
        if entry and entry.get("source") in {"nominatim_city", "zip_centroid"}:
            assert geo.accepted_coordinates(key, entry)[:2] == (None, None), key
            assert geo._needs_lookup(key, cache), key


# ---- the whole chain with Nominatim's answers mocked: a wrong place never becomes the pin

PHOENIX = _place("Phoenix", "city", 33.4484367, -112.0741410, "AZ")
PHOENIX_KEY = "PO BOX 7586|PHOENIX|AZ|85011"
PHOENIX_TATUM_PIN = (33.6660986, -111.9738117)   # the cached in-city pin (19801 N Tatum Blvd)


class _Census:
    """Stands in for engines.census: records the streets it is asked for."""

    def __init__(self, answer=(None, None, None)):
        self.answer = answer
        self.streets = []

    def __call__(self, street, city, state, zipcode):
        self.streets.append(street)
        return self.answer


@pytest.mark.parametrize("key, wrong", [
    ("PO BOX 13026|AUSTIN|TX|78711", MURPHY_USA_SEALY),
    ("PO BOX 2930|JACKSON|MS|39207", JACKSON_COUNTY),
    ("PO BOX 55|CROWN POINT|IN|46308", CROWN_POINT_ROAD),
    ("PO BOX 7839|LITTLE ROCK|AR|72217", NORTH_LITTLE_ROCK),
])
def test_a_wrong_place_is_never_published_for_a_po_box(monkeypatch, no_sleep, key, wrong):
    service = _serve(monkeypatch, [wrong])
    assert geo._geocode_one(*key.split("|"))[:2] == (None, None)
    assert service.sent == []


def test_recheck_does_not_move_a_po_box_to_its_town(tmp_path, monkeypatch, no_sleep):
    key = "PO BOX 7839|LITTLE ROCK|AR|72217"
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[key] = {"lat": 34.769536, "lng": -92.2670941, "source": "nominatim_city"}
    service = _serve(monkeypatch, [NORTH_LITTLE_ROCK, LITTLE_ROCK])
    geo._geocode_todo([key], cache, 50)
    assert cache.get(key)["source"] == "nominatim_city"
    assert cache.get(key)["lookup_status"] == "not_found"
    assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)
    assert service.sent == []


def test_recheck_does_not_keep_an_in_city_approximation(tmp_path, monkeypatch, no_sleep):
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[PHOENIX_KEY] = {"lat": PHOENIX_TATUM_PIN[0], "lng": PHOENIX_TATUM_PIN[1], "source": "nominatim_city"}
    assert geo._needs_lookup(PHOENIX_KEY, cache)
    service = _serve(monkeypatch, [MURPHY_USA_SEALY, PHOENIX])
    geo._geocode_todo([PHOENIX_KEY], cache, 50)
    assert geo.accepted_coordinates(PHOENIX_KEY, cache.get(PHOENIX_KEY))[:2] == (None, None)
    assert service.sent == []


def test_recheck_of_an_unknown_address_leaves_no_town_pin(tmp_path, monkeypatch, no_sleep):
    key = "17290 HIGHLAND AVE STE 1|JAMAICA|NY|11432"
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[key] = {"lat": 40.699835, "lng": -73.8077023, "source": "nominatim_city", "country": "US"}
    service = _serve(monkeypatch, [JAMAICA_STATION])
    census = _Census()
    patch_geo(monkeypatch, "census", census)
    assert geo._needs_lookup(key, cache)
    geo._geocode_todo([key], cache, 50)
    assert cache.get(key)["source"] == "nominatim_city"
    assert cache.get(key)["lookup_status"] == "not_found"
    assert geo.accepted_coordinates(key, cache.get(key))[:2] == (None, None)
    assert not geo._needs_lookup(key, cache)
    assert census.streets == ["17290 HIGHLAND AVE STE 1"]
    assert service.sent[0]["q"] == "17290 HIGHLAND AVE STE 1, JAMAICA, NY, 11432"


def test_a_blocked_client_is_an_outage_not_a_miss(tmp_path, monkeypatch, no_sleep):
    class Forbidden(_Response):
        status_code = 403
        ok = False
    monkeypatch.setattr(engines.requests, "get", lambda *_args, **_kwargs: Forbidden([]))
    with pytest.raises(NominatimUnavailable):
        engines.nominatim("10 MAIN ST", "LITTLE ROCK", "AR", "72201")
    cache = _recheck_cache(tmp_path, monkeypatch)
    key = "10 MAIN ST|LITTLE ROCK|AR|72201"
    patch_geo(monkeypatch, "census", lambda *_args: (None, None, None))
    geo._geocode_todo([key], cache, 50)
    assert cache.get(key)["source"] == "transient_fail" and geo._needs_lookup(key, cache)
