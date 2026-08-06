"""The donor's own name in the employer field becomes SELF-EMPLOYED; shared-surname firms are kept."""
import pandas as pd

from fec.cleaning.safety_nets.employer_swaps import (
    _fix_own_name_as_employer,
    _fix_self_employed_consistency,
)


def _frame(rows):
    """rows = (first, middle, last, employer, occupation)"""
    return pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * len(rows),
        "contributor_first_name": [r[0] for r in rows],
        "contributor_middle_name": [r[1] for r in rows],
        "contributor_last_name": [r[2] for r in rows],
        "contributor_employer": [r[3] for r in rows],
        "contributor_occupation": [r[4] for r in rows],
    })


def test_own_name_becomes_self_employed_regardless_of_occupation():
    # FEC stores "LAST, FIRST"; filers write the employer "FIRST LAST" with a real occupation.
    df = _frame([
        ("ALIDA", "", "HOWARD", "ALIDA HOWARD", "RETIRED"),
        ("JEFFREY", "", "HALBRECHT", "JEFFREY HALBRECHT", "PHYSICIAN"),
        ("ERIK", "", "COOPER", "ERIK A COOPER", "DOCTOR"),      # middle initial
        ("URI", "", "KAUFMAN", "KAUFMAN, URI", "REAL ESTATE"),  # reversed order
        ("MARVIN", "S", "ROSEN", "MARVIN S ROSEN", "RETIRED"),  # middle name col
    ])

    n = _fix_own_name_as_employer(df)

    assert n == 5
    assert (df["contributor_employer"] == "SELF-EMPLOYED").all()
    # the occupation must survive untouched
    assert df["contributor_occupation"].tolist() == [
        "RETIRED", "PHYSICIAN", "DOCTOR", "REAL ESTATE", "RETIRED",
    ]


def test_shared_surname_firms_are_kept():
    # Requires the WHOLE name. A shared surname is normal and correct.
    df = _frame([
        ("RICHARD", "", "COMITER", "COMITER, SINGER, BASEMAN & BRAUN", "ATTORNEY"),
        ("NORMAN", "", "BROWNSTEIN", "BROWNSTEIN HYATT FARBER SCHRECK", "LAWYER"),
        ("PAUL", "", "WEISS", "PAUL WEISS", "LAWYER"),  # firm == a real person's name
        ("DAVID", "", "GOLDMAN", "GOLDMAN SACHS", "FINANCE"),
    ])
    before = df["contributor_employer"].tolist()

    n = _fix_own_name_as_employer(df)

    # "PAUL WEISS" is the deliberate trade-off: whole-name equality catches a firm named after a real person.
    assert n == 1
    assert df["contributor_employer"].iloc[2] == "SELF-EMPLOYED"
    assert df["contributor_employer"].tolist()[:2] == before[:2]
    assert df["contributor_employer"].iloc[3] == "GOLDMAN SACHS"


def test_partial_name_is_not_enough():
    df = _frame([
        ("NORMAN", "", "BROWNSTEIN", "BROWNSTEIN", "CHAIRMAN"),   # surname only
        ("ALIDA", "", "", "ALIDA HOWARD", "RETIRED"),             # no surname on file
        ("", "", "HOWARD", "ALIDA HOWARD", "RETIRED"),            # no given name
    ])

    assert _fix_own_name_as_employer(df) == 0
    assert df["contributor_employer"].tolist() == [
        "BROWNSTEIN", "ALIDA HOWARD", "ALIDA HOWARD",
    ]


def test_own_named_legal_company_is_kept():
    df = _frame([
        ("JOEL", "", "REINSTEIN", "JOEL REINSTEIN", "ATTORNEY"),
        ("SCOTT", "", "NAWY", "SCOTT NAWY", "ORTHODONTIST"),
    ])
    df["contributor_employer_original"] = ["JOEL REINSTEIN PLLC", "SCOTT NAWY LLC"]

    assert _fix_own_name_as_employer(df) == 0
    assert df["contributor_employer"].tolist() == ["JOEL REINSTEIN", "SCOTT NAWY"]


def test_self_employed_occupation_does_not_hide_a_company():
    df = _frame([
        ("ELLYN", "", "BANK", "ELLYN BANK LAW", "SELF-EMPLOYED"),
        ("LIZZIE", "", "TISCH", "LTD X LIZZIE TISCH", "SELF-EMPLOYED"),
        ("ALIDA", "", "HOWARD", "ALIDA HOWARD", "SELF-EMPLOYED"),
    ])

    assert _fix_self_employed_consistency(df) == 1
    assert df["contributor_employer"].tolist() == [
        "ELLYN BANK LAW", "LTD X LIZZIE TISCH", "SELF-EMPLOYED",
    ]


def test_short_legal_company_keeps_its_suffix():
    from fec.cleaning.employer_synonyms.apply import fix_normalized_mid_suffix
    from fec.cleaning.employer_synonyms.normalize import normalize_employer_canonical

    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"],
        "contributor_employer": ["JB INC"],
    })
    normalize_employer_canonical(df)
    fix_normalized_mid_suffix(df)

    assert df["contributor_employer"].iloc[0] == "JB INC"


def test_committees_are_untouched():
    df = _frame([("ALIDA", "", "HOWARD", "ALIDA HOWARD", "RETIRED")])
    df["entity_type"] = "COMMITTEE/PAC"

    assert _fix_own_name_as_employer(df) == 0
    assert df["contributor_employer"].iloc[0] == "ALIDA HOWARD"


def test_resolve_guard_matches_reversed_name_order():
    """The resolve-stage twin: previous_employer == the donor's own name."""
    from fec.resolve.pipeline.quality_fixes import _normalize_previous_employer_column

    df = pd.DataFrame({
        "contributor_name": ["HOWARD, ALIDA", "COMITER, RICHARD", "ROSEN, MARVIN"],
        "previous_employer": [
            "ALIDA HOWARD",                       # own name, reversed -> cleared
            "COMITER, SINGER, BASEMAN & BRAUN",   # real firm, shared surname -> kept
            "MARVIN S ROSEN",                     # own name + middle initial -> cleared
        ],
    })

    _normalize_previous_employer_column(df)

    assert df["previous_employer"].iloc[0] == ""
    assert df["previous_employer"].iloc[1] != ""
    assert df["previous_employer"].iloc[2] == ""
