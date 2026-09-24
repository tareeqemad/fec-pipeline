"""build_employers publishes what resolve's apply step resolves, and nothing without a source.

Audit 2026-09-24 (employer-format-dupes / employer-accuracy):
* F1: 67 employers got no published office although apply's publishable lookup
  already picked a grounded canonical sibling ('SCOTT FANE,CPA PA' -> 'SCOTT FANE CPA
  PA'); build_employers used the full lookup, where the exact closed-book answer hid it.
  A sibling address in no donor's state goes to the review list instead (WILLIAMS &
  CONNOLLY's grounded sibling is its pre-2022 office).
* 32 rows kept from the previous employer_locations.csv without any cache or manual
  source kept publishing as verified/grounded (at least 9 proven wrong).
"""
import json

import pandas as pd

import build_employers
from fec.resolve.pipeline.locations import (
    address_cache_lookup,
    publishable_first_entry,
)


def _filings(rows):
    return pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "previous_employer": "", "employer_status": "active",
        **row,
    } for row in rows])


def _build(tmp_path, monkeypatch, filings, cache, geocodes=None, previous=None):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    filings.to_csv(cleaned, index=False)
    if previous is not None:
        pd.DataFrame(previous).to_csv(output, index=False)
    (tmp_path / "resolve_employer_addr.json").write_text(json.dumps(cache), encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text(json.dumps(geocodes or {}), encoding="utf-8")
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)
    build_employers.build()
    locations = pd.read_csv(output, dtype=str, keep_default_na=False, na_values=[])
    review_csv = tmp_path / build_employers.REVIEW_CSV
    review = (pd.read_csv(review_csv, dtype=str, keep_default_na=False, na_values=[])
              if review_csv.exists() else pd.DataFrame(columns=["reason", "employer_address"]))
    return locations, review


def _published(locations):
    return locations[locations["employer_address"].ne("")
                     & locations["address_trust"].isin({"verified", "grounded"})]


CLOSED_BOOK = {
    "employer_address": "7401 Wiles Rd", "employer_city": "Coral Springs",
    "employer_state": "FL", "employer_zip": "33067", "method": "ai_openai",
}
GROUNDED = {
    "employer_address": "555 SE 6th Ave, Suite 200", "employer_city": "Delray Beach",
    "employer_state": "FL", "employer_zip": "33483", "method": "ai_openai_search",
}


def test_build_publishes_the_grounded_sibling_apply_uses(tmp_path, monkeypatch):
    cache = {"SCOTT FANE,CPA PA": CLOSED_BOOK, "SCOTT FANE CPA PA": GROUNDED}
    filings = _filings([{"contributor_employer": "SCOTT FANE,CPA PA", "contributor_state": "FL"}])

    locations, review = _build(tmp_path, monkeypatch, filings, cache)

    published = _published(locations)
    assert published["employer_address"].tolist() == ["555 SE 6TH AVE STE 200"]
    assert published["address_trust"].tolist() == ["grounded"]
    assert review.empty


def test_a_sibling_outside_the_donor_states_goes_to_review(tmp_path, monkeypatch):
    cache = {
        "WILLIAMS & CONNOLLY LLP": {**CLOSED_BOOK, "employer_state": "MD", "employer_zip": "20814",
                                    "employer_city": "Bethesda"},
        "WILLIAMS & CONNOLLY LLC": {
            "employer_address": "725 Twelfth Street, N.W", "employer_city": "Washington",
            "employer_state": "DC", "employer_zip": "20005", "method": "ai_openai_search",
        },
    }
    filings = _filings([{"contributor_employer": "WILLIAMS & CONNOLLY LLP", "contributor_state": "MD"}])

    locations, review = _build(tmp_path, monkeypatch, filings, cache)

    assert _published(locations).empty
    # the full lookup's own answer stays, as before (closed-book: not published)
    assert locations["employer_state"].tolist() == ["MD"]
    assert review["reason"].tolist() == [build_employers.REVIEW_ALIAS_OUTSIDE_DONOR_STATES]
    assert review.loc[0, "employer_address"] == "725 Twelfth Street, N.W"
    assert review.loc[0, "donor_states"] == "MD"


def test_an_explicit_not_found_still_blocks_a_sibling(tmp_path, monkeypatch):
    # the tested F2 rule is unchanged: an exact ai_not_found beats a canonical sibling
    cache = {
        "SURFACE TECHNOLOGY INC": {"employer_address": "", "method": "ai_not_found"},
        "SURFACE TECHNOLOGY, INC": {**GROUNDED, "employer_state": "NJ"},
    }
    filings = _filings([{"contributor_employer": "SURFACE TECHNOLOGY INC", "contributor_state": "NJ"}])

    locations, review = _build(tmp_path, monkeypatch, filings, cache)

    assert _published(locations).empty
    assert review.empty


def test_publishable_first_entry_keeps_the_full_entry_when_they_agree():
    cache = {"BIG FIRM": {**GROUNDED, "locations": [CLOSED_BOOK]}}
    entry, changed = publishable_first_entry(
        address_cache_lookup(cache, publishable_only=True), address_cache_lookup(cache), "BIG FIRM",
    )
    assert not changed
    assert entry["locations"] == [CLOSED_BOOK]   # the unpublished extra office stays in the CSV


def test_a_carried_over_row_without_a_source_is_not_published(tmp_path, monkeypatch):
    previous = [{
        "employer_name": "HERZL-NER TAMID CONSERVATIVE CONGREGATION",
        "employer_address": "3700 W TOUHY AVE", "employer_city": "SKOKIE",
        "employer_state": "IL", "employer_zip": "60076",
        "employer_latitude": "42.01", "employer_longitude": "-87.72",
        "is_primary": True, "address_source": "manual", "address_trust": "verified",
    }]
    filings = _filings([{"contributor_employer": "HERZL-NER TAMID CONSERVATIVE CONGREGATION",
                         "contributor_state": "WA"}])

    first, review = _build(tmp_path, monkeypatch, filings, {}, previous=previous)

    assert _published(first).empty
    assert first.loc[0, "address_trust"] == "uncorroborated"
    assert first.loc[0, "employer_address"] == "3700 W TOUHY AVE"   # kept for reference only
    assert review["reason"].tolist() == [build_employers.REVIEW_NO_CURRENT_SOURCE]
    assert review.loc[0, "method"] == "previous:verified"

    # the next build keeps it unpublished and still lists it
    second, second_review = _build(tmp_path, monkeypatch, filings, {})
    pd.testing.assert_frame_equal(first, second)
    assert second_review["reason"].tolist() == [build_employers.REVIEW_NO_CURRENT_SOURCE]
