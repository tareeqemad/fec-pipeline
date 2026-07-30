"""fec.cleaning.address_review: safe text fixes + review reports."""
import csv

import numpy as np
import pandas as pd

from fec.cleaning.address_review import (
    apply_safe_fixes, _fix_house_number, _collapse_dup_words,
    build_address_reports,
)


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
