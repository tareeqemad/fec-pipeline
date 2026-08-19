"""Contract tests for the public curated-people view."""

from pathlib import Path

import sqlparse


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "fec/database/schema.sql").read_text(encoding="utf-8")
REGISTRY = (ROOT / "fec/database/loader/schema_create.py").read_text(encoding="utf-8")
HEALTHCHECK = (ROOT / "fec/database/healthcheck.py").read_text(encoding="utf-8")
QUERY_CHECKS = (ROOT / "fec/database/query_checks.py").read_text(encoding="utf-8")


def _view_sql() -> str:
    marker = "CREATE OR REPLACE VIEW v_curated_people AS"
    statement = next(part for part in sqlparse.split(SCHEMA) if marker in part)
    return statement[statement.index(marker):]


def test_public_curated_people_view_exists_and_has_no_product_filter():
    sql = _view_sql()

    assert sql.startswith("CREATE OR REPLACE VIEW v_curated_people AS")
    assert "chatbot." not in sql
    assert "500" not in sql


def test_curated_people_uses_one_row_sources_and_full_join():
    sql = _view_sql()

    assert "FROM v_leaders l" in sql
    assert "FULL OUTER JOIN v_key_accomplices k" in sql
    assert "ON k.donor_id = l.donor_id" in sql
    assert "COALESCE(l.donor_id, k.donor_id) AS donor_id" in sql
    assert "v_curated_people: one row per donor" in QUERY_CHECKS


def test_curated_people_roles_cover_each_membership_case():
    sql = _view_sql()

    assert "WHEN l.leader_id IS NOT NULL THEN 'leader'" in sql
    assert "WHEN k.accomplice_id IS NOT NULL THEN 'key_accomplice'" in sql
    assert "v_curated_people: leader-only roles" in QUERY_CHECKS
    assert "v_curated_people: accomplice-only roles" in QUERY_CHECKS
    assert "v_curated_people: dual-role roles" in QUERY_CHECKS


def test_curated_people_profile_fields_come_from_profile():
    sql = _view_sql()

    assert "p.current_employer" in sql
    assert "p.current_occupation" in sql
    assert "p.total_amount" in sql
    assert "JOIN mv_donor_profile p" in sql
    assert "v_curated_people: profile fields match" in QUERY_CHECKS


def test_curated_people_card_fields_come_from_key_accomplices():
    sql = _view_sql()

    for field in (
        "subtitle", "body_text", "committee_name", "committee_short", "display_order"
    ):
        assert f"k.{field}" in sql
    assert "v_curated_people: card fields match key accomplices" in QUERY_CHECKS


def test_curated_people_is_registered_after_both_sources():
    for text in (REGISTRY, HEALTHCHECK):
        leader = text.index('"v_leaders"')
        accomplice = text.index('"v_key_accomplices"')
        curated = text.index('"v_curated_people"')
        assert curated > leader
        assert curated > accomplice
