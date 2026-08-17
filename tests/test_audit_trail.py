import numpy as np
import pandas as pd

from fec.cleaning.audit_keys import (
    city_key,
    employer_key,
    format_key,
    organization_name_key,
    person_name_key,
    street_key,
    zip_key,
)
from fec.cleaning.audit_trail import FORMAT, SEMANTIC, UNTRACKED_STEP, AuditTrail, summarize


def _semantic(trail):
    return [r for r in trail.net_records() if r['kind'] == SEMANTIC]


def _frame():
    return pd.DataFrame({
        'sub_id': ['1', '2', '3'],
        'is_individual': ['f', 't', 'f'],
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL', 'COMMITTEE/PAC'],
        'contributor_name': ['SMITH, JOHN', 'DOE, JANE', 'BELL FOR MISSOURI, WESLEY MR'],
        'contributor_street_1': ['123 MAIN STREET STE 4', 'FAIRWAY DRIVE', 'PO BOX 1900669'],
        'contributor_street_2': [np.nan, np.nan, np.nan],
        'contributor_zip': ['10022-1234', '01367', np.nan],
        'contributor_employer': ['KIRKLAND & ELLIS', 'SELF', 'NOT EMPLOYED'],
    })


def test_street_key_ignores_abbreviations_units_and_order():
    assert street_key('123 MAIN STREET STE 4') == street_key('123 MAIN ST # 4')
    assert street_key('123 N MAIN ST') == street_key('123 MAIN ST NORTH')
    assert street_key('P.O. BOX 12') == street_key('PO BOX 12')
    assert street_key('FAIRWAY DRIVE') != street_key('108 FAIRWAY DR')
    assert street_key('PO BOX 1900669') != street_key('PO BOX 190669')


def test_other_keys():
    assert city_key('ST. LOUIS') == city_key('SAINT LOUIS')
    assert city_key('ENCINO, CA') == city_key('ENCINO')
    assert city_key('NEW YORK CITY') != city_key('NEW YORK')
    assert zip_key('10022-1234') == zip_key('10022')
    assert zip_key('1234') == zip_key('01234')
    assert zip_key('01367') != zip_key('91367')
    assert employer_key('KIRKLAND & ELLIS') == employer_key('KIRKLAND AND ELLIS LLP')
    assert employer_key('SELF') == employer_key('SELF-EMPLOYED')
    assert employer_key('ACME MGMT') == employer_key('ACME MANAGEMENT')
    assert employer_key('NOT EMPLOYED') != employer_key('RETIRED')
    assert employer_key('CAPITAL GROUP COMPANIES') != employer_key('CAPITAL GROUP')
    assert person_name_key('MILLER MD') == person_name_key('MILLER')
    assert person_name_key('SMITH, JOHN', ordered=False) == person_name_key('JOHN SMITH', ordered=False)
    assert person_name_key('POTACK, MICHAEL R') != person_name_key('POTACK, MICHAEL')
    assert organization_name_key('SHERMAN FOR CONGRESS, BRAD REP.') == organization_name_key('SHERMAN FOR CONGRESS')
    assert organization_name_key('LAWLER FOR CONGRESS, INC., MIKE REP.') == organization_name_key('LAWLER FOR CONGRESS')


def test_format_key_uses_entity_for_contributor_name():
    values = pd.Series(['SMITH, JOHN', 'X FOR CONGRESS, JOHN REP.'], index=['a', 'b'])
    individual = pd.Series([True, False], index=['a', 'b'])
    keys = format_key('contributor_name', values, individual)
    assert keys['a'] == 'JOHN SMITH'
    assert keys['b'] == 'X FOR CONGRESS'


def test_trail_records_semantic_and_format_changes():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    def step(frame):
        frame['contributor_street_1'] = ['123 MAIN ST', '108 FAIRWAY DR', 'PO BOX 190669']
        frame['contributor_street_2'] = ['STE 4', np.nan, np.nan]
        frame['contributor_zip'] = ['10022', '91367', np.nan]
        frame['contributor_employer'] = ['KIRKLAND & ELLIS LLP', 'SELF-EMPLOYED', 'RETIRED']
        return 3

    trail.run(df, step, 'test_step', 'test_reason',
              ('contributor_street_1', 'contributor_zip', 'contributor_employer'))

    kinds = {(r['sub_id'], r['field']): r['kind'] for r in trail.records}
    assert kinds[('1', 'contributor_street_1')] == FORMAT
    assert kinds[('1', 'contributor_street_2')] == FORMAT
    assert kinds[('2', 'contributor_street_1')] == SEMANTIC
    assert kinds[('3', 'contributor_street_1')] == SEMANTIC
    assert kinds[('1', 'contributor_zip')] == FORMAT
    assert kinds[('2', 'contributor_zip')] == SEMANTIC
    assert kinds[('1', 'contributor_employer')] == FORMAT
    assert kinds[('2', 'contributor_employer')] == FORMAT
    assert kinds[('3', 'contributor_employer')] == SEMANTIC
    assert trail.finish(df) == 0
    semantic = _semantic(trail)
    assert all(r['step'] == 'test_step' and r['reason'] == 'test_reason' for r in semantic)
    assert len(semantic) == 4


def test_trail_flags_changes_made_outside_tracked_steps():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)
    df.loc[0, 'contributor_employer'] = 'ACME'
    trail.run(df, lambda frame: 0, 'noop', 'nothing', ('contributor_employer',))
    assert trail.finish(df) == 1
    untracked = [r for r in trail.records if r['step'] == UNTRACKED_STEP]
    assert untracked[0]['sub_id'] == '1'
    assert untracked[0]['before'] == 'KIRKLAND & ELLIS'
    assert untracked[0]['after'] == 'ACME'


def test_trail_flags_untracked_change_between_steps():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)
    df.loc[1, 'contributor_employer'] = 'ACME'

    def later(frame):
        frame.loc[1, 'contributor_employer'] = 'ACME CORPORATION'
        return 1

    trail.run(df, later, 'later', 'suffix', ('contributor_employer',))
    steps = [(r['step'], r['before'], r['after']) for r in trail.records if r['sub_id'] == '2']
    assert steps == [
        (UNTRACKED_STEP, 'SELF', 'ACME'),
        ('later', 'ACME', 'ACME CORPORATION'),
    ]
    assert trail.finish(df) == 0


def test_per_row_reason_and_evidence():
    df = _frame()
    df['_why'] = ['a', 'b', np.nan]
    trail = AuditTrail()
    trail.start(df)

    def flip(frame):
        frame['is_individual'] = [True, True, False]
        return 1

    trail.run(df, flip, 'reclassify', lambda frame: frame['_why'], ('is_individual',),
              evidence='was: f')
    trail.finish(df)
    semantic = _semantic(trail)
    assert len(semantic) == 1
    record = semantic[0]
    assert record['sub_id'] == '1'
    assert record['reason'] == 'a'
    assert record['evidence'] == 'was: f'
    # 't' -> True and 'f' -> False: format
    assert len(trail.net_records()) == 3


def test_created_column_is_a_baseline_and_category_fills_are_format():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    def create(frame):
        frame['occupation_category'] = ['LEGAL', 'OTHER', 'POLITICAL COMMITTEE']
        return 3

    trail.run(df, create, 'categorize', 'derived', ('occupation_category',))
    assert trail.records == []

    def change(frame):
        frame.loc[0, 'occupation_category'] = 'FINANCE / INVESTMENT'
        frame.loc[1, 'occupation_category'] = np.nan
        return 2

    trail.run(df, change, 'recategorize', 'rule', ('occupation_category',))
    assert [(r['sub_id'], r['kind']) for r in trail.records] == [('1', SEMANTIC), ('2', FORMAT)]
    assert trail.finish(df) == 0


def test_person_name_fields_of_non_individuals_are_format():
    df = _frame()
    df['contributor_first_name'] = ['JOHN', 'JANE', 'WESLEY']
    df['contributor_last_name'] = ['SMITH', 'DOE', 'BELL FOR MISSOURI']
    trail = AuditTrail()
    trail.start(df)

    def clear_committee_names(frame):
        frame.loc[2, ['contributor_first_name', 'contributor_last_name']] = np.nan
        frame.loc[0, 'contributor_first_name'] = 'JONATHAN'
        return 3

    trail.run(df, clear_committee_names, 'names', 'cleared',
              ('contributor_first_name', 'contributor_last_name'))
    kinds = {(r['sub_id'], r['field']): r['kind'] for r in trail.records}
    assert kinds[('3', 'contributor_first_name')] == FORMAT
    assert kinds[('3', 'contributor_last_name')] == FORMAT
    assert kinds[('1', 'contributor_first_name')] == SEMANTIC


def test_changes_undone_later_are_dropped():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    def null_it(frame):
        frame.loc[0, 'contributor_employer'] = np.nan
        frame.loc[1, 'contributor_employer'] = np.nan
        return 2

    def restore_it(frame):
        frame.loc[0, 'contributor_employer'] = 'KIRKLAND & ELLIS'
        frame.loc[1, 'contributor_employer'] = 'SELF'
        return 2

    def change_it(frame):
        frame.loc[1, 'contributor_employer'] = 'ACME'
        return 1

    trail.run(df, null_it, 'null', 'nulled', ('contributor_employer',))
    trail.run(df, restore_it, 'restore', 'restored', ('contributor_employer',))
    trail.run(df, change_it, 'change', 'changed', ('contributor_employer',))
    assert trail.finish(df) == 0
    assert [r['step'] for r in trail.records] == ['null', 'null', 'restore', 'restore', 'change']
    assert [(r['sub_id'], r['step']) for r in trail.net_records()] == [('2', 'change')]


def test_summary_counts_by_step_and_reason():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)
    trail.run(df, lambda frame: frame.__setitem__('contributor_zip', ['10022', '91367', np.nan]),
              'zips', 'zip_cleaned', ('contributor_zip',))
    trail.finish(df)
    assert summarize(trail.net_records())['zips'] == {'semantic': 1, 'format': 1, 'reasons': {'zip_cleaned': 1}}
