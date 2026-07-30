"""An entity word in the surname slot does not by itself prove the row is an organisation."""
import pandas as pd

from fec.cleaning.safety_nets.names import _fix_misclassified_foundation


def _frame(rows):
    """rows = (first, last, employer)"""
    return pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * len(rows),
        "contributor_name": [f"{r[1]}, {r[0]}" for r in rows],
        "contributor_first_name": [r[0] for r in rows],
        "contributor_last_name": [r[1] for r in rows],
        "contributor_employer": [r[2] for r in rows],
        "is_individual": [True] * len(rows),
        "occupation_status": ["DISCLOSED"] * len(rows),
        # object dtype: the rule writes a string into it
        "committee_type": pd.Series([None] * len(rows), dtype=object),
    })


def test_real_person_with_entity_surname_is_untouched():
    """A real donor with an entity-word surname must not be renamed or reclassified."""
    df = _frame([
        ("MARK", "TRUST", "NOT EMPLOYED"),
        ("SUSAN", "FUND", "ACME WIDGETS INC"),
        ("DAVID", "SOCIETY", ""),
    ])

    assert _fix_misclassified_foundation(df) == 0
    assert df["entity_type"].tolist() == ["INDIVIDUAL"] * 3
    assert df["contributor_name"].tolist() == ["TRUST, MARK", "FUND, SUSAN", "SOCIETY, DAVID"]
    assert df["contributor_last_name"].tolist() == ["TRUST", "FUND", "SOCIETY"]


def test_org_name_in_person_fields_is_reclassified():
    """Org words crammed into the given-name slot get reclassified."""
    df = _frame([("WEINER MARC", "FOUNDATION", "WEINER MARC FOUNDATION")])

    assert _fix_misclassified_foundation(df) == 1
    assert df["entity_type"].iloc[0] == "COMMITTEE/PAC"
    assert df["contributor_name"].iloc[0] == "WEINER MARC FOUNDATION"
    assert pd.isna(df["contributor_first_name"].iloc[0])
    assert pd.isna(df["contributor_last_name"].iloc[0])


def test_status_word_employer_is_not_used_as_the_entity_name():
    """Reclassify, but never adopt a status word as the organisation's name."""
    df = _frame([("SMITH JOHN", "TRUST", "NOT EMPLOYED")])

    assert _fix_misclassified_foundation(df) == 1
    assert df["entity_type"].iloc[0] == "COMMITTEE/PAC"
    # name keeps its original text rather than becoming "NOT EMPLOYED"
    assert df["contributor_name"].iloc[0] == "TRUST, SMITH JOHN"
