"""tests/test_misclassified_foundation.py — an entity word in the surname slot
does not, by itself, prove the row is an organisation.

TRUST / FUND / SOCIETY are ordinary surnames. The rule must catch an org name
typed into the person fields without catching a person whose family name happens
to be one of those words — the same trap as the house rule that 'NULL' is a real
surname."""
import numpy as np
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
        # object dtype — the rule writes a string into it
        "committee_type": pd.Series([None] * len(rows), dtype=object),
    })


def test_real_person_with_entity_surname_is_untouched():
    """Mark Trust of Atlanta — a real donor the rule used to rename to his
    employer field, which read 'NOT EMPLOYED'."""
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
    """The shape the rule exists for: the org's remaining words are crammed
    into the given-name slot."""
    df = _frame([("WEINER MARC", "FOUNDATION", "WEINER MARC FOUNDATION")])

    assert _fix_misclassified_foundation(df) == 1
    assert df["entity_type"].iloc[0] == "COMMITTEE/PAC"
    assert df["contributor_name"].iloc[0] == "WEINER MARC FOUNDATION"
    assert pd.isna(df["contributor_first_name"].iloc[0])
    assert pd.isna(df["contributor_last_name"].iloc[0])


def test_status_word_employer_is_not_used_as_the_entity_name():
    """Reclassify if the name shape warrants it, but never adopt a status word
    as an organisation's name."""
    df = _frame([("SMITH JOHN", "TRUST", "NOT EMPLOYED")])

    assert _fix_misclassified_foundation(df) == 1
    assert df["entity_type"].iloc[0] == "COMMITTEE/PAC"
    # name keeps its original text rather than becoming "NOT EMPLOYED"
    assert df["contributor_name"].iloc[0] == "TRUST, SMITH JOHN"
