"""A filing's employment status and previous employer survive the load.

RETIRED, SELF-EMPLOYED (no company), NOT EMPLOYED and blank employers all
resolve to employer_id NULL, so the employment identity must include
employer_status (audit: 38 filings of 14 donors showed another status).  A
retiree whose previous employer is the contract literal 'SELF-EMPLOYED' keeps
that fact in donor_employments.previous_self_employed (audit: 247 filings).
"""
import re

import numpy as np
import pandas as pd
import pytest

import fec.database.loader.employers as employer_loader
import fec.database.loader.previous_employers as previous_loader
from fec.database.loader.contributions import _map_employment_ids
from fec.database.loader.employers import (
    EMPLOYMENT_KEY_COLUMNS,
    _make_employer_resolver,
    employment_key,
    load_employments,
)
from fec.database.loader.previous_employers import (
    link_previous_employers,
    previous_self_employed_donors,
)
from fec.env import SCHEMA_SQL


def _schema_unique_columns() -> tuple[str, ...]:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE donor_employments \((.*?)\n\);", text, re.S).group(1)
    columns = re.search(r"UNIQUE NULLS NOT DISTINCT \(([^)]*)\)", table).group(1)
    return tuple(column.strip() for column in columns.split(","))


class FakeEmploymentDB:
    """donor_employments with the UNIQUE NULLS NOT DISTINCT key read from schema.sql."""

    def __init__(self):
        self.unique_columns = _schema_unique_columns()
        self.rows: list[dict] = []
        self._result = []

    def commit(self):
        pass

    def insert(self, sql, values):
        columns = [c.strip() for c in re.search(
            r"donor_employments\s*\(([^)]*)\)", sql, re.S).group(1).split(",")]
        keys = {tuple(row.get(c) for c in self.unique_columns) for row in self.rows}
        for value in values:
            row = dict(zip(columns, value))
            key = tuple(row.get(c) for c in self.unique_columns)
            if key not in keys:  # ON CONFLICT DO NOTHING
                keys.add(key)
                self.rows.append(row)

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        assert text.startswith("SELECT donor_employment_id,"), text
        columns = [c.strip() for c in text[len("SELECT "):text.index(" FROM ")].split(",")]
        self._result = [
            tuple(index if c == "donor_employment_id" else row.get(c) for c in columns)
            for index, row in enumerate(self.rows, 1)
        ]

    def fetchall(self):
        return self._result


@pytest.fixture
def db(monkeypatch):
    fake = FakeEmploymentDB()
    monkeypatch.setattr(employer_loader, "execute_values",
                        lambda _cur, sql, rows, **_kw: fake.insert(sql, rows))
    monkeypatch.setattr(employer_loader, "_count", lambda *_args: len(fake.rows))
    return fake


def _filing(sub_id, date, employer, occupation, status, previous=None, donor="D1"):
    return {
        "sub_id": sub_id,
        "entity_type": "INDIVIDUAL",
        "donor_key": donor,
        "contributor_employer": employer,
        "contributor_occupation": occupation,
        "occupation_category": "LEGAL",
        "employer_status": status,
        "previous_employer": previous if previous is not None else np.nan,
        "contribution_receipt_date": date,
        "contributor_street_1": "1 HOME ST",
        "contributor_street_2": np.nan,
        "contributor_city": "SOMEWHERE",
        "contributor_state": "CA",
        "contributor_zip": "94104",
    }


HOME = {("1 HOME ST", "", "SOMEWHERE", "CA", "94104"): 17}


def _load(db, filings, previous_ids=None, previous_self=None, employers=None):
    """Run load_employments + the contributions lookup; return each filing's linked row."""
    frame = pd.DataFrame(filings)
    donor_ids = {key: index for index, key in enumerate(sorted(frame["donor_key"].unique()), 1)}
    get_employer_id = _make_employer_resolver(employers or {})
    employment_ids = load_employments(
        db, db, frame, donor_ids, {"LEGAL": 1}, previous_ids or {},
        get_employer_id, HOME, [], previous_self,
    )
    rows = frame.assign(_donor_id=frame["donor_key"].map(donor_ids))
    _map_employment_ids(rows, employment_ids, get_employer_id)
    by_id = dict(enumerate(db.rows, 1))
    return {sub_id: by_id[int(employment_id)]
            for sub_id, employment_id in zip(rows["sub_id"], rows["_employment_id"])}


def test_schema_unique_key_is_the_loader_key():
    assert _schema_unique_columns() == EMPLOYMENT_KEY_COLUMNS
    assert "employer_status" in EMPLOYMENT_KEY_COLUMNS


def test_employment_key_treats_nan_as_null():
    assert employment_key(1.0, np.nan, np.nan, "retired") == (1, None, None, "retired")
    assert employment_key(np.int64(2), 5.0, "ATTORNEY", "active") == (2, 5, "ATTORNEY", "active")
    assert employment_key(1, None, None, "retired") != employment_key(1, None, None, "self_employed")


def test_statuses_sharing_a_null_employer_keep_their_own_rows(db):
    """ABELSON: 15 SELF-EMPLOYED ATTORNEY filings, then one RETIRED ATTORNEY filing."""
    linked = _load(db, [
        _filing("1", "2022-03-03", "SELF-EMPLOYED", "ATTORNEY", "self_employed"),
        _filing("2", "2024-10-24", "SELF-EMPLOYED", "ATTORNEY", "self_employed"),
        _filing("3", "2026-04-07", "RETIRED", "ATTORNEY", "retired"),
    ])

    assert len(db.rows) == 2
    assert linked["1"]["employer_status"] == "self_employed"
    assert linked["2"]["employer_status"] == "self_employed"
    assert linked["3"]["employer_status"] == "retired"
    assert linked["1"] is linked["2"]
    assert linked["1"]["address_id"] == 17  # self-employed: reported address


def test_retired_filing_keeps_previous_employer_self_employed_does_not(db):
    """AVIDAN / MINAS: the previous employer follows the retired filings only."""
    linked = _load(
        db,
        [
            _filing("1", "2023-04-13", "RETIRED", "MANAGING MEMBER", "retired",
                    previous="BAG INVESTMENTS"),
            _filing("2", "2026-07-30", "SELF-EMPLOYED", "MANAGING MEMBER", "self_employed"),
        ],
        previous_ids={"D1": 42},
        employers={"BAG INVESTMENTS": 42},
    )

    assert linked["1"]["employer_status"] == "retired"
    assert linked["1"]["previous_employer_id"] == 42
    assert linked["2"]["employer_status"] == "self_employed"
    assert linked["2"]["previous_employer_id"] is None


def test_not_employed_and_self_employed_stay_apart(db):
    linked = _load(db, [
        _filing("1", "2022-03-09", "NOT EMPLOYED", "MEDICAL DOCTOR", "not_employed"),
        _filing("2", "2024-06-07", "SELF-EMPLOYED", "MEDICAL DOCTOR", "self_employed"),
    ])

    assert linked["1"]["employer_status"] == "not_employed"
    assert linked["1"]["address_id"] is None
    assert linked["2"]["employer_status"] == "self_employed"


def test_same_status_still_collapses_to_one_row(db):
    linked = _load(db, [
        _filing("1", "2022-01-01", "RETIRED", "RETIRED", "retired"),
        _filing("2", "2023-01-01", "RETIRED", "RETIRED", "retired"),
        _filing("3", "2024-01-01", "RETIRED", "RETIRED", "retired"),
    ])

    assert len(db.rows) == 1
    assert {id(row) for row in linked.values()} == {id(db.rows[0])}


def test_filings_with_the_same_raw_text_but_another_status_get_a_row():
    rows = pd.DataFrame([
        _filing("1", "2025-01-01", "SELF-EMPLOYED", "CONSULTANT", "self_employed"),
        _filing("2", "2024-01-01", "SELF-EMPLOYED", "CONSULTANT", "missing"),
    ])

    latest = employer_loader._latest_employment_rows(rows)

    assert sorted(latest["employer_status"]) == ["missing", "self_employed"]


def test_self_employed_previous_employer_is_a_flag_on_retired_rows(db):
    filings = [
        _filing("1", "2024-01-01", "RETIRED", "RETIRED", "retired", previous="SELF-EMPLOYED"),
        _filing("2", "2025-01-01", "NOT EMPLOYED", "NOT EMPLOYED", "not_employed"),
    ]
    flagged = previous_self_employed_donors(pd.DataFrame(filings))
    linked = _load(db, filings, previous_self=flagged)

    assert flagged == {"D1"}
    assert linked["1"]["previous_self_employed"] is True
    assert linked["1"]["previous_employer_id"] is None
    assert linked["2"]["previous_self_employed"] is False


def test_rows_default_to_not_previously_self_employed(db):
    linked = _load(db, [_filing("1", "2024-01-01", "RETIRED", "RETIRED", "retired")])

    assert linked["1"]["previous_self_employed"] is False


def test_newest_previous_employer_decides_company_or_self_employed():
    older_self = pd.DataFrame([
        _filing("1", "2020-01-01", "RETIRED", "RETIRED", "retired", previous="SELF-EMPLOYED"),
        _filing("2", "2024-01-01", "RETIRED", "RETIRED", "retired", previous="ACME"),
    ])
    newer_self = pd.DataFrame([
        _filing("1", "2020-01-01", "RETIRED", "RETIRED", "retired", previous="ACME"),
        _filing("2", "2024-01-01", "RETIRED", "RETIRED", "retired", previous="SELF-EMPLOYED"),
    ])

    assert previous_self_employed_donors(older_self) == set()
    assert previous_self_employed_donors(newer_self) == {"D1"}


def test_previous_self_employed_ignores_non_retired_rows():
    frame = pd.DataFrame([
        _filing("1", "2024-01-01", "SELF-EMPLOYED", "ATTORNEY", "self_employed",
                previous="SELF-EMPLOYED"),
    ])

    assert previous_self_employed_donors(frame) == set()


def test_previous_employer_cannot_be_both_kinds(db):
    with pytest.raises(RuntimeError, match="both a company and SELF-EMPLOYED"):
        _load(
            db,
            [_filing("1", "2024-01-01", "RETIRED", "RETIRED", "retired", previous="ACME")],
            previous_ids={"D1": 42},
            previous_self={"D1"},
        )


class EmployerCursor:
    def __init__(self):
        self.names: list[str] = []

    def commit(self):
        pass

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [(index, name) for index, name in enumerate(self.names, 1)]


def test_self_employed_never_becomes_an_employer_row(monkeypatch):
    cursor = EmployerCursor()
    monkeypatch.setattr(
        previous_loader, "execute_values",
        lambda _cur, _sql, rows, **_kw: cursor.names.extend(name for (name,) in rows),
    )
    frame = pd.DataFrame([
        _filing("1", "2024-01-01", "RETIRED", "RETIRED", "retired",
                previous="SELF-EMPLOYED", donor="D1"),
        _filing("2", "2024-01-01", "RETIRED", "RETIRED", "retired",
                previous="ACME", donor="D2"),
    ])

    mapped = link_previous_employers(cursor, cursor, frame, {})

    assert cursor.names == ["ACME"]
    assert mapped == {"D2": 1}


def test_other_non_company_previous_values_are_reported(monkeypatch, caplog):
    cursor = EmployerCursor()
    monkeypatch.setattr(previous_loader, "execute_values", lambda *_a, **_kw: None)
    frame = pd.DataFrame([
        _filing("1", "2024-01-01", "RETIRED", "RETIRED", "retired", previous="RETIRED"),
    ])

    with caplog.at_level("WARNING"):
        assert link_previous_employers(cursor, cursor, frame, {}) == {}

    assert "non-company value(s) not stored: 'RETIRED'" in caplog.text
