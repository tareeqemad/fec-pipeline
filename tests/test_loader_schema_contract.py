"""The schema and loader agree on employment keys and previous-employer fields."""
import re

import pytest

from fec.database.loader.employers import EMPLOYMENT_KEY_COLUMNS
from fec.database.loader.schema_create import (
    _is_employment_key,
    _verify_schema_integrity,
)
from fec.env import SCHEMA_SQL

SCHEMA = SCHEMA_SQL.read_text(encoding="utf-8")


def _block(start: str, end: str = "\n);") -> str:
    begin = SCHEMA.index(start)
    return SCHEMA[begin:SCHEMA.index(end, begin)]


def _statement(start: str) -> str:
    begin = SCHEMA.index(start)
    return SCHEMA[begin:SCHEMA.index(";", begin)]


def test_previous_self_employed_column_is_a_non_null_flag():
    table = _block("CREATE TABLE donor_employments")

    assert re.search(r"previous_self_employed\s+BOOLEAN\s+NOT NULL DEFAULT FALSE", table)


def test_previous_employers_belong_to_retired_rows_only():
    table = _block("CREATE TABLE donor_employments")
    compact = " ".join(table.split())

    assert ("CHECK ( employer_status = 'retired' OR (previous_employer_id IS NULL "
            "AND NOT previous_self_employed) )") in compact
    assert "CHECK ( NOT (previous_self_employed AND previous_employer_id IS NOT NULL) )" in compact


def test_current_employment_view_exposes_the_flag():
    view = _statement("CREATE OR REPLACE VIEW v_donor_current_employment")

    assert "e.previous_self_employed" in view
    assert "DISTINCT ON (c.donor_id)" in view  # still one row per donor


def test_profile_shows_self_employed_when_there_is_no_previous_company():
    view = " ".join(_statement("CREATE OR REPLACE VIEW v_donor_profile").split())

    assert ("COALESCE( prev.name, CASE WHEN e.previous_self_employed "
            "THEN 'SELF-EMPLOYED' END ) AS previous_employer") in view
    assert "mv_donor_profile AS\nSELECT * FROM v_donor_profile" in SCHEMA


def test_verifier_accepts_only_the_loader_key():
    new = "UNIQUE NULLS NOT DISTINCT (" + ", ".join(EMPLOYMENT_KEY_COLUMNS) + ")"
    old = "UNIQUE NULLS NOT DISTINCT (donor_id, employer_id, occupation)"

    assert _is_employment_key(new)
    assert not _is_employment_key(old)
    assert not _is_employment_key("UNIQUE (donor_id, employer_id, occupation, employer_status)")


class SchemaCursor:
    """Answers the verifier's catalog queries."""

    def __init__(self, unique_defs, has_flag=True):
        self.unique_defs = unique_defs
        self.has_flag = has_flag
        self._result = []

    def execute(self, sql, params=None):
        if "pg_views" in sql:
            self._result = [(name,) for name in (
                "v_donor_current_address", "v_donor_current_employment",
                "v_donor_stats", "v_donor_profile")]
        elif "pg_matviews" in sql:
            self._result = [("mv_donor_profile",)]
        elif "leader_committees" in sql:
            self._result = [(1,)]
        elif "pg_get_constraintdef" in sql:
            self._result = [(definition,) for definition in self.unique_defs]
        elif "previous_self_employed" in sql:
            self._result = [(1,)] if self.has_flag else []
        else:  # legacy donor pointer columns
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def test_verifier_rejects_a_database_with_the_old_key():
    old = "UNIQUE NULLS NOT DISTINCT (donor_id, employer_id, occupation)"

    with pytest.raises(RuntimeError, match="employer_status"):
        _verify_schema_integrity(SchemaCursor([old]))


def test_verifier_requires_the_flag_column():
    new = "UNIQUE NULLS NOT DISTINCT (" + ", ".join(EMPLOYMENT_KEY_COLUMNS) + ")"

    _verify_schema_integrity(SchemaCursor([new]))
    with pytest.raises(RuntimeError, match="previous_self_employed"):
        _verify_schema_integrity(SchemaCursor([new], has_flag=False))
