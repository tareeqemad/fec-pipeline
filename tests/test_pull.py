import csv

import pytest

from fec.pull import api
from fec.pull import pipeline as pull_pipeline


class Response:
    status_code = 200
    text = ""
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": []}


def test_fetch_page_stops_after_rate_limit_timeout(monkeypatch):
    response = Response()
    response.status_code = 429

    class Session:
        def get(self, *args, **kwargs):
            return response

    class Limiter:
        def wait(self):
            pass

    monkeypatch.setattr(api, "RATE_LIMIT_MAX_WAIT", 2)
    monkeypatch.setattr(api, "sleep", lambda _delay: None)

    with pytest.raises(api.PullError, match="rate limit did not clear"):
        api.fetch_page(Session(), {"api_key": "secret"}, Limiter())


def test_run_appends_only_new_sub_ids(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions.csv"
    existing = pull_pipeline.build_row(
        {
            "sub_id": "1",
            "committee_id": "C00000001",
            "two_year_transaction_period": 2026,
            "contribution_receipt_date": "2026-08-31T00:00:00",
            "contribution_receipt_amount": 10,
        }
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(pull_pipeline.COLUMNS)
        writer.writerow(existing)

    params_seen = {}

    def fake_pages(_session, params, _limiter):
        params_seen.update(params)
        late_filing = {
            "sub_id": "2",
            "committee_id": "C00000001",
            "two_year_transaction_period": 2026,
            "contributor_name": "JOSÉ TEST",
            "contribution_receipt_date": "2026-02-03T12:00:00",
            "contribution_receipt_amount": "25.50",
        }
        yield {
            "results": [
                {"sub_id": "1", "committee_id": "C00000001"},
                late_filing,
                late_filing,
                {"committee_id": "C00000001"},
            ],
            "pagination": {},
        }
        yield {"results": [late_filing], "pagination": {}}

    class FakeSession:
        def get(self, *args, **kwargs):
            raise AssertionError("iter_pages should be mocked")

    monkeypatch.setattr(pull_pipeline, "RAW_CSV", csv_path)
    monkeypatch.setattr(pull_pipeline, "build_session", FakeSession)
    monkeypatch.setattr(pull_pipeline, "iter_pages", fake_pages)
    monkeypatch.setenv("FEC_API_KEY", "test")

    pull_pipeline.pull_committee("C00000001", 2026)
    pull_pipeline.pull_committee("C00000001", 2026)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in rows] == ["1", "2"]
    assert rows[1]["contributor_name"] == "JOSÉ TEST"
    assert rows[1]["contributor_year"] == "2026"
    assert rows[0]["contribution_receipt_amount"] == "10"
    assert rows[1]["contribution_receipt_amount"] == "25.50"
    assert "min_date" not in params_seen
    with pull_pipeline.source_path(csv_path).open(encoding="utf-8", newline="") as handle:
        assert [row["sub_id"] for row in csv.DictReader(handle)] == ["2"]


def _raw(
    sub_id, name="AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE", tx="SA11AI.1", date="2024-10-18", amount="11000000.00"
):
    return {
        "sub_id": sub_id,
        "transaction_id": tx,
        "two_year_transaction_period": "2024",
        "committee_id": "C00797670",
        "contributor_name": name,
        "contribution_receipt_date": date,
        "contribution_receipt_amount": amount,
        "is_individual": "True",
    }


def _result(sub_id, entity="ORG", contributor_id="", **raw):
    return {**_raw(sub_id, **raw), "entity_type": entity, "contributor_id": contributor_id}


def test_source_file_keeps_other_rows_when_appending(tmp_path):
    path = tmp_path / "fec_source_fields.csv"
    first = pull_pipeline._source_record("1", {**_result("1", entity="IND"), "image_number": "202410189698745485"})
    second = pull_pipeline._source_record("5", _result("5", contributor_id="C00000935"))
    pull_pipeline.append_source_fields(path, [first])
    pull_pipeline.append_source_fields(path, [second])
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == pull_pipeline.SOURCE_COLUMNS
        assert list(reader) == [first, second]
    assert first["fec_entity_type"] == "IND" and first["fec_image_number"] == "202410189698745485"
    assert second["fec_entity_type"] == "ORG" and second["fec_contributor_id"] == "C00000935"
    assert pull_pipeline.source_path(tmp_path / "contributions.csv") == path
