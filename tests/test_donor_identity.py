"""Donor identity uses the complete dataset."""
import pandas as pd

from fec.cleaning.pipeline import identify_donors, standardize_donors
from fec.cleaning.entity_classification import apply_name_corrections
from fec.donor_match import canonicalize_donor_names


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
        'contributor_zip': zip5,
        'contributor_employer': employer,
        'contributor_occupation': occupation,
        'occupation_category': 'LEGAL',
        'occupation_status': 'DISCLOSED',
        'committee_type': None,
        'contribution_receipt_date': '2024-03-05',
        'contribution_receipt_amount': 100.0,
    }


OLD_ROWS = [
    _row('101', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'ENCINO', 'CA', '91316', '123 MAPLE ST'),
    _row('102', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'LOS ANGELES', 'CA', '91316', '123 MAPLE ST'),
    _row('103', 'NULL, KAREN', 'KAREN', 'NULL',
         'WESTPORT', 'CT', '06880', '9 ELM RD', employer='YALE'),
]

NEW_ROWS = [
    _row('201', 'SHTREMBERG, VICTOR', 'VICTOR', 'SHTREMBERG',
         'LOS ANGELES', 'CA', '91316', '123 MAPLE ST'),
    _row('202', 'NULL, KAREN', 'KAREN', 'NULL',
         'WESTPORT', 'CT', '06880', '9 ELM RD', employer='YALE'),
]


def _identify(rows):
    return identify_donors(pd.DataFrame(rows))


def _keys(df):
    return dict(zip(df['sub_id'], df['donor_key']))


def test_same_donor_gets_one_key():
    keys = _keys(_identify(OLD_ROWS + NEW_ROWS))
    assert keys['101'] == keys['102'] == keys['201']


def test_identity_needs_complete_dataset():
    old_key = _keys(_identify(OLD_ROWS))['101']
    isolated_key = _keys(_identify(NEW_ROWS))['201']
    assert old_key != isolated_key


def test_single_identity_key_stays_stable():
    old_key = _keys(_identify(OLD_ROWS))['103']
    combined = _keys(_identify(OLD_ROWS + NEW_ROWS))
    assert combined['103'] == combined['202'] == old_key


def test_null_surname_survives():
    identified = _identify(OLD_ROWS + NEW_ROWS)
    karen = identified[identified['sub_id'] == '103'].iloc[0]
    assert karen['contributor_last_name'] == 'NULL'
    assert karen['contributor_zip'] == '06880'


def test_canonicalizer_accepts_filtered_indexes():
    rows = pd.DataFrame([
        _row('301', 'DOE, J', 'J', 'DOE', 'BOSTON', 'MA', '02108', '1 MAIN ST'),
        _row('302', 'DOE, JOHN', 'JOHN', 'DOE', 'BOSTON', 'MA', '02108', '1 MAIN ST'),
    ], index=[10, 20])
    rows['donor_key'] = 'same'

    assert canonicalize_donor_names(rows) == 1
    assert rows['contributor_name'].tolist() == ['DOE, JOHN', 'DOE, JOHN']


def test_standardize_donors_resets_filtered_index(tmp_path):
    rows = pd.DataFrame([
        _row('401', 'DOE, JOHN', 'JOHN', 'DOE',
             'BOSTON', 'MA', '02108', '1 MAIN ST'),
    ], index=[10])
    rows['donor_key'] = 'same'
    rows['previous_employer'] = pd.NA

    result = standardize_donors(rows, out_dir=str(tmp_path))

    assert result.index.tolist() == [0]


def test_same_name_retirees_need_real_shared_evidence():
    rows = [
        _row('501', 'PORTER, DAVID', 'DAVID', 'PORTER',
             'PRAIRIE VILLAGE', 'KS', '66208', 'PO BOX 8770',
             employer='RETIRED', occupation='RETIRED'),
        _row('502', 'PORTER, DAVID', 'DAVID', 'PORTER',
             'MARYLAND HEIGHTS', 'MO', '63043', '12459 GLENBUSH DR',
             employer='RETIRED', occupation='RETIRED'),
    ]
    for row in rows:
        row['occupation_category'] = 'RETIRED'

    keys = _keys(_identify(rows))

    assert keys['501'] != keys['502']


def test_occupation_transition_does_not_replace_shared_evidence():
    rows = [
        _row('511', 'JACKSON, ANN', 'ANN', 'JACKSON',
             'ORONO', 'MN', '55356', '900 PARTENWOOD RD',
             employer='SELF-EMPLOYED', occupation='BOARD MEMBER'),
        _row('512', 'JACKSON, ANN', 'ANN', 'JACKSON',
             'ENGLEWOOD', 'FL', '34223', '7410 MANASOTA KEY RD',
             employer='RETIRED', occupation='RETIRED'),
    ]
    rows[0]['occupation_category'] = 'NONPROFIT / PHILANTHROPY'
    rows[1]['occupation_category'] = 'RETIRED'

    keys = _keys(_identify(rows))

    assert keys['511'] != keys['512']


def test_verified_leonard_feinstein_profiles_stay_together():
    rows = [
        _row('601', 'FEINSTEIN, LEONARD', 'LEONARD', 'FEINSTEIN',
             'JERICHO', 'NY', '11753', '2 JERICHO PLZ',
             employer='RETIRED', occupation='RETIRED'),
        _row('602', 'FEINSTEIN, LEONARD', 'LEONARD', 'FEINSTEIN',
             'SPRINGFIELD', 'NJ', '07081', '955 S SPRINGFIELD AVE',
             employer='RETIRED', occupation='RETIRED'),
    ]
    for row in rows:
        row['occupation_category'] = 'RETIRED'

    keys = _keys(_identify(rows))

    assert keys['601'] == keys['602']


def test_joint_spouse_name_stays_separate():
    rows = [
        _row('701', 'BLECHER, LEE', 'LEE', 'BLECHER',
             'ROCKVILLE', 'MD', '20852', '6307 HUNTOVER LN',
             employer='INOVA', occupation='PHYSICIAN'),
        _row('702', 'BLECHER, LEEMIA', 'LEEMIA', 'BLECHER',
             'ROCKVILLE', 'MD', '20852', '6307 HUNTOVER LN',
             employer='INOVA', occupation='PHYSICIAN'),
    ]

    corrected, _ = apply_name_corrections(pd.DataFrame(rows))
    keys = _keys(identify_donors(corrected))

    assert corrected.loc[1, 'contributor_name'] == 'BLECHER, LEEMIA'
    assert keys['701'] != keys['702']
