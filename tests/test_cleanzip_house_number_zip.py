"""ZIP box holding the house number: the real city + ZIP typed into the street is moved back.

Raw FEC rows (sub_ids 4062320251206918355 AGAM, 4010420231645702852 KREBS,
4080620251215491506 SACKHEIM, 4080120261540163141 JANIS) carry ZIP = house number
+ '0001' with a city/state derived from that fake ZIP (ROCHESTER NY, WASHINGTON DC,
ALBANY NY, JAMAICA NY), while the filer's real city and ZIP sit in the street.
"""
import pandas as pd
import pytest

from fec.cleaning.addresses import (
    _drop_repeated_street,
    _normalize_street,
    clean_cities,
    clean_streets,
    clean_zips,
)
from fec.cleaning.audit_trail import PLACE_FIELDS, STREET_FIELDS, UNTRACKED_STEP, AuditTrail
from fec.cleaning.pipeline.address_fixes import state_zip
from fec.cleaning.pipeline.address_fixes.state_zip import is_zcta, zip_state

COLUMNS = [
    'sub_id', 'contributor_first_name', 'contributor_last_name',
    'contributor_street_1', 'contributor_street_2',
    'contributor_city', 'contributor_state', 'contributor_zip',
]


def _frame(rows):
    df = pd.DataFrame(
        [dict(zip(COLUMNS, [str(i)] + list(row))) for i, row in enumerate(rows)],
        columns=COLUMNS,
    )
    df['contributor_zip'] = df['contributor_zip'].astype('string')
    return df


def _others(city, state, zip5, n=3, street='1 MAIN ST'):
    """n different people filing the same city/state/ZIP (the place, not a person's address)."""
    return [
        (f'FIRST{k}', f'OTHER{k}', f'{k + 10} {street}', '', city, state, zip5)
        for k in range(n)
    ]


def _clean(df):
    df, _ = clean_streets(df)
    df, _ = clean_cities(df, fuzzy=False)
    df, _ = clean_zips(df)
    return df


def _place(df, sub_id):
    row = df.loc[df['sub_id'] == sub_id].iloc[0]
    blank = lambda v: '' if pd.isna(v) else v  # noqa: E731
    return (
        blank(row['contributor_street_1']), blank(row['contributor_street_2']),
        blank(row['contributor_city']), blank(row['contributor_state']),
        blank(row['contributor_zip']),
    )


def test_agam_city_zip_leave_street1_and_state_comes_from_zip():
    df = _frame([
        ('AMIR', 'AGAM', '14638 TUDOR DRIVE ENCINO 91436', '', 'ROCHESTER', 'NY', '146380001'),
        *_others('ENCINO', 'CA', '91436'),
    ])
    out = _clean(df)
    assert _place(out, '0') == ('14638 TUDOR DR', '', 'ENCINO', 'CA', '91436')
    assert not [column for column in out.columns if column.startswith('_house_zip')]


def test_krebs_street_without_type_keeps_the_attested_city():
    df = _frame([
        ('DAVID', 'KREBS', '20440 PCH MALIBU 90265', '', 'WASHINGTON', 'DC', '204400001'),
        ('JANE', 'OTHER', '22000 PACIFIC COAST HWY', '', 'MALIBU', 'CA', '90265'),
    ])
    assert _place(_clean(df), '0') == ('20440 PCH', '', 'MALIBU', 'CA', '90265')


def test_sackheim_state_comes_from_her_own_filings(monkeypatch):
    # the crosswalk is switched off: UT can only come from her own KAMAS filings
    monkeypatch.setattr(state_zip, 'zip_state', lambda zip5: '')
    df = _frame([
        ('MICHELE', 'SACKHEIM', '12230 HOLLOW ROAD KAMAS 84036', '', 'ALBANY', 'NY', '122300001'),
        ('MICHELE', 'SACKHEIM', '12230 BONE HOLLOW RD', '', 'KAMAS', 'UT', '840369340'),
        ('MICHELE', 'SACKHEIM', '12230 BONE HOLLOW RD', '', 'KAMAS', 'UT', '840369340'),
    ])
    # the street word the filing dropped (BONE) comes back from her own filings too
    assert _place(_clean(df), '0') == ('12230 BONE HOLLOW RD', '', 'KAMAS', 'UT', '84036')


def test_street_is_never_completed_from_another_persons_filings():
    # a household member (another first name) at the same house is another person
    df = _frame([
        ('MICHELE', 'SACKHEIM', '12230 HOLLOW ROAD KAMAS 84036', '', 'ALBANY', 'NY', '122300001'),
        ('JOHN', 'SACKHEIM', '12230 BONE HOLLOW RD', '', 'KAMAS', 'UT', '840369340'),
    ])
    assert _place(_clean(df), '0') == ('12230 HOLLOW RD', '', 'KAMAS', 'UT', '84036')


def test_own_street_needs_the_same_house_number():
    df = _frame([
        ('MICHELE', 'SACKHEIM', '12230 HOLLOW ROAD KAMAS 84036', '', 'ALBANY', 'NY', '122300001'),
        ('MICHELE', 'SACKHEIM', '900 BONE HOLLOW RD', '', 'KAMAS', 'UT', '84036'),
    ])
    assert _place(_clean(df), '0') == ('12230 HOLLOW RD', '', 'KAMAS', 'UT', '84036')


def test_no_state_from_any_source_leaves_the_row_as_filed(monkeypatch):
    monkeypatch.setattr(state_zip, 'zip_state', lambda zip5: '')
    df = _frame([
        ('MICHELE', 'SACKHEIM', '12230 HOLLOW ROAD KAMAS 84036', '', 'ALBANY', 'NY', '122300001'),
    ])
    out = _clean(df)
    assert _place(out, '0')[2:] == ('ALBANY', 'NY', '12230')


def test_typed_state_before_the_zip_is_used_and_removed():
    df = _frame([
        ('AMIR', 'AGAM', '14638 TUDOR DRIVE ENCINO CA 91436', '', 'ROCHESTER', 'NY', '14638'),
        *_others('ENCINO', 'CA', '91436'),
    ])
    assert _place(_clean(df), '0') == ('14638 TUDOR DR', '', 'ENCINO', 'CA', '91436')


def test_typed_state_that_contradicts_the_zip_is_not_used_nor_kept_in_the_city():
    df = _frame([
        ('AMIR', 'AGAM', '14638 TUDOR DRIVE ENCINO NY 91436', '', 'ROCHESTER', 'NY', '146380001'),
        ('ANN', 'SMITH', '14638 TUDOR DRIVE ENCINO NY 91436', '', 'ROCHESTER', 'NY', '146380001'),
        *_others('ENCINO', 'CA', '91436'),
    ])
    out = _clean(df)
    assert _place(out, '0') == ('14638 TUDOR DR', '', 'ENCINO', 'CA', '91436')
    # the same with no filing naming ENCINO at 91436: the city still stops before NY
    df = _frame([
        ('AMIR', 'AGAM', '14638 TUDOR DRIVE ENCINO NY 91436', '', 'ROCHESTER', 'NY', '146380001'),
    ])
    assert _place(_clean(df), '0') == ('14638 TUDOR DR', '', 'ENCINO', 'CA', '91436')


def test_janis_cut_off_street2_place_is_completed_from_widely_filed_place():
    df = _frame([
        ('MARTIN', 'JANIS', '11425 TWINING LANE 11425 TWINING L', 'POTO 2085', 'JAMAICA', 'NY', '114250001'),
        *_others('POTOMAC', 'MD', '20854'),
        # a PO-box ZIP filed by one person is not a second candidate
        ('PO', 'BOXER', 'PO BOX 59', '', 'POTOMAC', 'MD', '20859'),
    ])
    assert _place(_clean(df), '0') == ('11425 TWINING LN', '', 'POTOMAC', 'MD', '20854')


def test_janis_is_not_completed_from_a_household_members_address():
    # only the spouse and one other person file POTOMAC 20854: that is a
    # person's address, not a place, so the cut-off text is not completed
    df = _frame([
        ('MARTIN', 'JANIS', '11425 TWINING LANE 11425 TWINING L', 'POTO 2085', 'JAMAICA', 'NY', '114250001'),
        ('LESLIE', 'JANIS', '11425 TWINING LN', '', 'POTOMAC', 'MD', '208541860'),
        ('ANN', 'OTHER', '1 MAIN ST', '', 'POTOMAC', 'MD', '20854'),
    ])
    street1, street2, city, state, zip5 = _place(_clean(df), '0')
    assert (street2, city, state, zip5) == ('POTO 2085', 'JAMAICA', 'NY', '11425')


def test_real_house_number_equal_to_zip_without_a_typed_place_is_untouched():
    # FADER: '21204 OLYMPIC PLACE' TOWSON 21204 names no other city or ZIP
    df = _frame([
        ('STEVE', 'FADER', '21204 OLYMPIC PLACE', '', 'TOWSON', 'MD', '21204'),
        ('STEVE', 'FADER', '21204 OLYMPIC PLACE', '', 'TOWSON', 'MD', '212040001'),
    ])
    out = _clean(df)
    assert _place(out, '0') == ('21204 OLYMPIC PL', '', 'TOWSON', 'MD', '21204')
    assert _place(out, '1') == ('21204 OLYMPIC PL', '', 'TOWSON', 'MD', '21204')


def test_ordinary_zip_with_a_typed_city_is_not_this_rule():
    # the ZIP box holds a real ZIP, not the house number: the place stays
    df = _frame([
        ('ANN', 'SMITH', '14638 TUDOR DRIVE ENCINO 91436', '', 'ENCINO', 'CA', '914361234'),
        ('BOB', 'JONES', '500 OAK ST SPRINGFIELD 62701', '', 'CHICAGO', 'IL', '60601'),
        *_others('ENCINO', 'CA', '91436'),
        *_others('SPRINGFIELD', 'IL', '62701'),
    ])
    out = _clean(df)
    assert _place(out, '0')[2:] == ('ENCINO', 'CA', '91436')
    assert _place(out, '1')[2:] == ('CHICAGO', 'IL', '60601')


def test_house_number_zip_without_typed_place_or_unit_street2_is_untouched():
    df = _frame([
        ('ANN', 'SMITH', '14638 TUDOR DRIVE', '', 'ROCHESTER', 'NY', '146380001'),
        ('BOB', 'JONES', '14638 TUDOR DRIVE', 'APT 950', 'ROCHESTER', 'NY', '146380001'),
        *_others('APTOS', 'CA', '95003'),
    ])
    out = _clean(df)
    assert _place(out, '0')[2:] == ('ROCHESTER', 'NY', '14638')
    assert _place(out, '1') == ('14638 TUDOR DR', 'APT 950', 'ROCHESTER', 'NY', '14638')


def test_street_type_marks_the_city_when_no_filing_attests_it():
    df = _frame([
        ('ANN', 'SMITH', '14638 TUDOR DRIVE NEW TOWNVILLE 91436', '', 'ROCHESTER', 'NY', '146380001'),
    ])
    assert _place(_clean(df), '0') == ('14638 TUDOR DR', '', 'NEW TOWNVILLE', 'CA', '91436')


def test_changes_are_recorded_by_the_steps_that_own_the_fields():
    df = _frame([
        ('AMIR', 'AGAM', '14638 TUDOR DRIVE ENCINO 91436', '', 'ROCHESTER', 'NY', '146380001'),
        *_others('ENCINO', 'CA', '91436'),
    ])
    trail = AuditTrail()
    trail.start(df)
    df, _ = trail.run(df, clean_streets, 'streets_normalize', 'r', STREET_FIELDS)
    df, _ = trail.run(df, lambda f: clean_cities(f, fuzzy=False), 'cities_normalize', 'r', PLACE_FIELDS)
    df, _ = trail.run(df, clean_zips, 'zips_normalize', 'r', PLACE_FIELDS)
    assert trail.finish(df) == 0
    steps = {(r['sub_id'], r['field']): r['step'] for r in trail.records if r['sub_id'] == '0'}
    assert UNTRACKED_STEP not in steps.values()
    assert steps[('0', 'contributor_street_1')] == 'streets_normalize'
    assert steps[('0', 'contributor_city')] == 'cities_normalize'
    assert steps[('0', 'contributor_state')] == 'cities_normalize'
    assert steps[('0', 'contributor_zip')] == 'cities_normalize'


@pytest.mark.parametrize('street, expected', [
    ('11425 TWINING LN 11425 TWINING L', '11425 TWINING LN'),
    ('43 KEOFFERAM RD 43 KEOFFERAM', '43 KEOFFERAM RD'),
    ('396 FOREST AVE 396 FOREST AVE', '396 FOREST AVE'),
    ('1020 HULL ST / 1020 HULL ST', '1020 HULL ST'),
    ('159 W 159 W', '159 W 159 W'),
    ('1300 E 1300 S', '1300 E 1300 S'),
    ('100 N 100 W', '100 N 100 W'),
    ('12 12TH ST', '12 12TH ST'),
    ('500 MAIN ST 500', '500 MAIN ST 500'),
])
def test_drop_repeated_street(street, expected):
    assert _drop_repeated_street(street) == expected


def test_normalize_street_drops_the_cut_copy_of_the_street():
    assert _normalize_street('11425 TWINING LANE 11425 TWINING L') == '11425 TWINING LN'
    assert _normalize_street('206 CARRIAGE LN, 206 CARRIAGE LN') == '206 CARRIAGE LN'


def test_zip_state_and_zcta_helpers():
    assert zip_state('84036') == 'UT'
    assert zip_state('12230') == 'NY'  # no ZCTA: ZIP3 prefix
    assert zip_state('') == '' and zip_state('ABCDE') == ''
    assert is_zcta('20854') and not is_zcta('20859')
