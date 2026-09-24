"""fec.cleaning.address_review: safe text fixes + review reports."""
import csv

import numpy as np
import pandas as pd

from fec.cleaning.address_review import build_address_reports
from fec.cleaning.pipeline.address_fixes.safe_text import (
    _collapse_dup_words,
    _fix_house_number,
    apply_safe_fixes,
)
from fec.cleaning.pipeline.address_fixes.verified import apply_verified_address_fixes
from fec.cleaning.addresses import clean_streets


def test_house_number_leading_symbol_and_zeros():
    assert _fix_house_number("!6854 MOONCREST DR") == "6854 MOONCREST DR"
    assert _fix_house_number("02393 SW MILITARY RD") == "2393 SW MILITARY RD"
    assert _fix_house_number("00000 X") == "00000 X"          # all-zeros untouched
    assert _fix_house_number("123 MAIN ST") == "123 MAIN ST"  # no change
    assert _fix_house_number("PO BOX 5") == "PO BOX 5"        # not a house number


def test_collapse_adjacent_duplicate_word():
    assert _collapse_dup_words("14200 E E MONCRIEFF") == "14200 E MONCRIEFF"
    assert _collapse_dup_words("100 MAIN MAIN ST") == "100 MAIN ST"
    assert _collapse_dup_words("100 MAIN ST") == "100 MAIN ST"   # nothing to collapse


def test_trailing_unit_split_into_street2():
    df = pd.DataFrame([
        {"contributor_street_1": "155 STEELE ST 616", "contributor_street_2": np.nan,
         "contributor_city": "DENVER", "contributor_state": "CO"},
        {"contributor_street_1": "40 W 57TH ST 28TH FLOOR", "contributor_street_2": np.nan,
         "contributor_city": "NEW YORK", "contributor_state": "NY"},
        {"contributor_street_1": "9208 NE HWY 9", "contributor_street_2": np.nan,
         "contributor_city": "VANCOUVER", "contributor_state": "WA"},  # HWY route # untouched
    ])
    df, _ = apply_safe_fixes(df)
    assert df.loc[0, "contributor_street_1"] == "155 STEELE ST"
    assert df.loc[0, "contributor_street_2"] == "# 616"
    assert df.loc[1, "contributor_street_1"] == "40 W 57TH ST"
    assert df.loc[1, "contributor_street_2"] == "28TH FLOOR"
    assert df.loc[2, "contributor_street_1"] == "9208 NE HWY 9"     # route number kept


def test_directional_unit_and_repeated_address_tail():
    df = pd.DataFrame([
        {"contributor_street_1": "5500 ISLAND ESTATES DR 705 N",
         "contributor_street_2": np.nan, "contributor_city": "AVENTURA",
         "contributor_state": "FL"},
        {"contributor_street_1": "4400 W 87TH TER 4400 W",
         "contributor_street_2": np.nan, "contributor_city": "PRAIRIE VILLAGE",
         "contributor_state": "KS"},
        {"contributor_street_1": "159 W 159 W",
         "contributor_street_2": np.nan, "contributor_city": "NEW YORK",
         "contributor_state": "NY"},
    ])

    df, _ = apply_safe_fixes(df)

    assert df.loc[0, "contributor_street_1"] == "5500 ISLAND ESTATES DR"
    assert df.loc[0, "contributor_street_2"] == "# 705 N"
    assert df.loc[1, "contributor_street_1"] == "4400 W 87TH TER"
    assert pd.isna(df.loc[1, "contributor_street_2"])
    assert df.loc[2, "contributor_street_1"] == "159 W 159 W"


def test_sute_typo_becomes_suite_and_is_extracted():
    df = pd.DataFrame([{
        "contributor_street_1": "14200 EAST MONCRIEFF SUTE E",
        "contributor_street_2": np.nan,
        "contributor_first_name": "MICHAEL",
        "contributor_last_name": "GELLER",
    }])

    df, _ = clean_streets(df)

    assert df.loc[0, "contributor_street_1"] == "14200 E MONCRIEFF"
    assert df.loc[0, "contributor_street_2"] == "STE E"


def test_verified_postal_corrections_are_exact():
    df = pd.DataFrame([
        {"contributor_street_1": "3750 LAS VEGAS BLVD S",
         "contributor_street_2": np.nan, "contributor_state": "NV",
         "contributor_zip": "89158"},
        {"contributor_street_1": "704C 13TH ST E",
         "contributor_street_2": np.nan, "contributor_state": "MT",
         "contributor_zip": "59937"},
        {"contributor_street_1": "3750 LAS VEGAS BLVD S",
         "contributor_street_2": np.nan, "contributor_state": "NV",
         "contributor_zip": "99999"},
        {"contributor_street_1": "42 W E 48TH ST",
         "contributor_street_2": np.nan, "contributor_state": "NY",
         "contributor_zip": "10017"},
        {"contributor_street_1": "159 W 159 W",
         "contributor_street_2": np.nan, "contributor_state": "NY",
         "contributor_zip": "10023"},
        {"contributor_street_1": "1101 IVEAN PEARSON RD",
         "contributor_street_2": "STE G101", "contributor_city": "LAGO VISTA",
         "contributor_state": "CA", "contributor_zip": "90292"},
        {"contributor_street_1": "268 CHESTNUT ST",
         "contributor_street_2": np.nan, "contributor_city": "ENGLEWOOD",
         "contributor_state": "NY", "contributor_zip": "11963"},
    ])

    assert apply_verified_address_fixes(df) == 6
    assert df.loc[0, "contributor_street_1"] == "3750 S LAS VEGAS BLVD"
    assert df.loc[1, "contributor_street_1"] == "704C E 13TH ST"
    assert df.loc[1, "contributor_street_2"] == "STE 260"
    assert df.loc[2, "contributor_street_1"] == "3750 LAS VEGAS BLVD S"
    assert df.loc[3, "contributor_street_1"] == "42 W 48TH ST"
    assert df.loc[3, "contributor_street_2"] == "STE 706-707"
    assert df.loc[3, "contributor_zip"] == "10036"
    assert df.loc[4, "contributor_street_1"] == "159 W 74TH ST"
    assert df.loc[4, "contributor_street_2"] == "APT GR"
    assert df.loc[5, "contributor_state"] == "TX"
    assert df.loc[5, "contributor_zip"] == "78645"
    assert df.loc[5, "contributor_street_2"] == "STE G101"
    assert df.loc[6, "contributor_state"] == "NJ"
    assert df.loc[6, "contributor_zip"] == "07631"


def test_care_of_prefix_recovered_or_flagged(tmp_path):
    df = pd.DataFrame([
        {"sub_id": "1", "contributor_street_1": "C/O 228 S WASHINGTON", "contributor_street_2": np.nan,
         "contributor_city": "DENVER", "contributor_state": "CO"},
        {"sub_id": "2", "contributor_street_1": "C/O JFI 410 PARK AVE", "contributor_street_2": np.nan,
         "contributor_city": "NEW YORK", "contributor_state": "NY"},
        {"sub_id": "3", "contributor_street_1": "C/O MOELIS", "contributor_street_2": np.nan,
         "contributor_city": "NEW YORK", "contributor_state": "NY"},
    ])
    df, counts = apply_safe_fixes(df)
    # C/O lines with a real address are recovered
    assert df.loc[df.sub_id == "1", "contributor_street_1"].iloc[0] == "228 S WASHINGTON"
    assert df.loc[df.sub_id == "2", "contributor_street_1"].iloc[0] == "410 PARK AVE"
    assert counts["care_of"] == 2
    # a name-only C/O is left intact and later flagged
    assert df.loc[df.sub_id == "3", "contributor_street_1"].iloc[0] == "C/O MOELIS"
    df["contributor_zip"] = ""
    df, _ = build_address_reports(df, str(tmp_path))
    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    assert any("care-of name" in r["review_reason"] for r in review)


def test_reports_split_review_vs_regeocode(tmp_path):
    df = pd.DataFrame([
        {"sub_id": "1", "contributor_street_1": "URBAN EQUITIES LLC", "contributor_street_2": np.nan,
         "contributor_city": "NEW YORK", "contributor_state": "NY", "contributor_zip": "10001"},
        {"sub_id": "2", "contributor_street_1": "PO BOX 4570", "contributor_street_2": np.nan,
         "contributor_city": "ASPEN", "contributor_state": "CO", "contributor_zip": "81612"},
        {"sub_id": "3", "contributor_street_1": "100 MAIN ST", "contributor_street_2": "STE",
         "contributor_city": "DENVER", "contributor_state": "CO", "contributor_zip": "80202"},
        {"sub_id": "4", "contributor_street_1": "2629 UPPER PARK RD", "contributor_street_2": "FL",
         "contributor_city": "ORLANDO", "contributor_state": "FL", "contributor_zip": "32801"},
        {"sub_id": "5", "contributor_street_1": "10702 CLOVERBROOKE DR", "contributor_street_2": "POTO MD",
         "contributor_city": "POTOMAC", "contributor_state": "MD", "contributor_zip": "20854"},
        {"sub_id": "6", "contributor_street_1": "PMB 288", "contributor_street_2": np.nan,
         "contributor_city": "WINTER PARK", "contributor_state": "FL", "contributor_zip": "32789"},
        # committee / organization mailboxes are their normal filing address: not queued
        {"sub_id": "7", "contributor_street_1": "PO BOX 378", "contributor_street_2": np.nan,
         "contributor_city": "VICTOR", "contributor_state": "NY", "contributor_zip": "14564"},
        {"sub_id": "8", "contributor_street_1": "PO BOX 12", "contributor_street_2": np.nan,
         "contributor_city": "ASPEN", "contributor_state": "CO", "contributor_zip": "81612"},
        {"sub_id": "9", "contributor_street_1": "PMB 288", "contributor_street_2": "501 N ORLANDO AVE",
         "contributor_city": "WINTER PARK", "contributor_state": "FL", "contributor_zip": "32789"},
    ])
    df["entity_type"] = ["INDIVIDUAL"] * 6 + ["COMMITTEE/PAC", "ORGANIZATION", "COMMITTEE/PAC"]
    df, counts = build_address_reports(df, str(tmp_path))

    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    regeo = list(csv.DictReader(open(tmp_path / "address_regeocode_suspects.csv", encoding="utf-8")))
    status_of = {r["review_reason"]: r["status"] for r in review}

    # human-judgment cases are open manual-review items
    assert status_of["entity / non-address in street_1"] == "open"
    # street_2 values the step already emptied stay visible, but as auto_fixed, not open
    assert status_of["street_2 unit keyword without a number"] == "auto_fixed"
    assert status_of["street_2 looks like a state/city abbreviation"] == "auto_fixed"
    fixed = {r["sub_id"]: r["contributor_street_2"] for r in review if r["status"] == "auto_fixed"}
    assert fixed == {"3": "STE", "4": "FL", "5": "POTO MD"}   # the value as filed, before emptying
    assert counts["manual_review"] == sum(r["status"] == "open" for r in review)
    assert counts["auto_fixed"] == 3 and counts["street2_emptied"] == 3
    # PO boxes and PMB private mailboxes go to the re-geocode notes, not manual review
    assert any("PO Box" in r["review_reason"] for r in regeo)
    assert any("PMB" in r["review_reason"] for r in regeo)
    assert not any("PO Box" in r for r in status_of)
    assert not any("PMB" in r for r in status_of)
    # ... and only for individuals
    mailbox_ids = {r["sub_id"] for r in regeo if "PO Box" in r["review_reason"] or "PMB" in r["review_reason"]}
    assert mailbox_ids == {"2", "6"}

    # The only edit this step makes: a bad street_2 is emptied
    assert df.loc[df.sub_id == "3", "contributor_street_2"].isna().all()
    assert df.loc[df.sub_id == "4", "contributor_street_2"].isna().all()


def test_near_street_spellings_are_reviewed_not_merged(tmp_path):
    df = pd.DataFrame([
        {"sub_id": "1", "entity_type": "INDIVIDUAL", "contributor_name": "SABAN, HAIM",
         "contributor_street_1": "11301 W OLYMIC BLVD", "contributor_street_2": "STE 121-6",
         "contributor_city": "LOS ANGELES", "contributor_state": "CA", "contributor_zip": "90064"},
        {"sub_id": "2", "entity_type": "INDIVIDUAL", "contributor_name": "SABAN, HAIM",
         "contributor_street_1": "11301 W OLYMPIC BLVD", "contributor_street_2": "STE 121-601",
         "contributor_city": "LOS ANGELES", "contributor_state": "CA", "contributor_zip": "90064"},
        {"sub_id": "3", "entity_type": "INDIVIDUAL", "contributor_name": "OTHER, PERSON",
         "contributor_street_1": "100 MAIN ST", "contributor_street_2": "APT 1",
         "contributor_city": "DENVER", "contributor_state": "CO", "contributor_zip": "80202"},
        {"sub_id": "4", "entity_type": "INDIVIDUAL", "contributor_name": "OTHER, PERSON",
         "contributor_street_1": "100 MAIN ST", "contributor_street_2": "APT 2",
         "contributor_city": "DENVER", "contributor_state": "CO", "contributor_zip": "80202"},
    ])

    original = df.copy(deep=True)
    _, counts = build_address_reports(df, str(tmp_path))
    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    spelling_rows = [row for row in review if row["review_reason"].startswith("near-duplicate")]

    assert {row["sub_id"] for row in spelling_rows} == {"1", "2"}
    assert counts["manual_review"] == 2
    pd.testing.assert_frame_equal(df, original)


def test_state_zip_and_city_fragments_in_street2_are_emptied(tmp_path):
    df = pd.DataFrame([
        {"sub_id": "1", "contributor_street_1": "16 THE DRAWBRIDGE", "contributor_street_2": "# NY1179",
         "contributor_city": "WOODBURY", "contributor_state": "NY", "contributor_zip": "11797"},
        {"sub_id": "2", "contributor_street_1": "29 HIGHVIEW RD", "contributor_street_2": "# NJU",
         "contributor_city": "SHORT HILLS", "contributor_state": "NJ", "contributor_zip": "07078"},
        {"sub_id": "3", "contributor_street_1": "1 PEACHTREE ST", "contributor_street_2": "ATLA",
         "contributor_city": "ATLANTA", "contributor_state": "GA", "contributor_zip": "30303"},
        {"sub_id": "4", "contributor_street_1": "100 MAIN ST", "contributor_street_2": "USA",
         "contributor_city": "DENVER", "contributor_state": "CO", "contributor_zip": "80202"},
        {"sub_id": "5", "contributor_street_1": "320 N MAPLE DR", "contributor_street_2": "# PH3",
         "contributor_city": "BEVERLY HILLS", "contributor_state": "CA", "contributor_zip": "90210"},
        {"sub_id": "6", "contributor_street_1": "8877 COLLINS AVE", "contributor_street_2": "PHA",
         "contributor_city": "BAL HARBOUR", "contributor_state": "FL", "contributor_zip": "33154"},
        {"sub_id": "7", "contributor_street_1": "57 MAPLE HILL RD", "contributor_street_2": "# A",
         "contributor_city": "GLENCOE", "contributor_state": "IL", "contributor_zip": "60022"},
    ])
    df, counts = build_address_reports(df, str(tmp_path))
    emptied = df.set_index("sub_id")["contributor_street_2"].isna()
    assert emptied[["1", "2", "3", "4"]].all()           # fragments of the truncated street
    assert not emptied[["5", "6", "7"]].any()            # penthouse / unit letters are real units
    assert counts["street2_emptied"] == 4


def test_city_state_zip_tail_typed_into_street_is_dropped():
    from fec.cleaning.pipeline.address_fixes.safe_text import _strip_city_state_tail as strip
    assert strip("3841 HAYVENHURST DR ENCINO CA", "ENCINO", "CA", "91436") == "3841 HAYVENHURST DR"
    assert strip("76 WALLACKS DR STAMFORD CT 0690", "STAMFORD", "CT", "06902") == "76 WALLACKS DR"
    assert strip("1904 BAY DR POMPANO BEACH FL", "POMPANO BEACH", "FL", "33062") == "1904 BAY DR"
    assert strip("16201 MEADOW RIDGE WAY ENCINO", "ENCINO", "CA", "91436") == "16201 MEADOW RIDGE WAY"
    assert strip("7 DANIEL CT", "WESTPORT", "CT", "06880") == "7 DANIEL CT"            # Court, not Connecticut
    assert strip("16A IDAR CT", "GREENWICH", "CT", "06830") == "16A IDAR CT"
    assert strip("5000 PKWY CALABASAS", "CALABASAS", "CA", "91302") == "5000 PKWY CALABASAS"  # Parkway Calabasas is the street
    assert strip("5 GREENWICH", "GREENWICH", "CT", "06830") == "5 GREENWICH"
    assert strip("100 MAIN ST", "DENVER", "CO", "80202") == "100 MAIN ST"
    assert strip("24530 TWICKENHAM DR BEACHWOOD", "BEACHWOOD", "OH", "44122") == "24530 TWICKENHAM DR"


def test_care_of_fragment_without_a_street_is_blanked():
    from fec.cleaning.pipeline.address_fixes.safe_text import _strip_care_of
    assert _strip_care_of("C/O MCCARTER & ENGLISH, LLP, 100 M") is np.nan or pd.isna(_strip_care_of("C/O MCCARTER & ENGLISH, LLP, 100 M"))
    assert _strip_care_of("C/O ARMANINO, 437 MADISON AVE") == "437 MADISON AVE"
    assert _strip_care_of("C/O MOELIS") == "C/O MOELIS"


def test_donor_street_with_and_without_type_unify():
    from fec.cleaning.pipeline.address_fixes.unify import _unify_street_types
    df = pd.DataFrame([
        {"contributor_name": "COLL, LISA", "contributor_street_1": "103 STANTON AVE", "contributor_city": "AUBURNDALE", "contributor_state": "MA"},
        {"contributor_name": "COLL, LISA", "contributor_street_1": "103 STANTON AVE", "contributor_city": "AUBURNDALE", "contributor_state": "MA"},
        {"contributor_name": "COLL, LISA", "contributor_street_1": "103 STANTON", "contributor_city": "AUBURNDALE", "contributor_state": "MA"},
        {"contributor_name": "OTHER, PERSON", "contributor_street_1": "103 STANTON", "contributor_city": "AUBURNDALE", "contributor_state": "MA"},
    ])
    assert _unify_street_types(df) == 1
    assert df.contributor_street_1.tolist() == ["103 STANTON AVE"] * 3 + ["103 STANTON"]


def test_typed_street_spelling_wins_even_as_a_minority():
    from fec.cleaning.pipeline.address_fixes.unify import _unify_street_types
    df = pd.DataFrame([
        {"contributor_name": "GELLER, MICHAEL", "contributor_street_1": "14200 E MONCRIEFF", "contributor_city": "AURORA", "contributor_state": "CO"},
        {"contributor_name": "GELLER, MICHAEL", "contributor_street_1": "14200 E MONCRIEFF", "contributor_city": "AURORA", "contributor_state": "CO"},
        {"contributor_name": "GELLER, MICHAEL", "contributor_street_1": "14200 E MONCRIEFF PL", "contributor_city": "AURORA", "contributor_state": "CO"},
    ])
    assert _unify_street_types(df) == 2
    assert set(df.contributor_street_1) == {"14200 E MONCRIEFF PL"}


def test_two_different_street_types_are_both_kept():
    # MAIN ST and MAIN AVE disagree: nothing proves which one is the typo
    from fec.cleaning.pipeline.address_fixes.unify import _unify_street_types
    rows = [("100 MAIN ST",), ("100 MAIN ST",), ("100 MAIN AVE",), ("100 MAIN",)]
    df = pd.DataFrame([
        {"contributor_name": "DOE, JANE", "contributor_street_1": street, "contributor_city": "AUSTIN",
         "contributor_state": "TX"} for (street,) in rows
    ])
    assert _unify_street_types(df) == 0
    assert df.contributor_street_1.tolist() == ["100 MAIN ST", "100 MAIN ST", "100 MAIN AVE", "100 MAIN"]


def _units(values):
    return pd.DataFrame([
        {"entity_type": "INDIVIDUAL", "contributor_name": "DOE, JANE", "contributor_street_1": "1 MAIN ST",
         "contributor_street_2": value, "contributor_city": "AUSTIN", "contributor_state": "TX",
         "contributor_zip": "78701"} for value in values
    ])


def test_a_floor_is_not_an_apartment():
    from fec.cleaning.pipeline.address_fixes.unify import _unify_unit_designators
    df = _units(["APT 9", "APT 9", "FL 9"])
    assert _unify_unit_designators(df) == 0
    assert df.contributor_street_2.tolist() == ["APT 9", "APT 9", "FL 9"]


def test_building_and_suite_numbers_stay_apart():
    from fec.cleaning.pipeline.address_fixes.unify import _unify_unit_designators
    df = _units(["BLDG 1 STE 23", "BLDG 1 STE 23", "BLDG 12 STE 3"])
    assert _unify_unit_designators(df) == 0


def test_the_same_unit_written_two_ways_still_unifies():
    from fec.cleaning.pipeline.address_fixes.unify import _unify_unit_designators
    df = _units(["APT 15-03", "APT 15-03", "# 1503", "UNIT 15-03"])
    assert _unify_unit_designators(df) == 2
    assert set(df.contributor_street_2) == {"APT 15-03"}


def test_a_space_inside_one_unit_id_does_not_split_it():
    # EPSTEIN filed APT 705N and # 705 N; FRIEDMANN PH 20 and APT PH20
    from fec.cleaning.pipeline.address_fixes.unify import _unit_core
    assert _unit_core("APT 705N") == _unit_core("# 705 N")
    assert _unit_core("PH 20") == _unit_core("APT PH20")
    assert _unit_core("UNIT PH-3") == _unit_core("PH 3")
    assert _unit_core("BLDG 1 STE 23") != _unit_core("BLDG 12 STE 3")
    assert _unit_core("FL 9") != _unit_core("APT 9")
