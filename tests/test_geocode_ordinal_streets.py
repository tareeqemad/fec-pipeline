"""Street formatting belongs to cleaning; geocoding keeps the cleaned text."""

import pandas as pd
import pytest

from fec.resolve import addresses, employer_locations
from fec.geocoding.pipeline import _contributor_keys
from fec.geocoding.pipeline import _employer_keys
from fec.cleaning.addresses.street_text import numbered_street


@pytest.mark.parametrize("street, expected", [
    ("777 THIRD AVE", "777 3RD AVE"),
    ("810 SEVENTH AVE", "810 7TH AVE"),
    ("320 FIRST ST SE", "320 1ST ST SE"),
    ("4300 E FIFTH AVE", "4300 E 5TH AVE"),
    ("10 W FIFTY SEVENTH ST", "10 W 57TH ST"),
    ("200 TWENTY-SECOND ST", "200 22ND ST"),
    ("1 ELEVENTH AVE", "1 11TH AVE"),
    ("5 TWELFTH ST", "5 12TH ST"),
    ("12 THIRTIETH ST", "12 30TH ST"),
    ("203 N. FIFTH ST. SUITE 100", "203 N. 5TH ST. SUITE 100"),
    ("750 Third Avenue, 29th Floor", "750 3RD Avenue, 29th Floor"),
])
def test_ordinal_words_before_a_street_type_become_numbers(street, expected):
    assert numbered_street(street) == expected


@pytest.mark.parametrize("street", [
    "12 SECOND LAKE RD",
    "777 3RD AVE",
    "1 FIRSTENBERG ST",
    "THE THIRD FLOOR",
    "120 FIFTH AVENUE PLACE, 120 FIFTH AVE",  # building named after the street
    "",
])
def test_other_streets_are_unchanged(street):
    assert numbered_street(street) == street


def test_geocode_keys_preserve_text_and_old_numbered_cache_is_reused(tmp_path):
    expected = "777 THIRD AVE|NEW YORK|NY|10017"
    donors = pd.DataFrame([{
        "contributor_street_1": "777 THIRD AVE", "contributor_city": "NEW YORK",
        "contributor_state": "NY", "contributor_zip": "10017",
    }])
    employers = pd.DataFrame([{
        "employer_address": " 777 third ave ", "employer_city": "New York",
        "employer_state": "ny", "employer_zip": "10017",
    }])
    assert _contributor_keys(donors).tolist() == [expected]
    assert _employer_keys(employers).tolist() == [" 777 third ave |New York|ny|10017"]
    assert addresses._address_key({
        "employer_address": "777 Third Ave", "employer_city": "New York",
        "employer_state": "NY", "employer_zip": "10017",
    }) == ("777 3RD AVE", "NEW YORK", "NY", "10017")

    from fec.geocoding.cache import GeoCache
    cache = GeoCache(str(tmp_path / "geocode_cache.json"))
    legacy = "777 3RD AVE|NEW YORK|NY|10017"
    cache.put(legacy, 40.7528, -73.9718, "nominatim")
    assert cache.get(expected) == cache.get(legacy)
    assert set(cache.data) == {legacy}

    from fec.geocoding.validation import accepted_coordinates
    old_wrong_key = "4440 W 25TH ST|MINNEAPOLIS|MN|55416"
    cleaned_key = "4440 W TWENTY FIFTH ST|MINNEAPOLIS|MN|55416"
    cache.put(old_wrong_key, 44.957979, -93.3012753, "nominatim")
    assert cache.get(cleaned_key) == cache.get(old_wrong_key)
    assert accepted_coordinates(cleaned_key, cache.get(cleaned_key)) == (
        None, None, "rejected_reviewed_wrong",
    )
