"""Spacing variants of one employer: the word breaks the donor's own fuller filings use win (TWINCITY FAN -> TWIN CITY FAN)."""
import pandas as pd

from fec.cleaning.employer_synonyms.canonical import restore_display_suffixes
from fec.cleaning.occupations.employer_groups import (
    _canonicalize_employers,
    _employer_group_key,
    employer_group_words,
)


def _frame(rows):
    """rows: [(employer, filer, times)] -> one row per filing."""
    records = [
        {'contributor_employer': employer, 'contributor_name': filer}
        for employer, filer, times in rows for _ in range(times)
    ]
    return pd.DataFrame(records)


def test_group_words_keep_breaks_and_key_ignores_them():
    assert employer_group_words('TWIN CITY FAN COMPANIES, LTD.') == ('TWIN', 'CITY', 'FAN', 'COMPANIES')
    assert employer_group_words('KIRKLAND&ELLIS') == ('KIRKLAND', 'AND', 'ELLIS')
    assert _employer_group_key('TWINCITY FAN') == _employer_group_key('TWIN CITY FAN') == 'TWINCITYFAN'
    assert _employer_group_key('KIRKLAND & ELLIS LLP') == 'KIRKLANDANDELLIS'


def test_donors_full_name_spacing_beats_a_more_frequent_misspelling():
    df = _frame([
        ('TWINCITY FAN', 'BARRY, MICHAEL', 7),
        ('TWIN CITY FAN', 'BARRY, MICHAEL', 4),
        ('TWIN CITY FAN COMPANIES LTD', 'BARRY, MICHAEL', 13),
    ])
    _canonicalize_employers(df)
    assert set(df['contributor_employer']) == {'TWIN CITY FAN', 'TWIN CITY FAN COMPANIES LTD'}


def test_unrelated_company_sharing_the_spaced_prefix_cannot_flip_the_group():
    # BLACK ROCK COFFEE is filed by other people: no evidence for BLACKROCK's filers
    df = _frame([
        ('BLACKROCK', 'SMITH, ANN', 10),
        ('BLACK ROCK', 'JONES, BOB', 2),
        ('BLACK ROCK COFFEE', 'LEE, CARL', 20),
    ])
    _canonicalize_employers(df)
    assert set(df['contributor_employer']) == {'BLACKROCK', 'BLACK ROCK COFFEE'}


def test_conflicting_evidence_keeps_the_frequency_winner():
    # the same donor writes AB INITIO SOFTWARE and ABINITIO SOFTWARE
    df = _frame([
        ('AB INITIO', 'DALEZMAN, ALLEN', 17),
        ('ABINITIO', 'DALEZMAN, ALLEN', 10),
        ('AB INITIO SOFTWARE', 'DALEZMAN, ALLEN', 3),
        ('ABINITIO SOFTWARE', 'DALEZMAN, ALLEN', 10),
    ])
    _canonicalize_employers(df)
    assert (df['contributor_employer'].isin(['AB INITIO', 'ABINITIO SOFTWARE'])).all()


def test_punctuation_breaks_are_not_spacing_evidence():
    df = _frame([
        ('BANK OF AMERICA/ MERRILL', 'MORSE, RICHARD', 4),
        ('BANK OF AMERICA/MERRILL', 'LEVINSON, PETER', 2),
        ('BANK OF AMERICA/MERRILL LYNCH', 'MORSE, RICHARD', 6),
    ])
    _canonicalize_employers(df)
    assert (df['contributor_employer'].iloc[:6] == 'BANK OF AMERICA/ MERRILL').all()


def test_without_filer_names_the_frequency_rule_is_unchanged():
    df = pd.DataFrame({'contributor_employer': ['TWINCITY FAN'] * 7 + ['TWIN CITY FAN'] * 4
                       + ['TWIN CITY FAN COMPANIES LTD'] * 13})
    _canonicalize_employers(df)
    assert set(df['contributor_employer']) == {'TWINCITY FAN', 'TWIN CITY FAN COMPANIES LTD'}


def test_restore_display_suffixes_does_not_revert_the_attested_spacing(tmp_path):
    raw = _frame([
        ('TWINCITY FAN', 'BARRY, MICHAEL', 7),
        ('TWIN CITY FAN', 'BARRY, MICHAEL', 4),
        ('TWIN CITY FAN COMPANIES LTD', 'BARRY, MICHAEL', 13),
        ('ACME INC', 'DOE, JANE', 3),
    ])
    raw_csv = tmp_path / 'contributions.csv'
    raw.to_csv(raw_csv, index=False)
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': ['TWIN CITY FAN', 'TWINCITY FAN', 'ACME'],
    })
    restore_display_suffixes(df, raw_csv)
    assert df['contributor_employer'].tolist() == ['TWIN CITY FAN', 'TWIN CITY FAN', 'ACME INC']


def test_restore_display_suffixes_without_filer_column_keeps_the_raw_mode(tmp_path):
    raw_csv = tmp_path / 'contributions.csv'
    pd.DataFrame({'contributor_employer': ['TWINCITY FAN'] * 7 + ['TWIN CITY FAN'] * 4}).to_csv(raw_csv, index=False)
    df = pd.DataFrame({'entity_type': ['INDIVIDUAL'], 'contributor_employer': ['TWIN CITY FAN']})
    restore_display_suffixes(df, raw_csv)
    assert df['contributor_employer'].iloc[0] == 'TWINCITY FAN'
