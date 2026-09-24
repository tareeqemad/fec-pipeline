"""Two people with one name in one city are kept apart by a ZIP-level separation rule."""
from fec.donor_match import rules
from fec.donor_match.keys import individual_record_id


def _rule(name_a, city_a, state_a, zip_a, name_b, city_b, state_b, zip_b):
    return {"action": "separate", "name_a": name_a, "name_b": name_b, "city_a": city_a,
            "state_a": state_a, "zip_a": zip_a, "city_b": city_b, "state_b": state_b, "zip_b": zip_b}


ROSENBERG = [
    _rule("ROSENBERG, ANDREW", "NEW YORK", "NY", "10128", "ROSENBERG, ANDREW", "NEW YORK", "NY", "10017"),
    _rule("ROSENBERG, ANDREW", "NEW YORK", "NY", "10128", "ROSENBERG, ANDREW", "HAWORTH", "NJ", "07641"),
]
GINDI = [_rule("GINDI, ISAAC", "BROOKLYN", "NY", "11223", "GINDI, ISAAC", "NEW YORK", "NY", "10280")]


def test_a_same_city_rule_splits_that_name_by_zip_only():
    _names, identities, zip_names = rules._load_separations(ROSENBERG + GINDI)

    assert zip_names == frozenset({"ROSENBERG, ANDREW"})
    # the physician is kept from the lawyer's office (same city) and his home
    assert frozenset({"ROSENBERG, ANDREW|NEW YORK|NY|10128", "ROSENBERG, ANDREW|NEW YORK|NY|10017"}) in identities
    assert frozenset({"ROSENBERG, ANDREW|NEW YORK|NY|10128", "ROSENBERG, ANDREW|HAWORTH|NJ|07641"}) in identities
    # a rule between two cities keeps working by city
    assert frozenset({"GINDI, ISAAC|BROOKLYN|NY", "GINDI, ISAAC|NEW YORK|NY"}) in identities


def test_only_a_zip_split_name_gets_a_zip_in_its_profile_id(monkeypatch):
    monkeypatch.setattr(rules, "ZIP_SPLIT_NAMES", frozenset({"ROSENBERG, ANDREW"}))

    assert rules.split_zip("ROSENBERG, ANDREW", "10128-1234") == "10128"
    assert rules.split_zip("DOE, JANE", "10128") == ""
    assert individual_record_id("DOE, JANE", "NEW YORK", "NY") == "DOE, JANE|NEW YORK|NY"
    assert individual_record_id("ROSENBERG, ANDREW", "NEW YORK", "NY", "", "10128") == (
        "ROSENBERG, ANDREW|NEW YORK|NY|@10128")


def test_the_lawyers_office_and_home_may_still_join(monkeypatch):
    _names, identities, _zips = rules._load_separations(ROSENBERG)
    monkeypatch.setattr(rules, "SEPARATE_IDENTITIES", identities)
    monkeypatch.setattr(rules, "SEPARATE_NAMES", set())
    office = {"name": "ROSENBERG, ANDREW", "city": "NEW YORK", "state": "NY", "zip5": "10017"}
    home = {"name": "ROSENBERG, ANDREW", "city": "HAWORTH", "state": "NJ", "zip5": "07641"}
    physician = {"name": "ROSENBERG, ANDREW", "city": "NEW YORK", "state": "NY", "zip5": "10128"}

    assert not rules.identities_must_stay_separate(office, home)
    assert rules.identities_must_stay_separate(physician, office)
    assert rules.identities_must_stay_separate(physician, home)
