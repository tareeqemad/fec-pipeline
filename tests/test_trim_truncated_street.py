"""A 34-char FEC street that extends the donor's own shorter street is trimmed back to it."""
import pandas as pd

from fec.cleaning.addresses.fixes.recovery import _trim_street_to_donor_short_form


def _df(rows):
    return pd.DataFrame(rows, columns=["entity_type", "contributor_name", "contributor_street_1",
                                       "contributor_street_2", "contributor_zip"])


def test_trailing_building_fragment_is_dropped_and_unit_restored():
    df = _df([
        ("INDIVIDUAL", "NEUBERGER, YEHUDA", "1777 REISTERSTOWN RD", "STE 290", "21208"),
        ("INDIVIDUAL", "NEUBERGER, YEHUDA", "1777 REISTERSTOWN RD", "STE 290", "21208"),
        ("INDIVIDUAL", "NEUBERGER, YEHUDA", "1777 REISTERSTOWN RD COMMERCE CE", "", "21208"),
    ])
    assert _trim_street_to_donor_short_form(df) == 1
    assert df.loc[2, "contributor_street_1"] == "1777 REISTERSTOWN RD"
    assert df.loc[2, "contributor_street_2"] == "STE 290"


def test_city_fragment_is_dropped():
    df = _df([
        ("INDIVIDUAL", "A, B", "1474 BIENVENEDA AVE", "", "90272"),
        ("INDIVIDUAL", "A, B", "1474 BIENVENEDA AVE PACIFIC PALIS", "", "90272"),
    ])
    assert _trim_street_to_donor_short_form(df) == 1
    assert df.loc[1, "contributor_street_1"] == "1474 BIENVENEDA AVE"


def test_directional_tail_is_part_of_the_street_not_a_fragment():
    df = _df([
        ("INDIVIDUAL", "A, B", "4715 CAMBRIDGE APPROACH CIR", "", "30319"),
        ("INDIVIDUAL", "A, B", "4715 CAMBRIDGE APPROACH CIR NE", "", "30319"),
    ])
    assert _trim_street_to_donor_short_form(df) == 0
    assert df.loc[1, "contributor_street_1"] == "4715 CAMBRIDGE APPROACH CIR NE"


def test_unit_only_known_from_the_long_form_is_kept():
    df = _df([
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD", "", "90046"),
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD #374 LOS ANGELES CA", "", "90046"),
    ])
    assert _trim_street_to_donor_short_form(df) == 0


def test_unit_known_from_the_short_form_makes_the_trim_safe():
    df = _df([
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD", "STE 374", "90046"),
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD #374 LOS ANGELES CA", "", "90046"),
    ])
    assert _trim_street_to_donor_short_form(df) == 1
    assert (df.loc[1, "contributor_street_1"], df.loc[1, "contributor_street_2"]) == ("8033 SUNSET BLVD", "STE 374")


def test_a_stray_or_junk_unit_is_not_restored():
    df = _df([
        ("INDIVIDUAL", "A, B", "3064 DEEP CANYON DR", "", "90210"),
        ("INDIVIDUAL", "A, B", "3064 DEEP CANYON DR", "", "90210"),
        ("INDIVIDUAL", "A, B", "3064 DEEP CANYON DR", "FL 27", "90210"),       # one stray unit out of three
        ("INDIVIDUAL", "A, B", "3064 DEEP CANYON DR MULLHOLLAND", "", "90210"),
        ("INDIVIDUAL", "C, D", "16 THE DRAWBRIDGE", "# NY1179", "11797"),      # state/ZIP fragment, not a unit
        ("INDIVIDUAL", "C, D", "16 THE DRAWBRIDGE WOODBURY NY 11", "", "11797"),
    ])
    assert _trim_street_to_donor_short_form(df) == 2
    assert (df.loc[3, "contributor_street_1"], df.loc[3, "contributor_street_2"]) == ("3064 DEEP CANYON DR", "")
    assert (df.loc[5, "contributor_street_1"], df.loc[5, "contributor_street_2"]) == ("16 THE DRAWBRIDGE", "")


def test_other_people_and_other_zips_are_no_evidence():
    df = _df([
        ("INDIVIDUAL", "A, B", "1474 BIENVENEDA AVE", "", "90272"),
        ("INDIVIDUAL", "C, D", "1474 BIENVENEDA AVE PACIFIC PALIS", "", "90272"),
        ("INDIVIDUAL", "A, B", "1474 BIENVENEDA AVE PACIFIC PALIS", "", "90210"),
        ("COMMITTEE", "A, B", "1474 BIENVENEDA AVE PACIFIC PALIS", "", "90272"),
    ])
    assert _trim_street_to_donor_short_form(df) == 0
