"""Curated donor identity rules."""
import pandas as pd
import pytest

from fec.donor_match import keys as K
from fec.donor_match import rules as R
from fec.donor_match.matcher import match_donors


def test_normalize_collapses_case_and_space():
    assert R._normalize("  cohen,   MISHEL ") == "COHEN, MISHEL"


def test_blocked_pair_matches_normalized(monkeypatch):
    pair = frozenset({R._normalize("COHEN, MICHELLE"), R._normalize("COHEN, MISHEL")})
    monkeypatch.setattr(R, "SEPARATE_NAMES", {pair})
    # different case / extra spaces still blocks
    assert R.names_must_stay_separate("cohen, michelle", "COHEN,  MISHEL") is True
    # order-independent
    assert R.names_must_stay_separate("COHEN, MISHEL", "COHEN, MICHELLE") is True


def test_unblocked_pair_not_matched(monkeypatch):
    monkeypatch.setattr(R, "SEPARATE_NAMES", set())
    assert R.names_must_stay_separate("SMITH, JOHN", "SMITH, JON") is False


def test_single_rules_file_has_all_actions():
    rows = R._read_rules()
    actions = {row["action"] for row in rows}
    assert actions == {"merge_keys", "merge_names", "separate", "hold"}
    assert all(row["source"] for row in rows)
    assert all(row["reviewed_at"] for row in rows)
    assert R.KEY_MERGES
    assert R.NAME_MERGES
    assert R.SEPARATE_NAMES or R.SEPARATE_IDENTITIES


def test_missing_rules_file_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "RULES_PATH", tmp_path / "missing.csv")
    with pytest.raises(FileNotFoundError, match="Missing donor identity rules"):
        R._read_rules()


def test_invalid_action_fails_clearly(monkeypatch, tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text("action,name_a,name_b\nmaybe,A,B\n", encoding="utf-8")
    monkeypatch.setattr(R, "RULES_PATH", path)
    with pytest.raises(ValueError, match="Invalid identity action"):
        R._read_rules()


def test_missing_review_metadata_fails_clearly(monkeypatch, tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text(
        "action,donor_key_a,donor_key_b\nmerge_keys,a,b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(R, "RULES_PATH", path)

    with pytest.raises(ValueError, match="Invalid review status"):
        R._read_rules()


def test_pending_status_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text(
        "action,donor_key_a,donor_key_b,review_status,source,reviewed_at\n"
        "merge_keys,keep,drop,pending,review,2026-08-14\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(R, "RULES_PATH", path)

    with pytest.raises(ValueError, match="Invalid review status"):
        R._read_rules()


def test_same_name_block_is_scoped_to_two_locations(monkeypatch):
    blocked = frozenset({
        R._identity("LEVY, HAROLD", "FAIRFIELD", "CT"),
        R._identity("LEVY, HAROLD", "BOCA RATON", "FL"),
    })
    monkeypatch.setattr(R, "SEPARATE_NAMES", set())
    monkeypatch.setattr(R, "SEPARATE_IDENTITIES", {blocked})

    fairfield = {
        "name": "LEVY, HAROLD", "city": "FAIRFIELD", "state": "CT",
    }
    boca = {
        "name": "LEVY, HAROLD", "city": "BOCA RATON", "state": "FL",
    }
    westport = {
        "name": "LEVY, HAROLD", "city": "WESTPORT", "state": "CT",
    }

    assert R.identities_must_stay_separate(fairfield, boca) is True
    assert R.identities_must_stay_separate(fairfield, westport) is False


def test_location_block_prevents_a_high_scoring_match(monkeypatch):
    blocked = frozenset({
        R._identity("LEVY, HAROLD", "FAIRFIELD", "CT"),
        R._identity("LEVY, HAROLD", "BOCA RATON", "FL"),
    })
    monkeypatch.setattr(R, "SEPARATE_NAMES", set())
    monkeypatch.setattr(R, "SEPARATE_IDENTITIES", {blocked})
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

    keys, audit = match_donors(rows)

    assert len(set(keys.values())) == 2
    assert "SEPARATED(curated_rule)" in audit[0]["signals"]


def test_final_guard_rejects_an_indirect_blocked_merge(monkeypatch):
    monkeypatch.setattr(
        K,
        "identities_must_stay_separate",
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

    with pytest.raises(ValueError, match="separation pair"):
        K.validate_separations(rows)


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
        "names_must_stay_separate",
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


def test_review_uses_last_first_separation_names(monkeypatch, tmp_path):
    checked = []
    monkeypatch.setattr(
        K,
        "names_must_stay_separate",
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


def test_review_writes_related_names_with_totals(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "names_must_stay_separate", lambda *_: False)
    monkeypatch.setattr(K, "NICKNAME_MAP", {})
    rows = pd.DataFrame([
        _person("alex", "MEYERS, ALEX", "ALEX"),
        _person("alexander", "MEYERS, ALEXANDER", "ALEXANDER"),
    ])
    rows["contribution_receipt_amount"] = ["100", "250"]

    assert K.build_donor_dedup_review(rows, tmp_path) == 1

    report = pd.read_csv(tmp_path / "donor_dedup_review.csv")
    assert report.loc[0, "reason"] == "initial/prefix ALEX->ALEXANDER"
    assert report.loc[0, "combined_amount"] == 350


def test_resolve_donor_key_follows_chains_and_stops_on_cycles(monkeypatch):
    monkeypatch.setattr(R, "KEY_MERGES", {"a": "b", "b": "c", "x": "y", "y": "x"})

    assert R.resolve_donor_key("a") == "c"
    assert R.resolve_donor_key("c") == "c"
    assert R.resolve_donor_key("x") in {"x", "y"}


def test_curated_key_merges_repoint_rows_to_the_final_key(monkeypatch):
    merges = {"a": "b", "b": "c"}
    monkeypatch.setattr(R, "KEY_MERGES", merges)
    monkeypatch.setattr(K, "KEY_MERGES", merges)
    rows = pd.DataFrame({"donor_key": ["a", "b", "c", "z"]})

    assert K.apply_curated_key_merges(rows) == 2
    assert list(rows["donor_key"]) == ["c", "c", "c", "z"]


def test_held_filings_leave_the_person_and_stay_together(monkeypatch):
    monkeypatch.setattr(K, "HELD_FILINGS", {"2": "MORRIS, ELLEN STUN", "3": "MORRIS, ELLEN STUN"})
    df = pd.DataFrame({"sub_id": ["1", "2", "3"], "donor_key": ["ellen", "ellen", "ellen"]})

    assert K.hold_unproven_filings(df) == 2
    assert df.at[0, "donor_key"] == "ellen"
    assert df.at[1, "donor_key"] == df.at[2, "donor_key"] != "ellen"
    # the export says the owner is unproven
    assert df["identity_status"].tolist()[1:] == ["held", "held"]


def test_a_hold_rule_needs_a_sub_id(monkeypatch, tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text(
        "action,group,sub_id,review_status,source,reviewed_at\n"
        "hold,SOME GROUP,,verified_fec,https://www.fec.gov,2026-09-24\n"
    )
    monkeypatch.setattr(R, "RULES_PATH", path)
    with pytest.raises(ValueError, match="sub_id"):
        R._read_rules()
