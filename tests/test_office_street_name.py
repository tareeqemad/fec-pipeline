"""OFFICE followed by a place word is part of the street's name, not a unit."""
import pandas as pd

from fec.cleaning.addresses import clean_streets


def _clean(street):
    frame = pd.DataFrame({"contributor_street_1": [street], "contributor_street_2": [None],
                          "contributor_first_name": [""], "contributor_last_name": [""]})
    out, _ = clean_streets(frame)
    s2 = out.at[0, "contributor_street_2"]
    return out.at[0, "contributor_street_1"], (None if pd.isna(s2) else s2)


def test_office_park_and_square_stay_in_the_street():
    assert _clean("5 GREENWICH OFFICE PARK") == ("5 GREENWICH OFFICE PARK", None)
    assert _clean("1 POST OFFICE SQ") == ("1 POST OFFICE SQ", None)
    assert _clean("421 OFFICE PARK DRIVE") == ("421 OFFICE PARK DR", None)
    assert _clean("5 GREENWICH OFFICE PARK, SUITE 400") == ("5 GREENWICH OFFICE PARK", "STE 400")


def test_a_real_office_unit_still_moves_to_street_2():
    assert _clean("200 PARK AVE OFFICE 400") == ("200 PARK AVE", "OFF 400")
