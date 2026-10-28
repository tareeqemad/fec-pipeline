import numpy as np
import pandas as pd
import fec.database.loader.addresses as address_loader
from fec.database.loader.addresses import load_address_dimension
import re
import pytest
import fec.database.loader.employers as employer_loader
from fec.database.loader.contributions import _map_employment_ids
from fec.database.loader.employers import _make_employer_resolver, load_employments
from fec.env import SCHEMA_SQL
from fec.database.loader._base import load_transaction, stage


class AddressCursor:
    def __init__(self):
        self.rows: list[tuple] = []
        self._result = []

    def commit(self):
        pass

    def execute(self, sql, params=None):
        # the loader reads addresses back with COALESCE(col, '')
        self._result = [
            (index, *[value if value is not None else "" for value in row[:5]])
            for index, row in enumerate(self.rows, 1)
        ]

    def fetchall(self):
        return self._result


def _load_addresses(monkeypatch, donors, locations):
    cursor = AddressCursor()
    monkeypatch.setattr(address_loader, "execute_values",
                        lambda _cur, _sql, rows, **_kw: cursor.rows.extend(rows))
    address_ids = load_address_dimension(cursor, donors, locations)
    return cursor.rows, address_ids


def _donors(**overrides):
    row = {
        "contributor_street_1": "1 HOME ST",
        "contributor_street_2": np.nan,
        "contributor_city": "SOMEWHERE",
        "contributor_state": "CA",
        "contributor_zip": "94104",
        "latitude": "37.1",
        "longitude": "-122.1",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_donor_path_values_are_unchanged(monkeypatch):
    stored, _ = _load_addresses(monkeypatch, _donors(), [])

    assert stored == [("1 HOME ST", None, "SOMEWHERE", "CA", "94104", 37.1, -122.1)]


def _schema_unique_columns() -> tuple[str, ...]:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE donor_employments \((.*?)\n\);", text, re.S).group(1)
    columns = re.search(r"UNIQUE NULLS NOT DISTINCT \(([^)]*)\)", table).group(1)
    return tuple(column.strip() for column in columns.split(","))


class FakeEmploymentDB:
    """donor_employments with the UNIQUE NULLS NOT DISTINCT key."""

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


def _load(db, filings, employers=None):
    """Run the employment load and return each filing's row."""
    frame = pd.DataFrame(filings)
    donor_ids = {key: index for index, key in enumerate(sorted(frame["donor_key"].unique()), 1)}
    get_employer_id = _make_employer_resolver(employers or {})
    employment_ids = load_employments(
        db, frame, donor_ids, {"LEGAL": 1}, get_employer_id, HOME, [],
    )
    rows = frame.assign(_donor_id=frame["donor_key"].map(donor_ids))
    _map_employment_ids(rows, employment_ids, get_employer_id, {"LEGAL": 1})
    by_id = dict(enumerate(db.rows, 1))
    return {sub_id: by_id[int(employment_id)]
            for sub_id, employment_id in zip(rows["sub_id"], rows["_employment_id"])}


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


def test_each_retired_filing_keeps_its_own_previous_employer(db):
    """MARGOLIN, RUTH: retired in 2022; named her clinic in 2026."""
    linked = _load(
        db,
        [
            _filing("1", "2022-02-02", "RETIRED", "RETIRED", "retired"),
            _filing("2", "2022-09-28", "RETIRED", "RETIRED", "retired"),
            _filing("3", "2026-05-03", "RETIRED", "RETIRED", "retired", previous="CLINIC"),
        ],
        employers={"CLINIC": 9},
    )

    assert len(db.rows) == 2
    assert linked["1"]["previous_employer_id"] is None
    assert linked["2"] is linked["1"]
    assert linked["3"]["previous_employer_id"] == 9


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
        with stage("schema"):
            pass  # the old tables are gone inside the transaction
        with stage("contributions"):
            raise ValueError("bad row")
    assert conn.commits == 0 and conn.rollbacks == 1
