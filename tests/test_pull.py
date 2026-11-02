import csv
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import clean
import fec.fec_api as fec_api
import fec.pull as pull
from fec.pull_rows import COLUMNS, ENTITY_FIELDS, build_row
from fec.receipts import read_current_receipts


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


AIPAC = "C00797670"
DMFI = "C00710848"
TX = "A7BF92F171DCE448DB38"
OLD_ID = "4030420261386077053"
NEW_ID = "4042420261483447659"


def _receipt(sub_id, **overrides):
    return {
        "sub_id": sub_id, "committee_id": AIPAC, "two_year_transaction_period": 2026,
        "transaction_id": TX, "contribution_receipt_date": "2025-12-31",
        "contribution_receipt_amount": "12.50", "entity_type": "IND", "is_individual": True,
        "contributor_name": "TEST, JOSÉ", "contributor_first_name": "JOSÉ",
        "contributor_last_name": "TEST", "contributor_city": "CHICAGO",
        "contributor_state": "IL", "contributor_zip": "60601", "contributor_street_1": "1 MAIN ST",
        "contributor_employer": "ACME LLP", "contributor_occupation": "ATTORNEY", **overrides,
    }


def _page(*receipts, cursor=None):
    return {"results": list(receipts), "pagination": {"last_indexes": cursor}}


def _cursor(sub_id):
    return {"last_index": sub_id, "last_contribution_receipt_date": "2025-12-31"}


def _scan(monkeypatch, *pages):
    responses = iter(pages)
    calls = []

    def fetch(_session, params, _limiter):
        calls.append(dict(params))
        page = next(responses)
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(pull, "fetch_page", fetch)
    pull.run(AIPAC, 2026)
    return calls


@pytest.fixture
def raw_path(tmp_path, monkeypatch):
    path = tmp_path / "contributions.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        csv.writer(file).writerows([
            COLUMNS, build_row(_receipt(OLD_ID)),
            build_row(_receipt("2", transaction_id="withdrawn")),
            build_row(_receipt("3", committee_id=DMFI)),
            build_row(_receipt("4", two_year_transaction_period=2024, contribution_receipt_date="2024-01-01")),
        ])
    monkeypatch.setattr(pull, "RAW_CSV", path)
    monkeypatch.setenv("FEC_API_KEY", "test")
    return path


def test_amendment_replaces_only_the_current_scope_and_clean_counts_it_once(raw_path, monkeypatch):
    original_file = raw_path.read_bytes()
    assert set(read_current_receipts(raw_path)["sub_id"]) == {OLD_ID, "2", "3", "4"}
    amended = _receipt(NEW_ID, image_number="202604149857195889")
    # A duplicate across pages is the same FEC item, while a separate report may reuse TX.
    separate = _receipt("5", contribution_receipt_amount="30.00")
    calls = _scan(monkeypatch, _page(amended, cursor=_cursor(NEW_ID)),
                  _page(amended, separate, cursor=_cursor("5")), _page())
    assert calls[1]["last_index"] == NEW_ID
    current = read_current_receipts(raw_path).set_index("sub_id")
    assert current.index.is_unique
    assert set(current.index) == {NEW_ID, "3", "4", "5"}
    assert current.loc[NEW_ID, "fec_image_number"] == "202604149857195889"
    assert current.loc[NEW_ID, "fec_entity_type"] == "IND"

    changed = _receipt("6", contribution_receipt_amount="19.75", contributor_name="TEST, JOSÉ ALBERTO")
    new_page = _page(changed, separate, cursor=_cursor("5"))
    for _ in range(2):
        _scan(monkeypatch, new_page, _page())
        current = read_current_receipts(raw_path).set_index("sub_id")
        assert current.index.is_unique
        assert set(current.index) == {"3", "4", "5", "6"}
        assert current.loc["6", "contributor_name"] == "TEST, JOSÉ ALBERTO"
        assert float(current.loc["6", "contribution_receipt_amount"]) == 19.75
    assert raw_path.read_bytes() == original_file

    monkeypatch.setattr(clean, "RAW_CSV", raw_path)
    monkeypatch.setattr(clean, "CLEANED_CSV", raw_path.parent / "cleaned.csv")
    monkeypatch.setattr(clean, "REPORTS_DIR", raw_path.parent / "reports")
    monkeypatch.setattr(clean, "DONOR_KEYS_CSV", raw_path.parent / "donor_keys.csv")
    output = clean.main()
    assert set(output["sub_id"]) == {"3", "4", "5", "6"}
    assert output["contribution_receipt_amount"].sum() == 74.75

    _scan(monkeypatch, _page())
    assert set(read_current_receipts(raw_path)["sub_id"]) == {"3", "4"}


def test_new_scan_uses_current_fields_even_if_the_filing_id_stays_the_same(raw_path, monkeypatch):
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(pull, "datetime", clock)
    monkeypatch.setattr(pull, "monotonic_ns", iter([100, 101]).__next__)
    for amount, name, line in [("12.50", "TEST, JOSÉ", "11AI"), ("19.75", "TEST, JOSÉ ALBERTO", "15")]:
        row = _receipt(OLD_ID, contribution_receipt_amount=amount, contributor_name=name,
                       filing_form="F3X", line_number=line, memoed_subtotal=False)
        calls = _scan(monkeypatch, _page(row, cursor=_cursor(OLD_ID)), _page())
        assert {"filing_form", "line_number", "memo_code", "memoed_subtotal"} <= set(calls[0]["fields"].split(","))

    current = read_current_receipts(raw_path).set_index("sub_id", verify_integrity=True)
    assert set(current.index) == {OLD_ID, "3", "4"}
    assert current.loc[OLD_ID, "contributor_name"] == "TEST, JOSÉ ALBERTO"
    assert current.loc[OLD_ID, "contribution_receipt_amount"] == 19.75
    assert current.loc[OLD_ID, "receipt_kind"] == "operating_offset"
    snapshots = sorted((raw_path.parent / "snapshots" / f"{AIPAC}-2026").glob("*.csv"))
    assert [name.stem.split("-")[1] for name in snapshots] == [f"{n:020d}" for n in (100, 101)]
    for snapshot, expected in zip(snapshots, ["contribution", "operating_offset"]):
        with snapshot.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            assert reader.fieldnames == [*COLUMNS, *ENTITY_FIELDS, "receipt_kind"]
            assert next(reader)["receipt_kind"] == expected


@pytest.mark.parametrize("bad_page", [
    {"pagination": {}},                                 # missing results is not an empty scope
    _page(_receipt(""), cursor=_cursor("11")),           # missing filing ID
    _page(_receipt("11", committee_id=DMFI), cursor=_cursor("11")),
    _page(_receipt("11", two_year_transaction_period=2024), cursor=_cursor("11")),
    _page(_receipt("11", contribution_receipt_amount="invalid"), cursor=_cursor("11")),
    _page(_receipt("11", contribution_receipt_date=None), cursor=_cursor("11")),
    _page(_receipt("11", contribution_receipt_date="2026-02-30"), cursor=_cursor("11")),
    _page(_receipt("11")),                              # nonempty page without cursor
    _page(_receipt("10", contributor_name="CONFLICT"), cursor=_cursor("11")),
    _page(_receipt("11"), cursor=_cursor("10")),
    {"results": [], "pagination": {"is_count_exact": True, "count": 2}},
    fec_api.PullError("network interruption"),
])
def test_failed_scan_keeps_previous_complete_snapshot(raw_path, monkeypatch, bad_page):
    _scan(monkeypatch, _page(_receipt(NEW_ID), cursor=_cursor(NEW_ID)), _page())
    directory = raw_path.parent / "snapshots" / f"{AIPAC}-2026"
    before = {p.name: p.read_bytes() for p in directory.glob("*.csv")}
    assert before
    with pytest.raises((ValueError, fec_api.PullError)):
        _scan(monkeypatch, _page(_receipt("10"), cursor=_cursor("10")), bad_page, _page())
    assert {p.name: p.read_bytes() for p in directory.glob("*.csv")} == before
    assert set(read_current_receipts(raw_path)["sub_id"]) == {NEW_ID, "3", "4"}


def test_changed_filing_line_during_scan_still_aborts_if_both_lines_mean_contribution(raw_path, monkeypatch):
    receipt = _receipt(OLD_ID, filing_form="F3X", line_number="11AI", memo_code=None)
    changed = {**receipt, "line_number": "11C"}
    with pytest.raises(fec_api.PullError, match="changed during the scan"):
        _scan(monkeypatch, _page(receipt, cursor=_cursor(OLD_ID)), _page(changed, cursor=_cursor("5")), _page())
    assert not (raw_path.parent / "snapshots").exists()


def test_pagination_switches_to_fec_null_date_cursor(raw_path, monkeypatch):
    null_cursor = {"last_index": "12", "sort_null_only": True}
    calls = _scan(monkeypatch, _page(_receipt("11"), cursor=_cursor("11")),
                  _page(_receipt("12"), cursor=null_cursor), _page())
    assert calls[-1]["last_index"] == "12"
    assert calls[-1]["sort_null_only"] is True
    assert "last_contribution_receipt_date" not in calls[-1]
