"""Two employers joined by a slash get one spacing style; tight slashes stay."""
import pandas as pd

from fec.cleaning.employer_synonyms.apply import tidy_employer_slashes, tidy_slash_spacing


def test_loose_or_doubled_slashes_become_spaced():
    assert tidy_employer_slashes("SUMMIT HEALTH/ VILLAGEMD") == "SUMMIT HEALTH / VILLAGEMD"
    assert tidy_employer_slashes("EMERALD//ARS") == "EMERALD / ARS"
    assert tidy_employer_slashes("FULLSTORY // GA TECH") == "FULLSTORY / GA TECH"
    assert tidy_employer_slashes("STATE OF OHIO/ HAMILTON COUNTY") == "STATE OF OHIO / HAMILTON COUNTY"
    assert tidy_employer_slashes("POSTERMEDIA / W INTERNATIONAL") == "POSTERMEDIA / W INTERNATIONAL"


def test_tight_slashes_and_missing_values_are_left_alone():
    assert tidy_employer_slashes("BRIDGESTONE/FIRESTONE") == "BRIDGESTONE/FIRESTONE"
    assert tidy_employer_slashes("C/O") == "C/O"
    assert pd.isna(tidy_employer_slashes(float("nan")))


def test_only_individual_rows_change():
    df = pd.DataFrame({"entity_type": ["INDIVIDUAL", "COMMITTEE"],
                       "contributor_employer": ["EMERALD//ARS", "EMERALD//ARS"]})
    df, n = tidy_slash_spacing(df)
    assert n == 1 and df.contributor_employer.tolist() == ["EMERALD / ARS", "EMERALD//ARS"]
