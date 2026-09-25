"""The rosters name committees by committee_short; the loader resolves them through the committees table."""
import pandas as pd
import pytest

from fec.database.loader import leadership, people
from fec.database.loader.leadership import (
    accomplice_committee_id,
    committee_lookup,
    leader_committee_ids,
    load_key_accomplices,
    load_leadership,
)

# The DB's SERIAL order (committees.csv row order), NOT the order the old integer rosters assumed.
COMMITTEES = {"AIPAC": 1, "DMFI": 2, "UDP": 3, "AIEF": 4, "ZOA": 5}


class Cursor:
    """Answers the committee lookup; records every statement."""

    def __init__(self):
        self.queries = []
        self._rows = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "FROM committees" in query:
            self._rows = list(COMMITTEES.items())

    def fetchall(self):
        return self._rows


def test_committee_lookup_reads_short_names_from_the_table():
    assert committee_lookup(Cursor()) == COMMITTEES


def test_leader_committees_resolve_by_short_name_not_position():
    assert leader_committee_ids("{AIPAC,DMFI}", COMMITTEES, "MELLMAN, MARK") == [1, 2]
    assert leader_committee_ids("{AIEF}", COMMITTEES, "SWIG, ROSELYNE") == [4]
    assert leader_committee_ids(" { AIPAC , AIEF } ", COMMITTEES, "X") == [1, 4]
    assert leader_committee_ids("", COMMITTEES, "X") == []
    assert leader_committee_ids("{}", COMMITTEES, "X") == []


@pytest.mark.parametrize("raw", ["{4}", "{1,2}", "{aipac}", "{AIPAC,NOPE}"])
def test_an_integer_or_unknown_leader_committee_stops_the_load(raw):
    # the old integer convention (4 meant DMFI) must never load silently again
    with pytest.raises(ValueError, match="unknown committee"):
        leader_committee_ids(raw, COMMITTEES, "MELLMAN, MARK")


def test_a_typo_in_leader_committees_stops_the_load():
    with pytest.raises(ValueError, match="empty entry"):
        leader_committee_ids("{AIPAC,}", COMMITTEES, "X")
    with pytest.raises(ValueError, match="twice"):
        leader_committee_ids("{DMFI,DMFI}", COMMITTEES, "X")


def test_accomplice_committee_resolves_by_short_name():
    assert accomplice_committee_id("ZOA", COMMITTEES, "Mort Klein") == 5
    assert accomplice_committee_id(" DMFI ", COMMITTEES, "Mark Mellman") == 2
    assert accomplice_committee_id("", COMMITTEES, "Jeff Yass") is None
    with pytest.raises(ValueError, match="unknown committee '3'"):
        accomplice_committee_id("3", COMMITTEES, "Mort Klein")


def _roster(tmp_path, monkeypatch, filename, header, rows):
    database = tmp_path / "data" / "database"
    database.mkdir(parents=True)
    pd.DataFrame(rows, columns=header).to_csv(database / filename, index=False)
    monkeypatch.setattr(people, "PROJECT_ROOT", tmp_path)

    calls = {"donors": [], "employment": []}

    def find_or_create_donor(cur, key, create, name, first, last):
        calls["donors"].append(key)
        return 100 + len(calls["donors"]), "donor_key_exact"

    def upsert_leader_employment(cur, donor_id, employer, occupation=None, **kwargs):
        calls["employment"].append((donor_id, employer, occupation, kwargs))

    monkeypatch.setattr(people, "find_or_create_donor", find_or_create_donor)
    monkeypatch.setattr(people, "upsert_donor_address", lambda *a, **k: None)
    monkeypatch.setattr(people, "upsert_leader_employment", upsert_leader_employment)
    monkeypatch.setattr(people, "_location_index", lambda *a: {"ORACLE": {"sentinel": True}})
    return calls


class Conn:
    def commit(self):
        pass

    def rollback(self):
        pass


class LoadCursor(Cursor):
    def fetchone(self):
        return (7,)


LEADER_HEADER = ["donor_key", "create_if_missing", "leader_name", "leader_street_1", "leader_street_2",
                 "leader_city", "leader_state", "leader_zip", "leader_employer", "leader_occupation",
                 "committee_ids", "address_lat", "address_lng", "image_path"]


def test_load_leadership_links_the_named_committees(tmp_path, monkeypatch):
    _roster(tmp_path, monkeypatch, "leaders.csv", LEADER_HEADER, [
        ["k1", "false", "MELLMAN, MARK", "1023 31ST ST NW", "FL 5", "WASHINGTON", "DC", "20007",
         "MELLMAN GROUP", "FOUNDER AND CEO", "{DMFI}", "", "", ""],
        ["k2", "false", "ROSENBERG, LEE", "", "", "CHICAGO", "IL", "60607", "ROSENBERG ADVISORY", "ADVISOR",
         "{AIPAC,AIEF}", "", "", ""],
    ])
    linked = []
    monkeypatch.setattr(leadership, "execute_values", lambda cur, sql, rows: linked.append(rows))
    load_leadership(Conn(), LoadCursor())
    assert linked == [[(7, 2)], [(7, 1), (7, 4)]]  # DMFI=2, AIPAC=1, AIEF=4 in the DB


def test_load_leadership_refuses_an_unknown_committee_before_writing(tmp_path, monkeypatch):
    calls = _roster(tmp_path, monkeypatch, "leaders.csv", LEADER_HEADER, [
        ["k1", "false", "MELLMAN, MARK", "", "", "WASHINGTON", "DC", "", "", "", "{DMFI}", "", "", ""],
        ["k2", "false", "LEWIS, ANN", "", "", "CHEVY CHASE", "MD", "", "", "", "{4}", "", "", ""],
    ])
    cur = LoadCursor()
    with pytest.raises(ValueError, match="LEWIS, ANN.*unknown committee '4'"):
        load_leadership(Conn(), cur)
    assert calls["donors"] == []  # nothing was written for the valid first row either
    assert not any("INSERT" in query for query, _ in cur.queries)


def test_loader_hands_the_person_state_zip_and_employer_locations_to_the_employment(tmp_path, monkeypatch):
    header = ["donor_key", "create_if_missing", "accomplice_name", "accomplice_first_name",
              "accomplice_last_name", "accomplice_street_1", "accomplice_street_2", "accomplice_city",
              "accomplice_state", "accomplice_zip", "accomplice_employer", "accomplice_occupation",
              "sign", "subtitle", "body_text", "image_path", "committee_id", "display_order",
              "address_lat", "address_lng", "accomplice_country"]
    calls = _roster(tmp_path, monkeypatch, "key_accomplices.csv", header, [
        ["k1", "true", "Larry Ellison", "Larry", "Ellison", "2300 ORACLE WAY", "", "AUSTIN", "TX", "78741",
         "ORACLE", "CTO", "", "", "", "", "", "3", "", "", ""],
        ["k2", "true", "Mort Klein", "Mort", "Klein", "633 3RD AVE", "STE 31B", "NEW YORK", "NY", "10017",
         "ZIONIST ORGANIZATION OF AMERICA", "NATIONAL PRESIDENT", "", "", "", "", "ZOA", "35", "", "", ""],
    ])
    cur = LoadCursor()
    load_key_accomplices(Conn(), cur)

    (_, employer, _, kwargs), _ = calls["employment"]
    assert employer == "ORACLE"
    assert (kwargs["state"], kwargs["zip_5"]) == ("TX", "78741")
    assert kwargs["locations"] == {"ORACLE": {"sentinel": True}}
    committee_ids = [params[4] for query, params in cur.queries if "INSERT INTO key_accomplices" in query]
    assert committee_ids == [None, 5]
