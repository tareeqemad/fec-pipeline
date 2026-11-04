import csv
import pytest
import fec.fec_api as fec_api
import fec.pull as pull


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

    monkeypatch.setattr(fec_api, "RATE_LIMIT_MAX_WAIT", 2)
    monkeypatch.setattr(fec_api, "sleep", lambda _delay: None)

    with pytest.raises(fec_api.PullError, match="rate limit did not clear"):
        fec_api.fetch_page(Session(), {"api_key": "secret"}, Limiter())


def test_run_appends_only_new_transactions(tmp_path, monkeypatch, caplog):
    csv_path = tmp_path / "contributions.csv"
    existing = pull.build_row({
        "sub_id": "1",
        "transaction_id": "T1",
        "committee_id": "C00000001",
        "two_year_transaction_period": 2026,
        "contribution_receipt_date": "2026-08-31T00:00:00",
        "contribution_receipt_amount": 10,
    })
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(pull.COLUMNS)
        writer.writerow(existing)

    params_seen = {}

    def fake_pages(_session, params, _limiter):
        params_seen.update(params)
        late_filing = {
            "sub_id": "2",
            "transaction_id": "T2",
            "committee_id": "C00000001",
            "two_year_transaction_period": 2026,
            "contributor_name": "JOSÉ TEST",
            "contribution_receipt_date": "2026-02-03T12:00:00",
            "contribution_receipt_amount": "25.50",
        }
        yield {"results": [
            # An amendment: same transaction, new sub_id.
            {"sub_id": "7", "transaction_id": "T1", "contribution_receipt_amount": 10},
            {"sub_id": "8", "transaction_id": "T1", "contribution_receipt_amount": 12},
            late_filing, late_filing,
            {"committee_id": "C00000001"},
        ], "pagination": {}}
        yield {"results": [late_filing], "pagination": {}}

    class FakeSession:
        def get(self, *args, **kwargs):
            raise AssertionError("iter_pages should be mocked")

    monkeypatch.setattr(pull, "RAW_CSV", csv_path)
    monkeypatch.setattr(pull, "build_session", FakeSession)
    monkeypatch.setattr(pull, "iter_pages", fake_pages)
    monkeypatch.setenv("FEC_API_KEY", "test")

    pull.run("C00000001", 2026)
    pull.run("C00000001", 2026)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in rows] == ["1", "2"]
    assert rows[1]["contributor_name"] == "JOSÉ TEST"
    assert rows[1]["contributor_year"] == "2026"
    assert rows[0]["contribution_receipt_amount"] == "10"
    assert rows[1]["contribution_receipt_amount"] == "25.50"
    assert "min_date" not in params_seen
    assert "Amount changed for T1: 10 -> 12" in caplog.text
