"""Street cleaning preserves incomplete addresses instead of borrowing another filing."""
import pandas as pd

from fec.cleaning.addresses.streets import clean_streets


def _row(street, name="GHITIS, LEO", city="GOLDEN BEACH", state="FL", et="INDIVIDUAL"):
    return {"contributor_street_1": street, "contributor_name": name,
            "contributor_street_2": "", "contributor_first_name": "LEO", "contributor_last_name": "GHITIS",
            "contributor_city": city, "contributor_state": state, "entity_type": et}


def test_place_name_does_not_borrow_the_donors_street():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR"),   # clean filing
        _row("240 GOLDEN BEACH DR"),
        _row("GOLDEN BEACH"),          # place description, no numbered street evidence
    ])
    df, _ = clean_streets(df)
    assert df["contributor_street_1"].tolist() == [
        "240 GOLDEN BEACH DR", "240 GOLDEN BEACH DR", "GOLDEN BEACH",
    ]


def test_never_overwrites_a_good_street():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR"),
        _row("17 OCEAN BLVD"),          # different but usable, must stay
    ])
    before = df["contributor_street_1"].tolist()
    df, _ = clean_streets(df)
    assert df["contributor_street_1"].tolist() == before


def test_never_recovers_one_fragment_with_another():
    # No usable street anywhere for this donor -> nothing to recover.
    df = pd.DataFrame([_row("GOLDEN BEACH"), _row("GOLDEN BEACH")])
    df, _ = clean_streets(df)
    assert (df["contributor_street_1"] == "GOLDEN BEACH").all()


def test_state_single_street_does_not_prove_a_place_description():
    # One street elsewhere in the state does not identify this filing's address.
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", city="GOLDEN BEACH"),
        _row("GOLDEN BEACH", city="GOLDEN BCH"),   # city typo
    ])
    df, _ = clean_streets(df)
    assert df["contributor_street_1"].tolist() == ["240 GOLDEN BEACH DR", "GOLDEN BEACH"]


def test_state_fallback_skipped_when_two_streets():
    # Donor has TWO clean streets in FL -> ambiguous -> do NOT guess.
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", city="GOLDEN BEACH"),
        _row("17 OCEAN BLVD", city="MIAMI"),
        _row("SOME FRAGMENT", city="TAMPA"),   # which one? skip
    ])
    df, _ = clean_streets(df)
    assert df.loc[2, "contributor_street_1"] == "SOME FRAGMENT"


def test_scoped_to_individuals_and_same_place():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", et="COMMITTEE/PAC"),  # committee clean
        _row("GOLDEN BEACH", et="COMMITTEE/PAC"),         # committee fragment, not touched
        _row("PINE ST FRAGMENT", name="DOE, JANE", city="MIAMI", state="FL"),  # no donor history
    ])
    before = df["contributor_street_1"].tolist()
    df, _ = clean_streets(df)
    assert df["contributor_street_1"].tolist() == before
