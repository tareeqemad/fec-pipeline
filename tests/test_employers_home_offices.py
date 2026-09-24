"""A home-based business is published with its city, state and ZIP only (owner decision 2026-09-24).

The audit found 144 published offices that are the filing address of a donor who
works there (street without unit + ZIP5), with no suite/unit, at most 2 filers at the
address and at most 3 donors for the employer: the company pin was a private home
(757CFO LLC at 905 ENFIELD CHASE, the manual HSK CONSULTING LLC row). Whatever the
source, the street and its pin are withheld; an office building, a floor of one, or a
company with more donors keeps its street.
"""
import json

import pandas as pd

import build_employers

HOME = "905 Enfield Chase"
CITY, STATE, ZIP = "Virginia Beach", "VA", "23452"


def _filing(employer="757CFO LLC", donor="d1", street="905 ENFIELD CHASE", street_2="",
            zip_code=ZIP, status="active"):
    return {
        "entity_type": "INDIVIDUAL", "donor_key": donor, "employer_status": status,
        "contributor_employer": employer, "previous_employer": "",
        "contributor_street_1": street, "contributor_street_2": street_2,
        "contributor_city": CITY.upper(), "contributor_state": STATE, "contributor_zip": zip_code,
    }


def _build(tmp_path, monkeypatch, filings, office=HOME, method="ai_openai_search"):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame(filings).to_csv(cleaned, index=False)
    cache = {"757CFO LLC": {
        "employer_address": office, "employer_city": CITY, "employer_state": STATE,
        "employer_zip": ZIP, "method": method,
    }}
    geocodes = {f"{office.upper()}|{CITY.upper()}|{STATE}|{ZIP}": {
        "lat": 36.8, "lng": -76.1, "source": "nominatim", "validated": True, "zip_checked": True,
    }}
    (tmp_path / "resolve_employer_addr.json").write_text(json.dumps(cache), encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text(json.dumps(geocodes), encoding="utf-8")
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)
    build_employers.build()
    location = pd.read_csv(output, dtype=str, keep_default_na=False, na_values=[]).iloc[0]
    review_csv = tmp_path / build_employers.REVIEW_CSV
    review = (pd.read_csv(review_csv, dtype=str, keep_default_na=False, na_values=[])
              if review_csv.exists() else pd.DataFrame(columns=["reason", "employer_address"]))
    return location, review


def test_the_owner_s_home_is_published_as_a_town_only(tmp_path, monkeypatch):
    location, review = _build(tmp_path, monkeypatch, [_filing()])

    assert location["employer_address"] == ""
    assert (location["employer_city"], location["employer_state"], location["employer_zip"]) == (
        "VIRGINIA BEACH", "VA", "23452")
    assert location["address_trust"] == "grounded"
    # the house's own point is gone; the ZIP's centroid stands in
    assert location["employer_latitude"] != "36.8"
    assert review["reason"].tolist() == [build_employers.REVIEW_HOME_OFFICE]
    assert review.loc[0, "employer_address"] == "905 ENFIELD CHASE"


def test_a_curated_home_office_is_withheld_too(tmp_path, monkeypatch):
    location, _review = _build(tmp_path, monkeypatch, [_filing()], method="manual_override")

    assert location["employer_address"] == ""
    assert location["address_trust"] == "verified"


def test_a_retired_owner_s_home_counts(tmp_path, monkeypatch):
    filing = {**_filing(employer="RETIRED", status="retired"), "previous_employer": "757CFO LLC"}

    location, _review = _build(tmp_path, monkeypatch, [filing])

    assert location["employer_address"] == ""


def test_an_office_with_a_suite_keeps_its_street(tmp_path, monkeypatch):
    location, review = _build(tmp_path, monkeypatch, [_filing(street="905 ENFIELD CHASE STE 2")],
                              office="905 Enfield Chase, Suite 2")

    assert location["employer_address"] == "905 ENFIELD CHASE STE 2"
    assert review.empty


def test_a_building_filed_with_a_floor_is_an_office(tmp_path, monkeypatch):
    # '55 HUDSON YARDS' / 'FL 50': the donor files the office, not a house
    filings = [_filing(), _filing(street_2="FL 50")]

    location, review = _build(tmp_path, monkeypatch, filings)

    assert location["employer_address"] == "905 ENFIELD CHASE"
    assert review.empty


def test_a_flat_is_still_the_owner_s_home(tmp_path, monkeypatch):
    # 'APT 7E' is a home's unit, not an office's: the building's street is the home street
    location, review = _build(tmp_path, monkeypatch, [_filing(street_2="APT 7E")])

    assert location["employer_address"] == ""
    assert review["reason"].tolist() == [build_employers.REVIEW_HOME_OFFICE]


def test_several_filers_or_donors_keep_the_street(tmp_path, monkeypatch):
    three_filers = [_filing(), _filing(employer="OTHER CO", donor="d2"), _filing(employer="OTHER CO", donor="d3")]
    location, _review = _build(tmp_path, monkeypatch, three_filers)
    assert location["employer_address"] == "905 ENFIELD CHASE"

    four_donors = [_filing()] + [_filing(donor=f"d{n}", street=f"{n} MAIN ST") for n in range(2, 5)]
    location, _review = _build(tmp_path, monkeypatch, four_donors)
    assert location["employer_address"] == "905 ENFIELD CHASE"


def test_an_office_that_is_no_employee_s_address_keeps_its_street(tmp_path, monkeypatch):
    location, review = _build(tmp_path, monkeypatch, [_filing(street="12 OTHER RD")])

    assert location["employer_address"] == "905 ENFIELD CHASE"
    assert review.empty


def test_a_manual_row_marked_office_premises_keeps_its_street(tmp_path, monkeypatch):
    # a dealership or casino whose owner files the business address is not a home
    pd.DataFrame([{
        "name": "757CFO LLC", "address": HOME, "city": CITY, "state": STATE, "zip": ZIP,
        "is_primary": "true", "note": "OFFICE PREMISES: storefront listed on the company's site",
        "source_name": "example", "source_url": "https://example.com",
    }]).to_csv(tmp_path / "manual_employer_addresses.csv", index=False)
    location, review = _build(tmp_path, monkeypatch, [_filing()], method="manual_override")

    assert location["employer_address"] == "905 ENFIELD CHASE"
    assert build_employers.REVIEW_HOME_OFFICE not in review["reason"].tolist()
