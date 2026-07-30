"""Entity reclassification (committee detection) and employer synonyms."""
import pandas as pd

from test_enhancements import _make_df


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


class TestEmployerSynonyms:
    def test_jpmorgan_variants(self):
        from fec.cleaning.employer_synonyms import apply_employer_synonyms
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
