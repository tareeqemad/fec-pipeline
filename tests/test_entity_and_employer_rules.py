"""Entity reclassification (committee detection) and employer synonyms."""
import pandas as pd

from test_record_rules import _make_df
from fec.cleaning.audit_trail import AuditTrail
from fec.cleaning.pipeline.core import _clean_people
from fec.cleaning.pipeline.names import _clean_names
from fec.cleaning.pipeline.reclassify import _reclassify_entities
from fec.cleaning.safety_nets.occupation import _fix_emp_occ_category_consistency


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


def test_person_names_survive_false_committee_filing():
    df = _make_df([
        {
            'is_individual': False,
            'entity_type': 'COMMITTEE/PAC',
            'contributor_name': 'FERRNCZ, ROBERT',
            'contributor_first_name': 'ROBERT',
            'contributor_last_name': 'FERRNCZ',
            'committee_type': 'PARTY ORGANIZATION',
        },
        {
            'is_individual': False,
            'entity_type': 'COMMITTEE/PAC',
            'contributor_name': 'WAL;DMAN, GARY',
            'contributor_first_name': 'GARY',
            'contributor_last_name': 'WAL;DMAN',
            'committee_type': 'POLITICAL COMMITTEE',
        },
    ])

    assert _reclassify_entities(df) == (2, 0)
    _clean_names(df)

    assert df['entity_type'].tolist() == ['INDIVIDUAL', 'INDIVIDUAL']
    assert df['contributor_name'].tolist() == ['FERRNCZ, ROBERT', 'WALDMAN, GARY']
    assert df['contributor_first_name'].tolist() == ['ROBERT', 'GARY']
    assert df['contributor_last_name'].tolist() == ['FERRNCZ', 'WALDMAN']


def test_name_punctuation_is_cleaned_before_entity_detection():
    df = _make_df([{
        'is_individual': False,
        'entity_type': 'COMMITTEE/PAC',
        'contributor_name': 'HAAS, .CANDICE',
        'contributor_first_name': '.CANDICE',
        'contributor_last_name': 'HAAS',
        'contributor_employer': 'NOT EMPLOYED',
        'contributor_occupation': 'NOT EMPLOYED',
        'committee_type': 'POLITICAL COMMITTEE',
    }])

    _clean_people(df, AuditTrail(), lambda message: None)

    assert df.loc[0, 'entity_type'] == 'INDIVIDUAL'
    assert df.loc[0, 'contributor_name'] == 'HAAS, CANDICE'
    assert df.loc[0, 'contributor_first_name'] == 'CANDICE'
    assert df.loc[0, 'contributor_last_name'] == 'HAAS'


def test_status_employer_fills_empty_occupation_idempotently():
    df = _make_df([
        {
            'contributor_employer': 'RETIRED',
            'contributor_occupation': None,
            'occupation_category': 'OTHER',
            'occupation_status': 'MISSING',
        },
        {
            'contributor_employer': 'NOT EMPLOYED',
            'contributor_occupation': None,
            'occupation_category': 'OTHER',
            'occupation_status': 'MISSING',
        },
    ])

    assert _fix_emp_occ_category_consistency(df) == 2
    assert df['contributor_occupation'].tolist() == ['RETIRED', 'NOT EMPLOYED']
    assert df['occupation_category'].tolist() == ['RETIRED', 'NOT EMPLOYED']
    assert _fix_emp_occ_category_consistency(df) == 0


def test_manual_individual_name_correction_keeps_name_columns_consistent():
    from fec.cleaning.entity_classification import apply_name_corrections

    df = _make_df([{
        'contributor_name': 'MEYERS, SARA STUART',
        'contributor_first_name': 'SARA STUART',
        'contributor_last_name': 'MEYERS',
    }])

    df, count = apply_name_corrections(df)

    assert count == 1
    assert df.loc[0, 'contributor_name'] == 'MEYERS, SARA'
    assert df.loc[0, 'contributor_first_name'] == 'SARA'
    assert df.loc[0, 'contributor_last_name'] == 'MEYERS'


def test_swapped_name_correction_is_limited_to_known_rows():
    from fec.cleaning.entity_classification import apply_name_corrections

    df = _make_df([
        {'sub_id': '4011420231698186281', 'contributor_name': 'HEALTH, GOOD'},
        {'sub_id': 'other', 'contributor_name': 'HEALTH, GOOD'},
    ])

    df, count = apply_name_corrections(df)

    assert count == 1
    assert df['contributor_name'].tolist() == [
        'KELLOGG, SARAH',
        'HEALTH, GOOD',
    ]


def test_laryl_kupor_uses_verified_lary_spelling():
    df = _make_df([{
        'contributor_name': 'KUPOR, LARYL',
        'contributor_first_name': 'LARYL',
        'contributor_last_name': 'KUPOR',
    }])

    _clean_names(df)

    assert df.loc[0, 'contributor_name'] == 'KUPOR, LARY'
    assert df.loc[0, 'contributor_first_name'] == 'LARY'
