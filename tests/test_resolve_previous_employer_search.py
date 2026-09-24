"""resolve keeps self-employment as work history and reads every FEC page before 'not found'."""
import pandas as pd

from fec.resolve.pipeline.steps import previous_employer as step


class FakeCache(dict):
    def put(self, key, value):
        self[key] = value

    def discard(self, key):
        self.pop(key, None)

    def save(self):
        pass


def test_a_retirees_self_employment_is_kept_when_the_cache_is_empty():
    rows = pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "donor_key": "D1", "contributor_name": "DOE, JANE",
        "contributor_state": "NY", "contributor_employer": "RETIRED",
        "contributor_occupation": "RETIRED", "occupation_category": "RETIRED",
        "previous_employer": "SELF-EMPLOYED", "contribution_receipt_date": "2025-01-01",
    }])
    cache = FakeCache()

    step.step_cross_record(rows, cache)

    assert cache["donor:D1"]["employer"] == "SELF-EMPLOYED"


class Response:
    def __init__(self, results, last_indexes=None):
        self.status_code = 200
        self._body = {"results": results, "pagination": {"last_indexes": last_indexes}}

    def json(self):
        return self._body


def _filing(employer):
    return {"contributor_name": "DOE, JANE", "contributor_state": "NY", "contributor_zip": "10001",
            "contributor_employer": employer, "contributor_occupation": "RETIRED" if employer == "RETIRED" else "CEO",
            "contribution_receipt_date": "2020-01-01"}


PERSON = {"prev_key": "donor:D1", "name": "DOE, JANE", "state": "NY", "city": "NEW YORK", "zip": "10001"}


def test_the_employer_on_the_second_page_is_found(monkeypatch):
    monkeypatch.setattr(step, "FEC_PAGE_SIZE", 2)
    pages = [
        Response([_filing("RETIRED"), _filing("RETIRED")], {"last_index": "9"}),
        Response([_filing("ACME INDUSTRIES")]),
    ]
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        return pages[len(calls) - 1]

    key, entry = step._fetch_fec_previous_employer(PERSON, "k", get)

    assert entry["employer"] == "ACME INDUSTRIES"
    assert calls[1]["last_index"] == "9"


def test_not_found_is_recorded_only_after_the_last_page(monkeypatch):
    monkeypatch.setattr(step, "FEC_PAGE_SIZE", 2)
    pages = [Response([_filing("RETIRED"), _filing("RETIRED")], {"last_index": "9"}), Response([])]
    key, entry = step._fetch_fec_previous_employer(PERSON, "k", lambda url, params, timeout: pages.pop(0))

    assert entry == {"employer": "", "method": "fec_api_not_found", "all_pages": True}


def test_an_old_first_page_only_not_found_is_searched_again():
    assert step._fec_search_due({"employer": "", "method": "fec_api_not_found"})
    assert not step._fec_search_due({"employer": "", "method": "fec_api_not_found", "all_pages": True})
    assert not step._fec_search_due({"employer": "ACME", "method": "fec_api"})
