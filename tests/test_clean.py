import pandas as pd
import pytest
import clean as cli
from fec.cleaning.donors.clean import identify_donors
from fec.cleaning.entities import source_entity_type
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.addresses.streets import clean_streets
from fec.cleaning.addresses import review as address_review
from fec.donor_match.keys import apply_curated_key_merges, merge_split_name_donors
from fec.donor_match.matcher import _validate_merge_audit, build_profiles


def test_new_occupations_keeps_unreviewed_filings_with_the_same_title(tmp_path, monkeypatch):
    df = pd.DataFrame({
        "sub_id": ["101", "102"], "entity_type": ["INDIVIDUAL"] * 2,
        "contributor_occupation": ["UNLISTED ROLE"] * 2, "occupation_category": ["MEDICAL / HEALTHCARE", "OTHER"],
    })
    monkeypatch.setattr(cli, "reviewed_occupation_categories", lambda: {"101": "MEDICAL / HEALTHCARE"})
    cli._write_new_occupations(df, tmp_path)
    report = pd.read_csv(tmp_path / "new_occupations.csv")
    assert report.to_dict("records") == [
        {"contributor_occupation": "UNLISTED ROLE", "occupation_category": "OTHER", "rows": 1}
    ]


def test_address_review_keeps_documented_pending_cases_and_new_forms(monkeypatch):
    rules = [
        dict(donor_key="a", address_forms="C/O FIRM", decision="pending", note="No street is filed", source="filing"),
        dict(donor_key="b", address_forms="A || B", decision="keep both as filed"),
    ]
    monkeypatch.setattr(address_review, "read_rows", lambda path: rules)
    cases = pd.DataFrame([
        {"donor_key": key, "address_forms": forms, "decision": "pending", "decision_basis": "", "evidence": ""}
        for key, forms in [("a", "C/O FIRM"), ("b", "B || A"), ("b", "A || C")]
    ])
    remaining = address_review._open_cases(cases)
    assert remaining.index.tolist() == [0, 2]
    assert remaining.loc[0, ["decision", "decision_basis", "evidence"]].tolist() == ["pending", "No street is filed", "filing"]
    assert remaining.at[2, "evidence"] == ""


def test_failed_quality_gates_keep_the_previous_cleaned_file(tmp_path, monkeypatch):
    output = tmp_path / 'contributions_cleaned.csv'
    output.write_text('previous good file\n', encoding='utf-8')
    cleaned = pd.DataFrame({
        'sub_id': ['1'], 'recipient_committee': [None], 'contributor_zip': ['60601'],
        'contributor_city': ['CHICAGO'], 'contributor_state': ['IL'],
        'contributor_street_1': ['1 MAIN ST'], 'contributor_street_2': [''],
    })
    monkeypatch.setattr(cli, 'CLEANED_CSV', output)
    monkeypatch.setattr(cli, 'read_current_receipts', lambda path: cleaned.copy())
    monkeypatch.setattr(cli, 'REPORTS_DIR', tmp_path)
    monkeypatch.setattr(cli, 'clean_pipeline', lambda df, **_dirs: df)
    monkeypatch.setattr(cli, 'run_quality_gates', lambda df: {
        'passed': False, 'checks': {}, 'issues': ['1 rows with a missing amount']})

    with pytest.raises(SystemExit):
        cli.main()

    assert output.read_text(encoding='utf-8') == 'previous good file\n'
    assert (tmp_path / 'quality_gates.json').exists()

    baseline = pd.DataFrame({
        'sub_id': ['1', '2', '3', '4'],
        'donor_key': ['first', 'first', 'second', 'second'],
        'contribution_receipt_amount': [10.0, 20.0, 30.0, 40.0],
        'contributor_city': ['NEW YORK', 'NEW YORK', 'CHICAGO', 'CHICAGO'],
    })
    renamed = baseline.assign(donor_key=['new-first', 'new-first', 'new-second', 'new-second'])
    cli._check_reclean(baseline, renamed.iloc[::-1])

    wrong_amounts = renamed.copy()
    wrong_amounts['contribution_receipt_amount'] = [11.0, 19.0, 30.0, 40.0]
    invalid = [
        wrong_amounts,
        renamed.iloc[:-1],
        pd.concat([renamed, renamed.iloc[:1]], ignore_index=True),
        renamed.assign(donor_key=['merged'] * 4),
        renamed.assign(donor_key=['split-one', 'split-two', 'new-second', 'new-second']),
        renamed.assign(contributor_city=['NEW YROK', 'NEW YORK', 'CHICAGO', 'CHICAGO']),
    ]
    monkeypatch.setattr(cli, 'read_current_receipts', lambda _path: baseline.copy())
    monkeypatch.setattr(cli, '_assert_known_committees', lambda _df: None)
    monkeypatch.setattr(cli, '_ensure_zip_format', lambda _df: None)
    for candidate in invalid:
        monkeypatch.setattr(cli, 'clean_pipeline',
                            lambda _df, result=candidate, **_dirs: result.copy())
        with pytest.raises(ValueError):
            cli.main(expected=baseline)
        assert output.read_text(encoding='utf-8') == 'previous good file\n'


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


@pytest.mark.parametrize(('signals', 'allowed'), [
    ('cross_name(+10); employer=ACME(+30)', True),
    ('surname_variant; zip5=92101(+25); employer=ACME(+30)', True),
    ('name_format_variant; zip5=92101(+25)', True),
    ('surname_superset; street(+60)', True),
    ('cross_name(+10); state=CA(+10)', False),
    ('surname_variant; zip5=92101(+25)', False),
    ('name_format_variant; employer=ACME(+30)', False),
    ('surname_superset; zip5=92101(+25); employer=ACME(+30)', False),
    ('street(+60); HARD_BLOCK=different middle names', False),
])
def test_merge_audit_checks_identity_evidence(signals, allowed):
    keys = {'a': 'donor', 'b': 'donor'}
    row = {'rid_a': 'a', 'rid_b': 'b', 'merged': True, 'signals': signals}
    if allowed:
        _validate_merge_audit(keys, [row])
    else:
        with pytest.raises(ValueError, match='1 unsafe merge'):
            _validate_merge_audit(keys, [row])


def test_merge_audit_checks_only_merges_kept_in_the_final_group():
    blocked = {'rid_a': 'a', 'rid_b': 'b', 'signals': 'HARD_BLOCK'}
    _validate_merge_audit({'a': 'first', 'b': 'second'}, [{**blocked, 'merged': True}])
    _validate_merge_audit({'a': 'same', 'b': 'same'}, [{**blocked, 'merged': False}])


def test_ejecting_a_weak_profile_preserves_the_other_donor_in_any_row_order():
    def filing(sub_id, city, zip5, street, employer):
        return _row(sub_id, 'MARLEY, SAM', 'SAM', 'MARLEY', city, 'CA',
                    zip5, street, employer)

    # Anaheim matches Brea by street, but lacks a link to the repeated Carlsbad profile.
    rows = [
        filing('1', 'ANAHEIM', '92801', '10 MAIN ST', 'OLD LAW'),
        filing('2', 'BREA', '92821', '10 MAIN ST', 'NEW LAW'),
        filing('3', 'CARLSBAD', '92010', '20 OCEAN ST', 'NEW LAW'),
        filing('4', 'DEL MAR', '92014', '20 OCEAN ST', 'NEW LAW'),
        filing('5', 'CARLSBAD', '92010', '20 OCEAN ST', 'NEW LAW'),
    ]
    keys = _keys(_identify(rows))
    assert keys['1'] != keys['2']
    assert keys['2'] == keys['3'] == keys['4'] == keys['5']
    assert _keys(_identify([rows[1], rows[0], *rows[2:]])) == keys
    assert _keys(_identify(rows[::-1])) == keys


@pytest.mark.parametrize(('zips', 'expected'), [
    (['', '', '92101', '92102', '92102'], '92102'),
    (['92102', '92101'], '92101'),
])
def test_profile_uses_most_frequent_filed_zip_with_stable_ties(zips, expected):
    rows = [_row(str(i), 'MARLEY, SAM', 'SAM', 'MARLEY', 'SAN DIEGO', 'CA', zip5, '10 MAIN ST')
            for i, zip5 in enumerate(zips)]
    for order in (rows, rows[::-1]):
        profile, = build_profiles(pd.DataFrame(order)).values()
        assert profile['zip5'] == expected


def test_tied_name_merge_keeps_the_same_existing_key_in_either_order():
    rows = [
        {**OLD_ROWS[0], 'donor_key': 'key-b'},
        {**OLD_ROWS[1], 'donor_key': 'key-a'},
    ]
    for order in (rows, rows[::-1]):
        filings = pd.DataFrame(order)
        assert merge_split_name_donors(filings) == 1
        assert _keys(filings) == {'101': 'key-a', '102': 'key-a'}


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


def test_apply_synonyms_rewrites_the_audit_examples():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 6,
        'contributor_employer': ['SPGI', 'NMRK', 'JPMC', 'C21 STORES', 'ABINITIO SOFTWARE', 'SOFTERWARE'],
    })
    apply_reviewed_employer_names(df, ("contributor_employer",))
    assert df['contributor_employer'].tolist() == [
        'S&P GLOBAL', 'NMRK', 'JPMC', 'CENTURY 21 STORES',
        'AB INITIO SOFTWARE', 'SOFTERWARE',
    ]


def _streets(rows):
    return pd.DataFrame({
        'contributor_street_1': [row[0] for row in rows],
        'contributor_street_2': [row[1] for row in rows],
        'contributor_first_name': ['MURIEL'] * len(rows),
        'contributor_last_name': ['SELIGSON'] * len(rows),
    })


def test_clean_streets_nulls_the_audit_placeholders():
    df = clean_streets(_streets([
        ('320 FIRST STREET SE', '.'),
        ('84 BIGELOW RD', 'NONE'),
        ('5918 TYNDALL AVE', 'HOME'),
        ('10 MAIN ST', 'APT 4'),
    ]))
    assert df['contributor_street_2'].isna().tolist() == [True, True, True, False]
    assert df['contributor_street_2'].iloc[3] == 'APT 4'


def test_keys_stay_with_their_filings_across_runs():
    from fec.donor_match.keys import keep_earlier_keys
    df = pd.DataFrame({
        'sub_id': ['1', '2', '3', '4', '5', '6'],
        'entity_type': ['INDIVIDUAL'] * 6,
        'donor_key': ['new-a', 'new-a', 'new-a', 'new-b', 'new-b', 'new-c'],
    })
    # 1-3 were one donor, 4-5 another; 6 is new; the old donor split from 3.
    lost = keep_earlier_keys(df, {'1': 'old-a', '2': 'old-a', '3': 'old-x', '4': 'old-b', '5': 'old-a'})
    assert df['donor_key'].tolist() == ['old-a', 'old-a', 'old-a', 'old-b', 'old-b', 'new-c']
    assert lost == ['old-x']


def test_reviewed_source_code_exception_is_specific_to_the_filing_and_conflict():
    filings = pd.DataFrame({
        'sub_id': ['4062420241962022358', '999', '4062420241962022358'],
        'contributor_name': ['SUNPAC'] * 3,
        'fec_entity_type': ['IND', 'IND', 'PAC'],
    })
    assert source_entity_type(filings).tolist() == ['', 'INDIVIDUAL', 'COMMITTEE/PAC']
    assert filings['fec_entity_type'].tolist() == ['IND', 'IND', 'PAC']


def test_reviewed_alias_only_changes_the_listed_filing():
    frame = pd.DataFrame({
        'sub_id': ['4110120231808001283', 'another-filing', 'unrelated'],
        'donor_key': ['58f2f2117f31', '58f2f2117f31', 'other-donor'],
    })
    assert apply_curated_key_merges(frame) == 1
    assert frame['donor_key'].tolist() == ['57623038bd1a', '58f2f2117f31', 'other-donor']
