"""Unit designators take the USPS form in donor and employer text alike (audit F5).

314 published employer addresses kept '10TH FLOOR' / 'SEVENTH FLOOR' / 'SUITE #211'
while donor rows wrote 'FL 10' / 'STE 211', and 19 donor street_2 values kept
'5TH FLOOR' / '2ND FL'. One shared rule set in fec/config/streets.py now writes
both: the designator abbreviated and first, the identifier as written.
"""
import pytest

from fec.cleaning.street_text import _normalize_unit
from fec.config.streets import usps_unit_designators


@pytest.mark.parametrize("value, expected", [
    ("5TH FLOOR", "FL 5"),
    ("2ND FL", "FL 2"),
    ("41ST FLOOR", "FL 41"),
    ("2 FLOOR", "FL 2"),
    ("SECOND FLOOR", "FL 2"),
    ("FLOOR 3", "FL 3"),
    ("SUITE 200", "STE 200"),
    ("SUITE #211", "STE 211"),
    ("STE #211", "STE 211"),
    # a spelled identifier after SUITE is kept: only the designator changes
    ("SUITE ONE", "STE ONE"),
    # a floor followed by more: the floor still leads, the rest is kept
    ("3RD FLOOR REAR", "FL 3 REAR"),
    ("10TH FLOOR STE 200", "FL 10 STE 200"),
    # a floor after another unit, and a street line a donor wrote into street_2
    ("STE 200 10TH FLOOR", "STE 200 FL 10"),
    ("501 N. ORLANDO AVENUE SUITE 313", "501 N. ORLANDO AVENUE STE 313"),
    # other units are unchanged
    ("STE 166 #399", "STE 166 #399"),
    ("PMB #333", "PMB #333"),
    ("# 539", "# 539"),
    ("APT 5", "APT 5"),
])
def test_donor_units_take_the_usps_form(value, expected):
    assert _normalize_unit(value) == expected


@pytest.mark.parametrize("street, expected", [
    ("450 7TH AVE 10TH FLOOR", "450 7TH AVE FL 10"),
    ("399 PARK AVE 25TH FLOOR STE 2502", "399 PARK AVE FL 25 STE 2502"),
    ("666 THIRD AVE FLOOR 24 STE 2402", "666 THIRD AVE FL 24 STE 2402"),
    ("9460 WILSHIRE BLVD SEVENTH FLOOR", "9460 WILSHIRE BLVD FL 7"),
    ("1 MAIN ST TWENTY-FIRST FLOOR", "1 MAIN ST FL 21"),
    # FLOOR spelled out is a floor even after a direction
    ("2600 VIRGINIA AVE NW THIRD FLOOR", "2600 VIRGINIA AVE NW FL 3"),
    ("2747 CONEY ISLAND AVE 2 FLOOR", "2747 CONEY ISLAND AVE FL 2"),
    ("11160 WARNER AVE SUITE #211", "11160 WARNER AVE STE 211"),
    ("3350 SW 148TH AVE SUITE 110 #353", "3350 SW 148TH AVE STE 110 #353"),
    ("2000 AVE OF THE STARS SUITE 400 N TOWER", "2000 AVE OF THE STARS STE 400 N TOWER"),
    ("175 STRAFFORD AVE SUITE ONE", "175 STRAFFORD AVE STE ONE"),
])
def test_unit_designators_on_a_street_line(street, expected):
    assert usps_unit_designators(street) == expected
    assert usps_unit_designators(expected) == expected  # the USPS form is stable


@pytest.mark.parametrize("street", [
    "100 FIRST FLOOR",        # a spelled ordinal right after the house number may be the street
    "100 SECOND FLOOR RD",    # a street named after a floor word
    "1 SUITE ST",             # SUITE followed by a street type is a street's name
    "1 FIFTH AVE",
    "120 FIFTH AVE PL",
    "EXECUTIVE SUITES 100",
    "3RD FLOOR",              # a line that is only a floor: the house-number position is never read
    "3 W 3RD FL",             # a bare FL after a direction and an ordinal: a street without its type
    "200 NW THIRD FL",
])
def test_street_names_are_not_units(street):
    assert usps_unit_designators(street) == street
