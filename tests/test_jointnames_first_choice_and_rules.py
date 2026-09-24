"""Canonical first name: equally full spellings go to the donor's majority, not
to filing order; plus the curated identity rules added by the joint-name audit
of 2026-09-23. Fixtures are the real filing counts from data/contributions.csv.
"""
import pandas as pd

from fec.donor_match import apply_donor_key, match_donors
from fec.donor_match import keys as K
from fec.donor_match import rules as R
from fec.donor_match.canonicalize import _choose_first


def test_equal_length_spellings_go_to_the_majority_not_the_first_filed():
    # round-2 audit: SCHLUSSEL ADDM (1) over ADAM (5), RADOW LINDY (2) over LINDA (12)
    assert _choose_first(["ADDM"] + ["ADAM"] * 5) == "ADAM"
    assert _choose_first(["LINDY"] * 2 + ["LINDA"] * 12) == "LINDA"
    assert _choose_first(["DEBBI"] * 6 + ["DEBRA"] * 14) == "DEBRA"


def test_conflicting_middle_initials_go_to_the_majority():
    # FELGOISE 7139 SHEAFF LN: MARC L (11) / MARC I (10); FEC has 50 L vs 12 I
    filings = ["MARC I"] + ["MARC"] * 47 + ["MARC L"] * 11 + ["MARC I"] * 9
    assert _choose_first(filings) == "MARC L"


def test_a_period_does_not_make_an_initial_fuller():
    # GINDI ISAAC S. / ISAAC A: one each, so the first filed decides, not the period
    assert _choose_first(["ISAAC"] * 4 + ["ISAAC A", "ISAAC S."]) == "ISAAC A"
    assert _choose_first(["ISAAC"] * 4 + ["ISAAC S.", "ISAAC A"]) == "ISAAC S."


def test_fullest_name_still_wins_and_keeps_its_period():
    assert _choose_first(["MARK"] * 9 + ["MARK L."]) == "MARK L."
    assert _choose_first(["MARK L"] * 3 + ["MARK L."]) == "MARK L."
    assert _choose_first(["DAVID"] * 14 + ["DAVID-JACQUES"] * 4 + ["DAVID JACQUES"] * 3) == "DAVID-JACQUES"
    assert _choose_first(["DAVID JACQUES"] * 3 + ["DAVID-JACQUES"] * 4) == "DAVID-JACQUES"


# -- curated rules ---------------------------------------------------------------

def test_glued_and_spaced_joint_names_have_separate_rules():
    pairs = [
        ("HELLER, GAYLE", "HELLER, GAYLEDAVID"),
        ("GOLDBERG, ADAM", "GOLDBERG, AMIRAADAM"),
        ("MORRIS, ELLEN", "MORRIS, ELLENSTU"),
        ("MORRIS, ELLEN", "MORRIS, ELLEN STU"),
        ("RUB, BENY", "RUB, BENY MARTA"),
        ("BRAVERMAN, HELEN", "BRAVERMAN, HELEN DAVID"),
        ("DAITCH, LOREN", "DAITCH, LOREN STUART"),
        ("BREIN, DAVID", "BREIN, DAVID RENEE"),
    ]
    for a, b in pairs:
        assert R.names_must_stay_separate(a, b), (a, b)
    # SPELLMAN relies on the automatic guard: a pairwise rule would also cut
    # the joint filing's link between Marc's two addresses and split him
    assert not R.names_must_stay_separate("SPELLMAN, MARC", "SPELLMAN, MARC MELISSA")


def test_ishofsky_avi_is_david_avi():
    assert R.resolve_donor_key("f1872fae897f") == "df17c7513a64"


def _gindi(first, city, zip5, street):
    return dict(
        entity_type="INDIVIDUAL", contributor_name=f"GINDI, {first}",
        contributor_first_name=first, contributor_last_name="GINDI",
        contributor_city=city, contributor_state="NY", contributor_zip=zip5,
        contributor_street_1=street, contributor_employer="ASG EQUITIES",
        contributor_occupation="CEO", occupation_category="EXECUTIVE / C-SUITE",
        _generational_suffix="",
    )


def _gindi_rows():
    return pd.DataFrame(
        [_gindi("ISAAC", "BROOKLYN", "11223", "1865 E 8TH ST")] * 4
        + [_gindi("ISAAC A", "BROOKLYN", "11223", "1865 E 8TH ST")]
        + [_gindi("ISAAC", "NEW YORK", "10280", "380 RECTOR PL")] * 10
        + [_gindi("ISAAC S", "NEW YORK", "10280", "380 RECTOR PL")]
    )


def _gindi_keys():
    df = _gindi_rows()
    rid_to_key, _ = match_donors(df, verbose=False)
    df = apply_donor_key(df, rid_to_key)
    return df, df.groupby("contributor_city")["donor_key"].agg(set)


def test_the_two_isaac_gindis_stay_apart():
    df, keys = _gindi_keys()
    assert len(keys["BROOKLYN"]) == 1 and len(keys["NEW YORK"]) == 1
    assert keys["BROOKLYN"] != keys["NEW YORK"]
    K.validate_separations(df)


def test_without_the_rule_the_shared_employer_joins_them(monkeypatch):
    """ASG EQUITIES + same state scored 55 (threshold 50) in the 2026-09-23 run."""
    monkeypatch.setattr(R, "SEPARATE_IDENTITIES", set())
    _, keys = _gindi_keys()
    assert keys["BROOKLYN"] == keys["NEW YORK"]


def test_separation_check_fails_when_a_joint_row_shares_the_filers_key():
    df = pd.DataFrame([
        dict(donor_key="K", entity_type="INDIVIDUAL", contributor_name=name,
             contributor_city="DELRAY BEACH", contributor_state="FL")
        for name in ("MORRIS, ELLEN", "MORRIS, ELLEN STU")
    ])
    try:
        K.validate_separations(df)
    except ValueError as err:
        assert "MORRIS, ELLEN" in str(err)
    else:
        raise AssertionError("a verified joint pair on one key must fail")
