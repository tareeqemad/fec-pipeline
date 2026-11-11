"""Tests of the database stage: addresses, employments and the load transaction."""

import re

import numpy as np
import pandas as pd
import pytest

from fec.common.settings import SCHEMA_SQL
from fec.database import addresses, employments
from fec.database.addresses import load_addresses
from fec.database.connection import load_transaction
from fec.database.employments import load_employers, load_employments


def _schema_unique_columns() -> tuple[str, ...]:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE donor_employments \((.*?)\n\);", text, re.S).group(1)
    columns = re.search(r"UNIQUE NULLS NOT DISTINCT \(([^)]*)\)", table).group(1)
    return tuple(column.strip() for column in columns.split(","))


class FakeDatabase:
    """Tables as lists of rows; a selected column that was never inserted is the row's serial ID."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self._result = []

    def insert(self, sql, rows):
        table, columns = re.search(r"INSERT INTO (\w+) \(([^)]*)\)", sql, re.S).groups()
        columns = [column.strip() for column in columns.split(",")]
        stored = self.tables.setdefault(table, [])
        stored.extend(dict(zip(columns, row)) for row in rows)
        if table == "donor_employments":  # the UNIQUE NULLS NOT DISTINCT key of schema.sql
            keys = [tuple(row[column] for column in _schema_unique_columns()) for row in stored]
            assert len(keys) == len(set(keys))

    def execute(self, sql, params=None):
        columns, table = re.search(r"SELECT (.*) FROM (\w+)", " ".join(sql.split())).groups()
        columns = re.sub(r"COALESCE\((\w+), ''\)", r"\1 or ''", columns).split(", ")
        self._result = [
            tuple(self._read(row, serial, column) for column in columns)
            for serial, row in enumerate(self.tables.get(table, []), 1)
        ]

    @staticmethod
    def _read(row, serial, column):
        if column.endswith(" or ''"):
            return row[column.removesuffix(" or ''")] or ""
        return row.get(column, serial)

    def fetchall(self):
        return self._result


@pytest.fixture
def db(monkeypatch):
    fake = FakeDatabase()
    for module in (addresses, employments):
        monkeypatch.setattr(module, "execute_values", lambda _cur, sql, rows, **_kw: fake.insert(sql, rows))
    return fake


def _filing(sub_id, date, employer, occupation, status, previous=None):
    return {
        "sub_id": sub_id,
        "entity_type": "INDIVIDUAL",
        "donor_key": "D1",
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
        "latitude": 37.1,
        "longitude": -122.1,
    }


def test_donor_path_values_are_unchanged(db):
    filings = pd.DataFrame([_filing("1", "2022-03-03", "SELF-EMPLOYED", "ATTORNEY", "self_employed")])

    address_ids = load_addresses(db, filings, [])

    assert [tuple(row.values()) for row in db.tables["addresses"]] == [
        ("1 HOME ST", None, "SOMEWHERE", "CA", "94104", 37.1, -122.1)
    ]
    assert address_ids == {("1 HOME ST", "", "SOMEWHERE", "CA", "94104"): 1}
    assert filings["address_id"].tolist() == [1]


def _load(db, filings):
    """Run the employer and employment loads and return each filing's employment row."""
    frame = pd.DataFrame(filings).assign(donor_id=1, occupation_category_id=1, address_id=17)
    load_employers(db, frame)
    load_employments(db, frame, [], {})
    rows = db.tables["donor_employments"]
    return {sub_id: rows[serial - 1] for sub_id, serial in zip(frame["sub_id"], frame["donor_employment_id"])}


def test_statuses_sharing_a_null_employer_keep_their_own_rows(db):
    """ABELSON: 15 SELF-EMPLOYED ATTORNEY filings, then one RETIRED ATTORNEY filing."""
    linked = _load(
        db,
        [
            _filing("1", "2022-03-03", "SELF-EMPLOYED", "ATTORNEY", "self_employed"),
            _filing("2", "2024-10-24", "SELF-EMPLOYED", "ATTORNEY", "self_employed"),
            _filing("3", "2026-04-07", "RETIRED", "ATTORNEY", "retired"),
        ],
    )

    assert len(db.tables["donor_employments"]) == 2
    assert linked["1"]["employer_status"] == "self_employed"
    assert linked["2"]["employer_status"] == "self_employed"
    assert linked["3"]["employer_status"] == "retired"
    assert linked["1"] is linked["2"]
    assert linked["1"]["address_id"] == 17  # self-employed: reported address
    assert linked["3"]["address_id"] is None


def test_each_retired_filing_keeps_its_own_previous_employer(db):
    """MARGOLIN, RUTH: retired in 2022; named her clinic in 2026."""
    linked = _load(
        db,
        [
            _filing("1", "2022-02-02", "RETIRED", "RETIRED", "retired"),
            _filing("2", "2022-09-28", "RETIRED", "RETIRED", "retired"),
            _filing("3", "2026-05-03", "RETIRED", "RETIRED", "retired", previous="CLINIC"),
        ],
    )

    assert db.tables["employers"] == [{"name": "CLINIC"}]
    assert len(db.tables["donor_employments"]) == 2
    assert linked["1"]["previous_employer_id"] is None
    assert linked["2"] is linked["1"]
    assert linked["3"]["previous_employer_id"] == 1


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_a_late_failure_rolls_back_the_drop_too():
    conn = Connection()
    with pytest.raises(ValueError), load_transaction(conn):
        # the old tables are already dropped inside the transaction when a later step fails
        raise ValueError("bad row")
    assert conn.commits == 0 and conn.rollbacks == 1


def test_a_finished_load_is_committed():
    conn = Connection()
    with load_transaction(conn):
        pass
    assert conn.commits == 1 and conn.rollbacks == 0
