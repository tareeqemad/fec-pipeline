"""Company similarities need an explicit rule even within one donor's filings."""
import pandas as pd

from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names

_COLS = ['entity_type', 'donor_key', 'contributor_employer']


def _df(rows):
    return pd.DataFrame(rows, columns=_COLS)


def test_reviewed_ambiguous_short_name_stays_separate_regardless_of_counts():
    df = _df([('INDIVIDUAL', 'k1', 'SYNERGY')] * 4 + [('INDIVIDUAL', 'k1', 'SYNERGY HEALTH PARTNERS')] * 4)
    assert apply_reviewed_employer_names(df) == 0
    assert df.contributor_employer.tolist() == ['SYNERGY'] * 4 + ['SYNERGY HEALTH PARTNERS'] * 4


def test_widely_used_parent_brand_does_not_absorb_a_reviewed_medical_unit():
    rows = [('INDIVIDUAL', 'k1', 'NYU')] * 8 + [('INDIVIDUAL', 'k1', 'NYU LANGONE HEALTH HUNTINGTON MEDICAL')] * 14
    rows += [('INDIVIDUAL', f'k{i}', 'NYU') for i in range(2, 8)]          # six other people file plain NYU
    df = _df(rows)
    assert apply_reviewed_employer_names(df) == 0
    assert df.contributor_employer.tolist() == (
        ['NYU'] * 8 + ['NYU LANGONE HEALTH HUNTINGTON MEDICAL'] * 14 + ['NYU'] * 6
    )


def test_containment_is_by_whole_words_only():
    df = _df([('INDIVIDUAL', 'k1', 'IDA'), ('INDIVIDUAL', 'k1', 'FLORIDA POWER')])
    assert apply_reviewed_employer_names(df) == 0


def test_acronym_next_to_its_expansion_needs_a_scoped_rule(monkeypatch):
    df = _df([('INDIVIDUAL', 'k1', 'WPCM')] * 3 + [('INDIVIDUAL', 'k1', 'WHITE PINE CAPITAL MANAGEMENT')] * 11
             + [('INDIVIDUAL', 'k1', 'JHU APL'), ('INDIVIDUAL', 'k1', 'JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LABORATORY')]
             + [('INDIVIDUAL', 'k2', 'HCC')] * 8 + [('INDIVIDUAL', 'k2', 'HOWARD COMMUNITY COLLEGE')] * 2
             + [('INDIVIDUAL', 'k3', 'HCC')] * 5)                            # another person's HCC is untouched
    before = df.contributor_employer.tolist()
    assert apply_reviewed_employer_names(df) == 0
    assert df.contributor_employer.tolist() == before
    # A reviewed filing anchors this donor and this cycle only.
    from fec.cleaning.work import employer_aliases
    df['sub_id'] = df.index.astype(str)
    df['identity_status'] = 'confirmed'
    df['two_year_transaction_period'] = '2024'
    anchor = df.index[df.donor_key.eq('k2')][0]
    monkeypatch.setattr(employer_aliases, '_SCOPED_RULES', [
        (str(anchor), '2024', 'HCC', 'HOWARD COMMUNITY COLLEGE'),
    ])
    assert apply_reviewed_employer_names(df) == 8
    assert set(df[df.donor_key == 'k2'].contributor_employer) == {'HOWARD COMMUNITY COLLEGE'}
    assert set(df[df.donor_key == 'k3'].contributor_employer) == {'HCC'}


def test_unrelated_short_names_and_other_companies_stay():
    df = _df([('INDIVIDUAL', 'k1', 'THE KRAFT GROUP')] * 7 + [('INDIVIDUAL', 'k1', 'KRAFT FAMILY PHILANTHROPIES')]
             + [('INDIVIDUAL', 'k2', 'ABC')] * 4 + [('INDIVIDUAL', 'k2', 'GOLDMAN SACHS')] * 2)
    assert apply_reviewed_employer_names(df) == 0
    assert apply_reviewed_employer_names(df) == 0
