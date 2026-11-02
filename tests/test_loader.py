import numpy as np
import pandas as pd
import fec.database.loader.addresses as address_loader
from fec.database.loader.addresses import load_address_dimension
import re
import sqlite3
import pytest
from fec.database.loader import validate
import fec.database.loader.employers as employer_loader
from fec.database.loader.contributions import _build_rows, _map_employment_ids
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


def test_donor_zip_fallback_respects_filing_geocoding_exclusions():
    common = {
        "transaction_id": "A", "contribution_receipt_amount": 1, "contribution_receipt_date": "2024-01-01",
        "two_year_transaction_period": 2024, "_donor_id": 1, "_committee_id": 1,
        "receipt_kind": "contribution",
        "_address_id": 1, "_employment_id": None, "contributor_state": "CA",
        "contributor_street_1": "", "contributor_street_2": "", "contributor_zip": "94104",
    }
    rows = pd.DataFrame([
        dict(common, sub_id="1", contributor_city="REHOVOT"),
        dict(common, sub_id="2", contributor_city="SAN FRANCISCO"),
    ])
    assert [row[-2:] for row in _build_rows(rows)] == [
        ("contribution", False), ("contribution", True)
    ]


def test_loader_preserves_legacy_and_unclassified_receipts(monkeypatch):
    monkeypatch.setattr(validate, "check_loadable", lambda columns: None)
    monkeypatch.setattr(validate, "run_quality_gates", lambda df: {"passed": True})
    legacy = pd.DataFrame({"sub_id": ["1", "2"]})
    validate._validate_cleaned_data(legacy)
    assert legacy["receipt_kind"].tolist() == ["unclassified", "unclassified"]

    legacy["receipt_kind"] = ["operating_offset", None]
    validate._validate_cleaned_data(legacy)
    assert legacy["receipt_kind"].tolist() == ["operating_offset", "unclassified"]

    legacy["receipt_kind"] = ["memo", "unknown label"]
    with pytest.raises(ValueError, match="receipt_kind"):
        validate._validate_cleaned_data(legacy)


def test_donor_totals_count_only_verified_itemized_contributions():
    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    with sqlite3.connect(":memory:") as db:
        db.execute("""CREATE TABLE receipts (
            sub_id INTEGER PRIMARY KEY, donor_id INTEGER, receipt_kind TEXT,
            amount NUMERIC, receipt_date TEXT, election_cycle INTEGER)""")
        for view in ("contributions", "v_donor_stats"):
            select = re.search(rf"CREATE OR REPLACE VIEW {view} AS\n(.*?);", schema, re.S).group(1)
            db.execute(f"CREATE VIEW {view} AS {select}")
        db.executemany("INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)", [
            (1, 1, "operating_offset", 100, "2020-01-01", 2020),
            (2, 1, "contribution", 50, "2024-01-01", 2024),
            (3, 1, "contribution", -10, "2026-02-01", 2026),
            (4, 1, "unclassified", 500, "2026-01-01", 2026),
            (5, 1, "memo", 50, "2026-02-01", 2026),
            (6, 2, "other_receipt", 250, "2026-01-01", 2026),
            (7, 2, "contribution_refund", -50, "2026-02-01", 2026),
            (8, 3, "unclassified", 50, "2026-02-02", 2026),
            (9, 1, "contribution", 0, "2020-01-01", 2020),
            (10, 4, "contribution", 0, "2026-01-01", 2026),
            (11, 5, "contribution", -10, "2026-01-01", 2026),
        ])
        assert db.execute("SELECT sub_id FROM contributions ORDER BY sub_id").fetchall() == [
            (2,), (3,), (9,), (10,), (11,)
        ]
        assert db.execute("SELECT * FROM v_donor_stats ORDER BY donor_id").fetchall() == [
            (1, 1, 40, 50, "2024-01-01", "2024-01-01", 1, 1),
            (2, 0, 0, None, None, None, 0, 0),
            (3, 0, 0, None, None, None, 0, 1),
            (4, 0, 0, None, None, None, 0, 0),
            (5, 0, -10, None, None, None, 0, 0),
        ]


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
            row["filing_work_address_id"] = (
                row["address_id"] if row["employer_status"] in ("active", "self_employed") else None
            )
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


def _load(db, filings, employers=None, address_ids=HOME, locations=()):
    """Run the employment load and return each filing's row."""
    frame = pd.DataFrame(filings)
    donor_ids = {key: index for index, key in enumerate(sorted(frame["donor_key"].unique()), 1)}
    get_employer_id = _make_employer_resolver(employers or {})
    employment_ids = load_employments(
        db, frame, donor_ids, {"LEGAL": 1}, get_employer_id, address_ids, locations,
    )
    _map_employment_ids(frame, employment_ids)
    by_id = dict(enumerate(db.rows, 1))
    return {sub_id: by_id[int(employment_id)]
            for sub_id, employment_id in zip(frame["sub_id"], frame["_employment_id"])}


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


def test_self_employed_filings_use_their_own_reported_address(db):
    def filing(sub_id, date, city, state):
        row = _filing(sub_id, date, "SELF-EMPLOYED", "ENTREPRENEUR", "self_employed")
        row.update(contributor_street_1=None, contributor_city=city,
                   contributor_state=state, contributor_zip=None)
        return row

    linked = _load(db, [
        filing("1", "2024-07-25", "BOCA RATON", "FL"),
        filing("2", "2024-08-15", None, None),
        filing("3", "2024-09-27", "MONTGOMERYVILLE", "PA"),
        filing("4", "2024-09-27", "BOCA RATON", "FL"),
    ], address_ids={("", "", "BOCA RATON", "FL", ""): 17,
                    ("", "", "MONTGOMERYVILLE", "PA", ""): 23})

    assert {sub_id: row["address_id"] for sub_id, row in linked.items()} == {
        "1": 17, "2": None, "3": 23, "4": 17,
    }
    assert linked["1"] is linked["4"]


def test_active_employment_uses_the_office_selected_for_each_filing(db):
    ca = _filing("1", "2023-01-01", "ACME LLP", "ATTORNEY", "active")
    pa = {**ca, "sub_id": "2", "contribution_receipt_date": "2024-01-01",
          "contributor_city": "PHILADELPHIA", "contributor_state": "PA", "contributor_zip": "19103"}
    offices = [
        {"employer_name": "ACME LLP", "employer_address": "1 OFFICE ST", "employer_city": "SOMEWHERE",
         "employer_state": "CA", "employer_zip": "94104", "is_primary": True},
        {"employer_name": "ACME LLP", "employer_address": "2 OFFICE ST", "employer_city": "PHILADELPHIA",
         "employer_state": "PA", "employer_zip": "19103", "is_primary": False},
    ]
    address_ids = {("1 OFFICE ST", "", "SOMEWHERE", "CA", "94104"): 31,
                   ("2 OFFICE ST", "", "PHILADELPHIA", "PA", "19103"): 32}

    back_in_ca = {**ca, "sub_id": "3", "contribution_receipt_date": "2025-01-01"}
    unknown_office = {**ca, "sub_id": "4", "contributor_employer": "OTHER LLP"}
    linked = _load(db, [ca, pa, back_in_ca, unknown_office],
                   employers={"ACME LLP": 1, "OTHER LLP": 2}, address_ids=address_ids, locations=offices)
    assert {sub_id: row["address_id"] for sub_id, row in linked.items()} == {
        "1": 31, "2": 32, "3": 31, "4": None,
    }
    assert linked["1"] is linked["3"]


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
