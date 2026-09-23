"""Roster rows linked to a donor follow that donor's newest cleaned filing; everything else is untouched."""
import pandas as pd

from fec.database import roster_sync
from fec.database.roster_sync import latest_filings, read_roster, sync_rows, sync_rosters, write_roster

_COLS = ["sub_id", "donor_key", "contribution_receipt_date", "contributor_street_1", "contributor_street_2",
         "contributor_city", "contributor_state", "contributor_zip", "contributor_employer",
         "contributor_occupation", "latitude", "longitude"]


def _cleaned(*rows):
    return pd.DataFrame(list(rows), columns=_COLS)


def test_latest_filing_is_newest_date_then_highest_sub_id():
    latest = latest_filings(_cleaned(
        ("2", "k1", "2026-01-01", "OLD ST", "", "AUSTIN", "TX", "78701", "OLD CO", "CEO", "1", "1"),
        ("9", "k1", "2026-07-14", "NEW RD", "APT 2", "BUCHANAN", "MI", "491071234", "NEW CO", "OWNER", "2", "2"),
        ("5", "k1", "2026-07-14", "TIE RD", "", "BUCHANAN", "MI", "49107", "TIE CO", "OWNER", "3", "3"),
    ))
    assert latest.loc["k1", "contributor_street_1"] == "NEW RD"


def test_linked_row_takes_the_newest_filing_and_unlinked_row_is_kept():
    latest = latest_filings(_cleaned(
        ("9", "k1", "2026-07-14", "NEW RD", "", "BUCHANAN", "MI", "491071234", "NEW CO", "OWNER", "42.1", "-86.3"),
    ))
    fieldnames = ["donor_key", "create_if_missing", "leader_name", "leader_street_1", "leader_street_2",
                  "leader_city", "leader_state", "leader_zip", "leader_employer", "committee_ids",
                  "address_lat", "address_lng", "image_path"]
    rows = [
        {"donor_key": "k1", "create_if_missing": "false", "leader_name": "RUDY, DEBORAH",
         "leader_street_1": "101 S WESTON LN", "leader_street_2": "", "leader_city": "AUSTIN",
         "leader_state": "TX", "leader_zip": "78733", "leader_employer": "OLD CO", "committee_ids": "{1,2}",
         "address_lat": "30.3", "address_lng": "-97.8", "image_path": "/images/people/deborah-rudy.jpg"},
        {"donor_key": "zz", "create_if_missing": "true", "leader_name": "NOBODY, NEW",
         "leader_street_1": "1 EDITORIAL WAY", "leader_street_2": "", "leader_city": "NOWHERE",
         "leader_state": "NY", "leader_zip": "", "leader_employer": "HAND WRITTEN", "committee_ids": "{4}",
         "address_lat": "", "address_lng": "", "image_path": ""},
    ]
    result = sync_rows(rows, fieldnames, "leader", latest)

    rudy, nobody = result.rows
    assert (rudy["leader_street_1"], rudy["leader_city"], rudy["leader_state"], rudy["leader_zip"]) == \
        ("NEW RD", "BUCHANAN", "MI", "49107")
    assert (rudy["leader_employer"], rudy["leader_occupation"]) == ("NEW CO", "OWNER")
    assert (rudy["address_lat"], rudy["address_lng"]) == ("42.1", "-86.3")
    assert (rudy["committee_ids"], rudy["image_path"]) == ("{1,2}", "/images/people/deborah-rudy.jpg")  # editorial columns kept
    assert nobody["leader_street_1"] == "1 EDITORIAL WAY" and nobody["leader_employer"] == "HAND WRITTEN"
    assert result.unlinked == ["NOBODY, NEW"]
    assert result.fieldnames.index("leader_occupation") == result.fieldnames.index("leader_employer") + 1
    assert {c for _, c, _, _ in result.changes} == {"leader_street_1", "leader_city", "leader_state", "leader_zip",
                                                    "leader_employer", "leader_occupation", "address_lat", "address_lng"}


def test_roster_keyed_by_a_merged_away_key_follows_the_surviving_donor(monkeypatch):
    monkeypatch.setattr(roster_sync, "resolve_donor_key", lambda key: {"old": "k1"}.get(key, key))
    latest = latest_filings(_cleaned(
        ("9", "k1", "2026-07-14", "NEW RD", "", "BUCHANAN", "MI", "49107", "NEW CO", "OWNER", "", ""),
    ))
    rows = [{"donor_key": "old", "accomplice_name": "Haim Saban", "accomplice_street_1": "", "accomplice_street_2": "",
             "accomplice_city": "Los Angeles", "accomplice_state": "CA", "accomplice_zip": "",
             "accomplice_employer": "", "address_lat": "", "address_lng": ""}]
    result = sync_rows(rows, list(rows[0].keys()), "accomplice", latest)
    assert result.unlinked == []
    assert rows[0]["accomplice_city"] == "BUCHANAN" and rows[0]["accomplice_occupation"] == "OWNER"


def test_a_row_already_in_sync_produces_no_change():
    latest = latest_filings(_cleaned(
        ("9", "k1", "2026-07-14", "NEW RD", "", "BUCHANAN", "MI", "49107", "NEW CO", "OWNER", "42.1", "-86.3"),
    ))
    rows = [{"donor_key": "k1", "leader_name": "X, Y", "leader_street_1": "NEW RD", "leader_street_2": "",
             "leader_city": "BUCHANAN", "leader_state": "MI", "leader_zip": "49107", "leader_employer": "NEW CO",
             "leader_occupation": "OWNER", "address_lat": "42.1", "address_lng": "-86.3"}]
    assert sync_rows(rows, list(rows[0].keys()), "leader", latest).changes == []


def test_write_keeps_the_files_own_line_endings(tmp_path):
    path = tmp_path / "leaders.csv"
    path.write_bytes(b"donor_key,leader_name\r\nk1,\"X, Y\"\r\n")
    rows, names = read_roster(path)
    write_roster(path, rows, names)
    assert path.read_bytes() == b"donor_key,leader_name\r\nk1,\"X, Y\"\r\n"
    path.write_bytes(b"donor_key,leader_name\nk1,\"X, Y\"\n")
    write_roster(path, *read_roster(path)[::-1][::-1])
    assert path.read_bytes() == b"donor_key,leader_name\nk1,\"X, Y\"\n"


def test_sync_rosters_check_mode_reports_but_does_not_write(tmp_path):
    cleaned = tmp_path / "cleaned.csv"
    _cleaned(("9", "k1", "2026-07-14", "NEW RD", "", "BUCHANAN", "MI", "49107", "NEW CO", "OWNER", "1", "2")) \
        .to_csv(cleaned, index=False)
    roster_dir = tmp_path / "database"
    roster_dir.mkdir()
    fieldnames = ["donor_key", "create_if_missing", "leader_name", "leader_street_1", "leader_street_2", "leader_city",
                  "leader_state", "leader_zip", "leader_employer", "committee_ids", "address_lat", "address_lng", "image_path"]
    row = dict.fromkeys(fieldnames, "")
    row.update(donor_key="k1", create_if_missing="false", leader_name="X, Y", leader_street_1="OLD", leader_city="AUSTIN",
               leader_state="TX", leader_zip="78701", leader_employer="OLD CO", committee_ids="{1}")
    write_roster(roster_dir / "leaders.csv", [row], fieldnames)
    before = (roster_dir / "leaders.csv").read_text()

    results = sync_rosters(check=True, cleaned_csv=cleaned, roster_dir=roster_dir)
    assert results[0].changes and (roster_dir / "leaders.csv").read_text() == before

    sync_rosters(check=False, cleaned_csv=cleaned, roster_dir=roster_dir)
    rows, names = read_roster(roster_dir / "leaders.csv")
    assert rows[0]["leader_street_1"] == "NEW RD" and rows[0]["leader_occupation"] == "OWNER"
    assert names[names.index("leader_employer") + 1] == "leader_occupation"
    assert sync_rosters(check=True, cleaned_csv=cleaned, roster_dir=roster_dir)[0].changes == []
