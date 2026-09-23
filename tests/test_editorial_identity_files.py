import csv
import re

from fec.env import DATA_DIR


def _rows(filename):
    path = DATA_DIR / "database" / filename
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _identity(last, first):
    return "".join(f"{last},{first}".upper().split())


def test_editorial_rows_have_explicit_keys():
    for filename in ("leaders.csv", "key_accomplices.csv"):
        rows = _rows(filename)
        assert rows
        assert all(re.fullmatch(r"[0-9a-f]{12}", row["donor_key"]) for row in rows)
        assert all(row["create_if_missing"] in {"true", "false"} for row in rows)


def test_people_shared_by_editorial_files_share_one_key():
    leaders = {}
    for row in _rows("leaders.csv"):
        last, first = row["leader_name"].split(",", 1)
        leaders[_identity(last, first)] = row["donor_key"]

    for row in _rows("key_accomplices.csv"):
        identity = _identity(
            row["accomplice_last_name"],
            row["accomplice_first_name"],
        )
        if identity in leaders:
            assert row["donor_key"] == leaders[identity], row["accomplice_name"]


def test_roster_addresses_are_uppercase_like_the_fec_data():
    # the dashboard matches cities exactly against uppercase FEC values
    for filename, prefix in (("leaders.csv", "leader"), ("key_accomplices.csv", "accomplice")):
        for row in _rows(filename):
            for field in ("street_1", "street_2", "city", "state"):
                value = row.get(f"{prefix}_{field}", "")
                assert value == value.upper(), (filename, row["donor_key"], field, value)


def test_a_person_in_both_rosters_has_one_address_spelling():
    fields = ("street_1", "street_2", "city", "state", "zip")
    leaders = {r["donor_key"]: tuple(r[f"leader_{f}"] for f in fields) for r in _rows("leaders.csv")}
    for row in _rows("key_accomplices.csv"):
        if row["donor_key"] in leaders:
            assert tuple(row[f"accomplice_{f}"] for f in fields) == leaders[row["donor_key"]], row["accomplice_name"]
