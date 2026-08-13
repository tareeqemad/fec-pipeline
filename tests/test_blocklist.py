"""_is_blocked_merge must block a flagged name pair regardless of case/spacing."""
import pandas as pd
import pytest

from fec.donor_match import constants as C
from fec.donor_match import keys as K
from fec.donor_match.matcher import match_donors


def test_norm_blk_collapses_case_and_space():
    assert C._norm_blk("  cohen,   MISHEL ") == "COHEN, MISHEL"


def test_blocked_pair_matches_normalized(monkeypatch):
    pair = frozenset({C._norm_blk("COHEN, MICHELLE"), C._norm_blk("COHEN, MISHEL")})
    monkeypatch.setattr(C, "_DNM_SET", {pair})
    # different case / extra spaces still blocks
    assert C._is_blocked_merge("cohen, michelle", "COHEN,  MISHEL") is True
    # order-independent
    assert C._is_blocked_merge("COHEN, MISHEL", "COHEN, MICHELLE") is True


def test_unblocked_pair_not_matched(monkeypatch):
    monkeypatch.setattr(C, "_DNM_SET", set())
    assert C._is_blocked_merge("SMITH, JOHN", "SMITH, JON") is False


def test_loader_returns_set_of_frozensets():
    # reads data/database/donor_no_merge.csv if present; always a set, never crashes
    result = C._load_do_not_merge()
    assert isinstance(result, set)
    assert all(isinstance(p, frozenset) for p in result)


def test_same_name_block_is_scoped_to_two_locations(monkeypatch):
    blocked = frozenset({
        C._identity("LEVY, HAROLD", "FAIRFIELD", "CT"),
        C._identity("LEVY, HAROLD", "BOCA RATON", "FL"),
    })
    monkeypatch.setattr(C, "_DNM_SET", set())
    monkeypatch.setattr(C, "_DNM_IDENTITY_SET", {blocked})

    fairfield = {
        "name": "LEVY, HAROLD", "city": "FAIRFIELD", "state": "CT",
    }
    boca = {
        "name": "LEVY, HAROLD", "city": "BOCA RATON", "state": "FL",
    }
    westport = {
        "name": "LEVY, HAROLD", "city": "WESTPORT", "state": "CT",
    }

    assert C._is_blocked_identity(fairfield, boca) is True
    assert C._is_blocked_identity(fairfield, westport) is False


def test_location_block_prevents_a_high_scoring_match(monkeypatch):
    blocked = frozenset({
        C._identity("LEVY, HAROLD", "FAIRFIELD", "CT"),
        C._identity("LEVY, HAROLD", "BOCA RATON", "FL"),
    })
    monkeypatch.setattr(C, "_DNM_SET", set())
    monkeypatch.setattr(C, "_DNM_IDENTITY_SET", {blocked})
    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL", "contributor_name": "LEVY, HAROLD",
            "contributor_city": "FAIRFIELD", "contributor_state": "CT",
            "contributor_zip": "06824", "contributor_street_1": "ONE ST",
            "contributor_employer": "ACME", "occupation_category": "FINANCE",
        },
        {
            "entity_type": "INDIVIDUAL", "contributor_name": "LEVY, HAROLD",
            "contributor_city": "BOCA RATON", "contributor_state": "FL",
            "contributor_zip": "33432", "contributor_street_1": "TWO ST",
            "contributor_employer": "ACME", "occupation_category": "FINANCE",
        },
    ])

    keys, audit = match_donors(rows, verbose=False)

    assert len(set(keys.values())) == 2
    assert "BLOCKED(do_not_merge)" in audit[0]["signals"]


def test_final_guard_rejects_an_indirect_blocked_merge(monkeypatch):
    monkeypatch.setattr(
        K,
        "_is_blocked_identity",
        lambda a, b: {a["city"], b["city"]} == {"FAIRFIELD", "BOCA RATON"},
    )
    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL", "donor_key": "wrong",
            "contributor_name": "LEVY, HAROLD",
            "contributor_city": "FAIRFIELD", "contributor_state": "CT",
        },
        {
            "entity_type": "INDIVIDUAL", "donor_key": "wrong",
            "contributor_name": "LEVY, HAROLD",
            "contributor_city": "BOCA RATON", "contributor_state": "FL",
        },
    ])

    with pytest.raises(ValueError, match="do-not-merge pair"):
        K.validate_do_not_merge(rows)


def _person(key, name, first):
    return {
        "entity_type": "INDIVIDUAL",
        "contributor_name": name,
        "contributor_first_name": first,
        "contributor_last_name": "MEYERS",
        "contributor_city": "DUNWOODY",
        "contributor_state": "GA",
        "contributor_zip": "30338",
        "occupation_category": "RETIRED",
        "donor_key": key,
    }


def test_split_name_merge_respects_blocked_people(monkeypatch):
    blocked = frozenset(("MEYERS, STUART", "MEYERS, SARA"))
    monkeypatch.setattr(
        K,
        "_is_blocked_merge",
        lambda a, b: frozenset((a, b)) == blocked,
    )
    rows = pd.DataFrame([
        _person("stuart", "MEYERS, STUART", "STUART"),
        _person("stuart", "MEYERS, STUART SARA", "STUART SARA"),
        _person("sara", "MEYERS, SARA", "SARA"),
        _person("sara", "MEYERS, SARA STUART", "SARA STUART"),
    ])

    assert K.merge_split_name_donors(rows) == 0
    assert set(rows["donor_key"]) == {"stuart", "sara"}


def test_review_uses_last_first_blocklist_names(monkeypatch, tmp_path):
    checked = []
    monkeypatch.setattr(
        K,
        "_is_blocked_merge",
        lambda a, b: checked.append((a, b)) or True,
    )
    rows = pd.DataFrame([
        _person("stuart", "MEYERS, STUART", "STUART"),
        _person("joint", "MEYERS, STUARTANDSARA", "STUARTANDSARA"),
    ])
    report = tmp_path / "donor_dedup_review.csv"
    report.write_text("stale")
    rows["contribution_receipt_amount"] = "100"

    assert K.build_donor_dedup_review(rows, tmp_path) == 0
    assert not report.exists()
    assert len(checked) == 1
    assert frozenset(checked[0]) == {"MEYERS, STUART", "MEYERS, STUARTANDSARA"}
