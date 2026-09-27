"""A unit written twice, a doubled hash, or a floor's ordinal left in street_1 is cleaned once."""
import pandas as pd
import pytest

from fec.cleaning.addresses.streets import clean_streets


def _clean(street_1, street_2=""):
    df = pd.DataFrame({"contributor_street_1": [street_1], "contributor_street_2": [street_2],
                       "contributor_first_name": ["JANE"], "contributor_last_name": ["DOE"],
                       "contributor_city": [""], "contributor_zip": [""]})
    out, _ = clean_streets(df)
    return out.loc[0, "contributor_street_1"], out.loc[0, "contributor_street_2"]


@pytest.mark.parametrize("filed, cleaned", [
    (("11693 SAN VICENTE BLVD, ##266", ""), ("11693 SAN VICENTE BLVD", "# 266")),    # doubled hash
    (("2711 SAKLAN INDIAN #1", "APT 1"), ("2711 SAKLAN INDIAN", "APT 1")),          # unit in both fields
    (("7400 E CRESTLINE CIR #250", "STE 250"), ("7400 E CRESTLINE CIR", "STE 250")),
    (("1555 BROADWAY STREET, 4TH FLOOR, 4", ""), ("1555 BROADWAY ST", "FL 4")),     # floor's ordinal
    (("1772 E 8TH ST 8TH", "FL 8"), ("1772 E 8TH ST", "FL 8")),
])
def test_unit_leftovers_are_cleaned_once(filed, cleaned):
    assert _clean(*filed) == cleaned


def test_a_different_unit_or_a_street_named_by_an_ordinal_stays():
    assert _clean("2711 SAKLAN INDIAN #2", "APT 1") == ("2711 SAKLAN INDIAN #2", "APT 1")  # not the same unit
    assert _clean("123 W 4TH", "FL 4") == ("123 W 4TH", "FL 4")  # 4TH is the street, no type before it


def test_each_leftover_is_logged_under_its_own_reason():
    from fec.cleaning.audit_trail import AuditTrail
    from fec.cleaning.pipeline.address_stage import _clean_street_text

    df = pd.DataFrame({
        "sub_id": ["h", "f", "b"],
        "contributor_street_1": ["2711 SAKLAN INDIAN #1", "11693 SAN VICENTE BLVD, ##266",
                                 "1555 BROADWAY STREET, 4TH FLOOR, 4"],
        "contributor_street_2": ["APT 1", "", ""],
        "contributor_first_name": "JANE", "contributor_last_name": "DOE",
        "contributor_city": "X", "contributor_state": "CA", "contributor_zip": "90049",
    })
    trail = AuditTrail()
    trail.start(df)
    _clean_street_text(df, trail, lambda _msg: None)
    reasons = {(r["sub_id"], r["field"]): r["reason"] for r in trail.records if r["step"] == "streets_normalize"}
    assert reasons[("h", "contributor_street_1")] == "street_1_unit_repeating_street_2_dropped"
    assert reasons[("f", "contributor_street_2")] == "doubled_hash_unit_moved_to_street_2"
    assert reasons[("b", "contributor_street_1")] == "floor_ordinal_left_in_street_1_dropped"
