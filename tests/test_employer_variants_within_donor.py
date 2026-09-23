"""One donor, one company name: substring and acronym variants converge; different companies stay."""
import pandas as pd

from fec.cleaning.donor_consistency.employer import (
    _employer_acronym_variants,
    _employer_substring_variants,
)

_COLS = ['entity_type', 'donor_key', 'contributor_employer']


def _df(rows):
    return pd.DataFrame(rows, columns=_COLS)


def test_truncated_short_form_takes_the_full_name_regardless_of_counts():
    df = _df([('INDIVIDUAL', 'k1', 'SYNERGY')] * 4 + [('INDIVIDUAL', 'k1', 'SYNERGY HEALTH PARTNERS')] * 4)
    assert _employer_substring_variants(df) == 4
    assert set(df.contributor_employer) == {'SYNERGY HEALTH PARTNERS'}


def test_widely_used_parent_brand_absorbs_its_division():
    rows = [('INDIVIDUAL', 'k1', 'NYU')] * 8 + [('INDIVIDUAL', 'k1', 'NYU LANGONE HEALTH HUNTINGTON MEDICAL')] * 14
    rows += [('INDIVIDUAL', f'k{i}', 'NYU') for i in range(2, 8)]          # six other people file plain NYU
    df = _df(rows)
    assert _employer_substring_variants(df) == 14
    assert set(df.contributor_employer) == {'NYU'}


def test_containment_is_by_whole_words_only():
    df = _df([('INDIVIDUAL', 'k1', 'IDA'), ('INDIVIDUAL', 'k1', 'FLORIDA POWER')])
    assert _employer_substring_variants(df) == 0


def test_acronym_next_to_its_expansion_takes_the_full_name():
    df = _df([('INDIVIDUAL', 'k1', 'WPCM')] * 3 + [('INDIVIDUAL', 'k1', 'WHITE PINE CAPITAL MANAGEMENT')] * 11
             + [('INDIVIDUAL', 'k1', 'JHU APL'), ('INDIVIDUAL', 'k1', 'JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LABORATORY')]
             + [('INDIVIDUAL', 'k2', 'HCC')] * 8 + [('INDIVIDUAL', 'k2', 'HOWARD COMMUNITY COLLEGE')] * 2
             + [('INDIVIDUAL', 'k3', 'HCC')] * 5)                            # another person's HCC is untouched
    assert _employer_acronym_variants(df) == 3 + 1 + 8
    assert df[df.donor_key == 'k1'].contributor_employer.isin({'WHITE PINE CAPITAL MANAGEMENT', 'JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LABORATORY'}).all()
    assert set(df[df.donor_key == 'k2'].contributor_employer) == {'HOWARD COMMUNITY COLLEGE'}
    assert set(df[df.donor_key == 'k3'].contributor_employer) == {'HCC'}


def test_unrelated_short_names_and_other_companies_stay():
    df = _df([('INDIVIDUAL', 'k1', 'THE KRAFT GROUP')] * 7 + [('INDIVIDUAL', 'k1', 'KRAFT FAMILY PHILANTHROPIES')]
             + [('INDIVIDUAL', 'k2', 'ABC')] * 4 + [('INDIVIDUAL', 'k2', 'GOLDMAN SACHS')] * 2)
    assert _employer_substring_variants(df) == 0
    assert _employer_acronym_variants(df) == 0
