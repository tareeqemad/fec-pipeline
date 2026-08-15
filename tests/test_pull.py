import csv
from importlib import import_module

import pytest
from requests.exceptions import ConnectTimeout

import fec.pull as pull

pull_cli = import_module("pull")


class Response:
    status_code = 200
    text = ""
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": []}


def test_fetch_page_retries_once(monkeypatch):
    actions = [ConnectTimeout(), Response()]
    delays = []

    class Session:
        def get(self, *args, **kwargs):
            action = actions.pop(0)
            if isinstance(action, Exception):
                raise action
            return action

    class Limiter:
        def wait(self):
            pass

    monkeypatch.setattr(pull, "sleep", delays.append)

    assert pull.fetch_page(Session(), {"api_key": "secret"}, Limiter()) == {"results": []}
    assert delays == [1]


def test_fetch_page_waits_through_rate_limit_window(monkeypatch):
    limited = []
    for _ in range(7):
        response = Response()
        response.status_code = 429
        limited.append(response)
    actions = limited + [Response()]
    delays = []

    class Session:
        def get(self, *args, **kwargs):
            return actions.pop(0)

    class Limiter:
        def wait(self):
            pass

    monkeypatch.setattr(pull, "sleep", delays.append)

    assert pull.fetch_page(Session(), {"api_key": "secret"}, Limiter()) == {
        "results": []
    }
    assert len(delays) == 7


def test_fetch_page_stops_after_rate_limit_timeout(monkeypatch):
    response = Response()
    response.status_code = 429

    class Session:
        def get(self, *args, **kwargs):
            return response

    class Limiter:
        def wait(self):
            pass

    monkeypatch.setattr(pull, "RATE_LIMIT_MAX_WAIT", 2)
    monkeypatch.setattr(pull, "sleep", lambda _delay: None)

    with pytest.raises(pull.PullError, match="rate limit did not clear"):
        pull.fetch_page(Session(), {"api_key": "secret"}, Limiter())


def test_iter_pages_passes_the_fec_cursor(monkeypatch):
    pages = [
        {
            "results": [{"sub_id": "1"}],
            "pagination": {"last_indexes": {
                "last_contribution_receipt_date": "2026-01-01",
                "last_index": "abc",
            }},
        },
        {"results": [], "pagination": {}},
    ]
    requests_seen = []

    def fake_fetch(_session, params, _limiter):
        requests_seen.append(dict(params))
        return pages.pop(0)

    monkeypatch.setattr(pull, "fetch_page", fake_fetch)

    assert len(list(pull.iter_pages(None, {"committee_id": "C1"}, None))) == 2
    assert "last_index" not in requests_seen[0]
    assert requests_seen[1]["last_index"] == "abc"


def test_run_appends_only_new_sub_ids(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions.csv"
    existing = pull.build_row({
        "sub_id": "1",
        "committee_id": "C00000001",
        "two_year_transaction_period": 2026,
        "contribution_receipt_date": "2026-01-01T00:00:00",
        "contribution_receipt_amount": 10,
    })
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(pull.COLUMNS)
        writer.writerow(existing)

    params_seen = {}

    def fake_pages(_session, params, _limiter):
        params_seen.update(params)
        yield {"results": [
            {"sub_id": "1", "committee_id": "C00000001"},
            {
                "sub_id": "2",
                "committee_id": "C00000001",
                "contributor_name": "JOSÉ TEST",
                "contribution_receipt_date": "2026-02-03T12:00:00",
                "contribution_receipt_amount": "25.50",
            },
            {"committee_id": "C00000001"},
        ], "pagination": {}}

    class FakeSession:
        def get(self, *args, **kwargs):
            raise AssertionError("iter_pages should be mocked")

    monkeypatch.setattr(pull, "RAW_CSV", csv_path)
    monkeypatch.setattr(pull, "build_session", FakeSession)
    monkeypatch.setattr(pull, "iter_pages", fake_pages)
    monkeypatch.setenv("FEC_API_KEY", "test")

    pull.run("C00000001", 2026)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["sub_id"] for row in rows] == ["1", "2"]
    assert rows[1]["contributor_name"] == "JOSÉ TEST"
    assert rows[1]["contributor_year"] == "2026"
    assert params_seen["min_date"] == "2026-01-01"


@pytest.mark.parametrize("sub_id", [None, "", "   "])
def test_build_row_rejects_blank_sub_id(sub_id):
    assert pull.build_row({"sub_id": sub_id}) is None


def test_permanent_api_error_raises_pull_error():
    response = Response()
    response.status_code = 403

    class Session:
        def get(self, *args, **kwargs):
            return response

    class Limiter:
        def wait(self):
            pass

    with pytest.raises(pull.PullError, match="invalid or expired"):
        pull.fetch_page(Session(), {"api_key": "bad"}, Limiter())


def test_cli_pulls_one_configured_committee(monkeypatch):
    calls = []
    tracked = {"C00000001": "ONE", "C00000002": "TWO"}

    monkeypatch.setattr(pull_cli, "committee_id_to_name", lambda: tracked)
    monkeypatch.setattr(
        pull_cli,
        "pull_run",
        lambda **kwargs: calls.append(kwargs),
    )

    from fec import env
    monkeypatch.setattr(env, "load_env", lambda: None)
    monkeypatch.setenv("FEC_API_KEY", "test")

    assert pull_cli.main(["C00000001", "--period", "2025"]) == 0
    assert [call["committee_id"] for call in calls] == ["C00000001"]
    assert {call["period"] for call in calls} == {2026}


def test_cli_missing_api_key_returns_error(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pull_cli, "committee_id_to_name", lambda: {"C00000001": "ONE"}
    )
    def missing_key(**kwargs):
        calls.append(kwargs)
        pull.required_env("FEC_API_KEY")

    monkeypatch.setattr(pull_cli, "pull_run", missing_key)

    from fec import env
    monkeypatch.setattr(env, "load_env", lambda: None)
    monkeypatch.delenv("FEC_API_KEY", raising=False)

    assert pull_cli.main(["C00000001"]) == 2
    assert len(calls) == 1


def test_cli_full_rechecks_selected_period(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pull_cli,
        "committee_id_to_name",
        lambda: {"C00000001": "ONE"},
    )
    monkeypatch.setattr(pull_cli, "pull_run", lambda **kwargs: calls.append(kwargs))

    from fec import env
    monkeypatch.setattr(env, "load_env", lambda: None)

    assert pull_cli.main(["C00000001", "--period", "2024", "--full"]) == 0
    assert calls == [{
        "committee_id": "C00000001",
        "period": 2024,
        "full": True,
    }]


def test_full_pull_omits_latest_date_filter():
    params = pull._pull_params(
        "key",
        "C00000001",
        2024,
        "2024-12-01",
        True,
    )

    assert "min_date" not in params


def test_committee_state_is_scoped_to_period(tmp_path):
    csv_path = tmp_path / "contributions.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pull.COLUMNS)
        writer.writeheader()
        writer.writerow({
            "sub_id": "2024-row",
            "committee_id": "C00000001",
            "two_year_transaction_period": "2024",
            "contribution_receipt_date": "2024-12-01",
        })
        writer.writerow({
            "sub_id": "2026-row",
            "committee_id": "C00000001",
            "two_year_transaction_period": "2026",
            "contribution_receipt_date": "2026-05-31",
        })

    ids, latest = pull.read_committee_state(csv_path, "C00000001", 2024)

    assert ids == {"2024-row"}
    assert latest == "2024-12-01"
