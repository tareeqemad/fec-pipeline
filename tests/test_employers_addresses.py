"""street_2 placeholders become NULL; a street_1 that is only a floor is a unit, not a street (findings 8/11/35)."""
import numpy as np
import pandas as pd
import pytest

from fec.cleaning.addresses.fixes.recovery import (
    _is_usable_street,
    _recover_nonstreet_from_donor,
    _recover_null_streets,
)
from fec.cleaning.addresses.street_text import (
    _normalize_street,
    _normalize_unit,
    _split_fused_house_number,
)
from fec.cleaning.addresses.streets import clean_streets


def _streets(rows):
    return pd.DataFrame({
        'contributor_street_1': [row[0] for row in rows],
        'contributor_street_2': [row[1] for row in rows],
        'contributor_first_name': ['MURIEL'] * len(rows),
        'contributor_last_name': ['SELIGSON'] * len(rows),
    })


@pytest.mark.parametrize('value', ['.', '..', '-', 'NONE', 'None', 'HOME', 'N/A', 'NA', 'N.A.', 'NULL',
                                   'SAME', 'UNKNOWN', 'USA', 'none.'])
def test_street2_placeholders_are_null(value):
    assert pd.isna(_normalize_unit(value))


@pytest.mark.parametrize('value,expected', [
    ('APT 5', 'APT 5'), ('SUITE 200', 'STE 200'), ('PH', 'PH'), ('X', 'X'), ('B', 'B'),
    ('#', '#'),                     # kept: address_review flags it as a unit without a number
    # USPS unit form (fec/config/streets.py UNIT_RULES): the floor designator first
    ('3RD FLOOR', 'FL 3'), ('FLOOR 3', 'FL 3'),
])
def test_real_units_are_kept(value, expected):
    assert _normalize_unit(value) == expected


def test_clean_streets_nulls_the_audit_placeholders():
    df, _ = clean_streets(_streets([
        ('320 FIRST STREET SE', '.'),
        ('84 BIGELOW RD', 'NONE'),
        ('5918 TYNDALL AVE', 'HOME'),
        ('10 MAIN ST', 'APT 4'),
    ]))
    assert df['contributor_street_2'].isna().tolist() == [True, True, True, False]
    assert df['contributor_street_2'].iloc[3] == 'APT 4'


def test_leading_ordinal_is_not_split_but_a_fused_house_number_is():
    assert _split_fused_house_number('3RD FLOOR') == '3RD FLOOR'
    assert _split_fused_house_number('21ST ST') == '21ST ST'
    assert _split_fused_house_number('112TH AVE NE') == '112TH AVE NE'
    assert _split_fused_house_number('123MAIN ST') == '123 MAIN ST'
    assert _split_fused_house_number('12THOMPSON ST') == '12 THOMPSON ST'
    # 12 takes TH, so ST here is SAINT after a fused house number
    assert _split_fused_house_number('12ST JAMES PL') == '12 ST JAMES PL'
    assert _normalize_street('3RD FLOOR') == '3RD FLOOR'


def test_floor_only_street1_moves_to_an_empty_street2():
    df, _ = clean_streets(_streets([('3RD FLOOR', np.nan), ('FLOOR 12', ''), ('3RD ST', np.nan)]))
    assert df['contributor_street_1'].isna().tolist() == [True, True, False]
    assert df['contributor_street_2'].tolist()[:2] == ['FL 3', 'FL 12']
    assert df['contributor_street_1'].iloc[2] == '3RD ST'


def test_floor_only_street1_with_a_unit_already_in_street2_is_left_for_recovery():
    df, _ = clean_streets(_streets([('3RD FLOOR', 'STE 5')]))
    assert df['contributor_street_1'].iloc[0] == '3RD FLOOR'
    assert df['contributor_street_2'].iloc[0] == 'STE 5'


def test_floor_only_street_is_not_usable():
    usable = _is_usable_street(pd.Series(['3RD FLOOR', 'FL 3', '12 FL', '3RD ST', '123 MAIN ST', 'PO BOX 5']))
    assert usable.tolist() == [False, False, False, True, True, True]


def _donor_rows(first_street, first_unit):
    return pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 2,
        'contributor_name': ['SELIGSON, MURIEL'] * 2,
        'contributor_city': ['NEW YORK'] * 2,
        'contributor_state': ['NY'] * 2,
        'contributor_street_1': [first_street, '15 W 72ND ST'],
        'contributor_street_2': [first_unit, np.nan],
    })


def test_moved_floor_row_is_refilled_from_the_donor_history():
    df, _ = clean_streets(_streets([('3RD FLOOR', np.nan)]))
    rows = _donor_rows(df['contributor_street_1'].iloc[0], df['contributor_street_2'].iloc[0])
    assert _recover_null_streets(rows) == 1
    assert rows['contributor_street_1'].tolist() == ['15 W 72ND ST', '15 W 72ND ST']
    assert rows['contributor_street_2'].iloc[0] == 'FL 3'


def test_floor_only_street_with_a_unit_is_replaced_by_the_donor_street():
    rows = _donor_rows('3RD FLOOR', 'STE 5')
    assert _recover_nonstreet_from_donor(rows) == 1
    assert rows['contributor_street_1'].iloc[0] == '15 W 72ND ST'
