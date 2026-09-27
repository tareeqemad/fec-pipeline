"""Address review cases: grouped per question, with the filed address, the step and the evidence."""
import pandas as pd

from fec.cleaning.addresses.review_cases import (
    FORMATTING_ONLY,
    PENDING,
    PO_BOX_NOTE,
    SPELLING_ONLY,
    SUPPORTED,
    build_review_cases,
)

SPELLING = "near-duplicate street spelling for same donor/location"
COLUMNS = ["sub_id", "donor_key", "entity_type", "contributor_name", "contributor_street_1",
           "contributor_street_2", "contributor_city", "contributor_state", "contributor_zip"]


def _rows(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def _cases(final, review_ids, filed=None, trail=(), reason=SPELLING):
    review = final[final.sub_id.isin(review_ids)].assign(review_reason=reason, status="open")
    filed = final if filed is None else filed
    filed = filed.drop(columns=["donor_key", "entity_type", "contributor_name"])
    return build_review_cases(review, final, filed, list(trail))


def _person(rows, key="k1", name="DOE, JANE"):
    return _rows([(sid, key, "INDIVIDUAL", name, s1, s2, "MIAMI", "FL", "33160") for sid, s1, s2 in rows])


def test_items_of_one_question_are_one_case_counting_every_filing():
    final = _person([("1", "3323 NE 163RD ST", ""), ("2", "3323 NE 163 ST", ""), ("3", "3323 NE 163RD ST", ""),
                     ("4", "9 OTHER RD", "")])
    cases = _cases(final, {"1", "2"})
    assert len(cases) == 1
    case = cases.iloc[0]
    assert case.filings == 3 and case.sub_ids.split(";") == ["1", "2", "3"]  # 3 has a listed form; 4 does not
    assert case.form_differences == FORMATTING_ONLY and case.priority == "low"


def test_a_direction_between_forms_is_high_priority():
    cases = _cases(_person([("1", "4131 E NORTHAMPTON PL", ""), ("2", "4131 W NORTHAMPTON PL", "")]), {"1", "2"})
    assert cases.iloc[0].priority == "high"
    assert cases.iloc[0].form_differences == "street_direction_changed"


def test_a_unit_that_disappears_between_forms_is_high_priority():
    cases = _cases(_person([("1", "360 CENTRAL PARK W", "APT 12A"), ("2", "360 CENTRAL PARK WEST", "")]), {"1", "2"})
    assert cases.iloc[0].form_differences == "unit_removed" and cases.iloc[0].priority == "high"


def test_a_spelling_difference_is_medium_priority():
    cases = _cases(_person([("1", "33 WINDSOR DR", ""), ("2", "33 WINDOSR DR", "")]), {"1", "2"})
    assert cases.iloc[0].form_differences == SPELLING_ONLY and cases.iloc[0].priority == "medium"


def test_the_case_shows_the_full_filed_address_with_its_original_zip():
    final = _person([("1", "1017 GREENTREE DR", ""), ("2", "1017 GREENTRE DR", "")])
    filed = final.copy()
    filed.loc[0, ["contributor_street_1", "contributor_zip"]] = ["10 17 GREENTREE DRIVE", "331601234"]
    case = _cases(final, {"1", "2"}, filed=filed).iloc[0]
    assert "10 17 GREENTREE DRIVE |  | MIAMI | FL | 331601234 x1" in case.filed_addresses
    assert case.changes_filed_to_cleaned == ""  # 10 17 -> 1017 is formatting, not a new house number


def test_a_change_from_the_donors_other_filings_stays_pending():
    final = _person([("1", "13876 DEGAS DR E", ""), ("2", "13876 DEGAS DR", "")])
    filed = final.copy()
    filed.loc[0, "contributor_street_1"] = "13876 DEGAS DR W"
    trail = [{"sub_id": "1", "field": "contributor_street_1", "step": "address_same_street_align",
              "reason": "aligned_to_donor_dominant_at_same_street", "source": None}]
    case = _cases(final, {"1", "2"}, filed=filed, trail=trail).iloc[0]
    assert case.priority == "high" and "street_direction_changed" in case.changes_filed_to_cleaned
    assert case.decision == PENDING and "not proof" in case.decision_basis
    assert "address_same_street_align" in case.changed_by
    assert "same donor's filings (not proof)" in case.evidence


def test_a_change_with_a_checked_source_is_a_supported_correction():
    final = _person([("1", "42 W 48TH ST", ""), ("2", "42 W 48 ST", "")])
    filed = final.copy()
    filed.loc[0, "contributor_street_1"] = "42W E 48TH ST"
    trail = [{"sub_id": "1", "field": "contributor_street_1", "step": "address_verified_rules",
              "reason": "verified_postal_correction", "source": "manual_verified: 42 W 48TH ST | NY | 10017"}]
    case = _cases(final, {"1", "2"}, filed=filed, trail=trail).iloc[0]
    assert case.decision == SUPPORTED
    assert "manual_verified: 42 W 48TH ST" in case.evidence


def test_a_po_box_is_a_geocoding_limit_not_an_address_error():
    final = _person([("1", "PO BOX 123", ""), ("2", "P O BOX 123", "")])
    case = _cases(final, {"1", "2"}).iloc[0]
    assert case.note == PO_BOX_NOTE
    assert case.priority == "low"


def test_the_house_numbers_typed_with_a_space_stay_safe():
    for filed_street, cleaned in (("10 17 GREENTREE DRIVE", "1017 GREENTREE DR"),
                                  ("13 867 LE BATEAU ISLE", "13867 LE BATEAU ISLE")):
        final = _person([("1", cleaned, ""), ("2", cleaned + " X", "")])
        filed = final.copy()
        filed.loc[0, "contributor_street_1"] = filed_street
        case = _cases(final, {"1"}, filed=filed).iloc[0]
        assert case.changes_filed_to_cleaned == ""


def test_care_of_items_without_a_place_change_are_low_priority():
    final = _rows([("1", "c1", "COMMITTEE/PAC", "SOME PAC", "C/O JANE DOE", "", "SALEM", "MA", "01915")])
    case = _cases(final, {"1"}, reason="care-of name (no street to recover)").iloc[0]
    assert case.priority == "low" and case.decision == PENDING
