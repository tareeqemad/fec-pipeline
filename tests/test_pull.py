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


def _filing(sub_id, name="DONOR, ONE", **fields):
    return {"sub_id": sub_id, "committee_id": "C00000001", "two_year_transaction_period": 2026,
            "contributor_name": name, "contribution_receipt_date": "2026-02-03T12:00:00",
            "contribution_receipt_amount": "25.50", "entity_type": "ind", **fields}


def _saved(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions.csv"
    rows = [pull.build_row(_filing("1")),
            pull.build_row({**_filing("9"), "committee_id": "C00000002"})]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(pull.COLUMNS)
        writer.writerows(rows)
    monkeypatch.setattr(pull, "RAW_CSV", csv_path)
    monkeypatch.setattr(pull, "build_session", lambda: None)
    monkeypatch.setenv("FEC_API_KEY", "test")
    return csv_path


def _pages(monkeypatch, *pages):
    monkeypatch.setattr(pull, "iter_pages", lambda *_args: iter(pages))


def test_run_replaces_one_scope_and_skips_actblue_memos(tmp_path, monkeypatch):
    csv_path = _saved(tmp_path, monkeypatch)
    results = [_filing("2", name="JOSÉ TEST"), _filing("3", name="ACTBLUE", memo_code="X"),
               _filing("4", name="DONOR, TWO", memo_code="X")]
    _pages(monkeypatch, {"results": results, "pagination": {"count": 3, "is_count_exact": True}},
           {"results": [], "pagination": {}})

    pull.run("C00000001", 2026)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in rows] == ["9", "2", "4"]
    assert rows[1]["contributor_name"] == "JOSÉ TEST" and "memo_code" not in rows[1]
    with pull.source_path(csv_path).open(encoding="utf-8", newline="") as handle:
        sources = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in sources] == ["2", "4"]
    assert sources[0]["fec_entity_type"] == "IND"


@pytest.mark.parametrize("pages", [
    ({"results": [], "pagination": {}},),
    ({"results": [_filing("2")], "pagination": {"count": 2, "is_count_exact": True}},
     {"results": [], "pagination": {}}),
])
def test_run_saves_nothing_from_an_empty_or_short_pull(tmp_path, monkeypatch, pages):
    csv_path = _saved(tmp_path, monkeypatch)
    before = csv_path.read_bytes()
    _pages(monkeypatch, *pages)
    with pytest.raises(fec_api.PullError):
        pull.run("C00000001", 2026)
    assert csv_path.read_bytes() == before


def test_iter_pages_stops_when_fec_ends_early(monkeypatch):
    class Limiter:
        pass

    monkeypatch.setattr(pull, "fetch_page", lambda *_args: {"results": [_filing("2")], "pagination": {}})
    with pytest.raises(fec_api.PullError):
        list(pull.iter_pages(None, {}, Limiter()))
