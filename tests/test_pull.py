"""Tests of the pull stage: rate limits, retries, paging and appending only new filings."""

import csv

import pytest
import requests

from fec.pull import api, rows
from fec.pull import pipeline as pull_pipeline


class Response:
    def __init__(self, status_code=200, data=None, headers=None, text=""):
        self.status_code = status_code
        self.data = data if data is not None else {"results": []}
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Error for url: https://fec/?api_key=secret")

    def json(self):
        return self.data


class Session:
    """Answer each request with the next prepared response, remembering the parameters sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.params_sent = []
        self.headers = {}

    def get(self, url, params, timeout):
        assert url == api.BASE_URL and timeout == (10, 180)
        self.params_sent.append(dict(params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Limiter:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


@pytest.fixture
def delays(monkeypatch):
    """Replace sleeping by a list of the delays asked for."""
    asked = []
    monkeypatch.setattr(api, "sleep", asked.append)
    return asked


def test_fetch_page_stops_after_rate_limit_timeout(monkeypatch, delays):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX_WAIT", 2)
    session = Session(*[Response(429) for _ in range(5)])

    with pytest.raises(api.PullError, match="rate limit did not clear"):
        api.fetch_page(session, {"api_key": "secret"}, Limiter())
    assert delays == [1]  # the next wait, 2 seconds, would pass the limit


def test_fetch_page_waits_for_the_rate_limit_as_long_as_the_server_asks(delays):
    over_limit = Response(200, text='{"error": {"code": "OVER_RATE_LIMIT"}}')
    session = Session(Response(429, headers={"Retry-After": "30"}), over_limit, Response(200, {"results": [1]}))
    limiter = Limiter()

    assert api.fetch_page(session, {"api_key": "secret"}, limiter) == {"results": [1]}
    assert delays == [30, 2]  # Retry-After, then doubling from the second answer
    assert limiter.waits == 3  # every request start goes through the limiter


def test_fetch_page_retries_temporary_failures(delays):
    session = Session(
        requests.exceptions.ConnectionError("down"),
        Response(503, headers={"Retry-After": "7"}),
        Response(502, headers={"Retry-After": "soon"}),
        Response(200, {"results": [1]}),
    )

    assert api.fetch_page(session, {"api_key": "secret"}, Limiter()) == {"results": [1]}
    assert delays == [1, 7, 4]


def test_fetch_page_gives_up_after_six_failures(delays):
    with pytest.raises(api.PullError, match="FEC API returned HTTP 500 after 6 attempts"):
        api.fetch_page(Session(*[Response(500) for _ in range(6)]), {"api_key": "secret"}, Limiter())
    assert delays == [1, 2, 4, 8, 16]

    unreachable = [requests.exceptions.ReadTimeout("slow") for _ in range(6)]
    with pytest.raises(api.PullError, match="FEC API unavailable after 6 attempts"):
        api.fetch_page(Session(*unreachable), {"api_key": "secret"}, Limiter())


def test_fetch_page_reports_a_refused_request_without_the_key():
    for status, message in ((403, "key is invalid or expired"), (404, "was not found")):
        with pytest.raises(api.PullError, match=message):
            api.fetch_page(Session(Response(status)), {"api_key": "secret"}, Limiter())
    with pytest.raises(api.PullError) as raised:
        api.fetch_page(Session(Response(400)), {"api_key": "secret"}, Limiter())
    assert "api_key=***" in str(raised.value) and "secret" not in str(raised.value)


def test_iter_pages_follows_the_cursor_until_a_page_has_no_results_or_no_cursor():
    cursor = {"last_index": "230", "last_contribution_receipt_date": "2026-03-01"}
    first = {"results": [{"sub_id": "1"}], "pagination": {"last_indexes": cursor}}
    empty = {"results": [], "pagination": {"last_indexes": cursor}}
    session = Session(Response(200, first), Response(200, empty))
    params = {"api_key": "secret", "per_page": 100}

    assert list(api.iter_pages(session, params, Limiter())) == [first, empty]
    assert session.params_sent == [params, {**params, **cursor}]

    last = {"results": [{"sub_id": "2"}], "pagination": {"last_indexes": None}}
    assert list(api.iter_pages(Session(Response(200, last)), params, Limiter())) == [last]


def test_rate_limiter_spaces_request_starts(monkeypatch):
    clock = iter([1000.0, 1000.0, 1001.0, 1004.0])
    asked = []
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api, "sleep", asked.append)
    limiter = api.RateLimiter(15)
    limiter.wait()
    limiter.wait()
    assert asked == [3.0]  # 15 a minute is one every 4 seconds; 1 second had passed


def test_build_row_keeps_the_date_and_refuses_an_unreadable_amount():
    result = {"contribution_receipt_date": "2026-08-31T00:00:00", "contribution_receipt_amount": "10", "sub_id": 7}
    row = dict(zip(rows.COLUMNS, rows.build_row("7", result)))
    assert row["sub_id"] == "7" and row["contribution_receipt_date"] == "2026-08-31"
    assert row["contributor_year"] == 2026 and str(row["contribution_receipt_amount"]) == "10"

    undated = dict(zip(rows.COLUMNS, rows.build_row("7", {"contribution_receipt_amount": 5})))
    assert undated["contribution_receipt_date"] is None and undated["contributor_year"] is None

    for amount in (None, "", "ten", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="Invalid contribution_receipt_amount for sub_id 7"):
            rows.build_row("7", {"contribution_receipt_amount": amount})


def _filing(sub_id, **fields):
    return {
        "sub_id": sub_id,
        "committee_id": "C00000001",
        "two_year_transaction_period": 2026,
        "contribution_receipt_date": "2026-08-31T00:00:00",
        "contribution_receipt_amount": 10,
        **fields,
    }


def test_pull_committee_appends_only_new_sub_ids(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions.csv"
    source_path = tmp_path / "fec_source_fields.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(rows.COLUMNS)
        writer.writerow(rows.build_row("1", _filing("1")))

    late_filing = _filing(
        "2",
        contributor_name="JOSÉ TEST",
        contribution_receipt_date="2026-02-03T12:00:00",
        contribution_receipt_amount="25.50",
        entity_type="ind",
    )
    cursor = {"last_index": "2", "last_contribution_receipt_date": "2026-02-03"}
    pages = [
        {
            "results": [_filing("1"), late_filing, late_filing, {"committee_id": "C00000001"}],
            "pagination": {"count": 150, "last_indexes": cursor},
        },
        {"results": [late_filing], "pagination": {"count": 150}},
    ]
    sessions = []

    def fake_session():
        sessions.append(Session(*[Response(200, page) for page in pages]))
        return sessions[-1]

    monkeypatch.setattr(pull_pipeline, "RAW_CSV", csv_path)
    monkeypatch.setattr(pull_pipeline, "FEC_SOURCE_CSV", source_path)
    monkeypatch.setattr(pull_pipeline.requests, "Session", fake_session)
    monkeypatch.setenv("FEC_API_KEY", "test")
    monkeypatch.setenv("FEC_RPM", "600000")

    pull_pipeline.pull_committee("C00000001", 2026)
    pull_pipeline.pull_committee("C00000001", 2026)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        saved = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in saved] == ["1", "2"]
    assert saved[1]["contributor_name"] == "JOSÉ TEST"
    assert saved[1]["contribution_receipt_date"] == "2026-02-03"
    assert saved[1]["contributor_year"] == "2026"
    assert saved[0]["contribution_receipt_amount"] == "10"
    assert saved[1]["contribution_receipt_amount"] == "25.50"
    with source_path.open(encoding="utf-8", newline="") as handle:
        sources = list(csv.DictReader(handle))
    assert [(row["sub_id"], row["fec_entity_type"], row["matched_by"]) for row in sources] == [("2", "IND", "pulled")]

    first_request, second_request = sessions[0].params_sent
    assert first_request == {
        "api_key": "test",
        "committee_id": "C00000001",
        "two_year_transaction_period": 2026,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(rows.FIELDS),
    }
    assert second_request == {**first_request, **cursor}
    assert sessions[0].headers == {"User-Agent": "fec-pull/1.0", "Accept-Encoding": "gzip, deflate"}


def test_source_file_keeps_other_rows_when_appending(tmp_path):
    path = tmp_path / "fec_source_fields.csv"
    first = rows.build_source_row("1", {"entity_type": "IND", "image_number": "202410189698745485"})
    second = rows.build_source_row("5", {"entity_type": "ORG", "contributor_id": "C00000935"})
    pull_pipeline.append_source_fields(path, [first])
    pull_pipeline.append_source_fields(path, [])
    pull_pipeline.append_source_fields(path, [second])
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == rows.SOURCE_COLUMNS
        assert list(reader) == [first, second]
    assert first["fec_entity_type"] == "IND" and first["fec_image_number"] == "202410189698745485"
    assert second["fec_entity_type"] == "ORG" and second["fec_contributor_id"] == "C00000935"


def test_main_checks_the_committee_and_rounds_the_period(monkeypatch):
    pulled = []
    monkeypatch.setattr(pull_pipeline, "committee_id_to_name", lambda: {"C00000001": "TEST"})
    monkeypatch.setattr(
        pull_pipeline, "pull_committee", lambda committee_id, period: pulled.append((committee_id, period))
    )

    assert pull_pipeline.main(["c00000001", "--period", "2025"]) == 0
    assert pull_pipeline.main(["C00000001", "--period", "2026"]) == 0
    assert pulled == [("C00000001", 2026), ("C00000001", 2026)]
    with pytest.raises(SystemExit):
        pull_pipeline.main(["C00000002"])


def test_main_returns_2_when_the_pull_cannot_continue(monkeypatch):
    monkeypatch.setattr(pull_pipeline, "committee_id_to_name", lambda: {"C00000001": "TEST"})
    monkeypatch.delenv("FEC_API_KEY", raising=False)
    monkeypatch.setattr(pull_pipeline, "load_env", lambda: None)
    assert pull_pipeline.main(["C00000001"]) == 2


def test_fetch_page_reports_an_answer_that_is_not_json():
    class NotJson(Response):
        def json(self):
            raise requests.exceptions.JSONDecodeError("no JSON", "", 0)

    with pytest.raises(api.PullError, match="invalid JSON"):
        api.fetch_page(Session(NotJson()), {"api_key": "secret"}, Limiter())


def test_main_returns_1_when_the_pull_fails_for_another_reason(monkeypatch):
    def fail(committee_id, period):
        raise OSError("disk full")

    monkeypatch.setattr(pull_pipeline, "committee_id_to_name", lambda: {"C00000001": "TEST"})
    monkeypatch.setattr(pull_pipeline, "load_env", lambda: None)
    monkeypatch.setattr(pull_pipeline, "pull_committee", fail)
    assert pull_pipeline.main(["C00000001"]) == 1


def test_saved_ids_are_read_from_a_file_excel_saved_and_from_an_empty_one(tmp_path):
    saved = tmp_path / "contributions.csv"
    saved.write_text(
        "sub_id,committee_id,two_year_transaction_period\n7,C00000001,2026\n8,C00000002,2026\n", "utf-8-sig"
    )
    assert pull_pipeline.read_existing_ids(saved, "C00000001", 2026) == {"7"}
    saved.write_text("", "utf-8")
    assert pull_pipeline.read_existing_ids(saved, "C00000001", 2026) == set()
    assert pull_pipeline.read_existing_ids(tmp_path / "missing.csv", "C00000001", 2026) == set()
