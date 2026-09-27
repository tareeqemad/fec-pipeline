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


def test_unit_only_known_from_the_long_form_moves_to_street_2():
    df = _df([
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD", "", "90046"),
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD #374 LOS ANGELES CA", "", "90046"),
    ])
    assert _trim_street_to_donor_short_form(df) == 1
    assert (df.loc[1, "contributor_street_1"], df.loc[1, "contributor_street_2"]) == ("8033 SUNSET BLVD", "# 374")


def test_the_filings_own_unit_is_kept_not_the_donors_usual_one():
    df = _df([
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD", "STE 374", "90046"),
        ("INDIVIDUAL", "A, B", "8033 SUNSET BLVD #374 LOS ANGELES CA", "", "90046"),
        # SQUARER, RON: the private mailbox is not dropped for the usual suite
        ("INDIVIDUAL", "C, D", "3980 BROADWAY ST", "STE 103", "80304"),
        ("INDIVIDUAL", "C, D", "3980 BROADWAY ST STE 103 PMB 132", "", "80304"),
        # SCHWIMMER, DANIEL
        ("INDIVIDUAL", "E, F", "6333 E MOCKINGBIRD LN", "", "75214"),
        ("INDIVIDUAL", "E, F", "6333 E MOCKINGBIRD LN PMB 147-470", "", "75214"),
    ])
    assert _trim_street_to_donor_short_form(df) == 3
    assert df.loc[1, "contributor_street_2"] == "# 374"
    assert df.loc[3, "contributor_street_2"] == "STE 103 PMB 132"
    assert df.loc[5, "contributor_street_2"] == "PMB 147-470"


def test_a_tail_starting_with_a_house_number_is_another_address():
    # ROBBINS, DAVID: 1125 BLACKSTONE BLDG 233 EAST BAY stays for review
    df = _df([
        ("INDIVIDUAL", "A, B", "1125 BLACKSTONE BLDG", "", "32202"),
        ("INDIVIDUAL", "A, B", "1125 BLACKSTONE BLDG 233 E BAY", "", "32202"),
    ])
    assert _trim_street_to_donor_short_form(df) == 0


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


def test_a_replaced_non_street_keeps_its_unit():
    # CHAPMAN, JEROME: RING HOUSE APT # 431, 1801 EAST JE -> 1801 E JEFFERSON ST | APT 431
    from fec.cleaning.addresses.fixes.recovery import _recover_nonstreet_from_donor
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * 2,
        "contributor_name": ["CHAPMAN, JEROME"] * 2,
        "contributor_street_1": ["1801 E JEFFERSON ST", "RING HOUSE APT # 431 1801 E JE"],
        "contributor_street_2": ["", ""],
        "contributor_city": ["ROCKVILLE"] * 2,
        "contributor_state": ["MD"] * 2,
    })
    assert _recover_nonstreet_from_donor(df) == 1
    assert (df.loc[1, "contributor_street_1"], df.loc[1, "contributor_street_2"]) == ("1801 E JEFFERSON ST", "APT 431")


def test_a_filing_that_named_an_office_keeps_its_city():
    # KOTT, DAVID filed C/O MCCARTER & ENGLISH, LLP, 100 M, NEWARK with his home ZIP 07039;
    # the cut-off office street is blanked, and the row is not moved to his LIVINGSTON home
    from fec.cleaning.addresses.fixes.same_street import _recover_address_from_same_street
    rows = [("KOTT, DAVID", "", "NEWARK", "07039")] + [("KOTT, DAVID", "7 TROY DR", "LIVINGSTON", "07039")] * 3
    rows += [("DOE, JANE", "", "NEW", "10022")] + [("DOE, JANE", "1 PARK AVE", "NEW YORK", "10022")] * 3
    rows += [("ROE, RICH", "9 PARK AVE", "NEW YORK", "10022"), ("POE, ANN", "5 TROY DR", "LIVINGSTON", "07039")]
    df = pd.DataFrame(rows, columns=["contributor_name", "contributor_street_1", "contributor_city", "contributor_zip"])
    df["entity_type"] = "INDIVIDUAL"
    df["contributor_state"] = ["NJ"] * 4 + ["NY"] * 5 + ["NJ"]
    df["_care_of_blanked"] = [True] + [False] * 9
    _recover_address_from_same_street(df)
    assert df.loc[0, "contributor_city"] == "NEWARK"
    assert df.loc[4, "contributor_city"] == "NEW YORK"


def test_a_repeated_street_is_still_trimmed_and_a_name_is_not_a_unit():
    df = _df([
        ("INDIVIDUAL", "A, B", "206 CARRIAGE LN", "", "19073"),
        ("INDIVIDUAL", "A, B", "206 CARRIAGE LN 206 CARRIAGE LN", "", "19073"),
    ])
    assert _trim_street_to_donor_short_form(df) == 1
    assert df.loc[1, "contributor_street_1"] == "206 CARRIAGE LN"
    from fec.cleaning.addresses.fixes.recovery import _units_text
    assert _units_text("9401 WILSHIRE BLVD STEPHEN") == ""
    assert _units_text("SUITE 720") == "STE 720"
