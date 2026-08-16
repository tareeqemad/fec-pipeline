"""clean_remaining_junk: junk employers/occupations, NIST, doctor-as-employer."""
import pandas as pd

from test_record_rules import _make_df


class TestJunkCleanup:
    def test_xxx_employer(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'XXX'}])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_employer'].iloc[0])

    def test_short_junk_occupation(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'ME',
            'occupation_status': 'DISCLOSED',
        }])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'

    def test_known_short_occ_preserved(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_occupation': 'MD'}])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'MD'

    def test_number_suffix_stripped(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_occupation': 'DENTIST 3779'}])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'DENTIST'

    def test_llp_standalone(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'LLP',
            'occupation_category': 'OTHER',
        }])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'ATTORNEY'
        assert df['occupation_category'].iloc[0] == 'LEGAL'

    def test_not_disclosed_wrong_status_cleared(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NOT DISCLOSED',
            'occupation_status': 'DISCLOSED',
        }])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'

    def test_emp_occ_word_occ_self_employed_swap(self):
        from fec.cleaning.record_junk import clean_remaining_junk
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
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'VARIOUS'}])
        df, _ = clean_remaining_junk(df)
        assert pd.isna(df['contributor_employer'].iloc[0])

    def test_self_prefix_preserves_named_company(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'SELF, TANTUM REAL ESTATE'}])
        df, n = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'TANTUM REAL ESTATE'
        assert n >= 1

    def test_self_prefix_without_named_company_stays_self_employed(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{'contributor_employer': 'SELF EMPLOYED LAW OFFICE'}])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'

    def test_verified_self_company_overrides(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([
            {'contributor_employer': 'SELF EMPLOYED- STANDARDIZED SUCCESS, L'},
            {'contributor_employer': 'SELF EMPLOYED AND TOTAL REALTY'},
            {'contributor_employer': 'SELF AND STEVENSON UNIVERSITY'},
        ])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_employer'].tolist() == [
            'STANDARDIZED SUCCESS LLC', 'TOTAL REALTY', 'STEVENSON UNIVERSITY',
        ]


class TestNistHandling:
    def test_nist_with_school_employer(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NIST',
            'contributor_employer': 'MDCPS',
        }])
        df, n = clean_remaining_junk(df)
        assert df['contributor_occupation'].iloc[0] == 'TEACHER'
        assert df['occupation_category'].iloc[0] == 'EDUCATION'

    def test_nist_with_non_school_employer(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'NIST',
            'contributor_employer': 'GOVERNMENT AGENCY',
        }])
        df, n = clean_remaining_junk(df)
        assert pd.isna(df['contributor_occupation'].iloc[0])
        assert df['occupation_status'].iloc[0] == 'MISSING'


class TestDoctorPhysicianEmployerFix:
    def test_emp_doctor_occ_physician(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'PHYSICIAN',
            'contributor_employer': 'DOCTOR',
        }])
        df, n = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'SELF-EMPLOYED'
        assert n >= 1

    def test_emp_doctor_occ_attorney_no_change(self):
        from fec.cleaning.record_junk import clean_remaining_junk
        df = _make_df([{
            'contributor_occupation': 'ATTORNEY',
            'contributor_employer': 'DOCTOR',
        }])
        df, _ = clean_remaining_junk(df)
        assert df['contributor_employer'].iloc[0] == 'DOCTOR'
