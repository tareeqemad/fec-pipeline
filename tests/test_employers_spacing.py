"""Reviewed spelling wins; word boundaries and legal forms remain significant."""
import pandas as pd

from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.work.employer_groups import (
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


def test_group_words_keep_breaks_and_legal_forms():
    assert employer_group_words('TWIN CITY FAN COMPANIES, LTD.') == ('TWIN', 'CITY', 'FAN', 'COMPANIES', 'LTD')
    assert employer_group_words('ACME&SONS') == ('ACME', 'AND', 'SONS')
    assert _employer_group_key('TWINCITY FAN') == _employer_group_key('TWIN CITY FAN') == 'TWIN CITY FAN COMPANIES LTD'
    assert _employer_group_key('ACME & SONS LLP') == 'ACME AND SONS LLP'


def test_documented_spelling_beats_a_more_frequent_misspelling():
    df = _frame([
        ('TWINCITY FAN', 'BARRY, MICHAEL', 7),
        ('TWIN CITY FAN', 'BARRY, MICHAEL', 4),
        ('TWIN CITY FAN COMPANIES LTD', 'BARRY, MICHAEL', 13),
    ])
    _canonicalize_employers(df)
    assert set(df['contributor_employer']) == {'TWIN CITY FAN COMPANIES LTD'}


def test_unrelated_company_sharing_the_spaced_prefix_cannot_flip_the_group():
    # BLACK ROCK COFFEE is filed by other people: no evidence for BLACKROCK's filers
    df = _frame([
        ('BLACKROCK', 'SMITH, ANN', 10),
        ('BLACK ROCK', 'JONES, BOB', 2),
        ('BLACK ROCK COFFEE', 'LEE, CARL', 20),
    ])
    _canonicalize_employers(df)
    # The reviewed BLACK ROCK spelling is not a verified global BlackRock alias.
    assert set(df['contributor_employer']) == {'BLACKROCK', 'BLACK ROCK', 'BLACK ROCK COFFEE'}


def test_explicit_rule_wins_regardless_of_frequency():
    df = _frame([
        ('AB INITIO', 'DALEZMAN, ALLEN', 17),
        ('ABINITIO', 'DALEZMAN, ALLEN', 10),
        ('AB INITIO SOFTWARE', 'DALEZMAN, ALLEN', 3),
        ('ABINITIO SOFTWARE', 'DALEZMAN, ALLEN', 10),
    ])
    _canonicalize_employers(df)
    assert df['contributor_employer'].eq('AB INITIO SOFTWARE').all()


def test_punctuation_breaks_are_not_spacing_evidence():
    df = _frame([
        ('BANK OF AMERICA/ MERRILL', 'MORSE, RICHARD', 4),
        ('BANK OF AMERICA/MERRILL', 'LEVINSON, PETER', 2),
        ('BANK OF AMERICA/MERRILL LYNCH', 'MORSE, RICHARD', 6),
    ])
    _canonicalize_employers(df)
    # An explicit formatting rule changes slash spacing, not the affiliation.
    assert df['contributor_employer'].tolist() == (
        ['BANK OF AMERICA/MERRILL'] * 4
        + ['BANK OF AMERICA/MERRILL'] * 2
        + ['BANK OF AMERICA/MERRILL LYNCH'] * 6
    )


def test_global_rule_does_not_need_filer_names():
    df = pd.DataFrame({'contributor_employer': ['TWINCITY FAN'] * 7 + ['TWIN CITY FAN'] * 4
                       + ['TWIN CITY FAN COMPANIES LTD'] * 13})
    _canonicalize_employers(df)
    assert set(df['contributor_employer']) == {'TWIN CITY FAN COMPANIES LTD'}


def test_reviewed_names_keep_attested_spacing():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': ['TWIN CITY FAN', 'TWINCITY FAN', 'ACME'],
    })
    apply_reviewed_employer_names(df)
    assert df['contributor_employer'].tolist() == ['TWIN CITY FAN COMPANIES LTD', 'TWIN CITY FAN COMPANIES LTD', 'ACME']


def test_reviewed_name_does_not_need_raw_file():
    df = pd.DataFrame({'entity_type': ['INDIVIDUAL'], 'contributor_employer': ['TWIN CITY FAN']})
    apply_reviewed_employer_names(df)
    assert df['contributor_employer'].iloc[0] == 'TWIN CITY FAN COMPANIES LTD'
