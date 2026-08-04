"""_is_blocked_merge must block a flagged name pair regardless of case/spacing."""
import pandas as pd

from fec.donor_match import constants as C
from fec.donor_match import keys as K


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


def _person(key, name, first):
    return {
        "entity_type": "INDIVIDUAL",
        "contributor_name": name,
        "contributor_first_name": first,
        "contributor_last_name": "MEYERS",
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
