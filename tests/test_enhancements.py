"""Occupation normalization, categorization, and occ/emp swap fixes."""
import numpy as np
import pandas as pd


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
