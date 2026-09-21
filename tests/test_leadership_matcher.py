import pytest

from fec.database.leadership_matcher import find_or_create_donor
from fec.database.loader.leadership import _boolean
from fec.donor_match import rules as R


class Cursor:
    def __init__(self, results):
        self.results = iter(results)
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return next(self.results)


def test_editorial_donor_uses_exact_key():
    cur = Cursor([(42,)])

    donor_id, method = find_or_create_donor(
        cur, "abc123", False, "PERSON, ONE", "ONE", "PERSON"
    )

    assert (donor_id, method) == (42, "donor_key_exact")
    assert len(cur.queries) == 1
    assert cur.queries[0][1] == ("abc123",)


def test_merged_away_key_links_to_the_surviving_donor(monkeypatch):
    monkeypatch.setattr(R, "KEY_MERGES", {"dropped": "kept"})
    cur = Cursor([(42,)])

    donor_id, method = find_or_create_donor(
        cur, "dropped", True, "WULIGER, TIM", "TIM", "WULIGER"
    )

    assert (donor_id, method) == (42, "donor_key_merged")
    assert len(cur.queries) == 1
    assert cur.queries[0][1] == ("kept",)


def test_unknown_fec_donor_fails():
    cur = Cursor([None])

    with pytest.raises(ValueError, match="Unknown donor_key"):
        find_or_create_donor(
            cur, "missing", False, "PERSON, ONE", "ONE", "PERSON"
        )

    assert len(cur.queries) == 1


def test_explicit_editorial_donor_can_be_created():
    cur = Cursor([None, (73,)])

    donor_id, method = find_or_create_donor(
        cur, "editorial", True, "Person One", "One", "Person"
    )

    assert (donor_id, method) == (73, "created")
    assert len(cur.queries) == 2
    assert cur.queries[1][1] == ("editorial", "ONE", "PERSON")


def test_editorial_boolean_is_strict():
    assert _boolean({"create_if_missing": "true"}, "create_if_missing", "x.csv", "X")
    assert not _boolean(
        {"create_if_missing": "false"}, "create_if_missing", "x.csv", "X"
    )
    with pytest.raises(ValueError, match="true or false"):
        _boolean({}, "create_if_missing", "x.csv", "X")
