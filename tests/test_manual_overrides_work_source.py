"""A manual override that names work the filing never did is marked as coming from outside it."""
import pandas as pd

from fec.cleaning.manual_overrides import _brings_outside_work


def _filing(employer, occupation, first="JOSH", last="BLATT"):
    return pd.DataFrame([{
        "contributor_employer": employer, "contributor_occupation": occupation,
        "contributor_first_name": first, "contributor_last_name": last,
    }])


def test_a_firm_from_other_filings_is_outside_the_filing():
    assert _brings_outside_work(_filing("BLATT", "HOMEBUILDER"), {"contributor_employer": "JOHN HENRY HOMES"})


def test_the_filers_own_surname_names_no_firm():
    filing = _filing("GIVNER", "ATTORNEY", first="JOEY", last="GIVNER")
    assert _brings_outside_work(filing, {"contributor_employer": "GIVNER LAW GROUP"})


def test_an_occupation_from_other_filings_is_outside_the_filing():
    filing = _filing("BOARD OF EDUCATION", "BOE", first="HILARY", last="AUERBACH")
    assert _brings_outside_work(filing, {"contributor_employer": "BOARD OF EDUCATION", "contributor_occupation": "TEACHER"})


def test_swapped_or_respelled_filed_words_stay_filed():
    assert not _brings_outside_work(_filing("CHAIRMAN", "KIMCO"), {"contributor_employer": "KIMCO REALTY"})
    assert not _brings_outside_work(_filing("GOOGEL", "ENGINEER"), {"contributor_employer": "GOOGLE"})


def test_an_abbreviation_the_filing_wrote_stays_filed():
    assert not _brings_outside_work(_filing("SFSS", "DOC"), {"contributor_employer": "SAN FRANCISCO SPINE SURGEONS"})
    assert not _brings_outside_work(_filing("AWM", "F A"), {"contributor_occupation": "FINANCIAL ADVISOR"})


def test_statuses_and_cleared_cells_are_not_work_details():
    assert not _brings_outside_work(_filing("CLARA MILLER", "INVESTOR"), {"contributor_employer": "SELF-EMPLOYED"})
    assert not _brings_outside_work(_filing("ACME", "CEO"), {"contributor_employer": pd.NA})
