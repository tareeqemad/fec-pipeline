"""MEYERS 'STUART SARA' / 'SARA STUART' are joint filings: kept as filed, on their own key.

1841 VERMACK CT, DUNWOODY GA: STUART MEYERS (35 solo filings) and SARA MEYERS
(25 solo filings, often the same day and amount) are two people with a
verified 'separate' rule. Six raw rows read 'MEYERS, STUART SARA' and one
'MEYERS, SARA STUART'. contributor_name_rules.csv used to trim them to the
first name, which put a joint gift on one spouse's key. Owner decision
2026-09-23: in a joint filing each person keeps their own name and the joint
row stays as filed on its own donor key.

Replay of the clean stage (2026-09-23):
- trims removed, no separation rules: the joint rows joined the spouses' keys
  and donor_canonical_names stamped 'STUART SARA' on all 41 of Stuart's rows
  and 'SARA STUART' on all 26 of Sara's (a partner's name on solo filings);
- trims removed + a guard in donor matching (the joint-filing guard in
  fec/donor_match, or 'separate' rows for each spouse vs each joint spelling):
  the 7 joint rows share one new key and the solo filings are untouched.
So the trims may only stay removed while donor matching keeps joint filings
apart; the second test checks exactly that.
"""
import pandas as pd

from fec.cleaning.entity_classification import apply_name_corrections
from fec.cleaning.name_rules import EXACT_NAME_CORRECTIONS
from fec.donor_match import apply_donor_key, match_donors, merge_split_name_donors

JOINT = ('MEYERS, STUART SARA', 'MEYERS, SARA STUART')


def test_joint_filings_are_kept_as_filed():
    assert not set(JOINT) & set(EXACT_NAME_CORRECTIONS)
    df = pd.DataFrame({
        'sub_id': ['4032520241885655800', '4092320242041367550'],
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_name': list(JOINT),
        'contributor_first_name': ['STUART SARA', 'SARA STUART'],
        'contributor_last_name': ['MEYERS', 'MEYERS'],
    })
    df, changed = apply_name_corrections(df)
    assert changed == 0
    assert df['contributor_name'].tolist() == list(JOINT)
    assert df['contributor_first_name'].tolist() == ['STUART SARA', 'SARA STUART']


def _household():
    rows = []

    def add(first, n, city='DUNWOODY'):
        rows.extend(
            dict(
                entity_type='INDIVIDUAL', contributor_name=f'MEYERS, {first}',
                contributor_first_name=first, contributor_last_name='MEYERS',
                contributor_city=city, contributor_state='GA', contributor_zip='30338',
                contributor_street_1='1841 VERMACK CT', contributor_employer='RETIRED',
                contributor_occupation='RETIRED', occupation_category='RETIRED',
                _generational_suffix='',
            )
            for _ in range(n)
        )

    # the household as filed, row counts as in the data
    add('STUART', 34)
    add('STUART', 1, city='ATLANTA')
    add('SARA', 25)
    add('STUART SARA', 6)
    add('SARA STUART', 1)
    add('STUARTANDSARA', 3)
    return pd.DataFrame(rows)


def test_joint_rows_get_their_own_donor_not_a_spouses():
    """Fails on the matcher of 2026-09-23 HEAD without a joint guard or 'separate'
    rows: the joint rows join Stuart's and Sara's keys (then donor_canonical_names
    stamps the partner's name on every solo filing). Passes when donor matching
    keeps a joint filing apart, by the joint-filing guard or by separate rules."""
    df = _household()
    rid_to_key, _ = match_donors(df, verbose=False)
    df = apply_donor_key(df, rid_to_key)
    merge_split_name_donors(df)
    keys = df.groupby('contributor_name')['donor_key'].agg(set)

    stuart, sara = keys['MEYERS, STUART'], keys['MEYERS, SARA']
    assert len(stuart) == 1 and len(sara) == 1 and stuart != sara
    for joint in JOINT:
        assert not keys[joint] & (stuart | sara), joint
    names_by_key = df.groupby('donor_key')['contributor_name'].agg(set)
    assert names_by_key[next(iter(stuart))] == {'MEYERS, STUART'}
    assert names_by_key[next(iter(sara))] == {'MEYERS, SARA'}
