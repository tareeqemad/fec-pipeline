"""Unit tests for the donor-match do-not-merge guard (the merge-review feedback).

`_is_blocked_merge` must block a flagged name pair regardless of case/spacing,
since the reviewer's names and the matcher's `name` field can differ in form.
"""
import pytest

from fec.database.donor_match import constants as C


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
