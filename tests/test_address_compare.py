"""Acceptance tests: a change that can move the place is flagged, formatting is not."""
import pytest

from fec.cleaning.addresses.compare import street_changes


@pytest.mark.parametrize("filed, cleaned, kind", [
    (("123 N MAIN ST", ""), ("123 S MAIN ST", ""), "street_direction_changed"),
    (("123 N MAIN ST", ""), ("123 MAIN ST", ""), "street_direction_changed"),
    (("123 MAIN RD", ""), ("123 MAIN ST", ""), "street_type_changed"),
    (("123 MAIN ST", ""), ("132 MAIN ST", ""), "house_number_changed"),
    (("123 MAIN ST APT 5", ""), ("123 MAIN ST", ""), "unit_removed"),
    (("123 MAIN ST", "APT 5"), ("123 MAIN ST", ""), "unit_removed"),
    (("123 MAIN ST", "APT 5"), ("123 MAIN ST", "APT 6"), "unit_changed"),
    (("123 MAIN ST", ""), ("123 MAIN ST", "APT 6"), "unit_added"),
    (("PO BOX 123", ""), ("PO BOX 132", ""), "po_box_number_changed"),
])
def test_a_change_that_can_move_the_place_is_flagged(filed, cleaned, kind):
    assert kind in street_changes(*filed, *cleaned)


@pytest.mark.parametrize("filed, cleaned", [
    (("123 NORTH MAIN STREET", ""), ("123 N MAIN ST", "")),       # spelled-out words
    (("123 MAIN ST APT 5", ""), ("123 MAIN ST", "APT 5")),         # unit moved to street_2
    (("123 MAIN ST", "APARTMENT 5"), ("123 MAIN ST", "# 5")),      # unit word respelled
    (("123 Main St.", ""), ("123 MAIN ST", "")),                   # case and period
    (("10 17 GREENTREE DRIVE", ""), ("1017 GREENTREE DR", "")),    # the reviewed example
    (("13 867 LE BATEAU ISLE", ""), ("13867 LE BATEAU ISLE", "")), # the reviewed example
    (("123 NORTH ST", ""), ("123 N ST", "")),                      # a direction that is the name
    (("5 E 72ND ST", ""), ("5 E 72 ST", "")),                      # ordinal suffix
    (("PO BOX 123", ""), ("P O BOX 123", "")),                     # a PO box is not a street error
])
def test_formatting_is_not_a_change(filed, cleaned):
    assert street_changes(*filed, *cleaned) == []


@pytest.mark.parametrize("filed, cleaned", [
    (("C/O AEDER, 52 GREENTREE DRIVE", ""), ("52 GREENTREE DR", "")),  # read out of the filed text
    (("2425 LST NW APT 314", ""), ("2425 L ST NW", "APT 314")),         # type glued to the name
    (("155 STEELE STREET 616", ""), ("155 STEELE ST", "# 616")),        # unit number with no unit word
    (("1 STATE ST FL 29", "FL 29"), ("1 STATE ST", "FL 29")),           # the same unit in both fields
    (("201 AQUA AVE. PH2", ""), ("201 AQUA AVE PH-2", "")),             # hyphen in the unit
    (("10866 WILSHIRE BLVD", "# LA"), ("10866 WILSHIRE BLVD", "")),     # a city fragment is no unit
    (("POB 227", ""), ("PO BOX 227", "")),                              # PO box written short
    (("4872 GABLES CROSSING", ""), ("4872 GABLES XING", "")),           # USPS abbreviation
    (("705 NORTH BEDFORD DRIVE 705 NORTH", ""), ("705 N BEDFORD DR", "")),  # house number repeated
    (("12 MAIN CT 0690", ""), ("12 MAIN CT", "")),                      # a ZIP is no unit
    (("1 MAIN ST # CA9260", ""), ("1 MAIN ST", "")),                    # a state and ZIP is no unit
    (("1 MAIN ST STE 105 #105", ""), ("1 MAIN ST", "STE 105")),         # the unit written twice
    (("UNIT 8606 7485 VICTORY LANE", ""), ("7485 VICTORY LN", "UNIT 8606")),  # unit written first
    (("1 MAIN ST 19TH FLOOR", ""), ("1 MAIN ST", "FL 19")),             # floor after its number
    (("139-04 58TH AVE", ""), ("13904 58 AVE", "")),                    # Queens house number
    (("02393 MAIN ST", ""), ("2393 MAIN ST", "")),                      # leading zero
    (("1 N. W. 5TH ST", ""), ("1 NW 5 ST", "")),                        # split direction
    (("W RESIDENCE 210 LAVACA ST", ""), ("210 LAVACA ST", "")),         # text before the number
    (("9 MEADOW RIDGE WAY, ENCINO", ""), ("9 MEADOW RIDGE WAY", "")),   # city after the street
    (("P.0. BOX 12", ""), ("PO BOX 12", "")),                           # zero for the letter O
])
def test_more_formatting_seen_in_the_data_is_not_a_change(filed, cleaned):
    assert street_changes(*filed, *cleaned) == []


def test_a_filing_with_no_street_is_replaced_not_renumbered():
    assert street_changes("AKIVA.DICKSTEIN@GMAIL.COM", "", "9 VISTA TER", "") == ["street_replaced"]
    assert street_changes("2655", "", "2655 OAK ST", "") == ["street_replaced"]
    assert street_changes("DS120NM@AOL.COM", "", "19821 NW 2ND AVE", "") == ["street_replaced"]


def test_a_street_number_after_the_house_is_the_name_not_the_house():
    assert street_changes("6620 24 ST N", "", "6620 25 ST N", "") == []  # name change: not a place part
    assert "house_number_changed" in street_changes("6620 24 ST N", "", "6621 24 ST N", "")


@pytest.mark.parametrize("filed, cleaned", [
    (("44 COCOANUT ROW", "# T8"), ("44 COCOANUT ROW", "# T-8")),        # hyphen in the unit
    (("20 CHAPEL ST", "# A-PH-1"), ("20 CHAPEL ST", "APT APH1")),        # PH inside a unit is no marker
    (("2660 S OCEAN BLVD  AP[T 503W", ""), ("2660 S OCEAN BLVD", "APT 503W")),  # stray bracket
    (("380 RECTOR PLACE APT 25J 25J", ""), ("380 RECTOR PL", "APT 25J")),  # the unit written twice
    (("14200 EAST MONCRIEFF PL SUTE E", ""), ("14200 E MONCRIEFF PL", "STE E")),  # misspelled SUITE
])
def test_unit_spellings_are_not_a_change(filed, cleaned):
    assert street_changes(*filed, *cleaned) == []


def test_a_type_the_filing_never_wrote_is_flagged_as_added():
    assert street_changes("2200 ISENGARD", "", "2200 ISENGARD ST", "") == ["street_type_added"]
