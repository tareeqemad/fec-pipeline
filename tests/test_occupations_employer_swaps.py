"""Bare sector / job words as the current employer (audit finding 24).

- HEALTH made a job title look like a company, so rule AD swapped raw
  OWN BUSINESS / MENTAL HEALTH COUNSELOR into employer=MENTAL HEALTH COUNSELOR.
- OFFICE, FAMILY OFFICE and REAL ESTATE SALES name a kind of workplace, like
  HEDGE FUND or LAW OFFICE, and are nulled by the sector rule.
- TRUST DEED INVESTMENTS filed as employer beside occupation SELF-EMPLOYED is
  the donor's line of work: the self-employment rule moves it to occupation.
"""
import numpy as np
import pandas as pd
import pytest

from fec.cleaning.safety_nets.employer import _null_sector_as_employer
from fec.cleaning.safety_nets.employer_swaps import (
    _COMPANY_NAME_RE,
    _fix_company_name_as_occupation,
    _fix_employer_equals_occupation,
    _fix_role_as_employer,
    _fix_self_employed_consistency,
)


def _frame(rows):
    """rows = (employer, occupation)"""
    return pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * len(rows),
        'contributor_first_name': ['PAT'] * len(rows),
        'contributor_last_name': ['DOE'] * len(rows),
        'contributor_employer': [r[0] for r in rows],
        'contributor_occupation': [r[1] for r in rows],
        'occupation_category': [''] * len(rows),
        'occupation_status': ['DISCLOSED'] * len(rows),
    })


@pytest.mark.parametrize('company', [
    'SUMMIT HEALTH', 'CVS HEALTH', 'ELLIE MENTAL HEALTH', 'DRG BEHAVIORAL HEALTH',
    'NYU LANGONE HEALTH', 'UNITED HEALTHCARE', 'SABRA HEALTH CARE REIT',
    'HEALTH ADVOCATE', 'ATLANTIC HEALTH SYSTEM', 'HEALTH FIRST',
])
def test_health_companies_still_look_like_companies(company):
    assert _COMPANY_NAME_RE.search(company)


@pytest.mark.parametrize('title', [
    'MENTAL HEALTH COUNSELOR', 'CLINICAL MENTAL HEALTH COUNSELOR',
    'MENTAL HEALTH THERAPIST', 'MENTAL HEALTH PROFESSIONAL', 'HEALTH COACH',
    'HEALTHCARE EXECUTIVE', 'HEALTH CARE CONSULTANT', 'HEALTHCARE ADMINISTRATOR',
    'HEALTH CARE INDUSTRY ADVISOR', 'PHARMACIST HEALTHCARE CONSULTANT',
])
def test_health_job_titles_do_not_look_like_companies(title):
    assert not _COMPANY_NAME_RE.search(title)


def test_own_business_mental_health_counselor_becomes_self_employed():
    df = _frame([
        ('OWN BUSINESS', 'MENTAL HEALTH COUNSELOR'),   # SMITH, BLYTHE's filing
        ('OWNER', 'SUMMIT HEALTH'),                    # a real swap
    ])
    assert _fix_role_as_employer(df) == 2
    assert df.loc[0, 'contributor_employer'] == 'SELF-EMPLOYED'
    assert df.loc[0, 'contributor_occupation'] == 'MENTAL HEALTH COUNSELOR'
    assert df.loc[1, 'contributor_employer'] == 'SUMMIT HEALTH'
    assert df.loc[1, 'contributor_occupation'] == 'OWNER'


def test_job_title_written_twice_is_self_employed():
    df = _frame([('HEALTH COACH', 'HEALTH COACH'), ('CVS HEALTH', 'CVS HEALTH')])
    is_indiv = df['entity_type'].eq('INDIVIDUAL')
    assert _fix_employer_equals_occupation(df, is_indiv) == 1
    assert df['contributor_employer'].tolist() == ['SELF-EMPLOYED', 'CVS HEALTH']


def test_workplace_kind_words_are_nulled_exact_match_only():
    df = _frame([
        ('OFFICE', 'MANAGER'),
        ('FAMILY OFFICE', 'PORTFOLIO MANAGER'),
        ('REAL ESTATE SALES', 'REALTOR'),
        ('SOFTWARE', 'MARKETING'),
        ('FAMILY OFFICE SERVICES LLC', 'ASSOCIATE'),   # a named firm
        ('SOFTERWARE', 'MARKETING'),                   # SofterWare Inc., a real firm
        ('BRIAR HALL', 'INVESTOR'),
    ])
    assert _null_sector_as_employer(df) == 4
    assert df['contributor_employer'].isna().tolist() == [
        True, True, True, True, False, False, False,
    ]
    assert df['contributor_occupation'].tolist()[:3] == ['MANAGER', 'PORTFOLIO MANAGER', 'REALTOR']


def test_trust_deed_investments_beside_self_employed_moves_to_occupation():
    df = _frame([
        ('TRUST DEED INVESTMENTS', 'SELF-EMPLOYED'),   # GOLDSTEIN, PHIL (swapped)
        ('SELF-EMPLOYED', 'TRUST DEED INVESTMENTS'),   # his other filings
    ])
    assert _fix_self_employed_consistency(df) == 1
    assert df['contributor_employer'].tolist() == ['SELF-EMPLOYED', 'SELF-EMPLOYED']
    assert df['contributor_occupation'].tolist() == ['TRUST DEED INVESTMENTS'] * 2


def test_trust_deed_investments_is_never_promoted_to_a_company():
    rows = [('SELF-EMPLOYED', 'TRUST DEED INVESTMENTS')]
    rows += [('TRUST DEED INVESTMENTS', 'LENDER')] * 10   # even if common as employer
    df = _frame(rows)
    assert _fix_company_name_as_occupation(df) == 0
    assert df.loc[0, 'contributor_employer'] == 'SELF-EMPLOYED'
    assert np.all(df.loc[1:, 'contributor_employer'] == 'TRUST DEED INVESTMENTS')
