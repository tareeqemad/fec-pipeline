"""previous_employer holds companies only (audit findings 16 / 34).

Job titles, professions, retirement markers, RETIRED typos and mixed-case
override spellings were stored as previous employers because they passed
is_real_employer. They now follow the same "is this a company?" contract as
the current employer (NOT_REAL_EMPLOYER).
"""
import pandas as pd
import pytest

from fec.cleaning.previous_employer import (
    normalize_previous_employer_column,
    normalize_previous_employer_value as V,
)
from fec.cleaning.employer_status import classify_employer_status, is_real_employer
from fec.resolve.pipeline.apply import _preserve_previous_employer_display
from fec.resolve.pipeline.helpers import _previous_employer_identity


@pytest.mark.parametrize(('raw', 'expected'), [
    ('GOLDMAN SACHS-RETIRED', 'GOLDMAN SACHS'),
    ('UCLA RETIRED', 'UCLA'),
    ('REICH AND TRUAX (SEMI RETIRED)', 'REICH AND TRUAX'),
    ('RETIRED FROM DAIICHI SANKYO', 'DAIICHI SANKYO'),
    ('RETIRED - ORTHOCAROLINA', 'ORTHOCAROLINA'),
    ('RETIRED/ACME WIDGETS', 'ACME WIDGETS'),
    # the marker around a bare profession leaves no company
    ('SEMI RETIRED PSYCHOLOGIST', ''),
    ('SEMI-RETIRED ATTORNEY', ''),
    ('ATTORNEY RETIRED', ''),
    ('DENTIST - RETIRED', ''),
    ('PHYSICIAN, RETIRED', ''),
    ('RETIRED MILITARY', ''),
    ('RETIRED US ARMY', 'US ARMY'),
    ('SEMI RETIRED', ''),
    ('MOSTLY RETIRED', ''),
    ('RETIRED', ''),
])
def test_retirement_marker_is_stripped(raw, expected):
    assert V(raw) == expected


@pytest.mark.parametrize('name', [
    'ERICKSON RETIREMENT COMMUNITIE',   # RETIREMENT is part of the name
    'RETIREE DIVISION CSEA',            # RETIREE is part of the name
    'RETIREE CHAPTER',
    'NATIONAL ASSOCIATION OF RETIRED',  # a name cut at 38 characters
])
def test_names_containing_retire_words_are_kept(name):
    assert V(name) == name


@pytest.mark.parametrize('title', [
    'MEDICAL DOCTOR', 'WRITER', 'ADMINISTRATOR', 'PROPERTY OWNER',
    'SENIOR MANAGING DIRECTOR', 'CHIROPRACTOR', 'OPHTHALMOLOGIST', 'PRODUCER',
    'PSYCHOTHERAPIST', 'OPERA SINGER', 'MANAGEMENT CONSULTANT', 'PHOTOGRAPHER',
    'CONTRACTOR', 'LAND DEVELOPER', 'EXECUTIVE DIRECTOR', 'BUILDER',
    'REAL ESTATE BROKER', 'FUNDRAISER', 'BOOKKEEPER', 'FARMER',
    'SPEECH PATHOLOGIST', 'FINANCIAL CONSULTANT', 'PERSONAL TRAINER',
    'BUSINESS EXECUTIVE', 'PERIODONTIST', 'SCIENTIST', 'ARTIST',
    'BOARD OF DIRECTORS', 'INTERIOR DESIGNER',
    'INVESTMENTS', 'INVESTMENT MANAGEMENT', 'PARTNERSHIPS', 'SEMICONDUCTOR SECTOR',
    'WRITER/PRODUCER',
    # professions cached from retirees' older FEC filings (review round 2)
    'BUILDING CONSULTANT', 'PLANNING CONSULTANT', 'COMPUTER CONSULTANT',
    'MUSEUM EDUCATION CONSULTANT', 'BOND BROKER', 'SOFTWARE DESIGNER',
    'SCRAP DEALER', 'ANTIQUE DEALER', 'RESIDENTIAL REAL ESTATE APPRAISER',
    'BROADCASTER', 'COMMUNITY ACTIVIST', 'COMMUNITY VOLUNTEER LEADER',
    'MUSEUM FOUNDER', 'ORTHOPAEDIC SURGEON', 'ORTHOPEDIC SURGEON', 'ADMIN',
    'IT SERVICES',
])
def test_bare_job_titles_are_not_previous_employers(title):
    assert V(title) == ''


@pytest.mark.parametrize('filed', [
    # every "RETIRED <profession>" string in the raw data whose remainder
    # used to survive as a company
    'RETIRED JUDGE', 'RETIRED JUDGE MEDIATOR', 'RETIRED FAMILY PHYSICIAN',
    'RETIRED ORAL SURGEON', 'RETIRED GENERAL CONTRACTOR',
    'RETIRED ASSISTANT DEAN', 'RETIRED JEWISH EDUCATOR (DIRECTOR)',
    'RETIRED MILITARY AND BUSINESS OWNER', 'SEMI RETIRED BOND BROKER',
])
def test_retired_marker_around_a_listed_profession_clears(filed):
    assert V(filed) == ''


@pytest.mark.parametrize(('filed', 'company'), [
    # an unlisted remainder is kept as filed: no word-level guess
    ('RETIRED US ARMY', 'US ARMY'),
    ('RETIRED - CAPE FEAR VALLEY MEDICAL', 'CAPE FEAR VALLEY MEDICAL'),
    ('RETIRED - ORACLE', 'ORACLE'),
])
def test_retired_marker_around_a_company_keeps_the_company(filed, company):
    assert V(filed) == company


@pytest.mark.parametrize('company', [
    # look like titles but may be a practice or firm name: not listed, kept
    'LEGEND MERCHANT', 'PARK AVE PHYSICIAN', 'JVS - DEVELOPER',
    'HERZOG LEADERSHIP CONSULTING', 'W. FLORIDA MEDICAL SPECIALIST',
    'ANN MARGOLIN CONSULTING', 'OFFICE OF THE COUNTY EXECUTIVE',
])
def test_named_firms_near_title_words_are_kept(company):
    assert V(company) == company


@pytest.mark.parametrize('typo', ['RETURED', 'REITRED', 'RETIREF', 'REIRED'])
def test_retired_typos_are_not_previous_employers(typo):
    assert V(typo) == ''


def test_titles_follow_the_current_employer_contract_too():
    for title in ('MEDICAL DOCTOR', 'SENIOR MANAGING DIRECTOR', 'RETIREF',
                  'BUILDING CONSULTANT', 'JUDGE', 'IT SERVICES'):
        assert not is_real_employer(title)
        assert classify_employer_status(title) == 'missing'
    assert is_real_employer('GOLDMAN SACHS')
    assert classify_employer_status('GOLDMAN SACHS') == 'active'


def test_legacy_title_contract_is_unchanged():
    """Bare titles already in ROLE/OCCUPATION_AS_EMPLOYER keep SELF-EMPLOYED."""
    assert V('ATTORNEY') == 'SELF-EMPLOYED'
    assert V('PSYCHOLOGIST') == 'SELF-EMPLOYED'
    assert V('SELF-EMPLOYED') == 'SELF-EMPLOYED'
    # ... but a retirement marker never manufactures SELF-EMPLOYED
    assert V('SELF RETIRED') == ''
    assert V('RETIRED CEO') == ''
    assert V('RETIRED LAWYER/EXECUTIVE') == ''


@pytest.mark.parametrize(('raw', 'expected'), [
    ('WhatsApp LLC', 'WHATSAPP LLC'),
    ('WhatsApp llc', 'WHATSAPP LLC'),
    ('whatsapp', 'WHATSAPP LLC'),
    ('Edison Properties', 'EDISON PROPERTIES'),
    ('Grace Communications Foundation', 'GRACE COMMUNICATIONS FOUNDATION'),
    ('QFS Asset Management', 'QFS ASSET MANAGEMENT'),
])
def test_previous_employer_is_uppercase(raw, expected):
    assert V(raw) == expected


def test_column_contract_on_real_leaks():
    df = pd.DataFrame({
        'contributor_name': ['KOUM, JAN', 'FRIEDSTEIN, SHELLY', 'BLEICH, ALLAN',
                             'REISS, FREDDIE', 'ZARIF, URI', 'SMITH, ANN'],
        'previous_employer': ['WhatsApp LLC', 'GOLDMAN SACHS-RETIRED',
                              'MEDICAL DOCTOR', 'SENIOR MANAGING DIRECTOR',
                              'REIRED', 'MARCUM'],
    })
    assert normalize_previous_employer_column(df) == 5
    assert df['previous_employer'].tolist() == [
        'WHATSAPP LLC', 'GOLDMAN SACHS', '', '', '', 'MARCUM',
    ]


def test_resolve_cache_entries_with_titles_resolve_to_nothing():
    """Old cache entries are cleaned on read, so no stale title is written."""
    stale = {'employer': 'SENIOR MANAGING DIRECTOR', 'method': 'cleaned_previous'}
    assert _previous_employer_identity(stale) == ('', ())
    for title in ('BUILDING CONSULTANT', 'PLANNING CONSULTANT', 'BOND BROKER'):
        entry = {'employer': title, 'employer_normalized': title,
                 'method': 'cleaned_previous',
                 'recovered_from': 'database_before_rebuild'}
        assert _previous_employer_identity(entry) == ('', ())
    fec_api = {'employer': 'MUSEUM EDUCATION CONSULTANT',
               'employer_normalized': 'MUSEUM EDUCATION CONSULTANT',
               'method': 'fec_api', 'source_date': '2002-06-22'}
    assert _previous_employer_identity(fec_api) == ('', ())
    koum = {'employer': 'WhatsApp LLC', 'employer_normalized': 'WhatsApp LLC',
            'method': 'manual_override'}
    name, keys = _previous_employer_identity(koum)
    assert name == 'WHATSAPP LLC'
    assert keys[0] == 'WHATSAPP LLC'


def test_tail_rerun_does_not_restore_a_mixed_case_spelling():
    df = pd.DataFrame({'previous_employer': ['WHATSAPP LLC', 'ACME']})
    prior = pd.Series(['WhatsApp LLC', 'ACME INC'])
    _preserve_previous_employer_display(df, prior)
    # same company: prior display spelling kept, but uppercase
    assert df['previous_employer'].tolist() == ['WHATSAPP LLC', 'ACME INC']
