"""first_name rules in contributor_name_rules.csv fire only on the filer they were verified on.

2026-09-23 audit (finding: names_garbled_first): the 29 keyboard-error rules
were keyed on the first word of the first name alone, so BRIA, CAROLL, AURI,
DORUS and ISSAC (real given names) and ERR / JOIN would have renamed any
other filer. Each rule now names its filer by surname + ZIP5, and the 62
verified corrections of the last run still come out the same.
"""
import numpy as np
import pandas as pd
import pytest

from fec.cleaning import name_rules
from fec.cleaning.pipeline.names import _clean_names, _fix_garbled_first_names


def _people(rows):
    base = dict(
        entity_type='INDIVIDUAL', is_individual=True,
        contributor_occupation='RETIRED', occupation_category='RETIRED',
        occupation_status='DISCLOSED', contributor_employer='RETIRED',
        committee_type=pd.NA,
    )
    records = []
    for last, first, zip_code in rows:
        name = f"{last}, {first}" if isinstance(last, str) else f", {first}"
        records.append({
            **base,
            'contributor_name': name,
            'contributor_last_name': last,
            'contributor_first_name': first,
            'contributor_zip': zip_code,
        })
    df = pd.DataFrame(records)
    for col in ('committee_type', 'occupation_category'):
        df[col] = df[col].astype(object)
    df['contributor_zip'] = df['contributor_zip'].astype('string')
    return df


# Every verified correction of the last run (one row per rule), as filed.
VERIFIED = [
    ('FISHER', 'JEFREY', '100212345', 'JEFFREY'),
    ('SHPALL', 'ROBERTB', '90034', 'ROBERT'),
    ('ROSENBERG', 'PETGER', '19072', 'PETER'),
    ('DAVIS', 'DORUS', '950323589', 'DORIS'),
    ('WEINTRAUB', 'RICHAR', '90264', 'RICHARD'),
    ('SILBERGLIED', 'RUSELL', '19806', 'RUSSELL'),
    ('LEITNER', 'MARRISSA', '91436', 'MARISSA'),
    ('BROOKS', 'STWART', '913112631', 'STEWART'),
    ('MILLMAN', 'MQRY', '10075', 'MARY'),
    ('BASS', 'RHONDS', '77401', 'RHONDA'),
    ('GAFFEN', 'YEHDUI', '92131', 'YEHUDI'),
    ('KUPIETZKY', 'ARLEBE', '90067', 'ARLENE'),
    ('ADLER', 'ERVI', '90254', 'ERVIN'),
    ('DROR', 'BRIA', '90036', 'BRIAN'),
    ('GOODMAN', 'CECIIA', '92660', 'CECILIA'),
    ('FOX', 'WILIAM', '21208', 'WILLIAM'),
    ('ROSS', 'MATTHWE', '90272', 'MATTHEW'),
    ('ROSS', 'MATTHW', '90272', 'MATTHEW'),
    ('ALBERS', 'DWNNIS', '81611', 'DENNIS'),
    ('SHICHMAN', 'CAROLL', '07076', 'CAROL'),
    ('KAGAN', 'ERR', '11021', 'ERRAN'),
    ('STREIT', 'AURI', '90025', 'AURIEL'),
    ('DIAMOND', 'JOIN E', '33154', 'JON E'),
    ('GINDI', 'ISSAC S.', '10280', 'ISAAC S.'),
    ('ELLIS', 'JONAATHAN', '33629', 'JONATHAN'),
    ('GLIKSBERG', 'JILLIN', '33312', 'JILLIAN'),
    ('KUPOR', 'LARYL', '77401', 'LARY'),
    ('HANNAN', 'JUDITY', '11201', 'JUDITH'),
    ('COHEN', 'PHILIP. H', '33139', 'PHILIP H'),
]


def test_every_first_name_rule_is_scoped_to_one_filer():
    assert len(name_rules.FIRST_NAME_FIXES) == 29
    for key in name_rules.FIRST_NAME_FIXES:
        last, word, zip5 = key
        assert last and word and len(zip5) == 5 and zip5.isdigit(), key


@pytest.mark.parametrize('last,first,zip_code,expected', VERIFIED)
def test_verified_filer_is_still_corrected(last, first, zip_code, expected):
    df = _people([(last, first, zip_code)])
    _clean_names(df)
    assert df.at[0, 'contributor_first_name'] == expected
    assert df.at[0, 'contributor_name'] == f"{last}, {expected}"


def test_the_verified_set_covers_every_rule():
    covered = {(last, first.split()[0], zip_code.zfill(5)[:5])
               for last, first, zip_code, _ in VERIFIED}
    assert covered == set(name_rules.FIRST_NAME_FIXES)


@pytest.mark.parametrize('last,first,zip_code', [
    # real given names on somebody else's filing
    ('SMITH', 'BRIA', '90036'),
    ('JONES', 'CAROLL', '07076'),
    ('LEE', 'AURI', '90025'),
    ('MILLER', 'DORUS', '95032'),
    ('COHEN', 'ISSAC', '10280'),
    ('BROWN', 'ERR', '11021'),
    ('GREEN', 'JOIN', '33154'),
    ('SMITH', 'JEFREY', '10021'),
    # the rule's surname and word, but a different filer elsewhere
    ('DAVIS', 'DORUS', '10001'),
    ('DROR', 'BRIA', '10001'),
    ('GINDI', 'ISSAC', '11223'),
    ('KAGAN', 'ERR', '94110'),
    ('DIAMOND', 'JOIN', '60622'),
    # no ZIP at all: never guessed
    ('DAVIS', 'DORUS', pd.NA),
    ('DROR', 'BRIA', ''),
])
def test_same_word_on_another_filer_is_left_as_filed(last, first, zip_code):
    df = _people([(last, first, zip_code)])
    _clean_names(df)
    assert df.at[0, 'contributor_first_name'] == first
    assert df.at[0, 'contributor_name'] == f"{last}, {first}"


@pytest.mark.parametrize('missing', [np.nan, None, pd.NA])
def test_missing_surname_never_becomes_nan_text(missing):
    """names.py used `last = df.at[idx, 'contributor_last_name'] or ''`: NaN is
    truthy, so a matching row with no surname was rebuilt as 'nan, JEFFREY'."""
    df = _people([('FISHER', 'JEFREY', '10021'), ('FISHER', 'JEFREY', '10021')])
    df['contributor_last_name'] = df['contributor_last_name'].astype(object)
    df.at[1, 'contributor_last_name'] = missing
    df.at[1, 'contributor_name'] = 'JEFREY'
    _fix_garbled_first_names(df, df['entity_type'].eq('INDIVIDUAL'))
    assert df.at[0, 'contributor_name'] == 'FISHER, JEFFREY'
    assert df.at[1, 'contributor_name'] == 'JEFREY'
    assert df.at[1, 'contributor_first_name'] == 'JEFREY'
    assert not df['contributor_name'].astype(str).str.contains('nan|None|<NA>').any()


def test_committee_rows_are_not_touched():
    df = _people([('FISHER', 'JEFREY', '10021')])
    df['entity_type'] = 'COMMITTEE/PAC'
    _fix_garbled_first_names(df, df['entity_type'].eq('INDIVIDUAL'))
    assert df.at[0, 'contributor_first_name'] == 'JEFREY'


def test_frame_without_zip_column_is_left_alone():
    df = _people([('FISHER', 'JEFREY', '10021')]).drop(columns='contributor_zip')
    _fix_garbled_first_names(df, df['entity_type'].eq('INDIVIDUAL'))
    assert df.at[0, 'contributor_first_name'] == 'JEFREY'


# ---- rule CSV format -------------------------------------------------------

HEADER = 'rule_type,raw_value,corrected_value,source,zip5\n'


def _load(tmp_path, body, header=HEADER):
    path = tmp_path / 'contributor_name_rules.csv'
    path.write_text(header + body, encoding='utf-8')
    return name_rules._load_rules(path)


def test_scoped_first_name_rule_loads_with_several_zips(tmp_path):
    rules = _load(tmp_path, 'first_name,"FISHER, JEFREY",JEFFREY,manual,10021|10065\n')
    assert rules['first_name'] == {
        ('FISHER', 'JEFREY', '10021'): 'JEFFREY',
        ('FISHER', 'JEFREY', '10065'): 'JEFFREY',
    }


@pytest.mark.parametrize('row', [
    'first_name,JEFREY,JEFFREY,manual,10021\n',             # no surname
    'first_name,"FISHER, JEFREY",JEFFREY,manual,\n',         # no ZIP5
    'first_name,"FISHER, JEFREY",JEFFREY,manual,1002\n',     # not a ZIP5
    'first_name,"FISHER, JEFREY M",JEFFREY,manual,10021\n',  # more than one word
    'first_name,"FISHER, JEFREY","FISHER, JEFFREY",manual,10021\n',
    'exact_name,"MEYERS, STUART SARA","MEYERS, STUART",manual,30338\n',
])
def test_unscoped_or_malformed_rule_is_rejected(tmp_path, row):
    with pytest.raises(ValueError):
        _load(tmp_path, row)


def test_same_filer_key_twice_is_rejected(tmp_path):
    body = ('first_name,"FISHER, JEFREY",JEFFREY,manual,10021\n'
            'first_name,"FISHER, JEFREY",GEOFFREY,manual,10021\n')
    with pytest.raises(ValueError, match='Duplicate'):
        _load(tmp_path, body)


def test_other_rule_kinds_load_from_the_old_four_column_format(tmp_path):
    body = ('exact_name,"CHENEY, D AVID","CHENEY, DAVID",manual\n'
            'sub_id,4011420231698186281,"KELLOGG, SARAH",manual\n'
            'committee_name,SOLOW AND CO,SOLOW & CO.,manual\n')
    rules = _load(tmp_path, body, header='rule_type,raw_value,corrected_value,source\n')
    assert rules['exact_name'] == {'CHENEY, D AVID': 'CHENEY, DAVID'}
    assert rules['sub_id'] == {'4011420231698186281': 'KELLOGG, SARAH'}
    assert rules['committee_name'] == {'SOLOW AND CO': 'SOLOW & CO.'}
    assert rules['first_name'] == {}
