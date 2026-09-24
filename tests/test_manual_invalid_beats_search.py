"""An INVALID manual row removes an AI web-search address (1901 PARTNERS kept 750 Third Ave)."""
from fec.resolve.pipeline.manual_overrides import _merge_manual_locations

SEARCH = {
    "employer_address": "750 Third Avenue, 29th Floor", "employer_city": "New York",
    "employer_state": "NY", "employer_zip": "10017", "method": "ai_openai_search",
    "locations": [{"employer_address": "1 Other St", "method": "ai_openai_search"}],
}


def _manual(method):
    return {"primary": {"employer_address": "", "employer_city": "", "employer_state": "",
                        "employer_zip": "", "method": method}, "extra": []}


def test_invalid_replaces_a_search_answer_and_its_other_offices():
    merged = _merge_manual_locations(SEARCH, _manual("manual_invalid"))

    assert merged["method"] == "manual_invalid"
    assert merged["employer_address"] == ""
    assert "locations" not in merged


def test_an_uncertain_manual_row_still_does_not_replace_a_search_answer():
    merged = _merge_manual_locations(SEARCH, _manual("manual_review"))

    assert merged["method"] == "ai_openai_search"
    assert merged["employer_address"] == "750 Third Avenue, 29th Floor"
