"""unify_donors() must give one person one donor_key even when rows were cleaned in different batches."""
import pandas as pd

from fec.cleaning import cli
from fec.cleaning.pipeline import unify_donors
from fec.io import read_pipeline_csv


def _row(sub_id, name, first, last, city, state, zip5, street,
         employer='ACME LLP', occupation='ATTORNEY'):
    return {
        'sub_id': sub_id,
        'transaction_id': f'T{sub_id}',
        'two_year_transaction_period': 2024,
        'recipient_committee': 'AIPAC',
        'entity_type': 'INDIVIDUAL',
        'contributor_name': name,
        'contributor_first_name': first,
        'contributor_last_name': last,
        'contributor_street_1': street,
        'contributor_street_2': None,
        'contributor_city': city,
        'contributor_state': state,
        'state_name': None,
        'contributor_zip': zip5,
        'contributor_employer': employer,
        'contributor_occupation': occupation,
        'occupation_category': 'LEGAL',
        'occupation_status': 'DISCLOSED',
        'committee_type': None,
        'employer_change_type': None,
        'contribution_receipt_date': '2024-03-05',
        'contribution_receipt_amount': 100.0,
    }


def _frame(rows):
    return pd.DataFrame(rows)


# Same donor filed under two cities but same street + ZIP, so the matcher merges the rids.
OLD_ROWS = [
    _row('101', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'ENCINO', 'CA', '91316', '123 MAPLE ST'),
    _row('102', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'LOS ANGELES', 'CA', '91316', '123 MAPLE ST'),
    # single-rid donor, only in the old batch
    _row('103', 'NULL, KAREN', 'KAREN', 'NULL',
         'WESTPORT', 'CT', '06880', '9 ELM RD', employer='YALE'),
]

# A new filing for the multi-rid donor arrives later, at the NON-root city.
NEW_ROWS = [
    _row('201', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'LOS ANGELES', 'CA', '91316', '123 MAPLE ST'),
    # and a new filing for the single-rid donor, identical identity
    _row('202', 'NULL, KAREN', 'KAREN', 'NULL',
         'WESTPORT', 'CT', '06880', '9 ELM RD', employer='YALE'),
]


def _keys_by_sub_id(df):
    return dict(zip(df['sub_id'], df['donor_key']))


def test_multi_rid_donor_gets_one_key_in_combined_run():
    combined = unify_donors(_frame(OLD_ROWS + NEW_ROWS))
    keys = _keys_by_sub_id(combined)
    assert keys['101'] == keys['102'] == keys['201'], (
        "same person (two rid spellings) must share one donor_key when "
        "unify_donors sees all their rows together"
    )


def test_batch_only_matching_would_split_the_donor():
    """The new batch matched alone gets a different key than the full run assigned."""
    full = unify_donors(_frame(OLD_ROWS))
    batch = unify_donors(_frame(NEW_ROWS))
    full_key = _keys_by_sub_id(full)['101']
    batch_key = _keys_by_sub_id(batch)['201']
    assert full_key != batch_key


def test_single_rid_donor_key_stable_across_batches():
    old = unify_donors(_frame(OLD_ROWS))
    combined = unify_donors(_frame(OLD_ROWS + NEW_ROWS))
    assert _keys_by_sub_id(old)['103'] == _keys_by_sub_id(combined)['103']
    assert _keys_by_sub_id(combined)['103'] == _keys_by_sub_id(combined)['202']


def test_null_surname_survives():
    combined = unify_donors(_frame(OLD_ROWS + NEW_ROWS))
    karen = combined[combined['sub_id'] == '103']
    assert karen['contributor_last_name'].iloc[0] == 'NULL'


def _round_trip(tmp_path, old_rows, new_rows):
    """Mirror clean.py's incremental chain: unify, save, read back, merge, unify, restore keys."""
    run1 = unify_donors(_frame(old_rows))
    out = tmp_path / 'out.csv'
    cli._drop_internal_cols(run1).to_csv(out, index=False)
    existing = read_pipeline_csv(out)
    combined = cli._merge_incremental(existing, _frame(new_rows))
    combined = unify_donors(combined)
    cli._restore_prior_donor_keys(combined, existing)
    return run1, combined


def test_donor_key_survives_incremental_round_trip(tmp_path):
    # BOB -> ROBERT canonicalization in the saved output would hash a different cluster root on re-match.
    old = [
        _row('101', 'SHTREMBERG, BOB', 'BOB', 'SHTREMBERG',
             'ENCINO', 'CA', '91316', '123 MAPLE ST'),
        _row('102', 'SHTREMBERG, ROBERT', 'ROBERT', 'SHTREMBERG',
             'ENCINO', 'CA', '91316', '123 MAPLE ST'),
    ]
    new = [
        _row('201', 'SHTREMBERG, ROBERT', 'ROBERT', 'SHTREMBERG',
             'ENCINO', 'CA', '91316', '123 MAPLE ST'),
    ]
    run1, combined = _round_trip(tmp_path, old, new)
    key1 = _keys_by_sub_id(run1)['101']
    keys = _keys_by_sub_id(combined)
    assert keys['101'] == keys['102'] == keys['201'] == key1


def test_incremental_round_trip_preserves_null_surname_and_zip(tmp_path):
    _, combined = _round_trip(tmp_path, OLD_ROWS, NEW_ROWS)
    karen = combined[combined['sub_id'] == '103']
    assert karen['contributor_last_name'].iloc[0] == 'NULL'
    assert karen['contributor_zip'].iloc[0] == '06880'
    keys = _keys_by_sub_id(combined)
    assert keys['103'] == keys['202']
