"""A direction word that is the street's name stays spelled out, and a route number stays in street_1.

Rows these cases come from (raw street_1 -> old output):
  "5555 SOUTH ST, STE. 200"  LINCOLN NE (471 committee rows) -> "5555 S ST"; Lincoln also has a lettered S St
  "750 SOUTH ST" NEEDHAM MA, "315 NORTH AVE" WESTPORT CT, "200 WEST ST" NEW YORK -> "S ST", "N AVE", "W ST"
  "3831 COUNTY ROAD 102" WALNUT MS -> street_1 "3831 COUNTY RD" + street_2 "# 102"
"""
import numpy as np
import pandas as pd
import pytest

from fec.cleaning.addresses import clean_streets
from fec.cleaning.street_text import _normalize_street
from fec.cleaning.pipeline.address_fixes.safe_text import apply_safe_fixes, is_state_zip_fragment


@pytest.mark.parametrize("raw, expected", [
    ("650 WEST AVE", "650 WEST AVE"),
    ("5555 SOUTH ST", "5555 SOUTH ST"),
    ("83 WEST LN", "83 WEST LN"),
    ("34 NORTH DR", "34 NORTH DR"),
    ("315 NORTH AVENUE", "315 NORTH AVE"),        # the suffix is still abbreviated
    ("200 WEST STREET", "200 WEST ST"),
    ("478 SOUTH PARKWAY", "478 SOUTH PKWY"),
    ("3500 SOUTHWEST BLVD", "3500 SOUTHWEST BLVD"),
    ("325 NORTHWEST DR", "325 NORTHWEST DR"),
    ("520 WEST AVE APT 1602", "520 WEST AVE APT 1602"),
    ("750 SOUTH ST.", "750 SOUTH ST"),
    ("SOUTH ST", "SOUTH ST"),                      # no house number: DIR_PREFIX obeys the same rule
])
def test_direction_word_that_is_the_street_name_is_kept(raw, expected):
    assert _normalize_street(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("123 NORTH MAIN ST", "123 N MAIN ST"),
    ("500 NORTH MAIN AVENUE", "500 N MAIN AVE"),
    ("4550 NORTH PARK AVENUE #210", "4550 N PARK AVE #210"),  # PARK is the name
    ("123 NORTH ST. JOHNS AVE", "123 N ST JOHNS AVE"),         # ST is SAINT, not the suffix
    ("2100 WEST LOOP S", "2100 W LOOP S"),                     # post-directional: Houston's W LOOP S
    ("1704 NORTH AVENUE 54", "1704 N AVE 54"),                 # LA's numbered Avenue 54
    ("185 WEST END AVENUE", "185 W END AVE"),                  # the filers' own majority form
    ("445 EAST SHORE RD", "445 E SHORE RD"),
    ("420 EAST SOUTH TEMPLE", "420 E SOUTH TEMPLE"),
    ("4350 EAST WEST HIGHWAY", "4350 E WEST HWY"),             # Pub 28: 2nd of E-W pair is the name
    ("100 WEST CENTER", "100 W CENTER"),                       # CENTER is usually Center St, type dropped
    ("EAST 63RD STREET", "E 63RD ST"),
    ("101 WESTON LN S", "101 WESTON LN S"),
])
def test_ordinary_directions_are_still_abbreviated(raw, expected):
    assert _normalize_street(raw) == expected


def test_normalizing_twice_changes_nothing():
    for raw in ("650 WEST AVE", "5555 SOUTH ST, STE. 200", "123 NORTH MAIN ST", "2100 WEST LOOP S"):
        once = _normalize_street(raw)
        assert _normalize_street(once) == once


def test_sreet_misspelling_keeps_the_direction_name():
    # AGUS, RAANAN files "200 WEST ST" 18 times and "200 WEST SREET" once
    assert _normalize_street("200 WEST SREET") == "200 WEST ST"
    assert _normalize_street("19 THATCHER SREET APARTMENT 4") == "19 THATCHER ST APARTMENT 4"


def _frame(streets):
    n = len(streets)
    return pd.DataFrame({
        "contributor_street_1": streets,
        "contributor_street_2": [np.nan] * n,
        "contributor_first_name": ["JANE"] * n,
        "contributor_last_name": ["DOE"] * n,
        "contributor_city": ["TOWN"] * n,
        "contributor_state": ["NY"] * n,
        "contributor_zip": ["10001"] * n,
    })


def _clean(streets):
    df, _ = clean_streets(_frame(streets))
    df, _ = apply_safe_fixes(df)
    return list(zip(df["contributor_street_1"], df["contributor_street_2"].fillna("")))


def test_unit_after_direction_name_is_split_and_name_kept():
    assert _clean(["5555 SOUTH ST, STE. 200", "520 WEST AVE APT 1602", "610 WEST END AVENUE, APT. 6A"]) == [
        ("5555 SOUTH ST", "STE 200"),
        ("520 WEST AVE", "APT 1602"),
        ("610 W END AVE", "APT 6A"),
    ]


@pytest.mark.parametrize("raw, expected", [
    ("3831 COUNTY ROAD 102", ("3831 COUNTY RD 102", "")),
    ("289 W STATE ROAD 130", ("289 W STATE RD 130", "")),
    ("191 COUNTY ROAD 516", ("191 COUNTY RD 516", "")),
    ("18 PRIVATE ROAD 20", ("18 PRIVATE RD 20", "")),
    ("5361 COUNTY ROAD 20", ("5361 COUNTY RD 20", "")),
    ("1704 N AVENUE 54", ("1704 N AVE 54", "")),
    ("1100 NW LOOP 410", ("1100 NW LOOP 410", "")),
    ("3831 COUNTY RD 102 N", ("3831 COUNTY RD 102 N", "")),
])
def test_route_number_stays_in_street_1(raw, expected):
    assert _clean([raw]) == [expected]


@pytest.mark.parametrize("raw, expected", [
    ("49 E. 92ND ST, 1", ("49 E 92ND ST", "# 1")),
    ("3219 E CAMELBACK RD 801", ("3219 E CAMELBACK RD", "# 801")),
    ("123 COUNTY LINE RD 5", ("123 COUNTY LINE RD", "# 5")),   # County Line Rd is a name
    ("123 FOREST RD 5", ("123 FOREST RD", "# 5")),
    ("5500 ISLAND ESTATES DRIVE, 705 N", ("5500 ISLAND ESTATES DR", "# 705 N")),
])
def test_trailing_unit_number_is_still_split(raw, expected):
    assert _clean([raw]) == [expected]


@pytest.mark.parametrize("value, expected", [
    ("ANTA GA3034", True),     # OVES, LYNN x4 at 55 S BATTERY PL, ATLANTA GA: "...ATLANTA GA 3034.." cut off
    ("# NY1179", True),        # the existing leading state + ZIP case
    ("STE NE200", False),      # a unit word first: a unit id, not a spill
    ("APT GA3034", False),
    ("ANTA XX3034", False),    # XX is not a state
    ("OAKH NJ", False),        # no ZIP digits: left to the city-prefix / state-abbreviation checks
    ("STE 200", False),
])
def test_city_tail_with_state_and_zip_is_a_fragment(value, expected):
    assert is_state_zip_fragment(value) is expected
