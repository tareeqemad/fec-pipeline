"""Town-level pins must be the filed town itself (2026-09-24 audit, area pobox-towns).

The old city_level sent free text 'CITY, ST ZIP, USA' with limit=1 and took the first
hit. OSM does not know PO-box-only or unique ZIPs, so the word 'USA' matched POIs
(Lego Miniland USA in Carlsbad for SAN FRANCISCO 94141, Murphy USA in Sealy for
AUSTIN 78711), or the hit was a county (Jackson County MS for JACKSON 39207), a road
named like the town (Crown Point in Zionsville), a building ('Potomac' in Towson) or
the neighbouring city (North Little Rock for LITTLE ROCK 72217). With no ZIP centroid
nothing checked them. Now only a settlement named like the filed city, inside the
filed state, is a town, and the cached pins in ZIPs without a centroid are
looked up once more.
"""
import pytest

from fec.geocoding import GeoCache
from fec.geocoding import engines, places
from fec.geocoding import pipeline as geo
from fec.geocoding.engines import NominatimUnavailable


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
    monkeypatch.setattr(geo.time, "sleep", lambda _delay: None)


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
def test_a_place_that_is_not_the_town_is_rejected(monkeypatch, no_sleep, city, state, wrong):
    service = _serve(monkeypatch, [wrong])

    assert engines.city_level(city, state, "") == (None, None, None)
    [structured] = service.sent
    assert structured["city"] == city and structured["state"] == places.STATE_NAMES[state]
    assert structured["countrycodes"] == "us" and structured["limit"] > 1
    assert "postalcode" not in structured and "q" not in structured


def test_the_town_is_taken_after_the_neighbouring_city(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [NORTH_LITTLE_ROCK, LITTLE_ROCK])

    assert engines.city_level("LITTLE ROCK", "AR", "") == (34.7465071, -92.2896267, "US")
    assert len(service.sent) == 1


def test_a_same_name_town_in_another_state_is_skipped(monkeypatch, no_sleep):
    # the structured 'state' is not strict: Houston, Missouri came back for Texas
    _serve(monkeypatch, [HOUSTON_MO, HOUSTON_TX])

    assert engines.city_level("HOUSTON", "TX", "")[:2] == (29.7589382, -95.3676974)


def test_state_is_sent_by_its_full_name(monkeypatch, no_sleep):
    # state='LA' was read as Los Angeles
    service = _serve(monkeypatch, [])

    engines.city_level("JEFFERSON", "LA", "")

    assert service.sent[0]["state"] == "Louisiana"


def test_the_zip_is_sent_only_when_given(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [POTOMAC_MONTGOMERY])

    engines.city_level("POTOMAC", "MD", "20854")

    assert service.sent[0]["postalcode"] == "20854"
    assert len(service.sent) == 1


def test_same_name_towns_in_one_state_follow_the_filed_zip(monkeypatch, no_sleep):
    # POTOMAC MD: Montgomery County (20854) and a hamlet in Allegany County, 153 km apart
    _serve(monkeypatch, [POTOMAC_ALLEGANY, POTOMAC_MONTGOMERY])
    rockville_area = (39.07, -77.18)

    assert engines.city_level("POTOMAC", "MD", "", rockville_area)[:2] == (39.017936, -77.2094542)
    # far from both: the closer one; no ZIP at all: Nominatim's first
    assert engines.city_level("POTOMAC", "MD", "", (39.6, -78.9))[:2] == (39.5675895, -78.83891)
    assert engines.city_level("POTOMAC", "MD", "")[:2] == (39.5675895, -78.83891)


def test_a_translation_is_not_a_name(monkeypatch, no_sleep):
    # live answer for HONOLULU HI: Kaneohe's Malagasy name is 'Honolulu'
    kaneohe = _place("Kaneohe", "city", 21.418555, -157.804184, "HI", "boundary", "census",
                     **{"name:en": "Kaneohe", "name:mg": "Honolulu"})
    _serve(monkeypatch, [kaneohe])

    assert engines.city_level("HONOLULU", "HI", "") == (None, None, None)


def test_the_city_wins_over_the_civil_town_around_it(monkeypatch, no_sleep):
    # live answer for ITHACA NY: the Town of Ithaca first, the City (post office) 4 km east
    town = _place("Town of Ithaca", "city", 42.4374175, -76.5483724, "NY",
                  official_name="Town of Ithaca", _place_name="Ithaca")
    city = _place("City of Ithaca", "city", 42.4396039, -76.4968019, "NY",
                  official_name="City of Ithaca", _place_name="Ithaca")
    _serve(monkeypatch, [town, city])

    assert engines.city_level("ITHACA", "NY", "", (42.439, -76.554))[:2] == (42.4396039, -76.4968019)


def test_a_township_that_is_the_town_is_kept(monkeypatch, no_sleep):
    # Cherry Hill NJ is a township; the hamlet of that name is in Bergen County, 100 km off
    hamlet = _place("Cherry Hill", "hamlet", 40.92044, -74.03909, "NJ", "place", "hamlet")
    _serve(monkeypatch, [CHERRY_HILL, hamlet])

    assert engines.city_level("CHERRY HILL", "NJ", "")[:2] == (39.93483, -75.03072)


def test_a_neighbourhood_named_like_the_city_is_a_town(monkeypatch, no_sleep):
    _serve(monkeypatch, [FOREST_HILLS_QUEENS])

    assert engines.city_level("FOREST HILLS", "NY", "")[:2] == (40.72141, -73.84382)


def test_no_city_sends_no_query(monkeypatch, no_sleep):
    service = _serve(monkeypatch, [LITTLE_ROCK])

    assert engines.city_level("", "AR", "72217") == (None, None, None)
    assert service.sent == []


def test_an_unexpected_answer_is_an_outage_not_a_miss(monkeypatch, no_sleep):
    monkeypatch.setattr(engines.requests, "get", lambda *_args, **_kwargs: _Response({"error": "busy"}))

    with pytest.raises(NominatimUnavailable):
        engines.city_level("LITTLE ROCK", "AR", "")


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
def test_town_name_forms_agree(filed, osm):
    assert places.town_name_key(filed) == places.town_name_key(osm)


@pytest.mark.parametrize("filed, osm", [
    ("LITTLE ROCK", "North Little Rock"),
    ("ORANGE", "East Orange"),
    ("JAMAICA", "South Jamaica Houses"),
    ("JACKSON", "Jackson County"),
    ("UNION", "Union City"),
])
def test_different_towns_do_not_agree(filed, osm):
    assert places.town_name_key(filed) != places.town_name_key(osm)


def test_an_alternative_name_counts():
    assert places.same_town_name(CHERRY_HILL, "CHERRY HILL")


# ---- the pipeline: the ZIP, the ZIP's area and the fallback

# synthetic reference: Little Rock's delivery ZIPs, North Little Rock, and a far 7xx ZIP
LR_CENTROIDS = {
    "72201": (34.748, -92.276), "72202": (34.742, -92.232), "72204": (34.726, -92.354),
    "72205": (34.750, -92.346), "72207": (34.772, -92.357), "72114": (34.766, -92.264),
    "72701": (36.06, -94.16),
}


@pytest.fixture
def little_rock_reference(monkeypatch, no_sleep):
    monkeypatch.setattr(geo, "_zip_centroids", lambda: LR_CENTROIDS)
    monkeypatch.setattr(geo, "_zip_neighbour_km", lambda _zip: 3.0)


def test_a_zip_without_centroid_is_placed_by_its_nearest_numbered_neighbours(little_rock_reference):
    lat, lng = geo._zip_area_point("72217", "AR")
    # the median of 72207, 72205, 72204, 72202, the nearest numbers in 722 (72114 and 72701 are other areas)
    assert lat == pytest.approx(34.746) and lng == pytest.approx(-92.350)
    assert geo._zip_area_point("72201", "AR") == LR_CENTROIDS["72201"]
    assert geo._zip_area_point("72217", "TX") is None    # no neighbour inside the filed state
    assert geo._zip_area_point("", "AR") is None


def test_po_box_without_zip_centroid_gets_the_town_point(monkeypatch, little_rock_reference):
    calls = []

    def city_level(city, state, zipcode, near):
        calls.append((city, state, zipcode, near))
        return 34.7465071, -92.2896267, "US"

    monkeypatch.setattr(geo, "city_level", city_level)

    result = geo._geocode_one("PO BOX 7839", "LITTLE ROCK", "AR", "72217")

    assert result == (34.7465071, -92.2896267, "US", "nominatim_city")
    # the PO-box ZIP OSM does not know is not sent; its area picks among same-name towns
    assert [call[:3] for call in calls] == [("LITTLE ROCK", "AR", "")]
    assert calls[0][3] == pytest.approx((34.746, -92.350))


def test_po_box_whose_town_is_not_found_is_not_searched_abroad(monkeypatch, little_rock_reference):
    # a ZIP of the filed state makes it a US filing: 'PO BOX 5, JAMAICA' abroad is the island
    monkeypatch.setattr(geo, "city_level", lambda *_args: (None, None, None))
    monkeypatch.setattr(geo, "nominatim_international", lambda *_args: (18.1096, -77.2975, "JM"))

    assert geo._geocode_one("PO BOX 7839", "LITTLE ROCK", "AR", "72217") == (None, None, None, "not_found")
    # no ZIP of the filed state: the international last resort still runs
    assert geo._geocode_one("PO BOX 7839", "KINGSTON", "AR", "")[3] == "nominatim_intl"


def test_zip_with_centroid_is_still_searched_first(monkeypatch, little_rock_reference):
    calls = []

    def city_level(city, state, zipcode, near):
        calls.append((zipcode, near))
        return 34.7465071, -92.2896267, "US"

    monkeypatch.setattr(geo, "city_level", city_level)

    geo._geocode_one("PO BOX 1", "LITTLE ROCK", "AR", "72201")

    assert calls == [("72201", LR_CENTROIDS["72201"])]


# ---- the one-time re-check of cached town pins

SF_KEY = "PO BOX 411291|SAN FRANCISCO|CA|94141"


@pytest.mark.parametrize("key, entry, due", [
    # the audit's wrong pins: a ZIP with no centroid, or no ZIP at all
    (SF_KEY, {"lat": 33.1282409, "lng": -117.3113959, "source": "nominatim_city"}, True),
    ("|NAPA|CA|", {"lat": 38.4898675, "lng": -122.3218414, "source": "nominatim_city"}, True),
    ("PO BOX 7839|LITTLE ROCK|AR|72217",
     {"lat": 34.769536, "lng": -92.2670941, "source": "nominatim_city", "validated": True}, True),
    # already re-checked
    (SF_KEY, {"lat": 37.78, "lng": -122.41, "source": "nominatim_city", "town_checked": True}, False),
    # not a town pin
    ("1 MARKET ST|SAN FRANCISCO|CA|94141", {"lat": 37.79, "lng": -122.39, "source": "census"}, False),
    ("PO BOX 1|SAN FRANCISCO|CA|94103", {"lat": 37.77, "lng": -122.41, "source": "zip_centroid"}, False),
    # a ZIP with a centroid holds its town pin already
    ("PO BOX 1|SAN FRANCISCO|CA|94103", {"lat": 37.7749, "lng": -122.4194, "source": "nominatim_city"}, False),
])
def test_cached_town_pins_without_zip_centroid_are_rechecked_once(tmp_path, monkeypatch, key, entry, due):
    monkeypatch.setattr(geo, "_zip_centroids", lambda: {"94103": (37.7726, -122.4099)})
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[key] = entry

    assert geo._needs_lookup(key, cache) is due


def _recheck_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(geo, "_zip_centroids", lambda: {})
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    cache.data[SF_KEY] = {"lat": 33.1282409, "lng": -117.3113959, "source": "nominatim_city"}
    return cache


def test_recheck_replaces_the_wrong_pin_and_marks_it(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(geo, "_geocode_one",
                        lambda *_args: (37.7792588, -122.4193286, "US", "nominatim_city"))

    geo._geocode_todo([SF_KEY], cache, 50)

    entry = cache.get(SF_KEY)
    assert (entry["lat"], entry["lng"], entry["town_checked"]) == (37.7792588, -122.4193286, True)
    assert not geo._needs_lookup(SF_KEY, cache)


def test_recheck_that_finds_nothing_keeps_the_pin(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(geo, "_geocode_one", lambda *_args: (None, None, None, "not_found"))

    geo._geocode_todo([SF_KEY], cache, 50)

    entry = cache.get(SF_KEY)
    assert (entry["lat"], entry["source"], entry["town_checked"]) == (33.1282409, "nominatim_city", True)
    assert not geo._needs_lookup(SF_KEY, cache)


def test_recheck_never_moves_a_us_filing_abroad(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(geo, "_geocode_one", lambda *_args: (18.1096, -77.2975, "JM", "nominatim_intl"))

    geo._geocode_todo([SF_KEY], cache, 50)

    assert (cache.get(SF_KEY)["lat"], cache.get(SF_KEY)["source"]) == (33.1282409, "nominatim_city")


def test_recheck_during_an_outage_leaves_the_pin_for_next_run(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)

    def outage(*_args):
        raise NominatimUnavailable("down")

    monkeypatch.setattr(geo, "_geocode_one", outage)

    geo._geocode_todo([SF_KEY], cache, 50)

    assert cache.get(SF_KEY) == {"lat": 33.1282409, "lng": -117.3113959, "source": "nominatim_city"}
    assert geo._needs_lookup(SF_KEY, cache)


def test_a_new_lookup_that_finds_nothing_is_still_final(tmp_path, monkeypatch):
    cache = _recheck_cache(tmp_path, monkeypatch)
    key = "PO BOX 5|SAN FRANCISCO|CA|94147"
    monkeypatch.setattr(geo, "_geocode_one", lambda *_args: (None, None, None, "not_found"))

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


def test_audit_wrong_town_pins_are_rechecked_or_fixed():
    """Each wrong pin is either due for the re-check or, once re-checked, at its town and off the wrong spot."""
    path = geo._ZIP_CENTROIDS.parents[1] / "geocode_cache.json"
    if not path.exists() or not geo._ZIP_CENTROIDS.exists():
        pytest.skip("geocode_cache.json or zip_centroids.csv not present")
    cache = GeoCache(str(path))
    present = [key for key in AUDIT_WRONG_TOWN_PINS if (cache.get(key) or {}).get("lat") is not None]
    if not present:
        pytest.skip("none of the audit's keys is in this cache")
    for key in present:
        town, wrong = AUDIT_WRONG_TOWN_PINS[key]
        entry = cache.get(key)
        point = (float(entry["lat"]), float(entry["lng"]))
        if not entry.get("town_checked"):
            assert geo._needs_lookup(key, cache), f"{key}: wrong town pin {point} is never looked up again"
            continue
        assert places.distance_km(point, town) <= 8, f"{key}: {point} is not at its town {town}"
        assert places.distance_km(point, wrong) >= 1, f"{key}: still on the audit's wrong point {wrong}"
