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
    ])
    df, counts = build_address_reports(df, str(tmp_path))

    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    regeo = list(csv.DictReader(open(tmp_path / "address_regeocode_suspects.csv", encoding="utf-8")))
    review_reasons = {r["review_reason"] for r in review}

    # human-judgment cases land in manual review
    assert "entity / non-address in street_1" in review_reasons
    assert "street_2 unit keyword without a number" in review_reasons
    assert "street_2 looks like a state/city abbreviation" in review_reasons
    # PO boxes and PMB private mailboxes go to the re-geocode notes, not manual review
    assert any("PO Box" in r["review_reason"] for r in regeo)
    assert any("PMB" in r["review_reason"] for r in regeo)
    assert not any("PO Box" in r for r in review_reasons)
    assert not any("PMB" in r for r in review_reasons)

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
