"""
tests/test_enhancements.py — Test suite for the FEC cleaning/enhancement pipeline.

Covers:
  - Occupation canonical normalization
  - Employer synonyms
  - Entity reclassification (committee/business detection)
  - Occupation/employer swap detection
  - Junk cleanup (XXX, short codes, NIST, LLP, number tails)
  - Address normalization helpers
  - Donor match scoring

Run with:  python -m pytest tests/test_enhancements.py -v
"""
import numpy as np
import pandas as pd
import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Test DataFrame builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_df(rows):
    """Build a minimal test DataFrame from a list of dicts."""
    defaults = dict(
        entity_type='INDIVIDUAL', is_individual=True,
        contributor_name='TEST, USER', contributor_first_name='USER',
        contributor_last_name='TEST', contributor_occupation='ATTORNEY',
        occupation_category='LEGAL', occupation_status='DISCLOSED',
        contributor_employer='LAW FIRM', committee_type=pd.NA,
    )
    records = [{**defaults, **r} for r in rows]
    df = pd.DataFrame(records)
    for col in ('committee_type', 'occupation_category'):
        if col in df.columns:
            df[col] = df[col].astype(object)
    return df


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNormHelper:
    def test_nan_becomes_empty(self):
        from fec.cleaning._helpers import _norm
        s = pd.Series([np.nan, None, 'hello'])
        result = _norm(s)
        assert result.iloc[0] == ''
        assert result.iloc[1] == ''
        assert result.iloc[2] == 'HELLO'

    def test_strips_and_uppers(self):
        from fec.cleaning._helpers import _norm
        s = pd.Series(['  attorney  ', 'CeO', ' llc '])
        result = _norm(s)
        assert list(result) == ['ATTORNEY', 'CEO', 'LLC']


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Entity reclassification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCommitteeReclassification:
    def test_friends_to_elect(self):
        from fec.cleaning.entity_classification import fix_remaining_misclassified
        df = _make_df([{
            'contributor_name': 'FRIENDS TO ELECT DR. GREG MURPHY, GREGORY',
            'contributor_last_name': 'FRIENDS TO ELECT DR. GREG MURP',
            'contributor_first_name': 'GREGORY',
        }])
        df, n = fix_remaining_misclassified(df)
        assert n == 1
        assert df['entity_type'].iloc[0] == 'COMMITTEE/PAC'
        assert pd.isna(df['contributor_first_name'].iloc[0])

    def test_4congress(self):
        from fec.cleaning.entity_classification import fix_remaining_misclassified
        df = _make_df([{
            'contributor_last_name': 'YVETTE4CONGRESS',
            'contributor_name': 'YVETTE4CONGRESS, YVETTE',
        }])
        df, n = fix_remaining_misclassified(df)
        assert n == 1
        assert df['entity_type'].iloc[0] == 'COMMITTEE/PAC'

    def test_campaign_suffix(self):
        from fec.cleaning.entity_classification import fix_remaining_misclassified
        df = _make_df([{
            'contributor_last_name': 'SYLVESTER TURNER CAMPAIGN',
            'contributor_name': 'SYLVESTER TURNER CAMPAIGN, SYLVESTER',
        }])
        df, n = fix_remaining_misclassified(df)
        assert n == 1

    def test_normal_name_untouched(self):
        from fec.cleaning.entity_classification import fix_remaining_misclassified
        df = _make_df([{
            'contributor_last_name': 'JOHNSON',
            'contributor_name': 'JOHNSON, MICHAEL',
        }])
        df, n = fix_remaining_misclassified(df)
        assert n == 0
        assert df['entity_type'].iloc[0] == 'INDIVIDUAL'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Employer synonyms
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEmployerSynonyms:
    def test_jpmorgan_variants(self):
        from fec.cleaning.employer_synonyms import apply_employer_synonyms, EMPLOYER_SYNONYMS
        df = _make_df([
            {'contributor_employer': 'JP MORGAN'},
            {'contributor_employer': 'JPMORGAN'},
            {'contributor_employer': 'JP MORGAN CHASE'},
        ])
        df, n = apply_employer_synonyms(df)
        assert n == 3
        assert all(df['contributor_employer'] == 'JPMORGAN CHASE')

    def test_unknown_employer_untouched(self):
        from fec.cleaning.employer_synonyms import apply_employer_synonyms
        df = _make_df([{'contributor_employer': 'SOME RANDOM COMPANY'}])
        df, n = apply_employer_synonyms(df)
        assert n == 0
        assert df['contributor_employer'].iloc[0] == 'SOME RANDOM COMPANY'

    def test_goldman_sachs(self):
        from fec.cleaning.employer_synonyms import apply_employer_synonyms
        df = _make_df([{'contributor_employer': 'GOLDMAN SACHS AND CO'}])
        df, n = apply_employer_synonyms(df)
        assert n == 1
        assert df['contributor_employer'].iloc[0] == 'GOLDMAN SACHS'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Swapped occupation/employer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSwappedOccEmp:
    def test_llp_in_occ_attorney_in_emp(self):
        from fec.cleaning.enhancements import fix_remaining_swapped_occ_emp
        df = _make_df([{
            'contributor_occupation': 'MARC BERN & PARTNERS LLP',
            'occupation_category': 'OTHER',
            'contributor_employer': 'ATTORNEY',
        }])
        df, n_swap, _ = fix_remaining_swapped_occ_emp(df)
        assert n_swap == 1
        assert df['contributor_occupation'].iloc[0] == 'ATTORNEY'
        assert df['contributor_employer'].iloc[0] == 'MARC BERN & PARTNERS LLP'

    def test_corp_finance_not_swapped(self):
        from fec.cleaning.enhancements import fix_remaining_swapped_occ_emp
        df = _make_df([{
            'contributor_occupation': 'CORP FINANCE',
            'occupation_category': 'FINANCE / INVESTMENT',
            'contributor_employer': 'KROLL LLC',
        }])
        df, n_swap, _ = fix_remaining_swapped_occ_emp(df)
        assert n_swap == 0
        assert df['contributor_occupation'].iloc[0] == 'CORP FINANCE'

    def test_same_value_fix(self):
        from fec.cleaning.enhancements import fix_remaining_swapped_occ_emp
        df = _make_df([{
            'contributor_occupation': 'ATTORNEY',
            'contributor_employer': 'ATTORNEY',
        }])
        df, _, n_same = fix_remaining_swapped_occ_emp(df)
        assert n_same == 1
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'

    def test_hospital_in_occ_physician_in_emp(self):
        from fec.cleaning.enhancements import fix_remaining_swapped_occ_emp
        df = _make_df([{
            'contributor_occupation': 'GREENWICH HOSPITAL',
            'occupation_category': 'OTHER',
            'contributor_employer': 'PHYSICIAN',
        }])
        df, n_swap, _ = fix_remaining_swapped_occ_emp(df)
        assert n_swap == 1
        assert df['contributor_occupation'].iloc[0] == 'PHYSICIAN'
        assert df['contributor_employer'].iloc[0] == 'GREENWICH HOSPITAL'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Occupation canonical normalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOccupationCanonical:
    def test_chief_executive_officer(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'CHIEF EXECUTIVE OFFICER'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'CEO'
        assert n == 1

    def test_cfo_stays(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'CFO'}])
        df, n = normalize_occupation_canonical(df)
        assert n == 0

    def test_chief_financial_officer_to_cfo(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'CHIEF FINANCIAL OFFICER'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'CFO'

    def test_vp_to_vice_president(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'VP'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'VICE PRESIDENT'

    def test_doctor_stays_doctor(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'DOCTOR'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'DOCTOR'
        assert n == 0

    def test_lawyer_stays_lawyer(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'LAWYER'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'LAWYER'
        assert n == 0

    def test_home_maker_to_homemaker(self):
        from fec.cleaning.enhancements import normalize_occupation_canonical
        df = _make_df([{'contributor_occupation': 'HOME MAKER'}])
        df, n = normalize_occupation_canonical(df)
        assert df['contributor_occupation'].iloc[0] == 'HOMEMAKER'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Junk cleanup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestJunkCleanup:
    def test_xxx_employer(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'XXX'}])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_employer'].iloc[0])

    def test_short_junk_occupation(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'ME',
            'occupation_status': 'DISCLOSED',
        }])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'

    def test_known_short_occ_preserved(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{'contributor_occupation': 'MD'}])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'MD'

    def test_number_suffix_stripped(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{'contributor_occupation': 'DENTIST 3779'}])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'DENTIST'

    def test_llp_standalone(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'LLP',
            'occupation_category': 'OTHER',
        }])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'ATTORNEY'
        assert df['occupation_category'].iloc[0] == 'LEGAL'

    def test_not_disclosed_wrong_status_cleared(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NOT DISCLOSED',
            'occupation_status': 'DISCLOSED',
        }])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'

    def test_emp_occ_word_occ_self_employed_swap(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'SELF-EMPLOYED',
            'occupation_category': 'SELF-EMPLOYED',
            'contributor_employer': 'ATTORNEY',
        }])
        df, n = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'
        assert df['contributor_occupation'].iloc[0] == 'ATTORNEY'
        assert n >= 1

    def test_emp_junk_various_to_nan(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'VARIOUS'}])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_employer'].iloc[0])

    def test_self_variant_normalized(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'SELF, TANTUM REAL ESTATE'}])
        df, n = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'
        assert n >= 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NIST handling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNistHandling:
    def test_nist_with_school_employer(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NIST',
            'contributor_employer': 'MDCPS',
        }])
        df, n = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'TEACHER'
        assert df['occupation_category'].iloc[0] == 'EDUCATION'

    def test_nist_with_non_school_employer(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NIST',
            'contributor_employer': 'GOVERNMENT AGENCY',
        }])
        df, n = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Doctor/Physician employer fix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDoctorPhysicianEmployerFix:
    def test_emp_doctor_occ_physician(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'PHYSICIAN',
            'contributor_employer': 'DOCTOR',
        }])
        df, n = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'
        assert n >= 1

    def test_emp_doctor_occ_attorney_no_change(self):
        from fec.cleaning.enhancements import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'ATTORNEY',
            'contributor_employer': 'DOCTOR',
        }])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'DOCTOR'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Donor match scoring
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDonorMatchScoring:
    def test_same_street_scores_high(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=5)
        assert score >= 50

    def test_no_corroboration_capped(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=20)
        assert score < 50

    def test_middle_name_conflict_blocks(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': 'DAVID', 'name': 'SMITH, JOHN'}
        p2 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': 'MICHAEL', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=5)
        assert score < 0  # HARD_BLOCK


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Occupation categorization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOccupationCategorization:
    def test_attorney_is_legal(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series(['ATTORNEY']))
        assert result.iloc[0] == 'LEGAL'

    def test_physician_is_medical(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series(['PHYSICIAN']))
        assert result.iloc[0] == 'MEDICAL / HEALTHCARE'

    def test_retired_is_retired(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series(['RETIRED']))
        assert result.iloc[0] == 'RETIRED'

    def test_software_engineer_is_technology(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series(['SOFTWARE ENGINEER']))
        assert result.iloc[0] == 'TECHNOLOGY'

    def test_unknown_is_other(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series(['SOMETHING UNUSUAL']))
        assert result.iloc[0] == 'OTHER'

    def test_nan_stays_na(self):
        from fec.cleaning.occupations import _categorize
        result = _categorize(pd.Series([np.nan]))
        assert pd.isna(result.iloc[0])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Cleanup session fixes (3 targeted patches)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCityNormalize:
    def test_s_orange_to_south_orange(self):
        from fec.config import CITY_NORMALIZE
        assert CITY_NORMALIZE['S ORANGE'] == 'SOUTH ORANGE'

    def test_so_orange_to_south_orange(self):
        from fec.config import CITY_NORMALIZE
        assert CITY_NORMALIZE['SO ORANGE'] == 'SOUTH ORANGE'


class TestRetiredDonorBonus:
    def test_retired_rare_name_with_geo_gets_bonus(self):
        from fec.database.donor_match import compute_score
        # Same state = geographic overlap; no employers = retired-like
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': 'CA', 'city': 'LOS ANGELES', 'zip5': '90210',
               'middle': '', 'name': 'KOUM, JAN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': 'CA', 'city': 'SAN FRANCISCO', 'zip5': '94102',
               'middle': '', 'name': 'KOUM, JAN'}
        score, signals = compute_score(p1, p2, name_freq=2)
        assert any('retired_no_emp' in s for s in signals)

    def test_common_name_no_employer_no_bonus(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=50)
        # Common name should NOT get retired bonus
        assert not any('retired_no_emp' in s for s in signals)


class TestTrailingDirection:
    def test_trailing_s_moved_to_prefix(self):
        """101 WESTON LN S → 101 S WESTON LN"""
        import re
        s = '101 WESTON LN S'
        s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert s == '101 S WESTON LN'

    def test_trailing_nw_moved_to_prefix(self):
        """500 MAIN AVE NW → 500 NW MAIN AVE"""
        import re
        s = '500 MAIN AVE NW'
        s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert s == '500 NW MAIN AVE'

    def test_no_trailing_direction_unchanged(self):
        """123 MAIN ST stays unchanged (no trailing direction)"""
        import re
        s = '123 MAIN ST'
        result = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert result == '123 MAIN ST'
