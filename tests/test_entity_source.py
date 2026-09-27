"""FEC's own entity type decides over every name-based guess; a documented override still wins."""
import pandas as pd

from fec.cleaning.donor_consistency.entity import _apply_source_entity_types
from fec.cleaning.donor_consistency.steps import CONSISTENCY_FIXES
from fec.cleaning.entity_source import attach_source_fields, entity_type_sources, source_entity_type
from fec.cleaning.pipeline.reclassify import _reclassify_entities


def _frame(rows):
    df = pd.DataFrame(rows, columns=["contributor_name", "is_individual", "fec_entity_type"])
    df["sub_id"] = [f"s{i}" for i in range(len(df))]
    for column in ("contributor_first_name", "contributor_last_name", "contributor_employer",
                   "contributor_occupation"):
        df[column] = ""
    df["committee_type"] = pd.NA
    df["occupation_category"] = pd.NA
    return df


def test_fec_type_beats_the_name_guess():
    df = _frame([
        ("AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE", False, "ORG"),  # COMMITTEE in the name
        ("HDS HERCULES", True, "ORG"),                               # no identity token at all
        ("SMITH FOR CONGRESS", False, "CCM"),
        ("GOOD HEALTH LLC", False, ""),                              # no FEC type: the guess decides
    ])
    _reclassify_entities(df)
    assert list(df["entity_type"]) == ["ORGANIZATION", "ORGANIZATION", "COMMITTEE/PAC", "ORGANIZATION"]
    assert df.loc[0, "_reclass_reason"] == "fec_source_entity_type"


def test_a_person_fec_types_as_one_is_reclassified_with_work_restored():
    df = _frame([("ACME HOLDINGS", False, "IND")])
    _reclassify_entities(df)
    assert df.loc[0, "entity_type"] == "INDIVIDUAL" and bool(df.loc[0, "is_individual"])
    assert df.loc[0, "_reclass_reason"].startswith("committee_to_individual")


def test_name_consistency_does_not_retype_a_row_fec_typed():
    df = _frame([("DEMOCRACY ENGINE", False, "COM"), ("DEMOCRACY ENGINE LLC", False, "")])
    df.loc[1, "contributor_name"] = "DEMOCRACY ENGINE"  # same name, second row guessed ORGANIZATION
    df["entity_type"] = ["COMMITTEE/PAC", "ORGANIZATION"]
    from fec.cleaning.pipeline.reclassify import _enforce_entity_name_consistency
    _enforce_entity_name_consistency(df)
    assert list(df["entity_type"]) == ["COMMITTEE/PAC", "ORGANIZATION"]


def test_a_later_retype_is_undone_and_overrides_run_after_it():
    df = _frame([("AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE", False, "ORG")])
    df["entity_type"] = "COMMITTEE/PAC"
    assert _apply_source_entity_types(df) == 1
    assert df.loc[0, "entity_type"] == "ORGANIZATION" and df.loc[0, "occupation_category"] == "ORGANIZATION"
    labels = [label for label, *_ in CONSISTENCY_FIXES]
    assert labels.index("apply fec source entity type") < labels.index("apply entity overrides")


def test_source_fields_join_by_sub_id_and_are_empty_without_a_row(tmp_path):
    path = tmp_path / "fec_source_fields.csv"
    path.write_text("sub_id,fec_entity_type,fec_contributor_id,matched_by,fec_sub_id\n1,org,,sub_id,\n")
    df = pd.DataFrame({"sub_id": ["1", "2"], "contributor_name": ["X", "Y"]})
    attach_source_fields(df, path)
    assert df["fec_entity_type"].tolist() == ["ORG", ""]
    attach_source_fields(df, tmp_path / "missing.csv")  # no file yet: every row empty
    assert df["fec_entity_type"].tolist() == ["", ""]
    df.loc[0, "fec_entity_type"] = "xyz"
    assert source_entity_type(df).tolist() == ["", ""]  # an unknown code is no evidence


def test_a_held_or_unresolved_filing_keeps_its_classification():
    # LA VALLEY POLITICAL NETWORK: FEC says IND, the filing stays an unresolved, isolated organization
    df = _frame([("LA VALLEY POLITICAL NETWORK", False, "IND"), ("SMITH, JOHN", True, "ORG")])
    df["entity_type"] = ["ORGANIZATION", "INDIVIDUAL"]
    df["identity_status"] = ["unresolved", "held"]
    assert _apply_source_entity_types(df) == 0
    assert list(df["entity_type"]) == ["ORGANIZATION", "INDIVIDUAL"]
    assert list(entity_type_sources(df, set(), set())) == ["rule", "rule"]


def test_each_row_says_where_its_type_came_from():
    df = _frame([("AIPAC ORG", False, "ORG"), ("HDS HERCULES", False, ""), ("ACME LLC", False, "")])
    df["entity_type"] = ["ORGANIZATION", "ORGANIZATION", "ORGANIZATION"]
    df["identity_status"] = "confirmed"
    assert list(entity_type_sources(df, {"HDS HERCULES"}, set())) == ["fec", "override", "rule"]
    # renamed after the override matched it: the audit trail still says override
    assert list(entity_type_sources(df, set(), {"s2"})) == ["fec", "rule", "override"]


def test_a_campaign_for_a_state_keeps_the_state():
    from fec.cleaning.entity_classification import normalize_business_names
    df = pd.DataFrame({"contributor_name": ["ROB FOR PA", "SMITH & JONES PA", "ACME, LLC"],
                       "entity_type": ["COMMITTEE/PAC", "ORGANIZATION", "ORGANIZATION"]})
    normalize_business_names(df)
    assert list(df["contributor_name"]) == ["ROB FOR PA", "SMITH & JONES", "ACME"]
