"""Bare DEVELOPER is specialised per filing from its employer, never by a blanket rule."""
import pandas as pd

from fec.cleaning.safety_nets.occupation_context import (
    _disambiguate_vague_occupation,
    _employer_signal,
)


def _frame(rows):
    return pd.DataFrame(rows, columns=['entity_type', 'contributor_name', 'contributor_employer',
                                       'contributor_occupation', 'occupation_category'])


def test_employer_name_decides_only_when_it_names_the_industry():
    assert _employer_signal('CHESAPEAKE REALTY PARTNERS') == 'REAL ESTATE'
    assert _employer_signal('SOLOMON BAY FINE HOMES LLC') == 'REAL ESTATE'
    assert _employer_signal('GOOGLE') == 'TECHNOLOGY'
    assert _employer_signal('ACME SOFTWARE INC') == 'TECHNOLOGY'
    assert _employer_signal('ROCK COMPANIES') is None          # name says nothing
    assert _employer_signal('APOLLO CAPITAL PARTNERS') is None  # finance words are not evidence
    assert _employer_signal('PROPERTY TECH LLC') is None        # names both industries
    assert _employer_signal('SELF-EMPLOYED') is None
    assert _employer_signal(None) is None


def test_developer_is_decided_per_filing():
    df = _frame([
        ('INDIVIDUAL', 'A, ONE', 'TERRACO REAL ESTATE', 'DEVELOPER', 'TECHNOLOGY'),
        ('INDIVIDUAL', 'B, TWO', 'GOOGLE', 'DEVELOPER', 'TECHNOLOGY'),
        ('INDIVIDUAL', 'C, THREE', 'ROCK COMPANIES', 'DEVELOPER', 'TECHNOLOGY'),
    ])
    assert _disambiguate_vague_occupation(df) == 2
    assert df['contributor_occupation'].tolist() == ['REAL ESTATE DEVELOPER', 'SOFTWARE DEVELOPER', 'DEVELOPER']
    assert df['occupation_category'].tolist() == ['REAL ESTATE', 'TECHNOLOGY', 'TECHNOLOGY']


def test_colleagues_decide_when_the_name_is_silent():
    df = _frame([
        ('INDIVIDUAL', 'A, ONE', 'ROCK COMPANIES', 'DEVELOPER', 'TECHNOLOGY'),
        ('INDIVIDUAL', 'B, TWO', 'ROCK COMPANIES', 'REAL ESTATE', 'REAL ESTATE'),
        ('INDIVIDUAL', 'B, TWO', 'ROCK COMPANIES', 'REAL ESTATE', 'REAL ESTATE'),   # same person twice
        ('INDIVIDUAL', 'C, THREE', 'ROCK COMPANIES', 'REAL ESTATE INVESTOR', 'REAL ESTATE'),
        ('INDIVIDUAL', 'D, FOUR', 'ROCK COMPANIES', 'PROPERTY MANAGER', 'REAL ESTATE'),
        ('INDIVIDUAL', 'E, FIVE', 'ROCK COMPANIES', 'CEO', 'EXECUTIVE / C-SUITE'),
    ])
    # 4 colleagues, 3 of them real estate (75%) -> decided
    assert _disambiguate_vague_occupation(df) == 1
    assert df.loc[0, 'contributor_occupation'] == 'REAL ESTATE DEVELOPER'
    assert df.loc[0, 'occupation_category'] == 'REAL ESTATE'


def test_colleagues_are_counted_as_people_across_all_industries():
    rows = [('INDIVIDUAL', 'A, ONE', 'ROCK COMPANIES', 'DEVELOPER', 'TECHNOLOGY')]
    rows += [('INDIVIDUAL', 'B, TWO', 'ROCK COMPANIES', 'REAL ESTATE', 'REAL ESTATE')] * 5   # one person, 5 checks
    rows += [('INDIVIDUAL', 'C, THREE', 'ROCK COMPANIES', 'REAL ESTATE', 'REAL ESTATE'),
             ('INDIVIDUAL', 'D, FOUR', 'ROCK COMPANIES', 'REAL ESTATE', 'REAL ESTATE'),
             ('INDIVIDUAL', 'E, FIVE', 'ROCK COMPANIES', 'CEO', 'EXECUTIVE / C-SUITE'),
             ('INDIVIDUAL', 'F, SIX', 'ROCK COMPANIES', 'CFO', 'EXECUTIVE / C-SUITE'),
             ('INDIVIDUAL', 'G, SEVEN', 'ROCK COMPANIES', 'ATTORNEY', 'LEGAL')]
    df = _frame(rows)
    # 6 colleagues, only 3 real estate (50%) -> not enough, stays DEVELOPER
    assert _disambiguate_vague_occupation(df) == 0
    assert df.loc[0, 'contributor_occupation'] == 'DEVELOPER'


def test_specific_occupations_and_non_individuals_are_untouched():
    df = _frame([
        ('INDIVIDUAL', 'A, ONE', 'TERRACO REAL ESTATE', 'SOFTWARE DEVELOPER', 'TECHNOLOGY'),
        ('INDIVIDUAL', 'B, TWO', 'GOOGLE', 'ATTORNEY', 'LEGAL'),
        ('COMMITTEE', 'SOME PAC', 'TERRACO REAL ESTATE', 'DEVELOPER', ''),
    ])
    assert _disambiguate_vague_occupation(df) == 0
    assert df['contributor_occupation'].tolist() == ['SOFTWARE DEVELOPER', 'ATTORNEY', 'DEVELOPER']
