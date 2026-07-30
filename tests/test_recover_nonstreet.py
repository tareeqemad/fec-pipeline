"""_recover_nonstreet_from_donor fills fragment streets from the donor's own filings."""
import pandas as pd

from fec.cleaning.pipeline.address_fixes import _recover_nonstreet_from_donor


def _row(street, name="GHITIS, LEO", city="GOLDEN BEACH", state="FL", et="INDIVIDUAL"):
    return {"contributor_street_1": street, "contributor_name": name,
            "contributor_city": city, "contributor_state": state, "entity_type": et}


def test_recovers_fragment_from_same_donor_clean_filing():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR"),   # clean filing
        _row("240 GOLDEN BEACH DR"),
        _row("GOLDEN BEACH"),          # fragment, should be recovered
    ])
    n = _recover_nonstreet_from_donor(df)
    assert n == 1
    assert (df["contributor_street_1"] == "240 GOLDEN BEACH DR").all()


def test_never_overwrites_a_good_street():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR"),
        _row("17 OCEAN BLVD"),          # different but usable, must stay
    ])
    before = df["contributor_street_1"].tolist()
    _recover_nonstreet_from_donor(df)
    assert df["contributor_street_1"].tolist() == before


def test_never_recovers_one_fragment_with_another():
    # No usable street anywhere for this donor -> nothing to recover.
    df = pd.DataFrame([_row("GOLDEN BEACH"), _row("GOLDEN BEACH")])
    assert _recover_nonstreet_from_donor(df) == 0
    assert (df["contributor_street_1"] == "GOLDEN BEACH").all()


def test_state_single_fallback_when_city_mismatches():
    # City is garbled so name+city+state misses, but the donor has exactly one clean FL street -> safe.
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", city="GOLDEN BEACH"),
        _row("GOLDEN BEACH", city="GOLDEN BCH"),   # city typo
    ])
    n = _recover_nonstreet_from_donor(df)
    assert n == 1
    assert (df["contributor_street_1"] == "240 GOLDEN BEACH DR").all()


def test_state_fallback_skipped_when_two_streets():
    # Donor has TWO clean streets in FL -> ambiguous -> do NOT guess.
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", city="GOLDEN BEACH"),
        _row("17 OCEAN BLVD", city="MIAMI"),
        _row("SOME FRAGMENT", city="TAMPA"),   # which one? skip
    ])
    n = _recover_nonstreet_from_donor(df)
    assert n == 0
    assert df.loc[2, "contributor_street_1"] == "SOME FRAGMENT"


def test_scoped_to_individuals_and_same_place():
    df = pd.DataFrame([
        _row("240 GOLDEN BEACH DR", et="COMMITTEE/PAC"),  # committee clean
        _row("GOLDEN BEACH", et="COMMITTEE/PAC"),         # committee fragment, not touched
        _row("PINE ST FRAGMENT", name="DOE, JANE", city="MIAMI", state="FL"),  # no donor history
    ])
    before = df["contributor_street_1"].tolist()
    _recover_nonstreet_from_donor(df)
    assert df["contributor_street_1"].tolist() == before
