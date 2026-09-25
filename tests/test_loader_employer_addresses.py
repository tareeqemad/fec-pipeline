"""Employer-location addresses store NULL, never '', for a missing part.

employer_locations.csv is read with keep_default_na=False, so a missing state
or zip arrived as '' (audit: KIFO London and SHOPPERAI Tel Aviv state_code '',
STANTON SAS Sibate zip_code ''), while the donor and roster paths store NULL.
"""
import numpy as np
import pandas as pd

import fec.database.loader.addresses as address_loader
from fec.database.loader.addresses import (
    _akey,
    load_address_dimension,
    load_employer_locations,
)
from fec.database.loader.employment_locations import (
    _employment_address_id, _location_index,
)


def _frame(**overrides):
    row = {
        "employer_name": "KIFO",
        "employer_address": "York Gate, 100 Marylebone Road",
        "employer_city": "London",
        "employer_state": "",
        "employer_zip": "NW1 5DX",
        "employer_latitude": "",
        "employer_longitude": "",
        "is_primary": "true",
        "address_source": "manual",
        "address_trust": "verified",
    }
    row.update(overrides)
    return pd.DataFrame([row], dtype=str)


def test_missing_employer_state_and_zip_become_none():
    kifo = load_employer_locations(_frame())[0]
    stanton = load_employer_locations(_frame(
        employer_name="STANTON SAS", employer_address="Km 25 Via a Sibate",
        employer_city="Sibate", employer_state="CUNDINAMARCA", employer_zip="",
    ))[0]

    assert kifo["employer_state"] is None
    assert kifo["employer_zip"] == "NW1 5DX"  # foreign parts kept exactly as filed
    assert kifo["employer_city"] == "London"
    assert stanton["employer_zip"] is None
    assert stanton["employer_state"] == "CUNDINAMARCA"


def test_whitespace_only_parts_are_missing_and_filled_parts_are_untouched():
    location = load_employer_locations(_frame(employer_city="  ", employer_zip=" NW1 5DX"))[0]

    assert location["employer_city"] is None
    assert location["employer_zip"] == " NW1 5DX"


def test_employer_without_street_or_town_is_not_published():
    assert load_employer_locations(_frame(employer_address="   ", employer_city=" ")) == []


def test_home_based_business_is_published_as_its_town_only():
    location = load_employer_locations(_frame(
        employer_name="HSK CONSULTING LLC", employer_address="", employer_city="BETHESDA",
        employer_state="MD", employer_zip="20817",
    ))[0]

    assert location["employer_address"] is None
    assert (location["employer_city"], location["employer_state"], location["employer_zip"]) == (
        "BETHESDA", "MD", "20817",
    )


def test_town_only_row_without_publishable_trust_is_not_published():
    assert load_employer_locations(_frame(
        employer_address="", employer_city="BETHESDA", address_trust="uncorroborated",
    )) == []


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
    monkeypatch.setattr(address_loader, "_count", lambda *_args: len(cursor.rows))
    address_ids = load_address_dimension(cursor, cursor, donors, locations)
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


def test_employer_address_stores_null_and_still_links(monkeypatch):
    locations = load_employer_locations(_frame())
    stored, address_ids = _load_addresses(monkeypatch, _donors(), locations)

    kifo = [row for row in stored if row[2] == "London"]
    assert kifo == [("York Gate, 100 Marylebone Road", None, "London", None,
                     "NW1 5DX", None, None)]
    assert all("" not in row[:5] for row in stored)

    # the employment and employer-location lookups still find the row
    key = _akey("York Gate, 100 Marylebone Road", None, "London", None, "NW1 5DX")
    assert key in address_ids
    row = pd.Series({"employer_status": "active", "contributor_zip": "10001",
                     "contributor_state": "NY"})
    assert _employment_address_id(row, "KIFO", _location_index(locations),
                                  address_ids) == address_ids[key]


def test_empty_string_from_any_caller_is_stored_as_null(monkeypatch):
    stored, address_ids = _load_addresses(
        monkeypatch, _donors(contributor_street_2="", contributor_zip=""), [],
    )

    assert stored == [("1 HOME ST", None, "SOMEWHERE", "CA", None, 37.1, -122.1)]
    assert ("1 HOME ST", "", "SOMEWHERE", "CA", "") in address_ids


def test_donor_path_values_are_unchanged(monkeypatch):
    stored, _ = _load_addresses(monkeypatch, _donors(), [])

    assert stored == [("1 HOME ST", None, "SOMEWHERE", "CA", "94104", 37.1, -122.1)]
