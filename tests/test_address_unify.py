"""_unify_street_spacing collapses spacing/punctuation-only street variants."""
import pandas as pd

from fec.cleaning.pipeline.address_fixes import _unify_street_spacing


def _df(streets):
    return pd.DataFrame({
        "contributor_street_1": streets,
        "contributor_name": ["DOE, JANE"] * len(streets),
        "contributor_city": ["MIAMI"] * len(streets),
        "contributor_state": ["FL"] * len(streets),
    })


def test_collapses_space_and_punctuation_variants():
    # Same letters/digits in order, provably one address.
    df = _df([
        "16201 MEADOWRIDGE WAY", "16201 MEADOWRIDGE WAY", "16201 MEADOWRIDGE WAY",
        "16201 MEADOW RIDGE WAY",          # extra space
        "201 AQUA AVE PH-2", "201 AQUA AVE PH2",   # hyphen vs none
        "143 BEAR'S CLUB DR",              # apostrophe
    ])
    # add the canonical for BEAR'S so it has a dominant peer
    df = pd.concat([df, _df(["143 BEARS CLUB DR", "143 BEARS CLUB DR"])], ignore_index=True)
    n = _unify_street_spacing(df)
    s = df["contributor_street_1"].tolist()
    assert "16201 MEADOW RIDGE WAY" not in s          # folded into dominant
    assert s.count("16201 MEADOWRIDGE WAY") == 4
    assert "201 AQUA AVE PH2" not in s or "201 AQUA AVE PH-2" not in s  # one form wins
    assert "143 BEAR'S CLUB DR" not in s              # folded into BEARS (dominant)
    assert n >= 3


def test_does_not_merge_letter_typo_or_move():
    # A dropped letter or a different house number is a different fingerprint, kept apart.
    df = _df([
        "8 SPENCEHILL CT", "8 SPENCEHILL CT",
        "8 SPENCEHIL CT",          # missing L, not a spacing variant
        "100 OAK ST", "200 OAK ST",  # different house number = different address
    ])
    _unify_street_spacing(df)
    after = df["contributor_street_1"].tolist()
    assert "8 SPENCEHIL CT" in after        # typo left untouched
    assert "100 OAK ST" in after and "200 OAK ST" in after  # move left untouched


def test_scoped_per_donor_never_across_people():
    # Two different donors with spacing-variant streets must NOT cross-merge.
    df = pd.DataFrame({
        "contributor_street_1": ["10 MEADOW RIDGE WAY", "10 MEADOWRIDGE WAY"],
        "contributor_name": ["DOE, JANE", "ROE, JOHN"],
        "contributor_city": ["MIAMI", "MIAMI"],
        "contributor_state": ["FL", "FL"],
    })
    before = df["contributor_street_1"].tolist()
    _unify_street_spacing(df)
    assert df["contributor_street_1"].tolist() == before   # untouched
