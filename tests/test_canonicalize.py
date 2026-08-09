"""Per-donor canonicalization: same entity unifies, different entities stay apart."""
import pandas as pd

from fec.donor_match.canonicalize import (
    canonicalize_donor_names,
    canonicalize_donor_employers,
)
from fec.donor_match.canonicalize import canonicalize_donor_addresses
from fec.donor_match.canonicalize import canonicalize_donor_addresses_geo


def _df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


def test_names_unify_and_regenerate_composite():
    df = _df(
        [
            ["INDIVIDUAL", "X", "HARBERG, FRANKLIN J", "FRANKLIN J.", "HARBERG"],
            ["INDIVIDUAL", "X", "HARBERG, FRANKLIN", "FRANKLIN", "HARBERG"],
            ["INDIVIDUAL", "X", "HARBERG JR, FRANKLIN J. JAY JR.", "FRANKLIN J. JAY", "HARBERG JR"],
        ],
        ["entity_type", "donor_key", "contributor_name",
         "contributor_first_name", "contributor_last_name"],
    )
    n = canonicalize_donor_names(df)
    assert n == 3
    assert df["contributor_name"].nunique() == 1
    assert df["contributor_first_name"].nunique() == 1
    assert df["contributor_last_name"].unique().tolist() == ["HARBERG"]
    # composite is rebuilt as "LAST, FIRST"
    assert df["contributor_name"].iloc[0].startswith("HARBERG, FRANKLIN")


def test_names_preserve_literal_null_surname():
    """NULL is a real surname here; canonicalization must not drop it."""
    df = _df(
        [
            ["INDIVIDUAL", "N", "NULL, JAMES", "JAMES", "NULL"],
            ["INDIVIDUAL", "N", "NULL, JAMES", "JAMES", "NULL"],
        ],
        ["entity_type", "donor_key", "contributor_name",
         "contributor_first_name", "contributor_last_name"],
    )
    canonicalize_donor_names(df)
    assert df["contributor_last_name"].unique().tolist() == ["NULL"]
    assert df["contributor_name"].iloc[0] == "NULL, JAMES"


def test_names_reversed_filing_does_not_wipe_first_name():
    """A reversed mis-parse must not win the fullest-first-name pick (HOFFMAN bug)."""
    df = _df(
        [
            ["INDIVIDUAL", "G", "HOFFMAN, GARY", "GARY", "HOFFMAN"],
            ["INDIVIDUAL", "G", "HOFFMAN, GARY", "GARY", "HOFFMAN"],
            ["INDIVIDUAL", "G", "GARY, HOFFMAN", "HOFFMAN", "GARY"],  # reversed mis-parse
        ],
        ["entity_type", "donor_key", "contributor_name",
         "contributor_first_name", "contributor_last_name"],
    )
    canonicalize_donor_names(df)
    assert df["contributor_last_name"].unique().tolist() == ["HOFFMAN"]
    assert df["contributor_first_name"].unique().tolist() == ["GARY"]
    assert df["contributor_name"].unique().tolist() == ["HOFFMAN, GARY"]


def test_names_surname_in_first_field_still_collapses_when_no_real_first():
    """No real first name at all (COHEN/COHEN) collapses to the bare surname."""
    df = _df(
        [
            ["INDIVIDUAL", "C", "COHEN, COHEN", "COHEN", "COHEN"],
            ["INDIVIDUAL", "C", "COHEN, COHEN", "COHEN", "COHEN"],
        ],
        ["entity_type", "donor_key", "contributor_name",
         "contributor_first_name", "contributor_last_name"],
    )
    canonicalize_donor_names(df)
    assert df["contributor_last_name"].unique().tolist() == ["COHEN"]
    assert df["contributor_name"].unique().tolist() == ["COHEN"]


def test_employers_merge_same_firm_to_most_complete():
    df = _df(
        [
            ["INDIVIDUAL", "H", "HARBERG HUVARD LLP"],
            ["INDIVIDUAL", "H", "HARBERG HUVARD LLP"],
            ["INDIVIDUAL", "H", "HARBERG HUVARD JACOBS WADLER LLP"],
            ["INDIVIDUAL", "H", "INTERFAITH MINISTRIES FOR GREATER HOUSTON"],
        ],
        ["entity_type", "donor_key", "contributor_employer"],
    )
    canonicalize_donor_employers(df)
    emps = set(df["contributor_employer"])
    assert "HARBERG HUVARD JACOBS WADLER LLP" in emps
    assert "HARBERG HUVARD LLP" not in emps
    assert "INTERFAITH MINISTRIES FOR GREATER HOUSTON" in emps


def test_employers_do_not_merge_on_single_shared_token():
    df = _df(
        [
            ["INDIVIDUAL", "S", "SMITH LLP"],
            ["INDIVIDUAL", "S", "SMITH JONES LLP"],
        ],
        ["entity_type", "donor_key", "contributor_employer"],
    )
    canonicalize_donor_employers(df)
    assert set(df["contributor_employer"]) == {"SMITH LLP", "SMITH JONES LLP"}


def test_addresses_unify_moved_directional():
    df = _df(
        [
            ["INDIVIDUAL", "A", "1742 GOLF RIDGE DR S", "48302"],
            ["INDIVIDUAL", "A", "1742 S GOLF RIDGE DR", "48302"],
            ["INDIVIDUAL", "A", "1742 GOLF RIDGE DR S", "48302"],
        ],
        ["entity_type", "donor_key", "contributor_street_1", "contributor_zip"],
    )
    canonicalize_donor_addresses(df)
    assert df["contributor_street_1"].nunique() == 1


def test_addresses_keep_grid_addresses_separate():
    """House number is anchored, so grid addresses must NOT collapse."""
    df = _df(
        [
            ["INDIVIDUAL", "G", "100 N 200 W", "84101"],
            ["INDIVIDUAL", "G", "200 N 100 W", "84101"],
        ],
        ["entity_type", "donor_key", "contributor_street_1", "contributor_zip"],
    )
    canonicalize_donor_addresses(df)
    assert df["contributor_street_1"].nunique() == 2


def test_addresses_different_zip_stays_separate():
    df = _df(
        [
            ["INDIVIDUAL", "Z", "1 MAIN ST", "10001"],
            ["INDIVIDUAL", "Z", "1 MAIN ST", "90001"],
        ],
        ["entity_type", "donor_key", "contributor_street_1", "contributor_zip"],
    )
    canonicalize_donor_addresses(df)
    assert df["contributor_street_1"].nunique() == 1  # same string already
    assert set(df["contributor_zip"]) == {"10001", "90001"}  # zips untouched


_GEO_COLS = ["entity_type", "donor_key", "contributor_street_1",
             "latitude", "longitude", "geocode_level"]


def test_geo_unifies_same_spot_and_keeps_far_apart():
    df = _df(
        [
            ["INDIVIDUAL", "A", "123 MAIN ST", 40.71280, -74.00600, "nominatim"],
            ["INDIVIDUAL", "A", "123 MAIN STREET STE 5", 40.71285, -74.00604, "nominatim"],
            ["INDIVIDUAL", "A", "9 FAR AWAY RD", 41.50000, -72.00000, "nominatim"],
        ],
        _GEO_COLS,
    )
    n = canonicalize_donor_addresses_geo(df, radius_m=50)
    assert n == 1
    assert set(df["contributor_street_1"]) == {"123 MAIN ST", "9 FAR AWAY RD"}


def test_geo_skips_city_level_geocodes():
    df = _df(
        [
            ["INDIVIDUAL", "C", "1 A ST", 40.70, -74.00, "nominatim_city"],
            ["INDIVIDUAL", "C", "2 B ST", 40.7001, -74.0001, "nominatim_city"],
        ],
        _GEO_COLS,
    )
    assert canonicalize_donor_addresses_geo(df) == 0


def test_geo_noop_without_coords():
    df = _df(
        [["INDIVIDUAL", "D", "1 MAIN ST"]],
        ["entity_type", "donor_key", "contributor_street_1"],
    )
    assert canonicalize_donor_addresses_geo(df) == 0
