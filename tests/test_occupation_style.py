"""Occupation style step unifies forms of the SAME words only; synonyms and industry plurals stay distinct."""
import pandas as pd
import pytest

from fec.cleaning.occupations.style import (
    apply_occupation_typo_fixes,
    normalize_occupation_style,
    normalize_occupation_style_step,
)
from fec.config.occupation_rules.rules import OCCUPATION_TYPO_FIXES


@pytest.mark.parametrize('raw, expected', [
    # separator spacing
    ('PRESIDENT / CEO', 'PRESIDENT/CEO'),
    ('PRESIDENT/ CEO', 'PRESIDENT/CEO'),
    ('CHIEF PEOPLE OFFICER// PROFESSOR', 'CHIEF PEOPLE OFFICER/PROFESSOR'),
    ('MD/JD/ REAL ESTATE DEVELOPER', 'MD/JD/REAL ESTATE DEVELOPER'),
    ('MERGERS&ACQUISITIONS', 'MERGERS & ACQUISITIONS'),
    # title joiners -> slash
    ('PRESIDENT & CEO', 'PRESIDENT/CEO'),
    ('PRESIDENT AND CEO', 'PRESIDENT/CEO'),
    ('FOUNDER & CHAIRMAN', 'FOUNDER/CHAIRMAN'),
    ('WRITER-PRODUCER', 'WRITER/PRODUCER'),
    ('CO-PRESIDENT AND GENERAL COUNSEL', 'CO-PRESIDENT/GENERAL COUNSEL'),
    ('CHAIRWOMAN AND CEO', 'CHAIRWOMAN/CEO'),
    # CO- prefix
    ('CO OWNER', 'CO-OWNER'),
    ('CO- CEO', 'CO-CEO'),
    ('VP/CO OWNER', 'VP/CO-OWNER'),
    ('CO CEO AND CO FOUNDER', 'CO-CEO/CO-FOUNDER'),
    # compound tokens
    ('HEALTH CARE CONSULTANT', 'HEALTHCARE CONSULTANT'),
    ('HOMEBUILDER', 'HOME BUILDER'),
    ('NON EXECUTIVE DIRECTOR', 'NON-EXECUTIVE DIRECTOR'),
    ('NON-PROFIT EXECUTIVE', 'NONPROFIT EXECUTIVE'),
    ('LAWYER/REALESTATE', 'LAWYER/REAL ESTATE'),
    ('SPEECH-LANGUAGE PATHOLOGIST', 'SPEECH LANGUAGE PATHOLOGIST'),
    # plural role nouns (last word only)
    ('FINANCIAL ADVISORS', 'FINANCIAL ADVISOR'),
    ('MANAGING PARTNERS', 'MANAGING PARTNER'),
    ('PEDIATRICIANS', 'PEDIATRICIAN'),
    ('ARCHITECTS', 'ARCHITECT'),
])
def test_same_word_variants_are_unified(raw, expected):
    assert normalize_occupation_style(raw) == expected


@pytest.mark.parametrize('value', [
    # synonyms are never merged
    'LAWYER', 'ATTORNEY', 'PHYSICIAN', 'MEDICAL DOCTOR',
    # industry plurals are the occupation
    'INVESTMENTS', 'SALES', 'RESTAURANTS', 'COMMUNICATIONS', 'CAR DEALERSHIPS',
    'FINANCIAL SERVICES', 'HUMAN RESOURCES', 'PUBLIC AFFAIRS',
    # "OF" phrases keep their plural
    'BOARD OF DIRECTORS', 'DIRECTOR OF OPERATIONS',
    # short abbreviations keep a tight ampersand
    'M&A', 'R&D', 'FP&A MANAGER', 'P&L',
    # comma is a title/department separator, never rewritten
    'VP, RETAILER RELATIONS', 'WRITER, EDITOR',
    # status values
    'RETIRED', 'NOT EMPLOYED', 'SELF-EMPLOYED', 'HOMEMAKER',
    # already canonical
    'PRESIDENT/CEO', 'CO-FOUNDER', 'REAL ESTATE INVESTOR/DEVELOPER',
    'EXECUTIVE DIRECTOR', 'MERGERS & ACQUISITIONS', 'VICE PRESIDENT',
])
def test_untouched_values(value):
    assert normalize_occupation_style(value) == value


def test_step_only_touches_individuals_and_counts_changes():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL', 'COMMITTEE', 'INDIVIDUAL'],
        'is_individual': [True, True, False, True],
        'contributor_occupation': ['PRESIDENT & CEO', 'ATTORNEY', 'HEALTH CARE', None],
        'contributor_employer': ['ACME INC', 'SELF-EMPLOYED', None, 'RETIRED'],
    })
    df, n = normalize_occupation_style_step(df)
    assert n == 1
    assert df['contributor_occupation'].tolist()[:3] == ['PRESIDENT/CEO', 'ATTORNEY', 'HEALTH CARE']
    assert pd.isna(df['contributor_occupation'].iloc[3])


def test_step_never_restyles_a_company_name_left_in_occupation():
    """A swapped filing (company in the occupation column) must stay byte-identical for the swap nets."""
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL', 'INDIVIDUAL', 'INDIVIDUAL'],
        'is_individual': [True] * 4,
        'contributor_occupation': [
            'CRESCENT REALTY ADVISORS',   # known employer elsewhere in the frame
            'ADLER REAL ESTATE PARTNERS LLC',  # legal suffix
            'FINANCIAL ADVISORS',         # plain occupation: restyled
            'REAL ESTATE',
        ],
        'contributor_employer': ['REAL ESTATE', 'FOUNDER', 'RETIRED', 'CRESCENT REALTY ADVISORS'],
    })
    df, n = normalize_occupation_style_step(df)
    assert n == 1
    assert df['contributor_occupation'].tolist() == [
        'CRESCENT REALTY ADVISORS', 'ADLER REAL ESTATE PARTNERS LLC', 'FINANCIAL ADVISOR', 'REAL ESTATE',
    ]


def test_typo_fixes_are_stable_under_style_and_have_no_chains():
    """Keys must be post-style spellings (else the map never fires) and values must be final."""
    for key, value in OCCUPATION_TYPO_FIXES.items():
        assert normalize_occupation_style(key) == key, key
        assert normalize_occupation_style(value) == value, value
        assert value not in OCCUPATION_TYPO_FIXES, f"chain: {key} -> {value}"
        assert key != value


def test_typo_fix_step():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL', 'COMMITTEE'],
        'contributor_occupation': ['PHYISCIAN', 'PHYSICIAN', 'PHYISCIAN'],
    })
    df, n = apply_occupation_typo_fixes(df)
    assert n == 1
    assert df['contributor_occupation'].tolist() == ['PHYSICIAN', 'PHYSICIAN', 'PHYISCIAN']
