"""Tests of address cleaning: the USPS form and the reviewed address rules."""

import pandas as pd


def test_address_rule_can_clear_the_street(monkeypatch, tmp_path):
    from fec.cleaning.addresses import address_rules

    rules_csv = tmp_path / "address_rules.csv"
    rules_csv.write_text(
        "match_street,match_state,match_zip,street,unit,city,state,zip,source,match_unit,match_sub_id\n"
        "CALIFORNIA SUPPLY,CA,90247,[CLEAR],,,,,the street field held a company name,,\n"
        "1150 KANE CONCOURSE,FL,33154,1150 KANE CONCOURSE,,BAY HARBOR ISLANDS,,,county records,,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(address_rules, "ADDRESS_RULES_CSV", rules_csv)
    df = pd.DataFrame(
        {
            "sub_id": ["1", "2"],
            "contributor_street_1": ["CALIFORNIA SUPPLY", "1150 KANE CONCOURSE"],
            "contributor_street_2": [None, "FL 2"],
            "contributor_city": ["GARDENA", "SURFSIDE"],
            "contributor_state": ["CA", "FL"],
            "contributor_zip": ["90247", "33154"],
        }
    )

    assert address_rules.apply_address_rules(df, city_only=False) == 2
    assert df["contributor_street_1"].isna().tolist() == [True, False]
    assert df["contributor_city"].tolist() == ["GARDENA", "BAY HARBOR ISLANDS"]
    # The city-only rule also runs in the second pass; the clearing rule does not.
    df.loc[1, "contributor_city"] = "SURFSIDE"
    assert address_rules.apply_address_rules(df, city_only=True) == 1
    assert df["contributor_city"].tolist() == ["GARDENA", "BAY HARBOR ISLANDS"]


def test_standard_address_follows_usps_with_no_table_of_ours():
    from fec.cleaning.addresses.standard_form import standard_address, standardize_addresses

    def std(street_1, street_2="", city="SAINT LOUIS", zipcode="63141"):
        return standard_address(street_1, street_2, city, "MO", zipcode)[:3]

    assert std("50 EAST 79TH STREET, 16D") == ("50 E 79TH ST", "# 16D", "SAINT LOUIS")
    assert std("123 Town Square Place #515".upper()) == (
        "123 TOWN SQUARE PL",
        "# 515",
        "SAINT LOUIS",
    )  # SQUARE is the name
    assert std("750 SOUTH ST") == ("750 SOUTH ST", "", "SAINT LOUIS")  # SOUTH is the name
    assert std("374 N COAST HIGHWAY 101 SUITE 2") == ("374 N COAST HWY 101", "STE 2", "SAINT LOUIS")
    assert std("9 W 57 ST", "PMB #333") == ("9 W 57TH ST", "PMB 333", "SAINT LOUIS")
    assert std("123 MAIN ST", "5TH FLOOR") == ("123 MAIN ST", "FL 5", "SAINT LOUIS")
    assert std("P.O. BOX 68033") == ("PO BOX 68033", "", "SAINT LOUIS")
    assert std("C/O RED CURVE SOLUTIONS LLC", "138 CONANT ST STE 401") == (
        "138 CONANT ST",
        "STE 401",
        "SAINT LOUIS",
    )
    assert std("SOMEONE@GMAIL.COM")[0] == ""
    assert std("1 MAIN ST", "# 539")[1] == "# 539"  # a unit is never a place
    assert std("1 MAIN ST", "SAIN MO")[1] == ""  # the city and state cut short
    assert (
        std("22649 SHAKER BLVD SAINT LOUIS MO 631")[0] == "22649 SHAKER BLVD"
    )  # the place typed after the street
    assert (
        std("103 STANTON AVE, 103 STANTON AVENU")[0] == "103 STANTON AVE"
    )  # the street typed twice
    assert std("UNIT 8606 7485 VICTORY LANE")[:2] == ("7485 VICTORY LN", "UNIT 8606")
    assert std("13764RIVOLI DRIVE")[0] == "13764 RIVOLI DR"
    assert std("BOX 137")[0] == "PO BOX 137"
    assert std("1 MAIN ST", city="ST LOUIS")[2] == "SAINT LOUIS"
    assert std("1 MAIN ST", city="ST LOUS")[2] == "SAINT LOUIS"  # one slip, read through the ZIP
    assert std("1 MAIN ST", city="CREVE COEUR")[2] == "CREVE COEUR"  # a USPS name of the ZIP stays
    assert std("1 MAIN ST", city="SPRINGFIELD")[2] == "SPRINGFIELD"  # another city is not rewritten

    df = pd.DataFrame(
        {
            "sub_id": ["1", "2"],
            "contributor_street_1": ["123 East 13th Street Apt 4", "10 Downing St"],
            "contributor_street_2": ["", ""],
            "contributor_city": ["Anniston", "London"],
            "contributor_state": ["AL", "ZZ"],
            "contributor_zip": ["362011234", "SW1A 2AA"],
        }
    )
    assert standardize_addresses(df) == 1
    assert df.loc[0, ["contributor_street_1", "contributor_street_2"]].tolist() == [
        "123 E 13TH ST",
        "APT 4",
    ]
    assert df.loc[1, "contributor_street_1"] == "10 Downing St"  # foreign: as filed
    assert standardize_addresses(df) == 0  # the standard form is stable
