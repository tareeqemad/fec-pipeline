"""Tests of the geocoding stage: the cache, the coordinate checks and the two providers."""

import json

import pandas as pd
import pytest
import requests

from fec.geocoding import pipeline as geocoding_pipeline
from fec.geocoding import providers
from fec.geocoding.coordinate_checks import accepted_coordinates

AUSTIN = "10 MAIN ST|AUSTIN|TX|78701"


def point(lat, lng, source, country="US"):
    return {"lat": lat, "lng": lng, "source": source, "country": country}


def use_providers(monkeypatch, nominatim, census):
    """Replace the two providers by functions taking the address fields."""
    monkeypatch.setattr(geocoding_pipeline, "nominatim", nominatim)
    monkeypatch.setattr(geocoding_pipeline, "census", census)


def test_an_accepted_census_point_is_not_looked_up_again():
    entry = {"lat": 30.2672, "lng": -97.7431, "source": "census", "validated": True, "zip_checked": True}
    assert not geocoding_pipeline._needs_lookup(AUSTIN, entry)


def test_only_new_failed_or_rejected_addresses_are_looked_up():
    needs_lookup = geocoding_pipeline._needs_lookup
    assert needs_lookup(AUSTIN, None)
    assert needs_lookup(AUSTIN, {"lat": None, "lng": None, "source": "transient_fail"})
    assert not needs_lookup(AUSTIN, {"lat": None, "lng": None, "source": "not_found"})
    in_chicago = point(41.88, -87.63, "nominatim")
    assert needs_lookup(AUSTIN, in_chicago)  # outside Texas: rejected, so asked again
    assert not needs_lookup(AUSTIN, {**in_chicago, "lookup_status": "not_found"})
    assert needs_lookup(AUSTIN, {**in_chicago, "lookup_status": "transient_fail"})


def test_coordinates_are_accepted_only_for_a_street_address_in_the_right_place():
    austin = point(30.2672, -97.7431, "nominatim")
    assert accepted_coordinates(AUSTIN, austin) == (30.2672, -97.7431)
    assert accepted_coordinates(AUSTIN, {"lat": 30.2672, "lng": -97.7431, "source": "google"}) == (30.2672, -97.7431)
    assert accepted_coordinates(AUSTIN, None) is None
    assert accepted_coordinates(AUSTIN, {"lat": None, "lng": None, "source": "not_found"}) is None
    assert accepted_coordinates(AUSTIN, {**austin, "source": "zip_centroid"}) is None
    assert accepted_coordinates(AUSTIN, {**austin, "lat": 41.88, "lng": -87.63}) is None  # Chicago
    assert accepted_coordinates(AUSTIN, {**austin, "country": "MX"}) is None
    assert accepted_coordinates("PO BOX 12|AUSTIN|TX|78701", austin) is None
    assert accepted_coordinates("#10 MAIN ST|AUSTIN|TX|78701", austin) is None
    assert accepted_coordinates("|AUSTIN|TX|78701", austin) is None
    san_juan = point(18.4655, -66.1057, "nominatim", "PR")
    assert accepted_coordinates("1 CALLE SOL|SAN JUAN|PR|00901", san_juan) == (18.4655, -66.1057)
    london = "1 HIGH ST|LONDON||SW1A 1AA"
    assert accepted_coordinates(london, point(51.5, -0.12, "nominatim", "GB")) == (51.5, -0.12)
    assert accepted_coordinates(london, point(39.88, -83.44, "nominatim", "US")) is None  # London, Ohio
    assert accepted_coordinates(london, {"lat": 51.5, "lng": -0.12, "source": "nominatim"}) is None


def test_openstreetmap_answer_prevents_a_census_request(monkeypatch):
    calls = []
    use_providers(
        monkeypatch,
        nominatim=lambda *_args: calls.append("osm") or point(35.0466, -85.3072, "nominatim"),
        census=lambda *_args: calls.append("census") or point(35.00681, -85.25003, "census"),
    )
    found = geocoding_pipeline._look_up("1 FOUNTAIN SQUARE|CHATTANOOGA|TN|37402")
    assert found == point(35.0466, -85.3072, "nominatim")
    assert calls == ["osm"]


def test_rejected_openstreetmap_answer_tries_census_with_same_address(monkeypatch):
    address = ("10 S LA SALLE ST", "CHICAGO", "IL", "60603")
    calls = []
    use_providers(
        monkeypatch,
        nominatim=lambda *args: calls.append(("osm", args)) or point(30.2672, -97.7431, "nominatim"),
        census=lambda *args: calls.append(("census", args)) or point(41.8814, -87.6324, "census"),
    )
    assert geocoding_pipeline._look_up("|".join(address)) == point(41.8814, -87.6324, "census")
    assert calls == [("osm", address), ("census", address)]


def test_po_box_is_not_sent_to_a_provider(monkeypatch):
    def unexpected(*_args):
        raise AssertionError("no provider should be asked")

    use_providers(monkeypatch, unexpected, unexpected)
    assert geocoding_pipeline._look_up("PO BOX 12|AUSTIN|TX|78701") is None


def _unavailable(*_args):
    raise providers.GeocoderUnavailable("Nominatim HTTP 503")


def test_lookups_are_saved_in_the_cache_file_format(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    found, missed, down = "1 A ST|AUSTIN|TX|78701", "2 B ST|AUSTIN|TX|78701", "3 C ST|AUSTIN|TX|78701"
    moved, kept, abroad = "4 D ST|AUSTIN|TX|78701", "5 E ST|AUSTIN|TX|78701", "1 HIGH ST|LONDON||SW1A 1AA"
    in_chicago = {"lat": 41.88, "lng": -87.63, "source": "google"}
    cache = {moved: dict(in_chicago), kept: {"lat": 30.3, "lng": -97.7, "source": "google"}}
    answers = {found: point(30.2672, -97.7431, "nominatim"), missed: None, moved: None}

    def nominatim(*address):
        key = "|".join(address)
        return _unavailable() if key == down else answers[key]

    use_providers(monkeypatch, nominatim, census=lambda *_address: None)
    monkeypatch.setattr(geocoding_pipeline, "GEOCODE_CACHE_JSON", cache_path)
    monkeypatch.setattr(geocoding_pipeline, "foreign_address_mask", lambda df: df["contributor_city"].eq("LONDON"))
    addresses = [key.split("|") for key in (found, missed, down, moved, kept, found, abroad)] + [[None] * 4]
    columns = ["contributor_street_1", "contributor_city", "contributor_state", "contributor_zip"]
    df = pd.DataFrame(addresses, columns=columns)

    geocoding_pipeline.geocode_addresses(df, cache)

    assert cache_path.read_text(encoding="utf-8") == json.dumps(cache, ensure_ascii=False)
    assert geocoding_pipeline.read_geocode_cache() == cache
    assert cache == {
        moved: {**in_chicago, "lookup_status": "not_found"},
        kept: {"lat": 30.3, "lng": -97.7, "source": "google"},
        found: {"lat": 30.2672, "lng": -97.7431, "source": "nominatim", "country": "US"},
        missed: {"lat": None, "lng": None, "source": "not_found"},
        down: {"lat": None, "lng": None, "source": "transient_fail"},
    }
    assert list(cache[found]) == ["lat", "lng", "source", "country"]

    df = geocoding_pipeline.apply_coordinates(df, cache)
    assert df["latitude"].iloc[0] == 30.2672 and df["longitude"].iloc[0] == -97.7431
    assert df["latitude"].notna().tolist() == [True, False, False, False, True, True, False, False]


def test_nothing_to_look_up_leaves_the_cache_file_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(geocoding_pipeline, "GEOCODE_CACHE_JSON", tmp_path / "geocode_cache.json")
    assert geocoding_pipeline.read_geocode_cache() == {}
    df = pd.DataFrame(
        {
            "sub_id": ["1"],
            "contributor_street_1": ["10 MAIN ST"],
            "contributor_street_2": [""],
            "contributor_city": ["AUSTIN"],
            "contributor_state": ["TX"],
            "contributor_zip": ["78701"],
        }
    )
    geocoding_pipeline.geocode_addresses(df, {AUSTIN: point(30.2672, -97.7431, "census")})
    assert not (tmp_path / "geocode_cache.json").exists()


class Response:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self.data = data

    def json(self):
        return self.data


@pytest.fixture
def web(monkeypatch):
    """Answer provider requests from a prepared list; record requests and sleeps."""
    seen = {"responses": [], "requests": [], "sleeps": []}

    def get(url, params, headers, timeout):
        seen["requests"].append((url, params, headers, timeout))
        response = seen["responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(providers.requests, "get", get)
    monkeypatch.setattr(providers.time, "sleep", seen["sleeps"].append)
    monkeypatch.setattr(providers.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(providers, "_last_nominatim_request", float("-inf"))
    return seen


HOUSE = {
    "lat": "30.2672",
    "lon": "-97.7431",
    "category": "building",
    "type": "house",
    "addresstype": "building",
    "address": {"house_number": "10", "ISO3166-2-lvl4": "US-TX", "postcode": "78701-1234", "country_code": "us"},
}


def test_nominatim_asks_for_one_us_address_and_reads_the_house(web):
    web["responses"] = [Response(200, [HOUSE])]
    assert providers.nominatim("10 MAIN ST", "AUSTIN", "TX", "78701") == point(30.2672, -97.7431, "nominatim")
    params = {
        "q": "10 MAIN ST, AUSTIN, TX, 78701",
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
        "countrycodes": "us",
    }
    assert web["requests"] == [(providers.NOMINATIM_URL, params, {"User-Agent": providers.NOMINATIM_USER_AGENT}, 10)]


def test_nominatim_refuses_an_area_another_house_or_another_state(web):
    street = {**HOUSE, "category": "highway", "type": "residential", "addresstype": "road"}
    other_house = {**HOUSE, "address": {**HOUSE["address"], "house_number": "12"}}
    other_state = {**HOUSE, "address": {**HOUSE["address"], "ISO3166-2-lvl4": "US-OK"}}
    other_zip = {**HOUSE, "address": {**HOUSE["address"], "postcode": "78702"}}
    for results in ([], [street], [other_house], [other_state], [other_zip]):
        web["responses"] = [Response(200, results)]
        assert providers.nominatim("10 MAIN ST", "AUSTIN", "TX", "78701") is None


def test_nominatim_has_no_us_filter_abroad_and_adds_the_territory(web):
    abroad = {**HOUSE, "address": {"house_number": "10", "country_code": "gb"}}
    web["responses"] = [Response(200, [abroad]), Response(200, [])]
    found = providers.nominatim("10 HIGH ST", "LONDON", "", "SW1A 1AA")
    assert found == point(30.2672, -97.7431, "nominatim", "GB")
    providers.nominatim("1 CALLE SOL", "SAN JUAN", "PR", "00901")
    assert "countrycodes" not in web["requests"][0][1]
    assert web["requests"][1][1]["countrycodes"] == "us,pr"


def test_nominatim_requests_are_15_seconds_apart(web, monkeypatch):
    web["responses"] = [Response(200, []), Response(200, [])]
    providers.nominatim("10 MAIN ST", "AUSTIN", "TX", "78701")
    monkeypatch.setattr(providers.time, "monotonic", lambda: 1004.0)
    providers.nominatim("10 MAIN ST", "AUSTIN", "TX", "78701")
    assert web["sleeps"] == [11.0]


def test_a_busy_provider_is_retried_twice_then_reported_unavailable(web):
    web["responses"] = [Response(503), requests.exceptions.Timeout("slow"), Response(429)]
    with pytest.raises(providers.GeocoderUnavailable, match="Census HTTP 429"):
        providers.census("10 MAIN ST", "AUSTIN", "TX", "78701")
    assert web["sleeps"] == [1, 2]

    web["responses"] = [Response(403)]
    with pytest.raises(providers.GeocoderUnavailable, match="Census HTTP 403"):
        providers.census("10 MAIN ST", "AUSTIN", "TX", "78701")
    web["responses"] = [Response(200, {"unexpected": "shape"})]
    with pytest.raises(providers.GeocoderUnavailable, match="Invalid Census response"):
        providers.census("10 MAIN ST", "AUSTIN", "TX", "78701")
    assert web["responses"] == []


def test_census_reads_a_match_of_the_same_house_and_place(web):
    match = {
        "matchedAddress": "10 MAIN ST, AUSTIN, TX, 78701",
        "coordinates": {"x": -97.7431, "y": 30.2672},
        "addressComponents": {"state": "TX", "zip": "78701"},
    }
    other_house = {**match, "matchedAddress": "12 MAIN ST, AUSTIN, TX, 78701"}
    other_zip = {**match, "addressComponents": {"state": "TX", "zip": "78799"}}
    web["responses"] = [
        Response(200, {"result": {"addressMatches": matches}}) for matches in ([match], [], [other_house], [other_zip])
    ]

    assert providers.census("10 MAIN ST", "AUSTIN", "TX", "78701") == point(30.2672, -97.7431, "census")
    assert [providers.census("10 MAIN ST", "AUSTIN", "TX", "78701") for _ in range(3)] == [None, None, None]
    params = {"address": "10 MAIN ST, AUSTIN, TX, 78701", "benchmark": "Public_AR_Current", "format": "json"}
    assert web["requests"][0] == (providers.CENSUS_URL, params, None, 10)
    assert providers.census("10 HIGH ST", "LONDON", "", "SW1A 1AA") is None  # foreign: Census is not asked
    assert len(web["requests"]) == 4


def test_nominatim_settings_come_from_the_environment(monkeypatch):
    from fec.geocoding import providers

    sent = []

    def fake_get_json(provider, url, params, headers):
        sent.append((url, headers))
        return []

    monkeypatch.setattr(providers, "_get_json", fake_get_json)
    providers.nominatim("1 MAIN ST", "AUSTIN", "TX", "78701")
    monkeypatch.setenv("NOMINATIM_URL", "http://localhost:8080/search")
    monkeypatch.setenv("NOMINATIM_USER_AGENT", "my project")
    providers.nominatim("1 MAIN ST", "AUSTIN", "TX", "78701")

    assert sent[0] == (providers.NOMINATIM_URL, {"User-Agent": providers.NOMINATIM_USER_AGENT})
    assert sent[1] == ("http://localhost:8080/search", {"User-Agent": "my project"})
    assert providers._seconds_between_nominatim_requests(providers.NOMINATIM_URL) == 15.0
    assert providers._seconds_between_nominatim_requests("http://localhost:8080/search") == 1.0
    monkeypatch.setenv("NOMINATIM_DELAY_SECONDS", "5")
    assert providers._seconds_between_nominatim_requests(providers.NOMINATIM_URL) == 15.0
    assert providers._seconds_between_nominatim_requests("http://localhost:8080/search") == 5.0
