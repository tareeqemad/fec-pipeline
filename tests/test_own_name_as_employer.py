"""tests/test_own_name_as_employer.py — the donor's own name in the employer
field becomes SELF-EMPLOYED instead of a phantom one-person company, while
firms that merely SHARE a donor's surname are left alone.

Covers both halves of the fix: the cleaning-stage net (contributor_employer)
and the resolve-stage guard (previous_employer)."""
import pandas as pd

from fec.cleaning.safety_nets.employer import _fix_own_name_as_employer


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
    # The bug these rows document: the FEC stores "LAST, FIRST" while filers
    # write the employer "FIRST LAST", and the occupation is a real one
    # (RETIRED / PHYSICIAN), not the 'SELF-EMPLOYED' the older net required.
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
    # the occupation is real information — it must survive untouched
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

    # "PAUL WEISS" is the one genuine ambiguity: a Paul Weiss who names the firm
    # is indistinguishable from one who named himself. Whole-name equality means
    # it IS caught — documented here so the trade-off is deliberate, not a surprise.
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


def test_committees_are_untouched():
    df = _frame([("ALIDA", "", "HOWARD", "ALIDA HOWARD", "RETIRED")])
    df["entity_type"] = "COMMITTEE/PAC"

    assert _fix_own_name_as_employer(df) == 0
    assert df["contributor_employer"].iloc[0] == "ALIDA HOWARD"


def test_resolve_guard_matches_reversed_name_order():
    """The resolve-stage twin: previous_employer == the donor's own name."""
    from fec.resolve.pipeline.apply import _normalize_previous_employer_column

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
