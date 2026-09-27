"""FEC's own entity type decides over every name-based guess; a documented override still wins."""
import pandas as pd

from fec.cleaning.donor_consistency.entity import _apply_source_entity_types
from fec.cleaning.donor_consistency.steps import CONSISTENCY_FIXES
from fec.cleaning.entity_source import ensure_source_columns, source_entity_type
from fec.cleaning.pipeline.reclassify import _reclassify_entities


def _frame(rows):
    df = pd.DataFrame(rows, columns=["contributor_name", "is_individual", "fec_entity_type"])
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


def test_rows_pulled_before_the_fields_existed_have_them_empty():
    df = pd.DataFrame({"contributor_name": ["X"]})
    ensure_source_columns(df)
    assert df.loc[0, "fec_entity_type"] == "" and df.loc[0, "fec_contributor_id"] == ""
    assert source_entity_type(df).tolist() == [""]
    df.loc[0, "fec_entity_type"] = "xyz"
    assert source_entity_type(df).tolist() == [""]  # an unknown code is no evidence
