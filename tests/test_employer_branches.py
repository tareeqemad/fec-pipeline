"""Branch offices: the donor's local office wins over the corporate HQ.

The dashboard prints this address under the donor's name as their workplace, so
a California donor at a New-York-headquartered firm must not be shown New York.
"""
import pandas as pd

from fec.resolve.pipeline.apply import _resolve_row
from fec.resolve.pipeline.constants import COMMUTER_STATE_PAIRS
from fec.resolve.pipeline.manual_overrides import (
    load_manual_branches, load_manual_overrides,
)


class FakeCache(dict):
    """Cache stand-in: the resolve caches only need get/put/save here."""

    def put(self, key, value):
        self[key] = value

    def save(self):
        pass


def _row(employer, state):
    return pd.Series({
        "entity_type": "INDIVIDUAL",
        "contributor_employer": employer,
        "contributor_state": state,
        "contributor_name": "DOE, JANE",
        "contributor_street_1": "1 HOME ST",
        "contributor_city": "SOMEWHERE",
        "contributor_zip": "12345",
    })


HQ = {"employer_address": "1585 Broadway", "employer_city": "New York",
      "employer_state": "NY", "employer_zip": "10036",
      "method": "manual_override", "confidence": "HIGH"}
BRANCH = {"employer_address": "555 California St", "employer_city": "San Francisco",
          "employer_state": "CA", "employer_zip": "94104",
          "method": "manual_override", "confidence": "HIGH"}


def test_branch_wins_for_a_donor_in_that_state():
    addr_cache = FakeCache({"BIG FIRM": HQ})
    branch_cache = FakeCache({"BIG FIRM|CA": BRANCH})

    result = _resolve_row(_row("BIG FIRM", "CA"), FakeCache(), addr_cache,
                          FakeCache(), branch_cache)

    assert result["employer_address"] == "555 California St"
    assert result["employer_state"] == "CA"
    # traceable: the method records that a branch, not the HQ, answered
    assert result["resolve_method"].startswith("branch_")


def test_donor_in_another_state_still_gets_the_hq():
    addr_cache = FakeCache({"BIG FIRM": HQ})
    branch_cache = FakeCache({"BIG FIRM|CA": BRANCH})

    result = _resolve_row(_row("BIG FIRM", "TX"), FakeCache(), addr_cache,
                          FakeCache(), branch_cache)

    assert result["employer_address"] == "1585 Broadway"
    assert result["resolve_method"] == "manual_override"


def test_no_branch_cache_is_the_old_behaviour():
    addr_cache = FakeCache({"BIG FIRM": HQ})

    result = _resolve_row(_row("BIG FIRM", "CA"), FakeCache(), addr_cache, FakeCache())

    assert result["employer_address"] == "1585 Broadway"


def test_commuter_pairs_are_listed_both_ways_where_people_commute_both_ways():
    # A New Jersey donor at a New York firm commutes; never invent a NJ branch.
    assert ("NJ", "NY") in COMMUTER_STATE_PAIRS
    assert ("VA", "DC") in COMMUTER_STATE_PAIRS
    # Cross-country is not commuting - those pairs stay eligible for a branch.
    assert ("CA", "NY") not in COMMUTER_STATE_PAIRS
    assert ("FL", "NY") not in COMMUTER_STATE_PAIRS


def test_curated_csv_splits_hq_rows_from_branch_rows(tmp_path):
    csv_path = tmp_path / "manual_employer_addresses.csv"
    csv_path.write_text(
        "name,address,city,state,zip,donor_state,note\n"
        "BIG FIRM,1585 Broadway,New York,NY,10036,,HQ\n"
        "BIG FIRM,555 California St,San Francisco,CA,94104,CA,SF office\n",
        encoding="utf-8",
    )

    addr_cache, branch_cache = FakeCache(), FakeCache()
    load_manual_overrides(csv_path, addr_cache)
    load_manual_branches(csv_path, branch_cache)

    # the HQ row keys on the name alone; the branch row keys on name|state
    assert addr_cache["BIG FIRM"]["employer_city"] == "New York"
    assert "BIG FIRM|CA" in branch_cache
    assert branch_cache["BIG FIRM|CA"]["employer_city"] == "San Francisco"
    # a branch row must never overwrite the HQ
    assert len(addr_cache) == 1
