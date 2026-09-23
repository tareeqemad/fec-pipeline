"""A donor's one job at one employer keeps one spelling; real job changes are kept."""
import pandas as pd

from fec.cleaning.donor_consistency.occupation import _converge_occupation_within_employer

_COLS = ['entity_type', 'employer_status', 'donor_key', 'contributor_employer', 'contributor_occupation',
         'occupation_category', 'contribution_receipt_date']


def _df(rows):
    return pd.DataFrame(rows, columns=_COLS)


def _smith(occupation, date):
    return ('INDIVIDUAL', 'active', 'k1', 'AUSTIN HEART', occupation, 'MEDICAL / HEALTHCARE', date)


def test_overlapping_same_category_spellings_take_the_dominant_one():
    df = _df([
        _smith('PHYSICIAN', '2022-03-01'), _smith('PHYSICIAN', '2023-06-01'), _smith('PHYSICIAN', '2024-03-01'),
        _smith('CARDIOLOGIST', '2023-04-26'), _smith('CARDIOLOGIST', '2024-01-01'),
        _smith('CARDIOLOGIST', '2025-01-01'), _smith('CARDIOLOGIST', '2026-04-03'),
    ])
    assert _converge_occupation_within_employer(df) == 3
    assert set(df['contributor_occupation']) == {'CARDIOLOGIST'}


def test_tie_goes_to_the_most_recent_spelling():
    df = _df([_smith('PHYSICIAN', '2022-03-01'), _smith('PHYSICIAN', '2023-06-01'),
              _smith('CARDIOLOGIST', '2023-04-26'), _smith('CARDIOLOGIST', '2026-04-03')])
    assert _converge_occupation_within_employer(df) == 2
    assert set(df['contributor_occupation']) == {'CARDIOLOGIST'}


def test_non_overlapping_periods_are_a_real_job_change():
    df = _df([
        ('INDIVIDUAL', 'active', 'k1', 'BIG LAW LLP', 'ASSOCIATE', 'LEGAL', '2022-01-01'),
        ('INDIVIDUAL', 'active', 'k1', 'BIG LAW LLP', 'ASSOCIATE', 'LEGAL', '2023-06-01'),
        ('INDIVIDUAL', 'active', 'k1', 'BIG LAW LLP', 'PARTNER', 'LEGAL', '2024-01-01'),
    ])
    assert _converge_occupation_within_employer(df) == 0


def test_different_categories_are_kept():
    df = _df([
        ('INDIVIDUAL', 'active', 'k1', 'US HEALTH PARTNERS', 'CEO', 'EXECUTIVE / C-SUITE', '2024-04-26'),
        ('INDIVIDUAL', 'active', 'k1', 'US HEALTH PARTNERS', 'CEO', 'EXECUTIVE / C-SUITE', '2026-06-24'),
        ('INDIVIDUAL', 'active', 'k1', 'US HEALTH PARTNERS', 'PHYSICIAN', 'MEDICAL / HEALTHCARE', '2026-03-13'),
    ])
    assert _converge_occupation_within_employer(df) == 0


def test_other_donors_employers_and_statuses_are_separate():
    df = _df([
        _smith('PHYSICIAN', '2022-09-01'), _smith('CARDIOLOGIST', '2022-06-01'), _smith('CARDIOLOGIST', '2023-01-01'),
        ('INDIVIDUAL', 'active', 'k2', 'AUSTIN HEART', 'PHYSICIAN', 'MEDICAL / HEALTHCARE', '2022-06-01'),
        ('INDIVIDUAL', 'active', 'k1', 'OTHER CLINIC', 'PHYSICIAN', 'MEDICAL / HEALTHCARE', '2022-06-01'),
        ('INDIVIDUAL', 'retired', 'k1', 'RETIRED', 'PHYSICIAN', 'MEDICAL / HEALTHCARE', '2022-06-01'),
        ('INDIVIDUAL', 'retired', 'k1', 'RETIRED', 'RETIRED', 'RETIRED', '2023-06-01'),
    ])
    assert _converge_occupation_within_employer(df) == 1
    assert df['contributor_occupation'].tolist() == ['CARDIOLOGIST', 'CARDIOLOGIST', 'CARDIOLOGIST',
                                                     'PHYSICIAN', 'PHYSICIAN', 'PHYSICIAN', 'RETIRED']
