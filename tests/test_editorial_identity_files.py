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


# --- committees are named, never numbered ------------------------------------------

def _committee_shorts():
    from fec.committees import load_committees
    return {row["committee_short"] for row in load_committees()}


def _leader_committees(row):
    text = row["committee_ids"].strip()
    assert text.startswith("{") and text.endswith("}"), (row["leader_name"], text)
    return [token.strip() for token in text[1:-1].split(",") if token.strip()]


def test_roster_committees_are_committee_short_names():
    # committee_id is a SERIAL in committees.csv row order; a hard-coded integer
    # once showed DMFI's board as AIEF's (the loader now refuses unknown names too)
    known = _committee_shorts()
    for row in _rows("leaders.csv"):
        shorts = _leader_committees(row)
        assert shorts, row["leader_name"]
        assert set(shorts) <= known and len(shorts) == len(set(shorts)), (row["leader_name"], shorts)
    for row in _rows("key_accomplices.csv"):
        assert row["committee_id"] in known | {""}, (row["accomplice_name"], row["committee_id"])


def test_known_affiliations_carry_their_own_committee():
    leaders = {row["donor_key"]: _leader_committees(row) for row in _rows("leaders.csv")}
    assert leaders["1cae088dc1e7"] == ["DMFI"]      # Mark Mellman, DMFI president
    assert leaders["95bebc5f6a33"] == ["DMFI"]      # Todd Richman, DMFI co-chair
    assert leaders["db72d4f101e3"] == ["DMFI"]      # Ann Lewis, DMFI co-chair
    assert leaders["5af180e2f4de"] == ["AIEF"]      # Roselyne Swig, AIEF director
    accomplices = {row["donor_key"]: row["committee_id"] for row in _rows("key_accomplices.csv")}
    assert accomplices["869039e58323"] == "ZOA"     # Mort Klein, ZOA national president
    assert accomplices["1cae088dc1e7"] == "DMFI"    # Mark Mellman, founder of DMFI
    assert accomplices["6afae80f15af"] == "UDP"     # Jan Koum, whose only FEC gifts went to UDP


# --- editorial-only addresses ---------------------------------------------------------

_PREFIX = {"leaders.csv": "leader", "key_accomplices.csv": "accomplice"}


def _editorial_rows():
    """(filename, prefix, row) for rows whose donor has no filing in the cleaned data."""
    import pandas as pd
    import pytest

    from fec.donor_match.rules import resolve_donor_key
    from fec.env import CLEANED_CSV

    if not CLEANED_CSV.exists():
        pytest.skip("contributions_cleaned.csv not built")
    linked = set(pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, usecols=["donor_key"]).donor_key)
    return [(filename, prefix, row)
            for filename, prefix in _PREFIX.items()
            for row in _rows(filename)
            if resolve_donor_key(row["donor_key"]) not in linked]


def test_editorial_roster_streets_are_unchanged_by_the_pipeline_normaliser():
    # FEC-linked rows are synced from cleaned filings; editorial ones must be in the same
    # USPS style or the loader stores '1211 AVENUE OF THE AMERICAS' and '1211 AVE OF THE
    # AMERICAS' as two addresses (run sync_rosters.py to rewrite them)
    from fec.database.roster_sync import editorial_street_changes, is_foreign_row

    rows = _editorial_rows()
    assert rows
    checked = 0
    for filename, prefix, row in rows:
        if is_foreign_row(row, prefix):
            continue
        checked += 1
        assert editorial_street_changes(row, prefix) == {}, (filename, row["donor_key"])
    assert checked > 40


def test_us_roster_zip_is_five_digits_or_blank():
    from fec.database.roster_sync import is_foreign_row

    for filename, prefix in _PREFIX.items():
        for row in _rows(filename):
            if is_foreign_row(row, prefix):
                continue
            value = row[f"{prefix}_zip"]
            assert value == "" or re.fullmatch(r"\d{5}", value), (filename, row["donor_key"], value)


def test_roster_zip_agrees_with_the_rows_own_coordinates():
    # Sandberg's 94027 (Atherton) sat 41 km from her San Francisco office pin; the largest
    # remaining gaps are ~12 km in big suburban ZIPs
    from fec.database.roster_sync import is_foreign_row
    from fec.geocoding.places import distance_km
    from fec.resolve.pipeline.location_choice import _zip_centroids

    centroids = _zip_centroids()
    compared = 0
    for filename, prefix in _PREFIX.items():
        for row in _rows(filename):
            zip_code, lat, lng = row[f"{prefix}_zip"], row["address_lat"], row["address_lng"]
            if is_foreign_row(row, prefix) or not (zip_code and lat and lng) or zip_code not in centroids:
                continue
            compared += 1
            km = distance_km((float(lat), float(lng)), centroids[zip_code])
            assert km < 20, (filename, row["donor_key"], zip_code, round(km, 1))
    assert compared > 100
