import pandas as pd
import pytest
import clean as cli
from fec.cleaning.donors.clean import identify_donors
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.addresses.streets import clean_streets


def test_failed_quality_gates_keep_the_previous_cleaned_file(tmp_path, monkeypatch):
    output = tmp_path / 'contributions_cleaned.csv'
    output.write_text('previous good file\n', encoding='utf-8')
    cleaned = pd.DataFrame({
        'sub_id': ['1'], 'recipient_committee': [None], 'contributor_zip': ['60601'],
        'contributor_city': ['CHICAGO'], 'contributor_state': ['IL'],
        'contributor_street_1': ['1 MAIN ST'], 'contributor_street_2': [''],
    })
    monkeypatch.setattr(cli, 'CLEANED_CSV', output)
    monkeypatch.setattr(cli, 'read_pipeline_csv', lambda path: cleaned.copy())
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
    monkeypatch.setattr(cli, 'read_pipeline_csv', lambda _path: baseline.copy())
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


def test_own_name_employer_handles_missing_name_parts():
    from fec.cleaning.rules.name_fields import _fix_own_name_as_employer
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_first_name': [None, 'JANE'],
        'contributor_last_name': ['DOE', 'DOE'],
        'contributor_employer': ['ACME CORP', 'JANE DOE'],
    })

    assert _fix_own_name_as_employer(df) == 1
    assert df['contributor_employer'].tolist() == ['ACME CORP', 'SELF-EMPLOYED']


def test_swap_job_and_company_moves_only_companies():
    from fec.cleaning.work.clean import swap_job_and_company
    df = pd.DataFrame({
        'is_individual': [True, True, True, True, True],
        'contributor_employer': ['ATTORNEY', 'DESIGNER', 'CONSULTANT', 'ATTORNEY', 'WOLF RIFKIN'],
        'contributor_occupation': [
            'WOLF, RIFKIN', 'SELF-EMPLOYED', 'PROJECT MANAGEMENT CONSULTANT', 'ATTORNEY', 'ATTORNEY',
        ],
    })

    _, swapped = swap_job_and_company(df)

    assert swapped == 2
    assert df['contributor_employer'].tolist()[:4] == [
        'WOLF, RIFKIN', 'SELF-EMPLOYED', 'CONSULTANT', 'ATTORNEY',
    ]
    assert df['contributor_occupation'].tolist()[:4] == [
        'ATTORNEY', 'DESIGNER', 'PROJECT MANAGEMENT CONSULTANT', 'ATTORNEY',
    ]


def test_job_in_employer_never_guesses_self_employed():
    from fec.cleaning.rules.employer_swaps import settle_job_in_employer
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 6,
        'contributor_name': ['A, B', 'C, D', 'E, F', 'G, H', 'I, J', 'K, L'],
        'contributor_employer': ['ATTORNEY', 'OWNER', 'ATTORNEY', 'CONSULTANT', 'CARDIOLOGIST', 'LAWYER'],
        'contributor_occupation': ['', '', 'RETIRED', 'INVESTOR', 'CARDIOLOGY PC', 'OWNER'],
        'occupation_category': [None] * 6,
    })

    settle_job_in_employer(df)

    assert df['contributor_employer'].fillna('').tolist() == [
        '', 'SELF-EMPLOYED', 'RETIRED', '', 'CARDIOLOGY PC', 'SELF-EMPLOYED',
    ]
    assert df['contributor_occupation'].tolist() == [
        'ATTORNEY', 'OWNER', 'ATTORNEY', 'INVESTOR', 'CARDIOLOGIST', 'OWNER',
    ]


def test_placeholder_employers_are_one_list():
    from fec.cleaning.rules.junk import clear_placeholder_employer_words, placeholder_employer
    values = pd.Series(['43209876', 'A@B.COM', 'XXX', 'NM', 'GE', 'N A', 'PRIVATE', 'ACME LLC'])
    assert placeholder_employer(values, words=False).tolist() == [
        True, True, True, True, False, False, False, False,
    ]
    assert placeholder_employer(values).tolist() == [
        True, True, True, True, False, True, True, False,
    ]

    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': ['NOTYOURBUSINESS', 'VOLUNTEER', 'ACME LLC'],
        'contributor_occupation': ['NOTYOURBUSINESS', 'VOLUNTEER', 'ENGINEER'],
        'occupation_category': ['OTHER', 'NONPROFIT / PHILANTHROPY', 'ENGINEERING'],
    })
    assert clear_placeholder_employer_words(df) == 2
    assert df['contributor_employer'].isna().tolist() == [True, True, False]
    assert df['contributor_occupation'].fillna('').tolist() == ['', 'VOLUNTEER', 'ENGINEER']


def test_company_in_occupation_keeps_the_role():
    from fec.cleaning.rules.occupation import _null_junk_occupation
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 5,
        'contributor_employer': ['', 'BOXER PROPERTY CORP', '', 'ACME LLC', 'SELF-EMPLOYED'],
        'contributor_occupation': [
            'PRESIDENT, RELAY HUMAN CLOUD, INC', 'CEO AT ACME LLC',
            'MD ANDERSON CANCER CENTER INC', 'PRESIDENT OF HEALTH TECH COMPANY', 'COMPANY',
        ],
        'occupation_category': [None] * 5,
    })

    _null_junk_occupation(df)

    assert df['contributor_employer'].tolist() == [
        'RELAY HUMAN CLOUD, INC', 'BOXER PROPERTY CORP', 'MD ANDERSON CANCER CENTER INC',
        'ACME LLC', 'SELF-EMPLOYED',
    ]
    assert df['contributor_occupation'].fillna('').tolist() == [
        'PRESIDENT', 'CEO', '', 'PRESIDENT OF HEALTH TECH COMPANY', '',
    ]


def test_company_evidence_levels():
    from fec.cleaning.work.company import company_evidence
    values = pd.Series([
        'ACME, L.L.C.', 'KIRKLAND & ELLIS LLP', 'BROWN & BROWN', 'REAL ESTATE',
        'INVESTMENT PORTFOLIO MANAGER', 'VENTURE CAPITALIST', 'CHAIRMAN AND CEO',
        'DIRECTOR OF POLICY', 'CRESCENT REALTY ADVISORS', 'ZORVAL CAPITAL',
        'ZORVAL & TARQUIN', 'WEALTH MANAGEMENT', 'MEDSTAR HEALTH', 'MATTEL', '',
    ])
    assert company_evidence(values).tolist() == [
        'legal_form', 'legal_form', 'reviewed', 'job',
        'job', 'job', 'job',
        'job', 'named_org', 'named_org',
        'named_org', 'job', 'named_org', 'unknown', '',
    ]

    # Other donors filing the value as their employer lifts an unknown name.
    employers = pd.Series(['MATTEL', 'MATTEL', 'MATTEL', 'REAL ESTATE MANAGEMENT', 'REAL ESTATE MANAGEMENT', 'ME'])
    filers = pd.Series(['A', 'B', 'C', 'D', 'E', 'F'])
    levels = company_evidence(pd.Series(['MATTEL', 'REAL ESTATE MANAGEMENT', 'ME']), employers=employers, filers=filers, min_filers=2)
    assert levels.tolist() == ['frequent', 'job', 'unknown']


def test_entity_type_from_committee_signal_and_company_evidence():
    from fec.cleaning.entities import _reclassify_entities
    names = [
        'BANK, ELLYN', 'KAHAN TRUST, DAVID', 'BRITT FOR ALABAMA INC, KATIE MS.',
        'SUNPAC, SUNPAC', 'AMALGAMATED BANK', 'KRAFT GROUP LLC',
        'CLEVELAND BUILDING AND CONSTRUCTION TRADES COUNCIL', 'SMITH, JOHN',
    ]
    df = pd.DataFrame({
        'sub_id': [str(i) for i in range(len(names))],
        'contributor_name': names,
        'contributor_first_name': ['ELLYN', 'DAVID', 'KATIE', '', '', '', '', 'JOHN'],
        'contributor_last_name': ['BANK', 'KAHAN TRUST', 'BRITT FOR ALABAMA INC', '', '', '', '', 'SMITH'],
        'contributor_employer': ['ACME LLC', '', '', '', '', '', '', 'ACME LLC'],
        'contributor_occupation': ['ANALYST', '', '', '', '', '', '', 'ANALYST'],
        'occupation_category': [None] * len(names),
        'is_individual': [False, False, False, True, False, True, False, False],
        'committee_type': [None] * len(names),
    })

    _reclassify_entities(df)

    assert df['entity_type'].tolist() == [
        'INDIVIDUAL', 'ORGANIZATION', 'COMMITTEE/PAC',
        'COMMITTEE/PAC', 'ORGANIZATION', 'ORGANIZATION',
        'ORGANIZATION', 'INDIVIDUAL',
    ]


def test_split_retired_reads_every_spelling():
    from fec.cleaning.work.retired import split_retired
    assert split_retired('RETIRED') == (True, '')
    assert split_retired('TETIRED') == (True, '')
    assert split_retired('SEMI-RETIRED') == (True, '')
    assert split_retired('RETIRED FROM DAIICHI SANKYO') == (True, 'DAIICHI SANKYO')
    assert split_retired('RETIRED - ORACLE') == (True, 'ORACLE')
    assert split_retired('UCLA RETIRED') == (True, 'UCLA')
    assert split_retired('PHYSICIAN, RETIRED') == (True, 'PHYSICIAN')
    assert split_retired('RETIRED ATTORNEY') == (True, 'ATTORNEY')
    assert split_retired("I'M RETIRED") == (True, "I'M")
    assert split_retired('OFFICE MANAGER, RETIRED NURSE') == (True, 'OFFICE MANAGER, NURSE')
    assert split_retired('RETIREDPHYSICIAN') == (True, 'PHYSICIAN')
    assert split_retired('VOLUNTEER (RETIRED ATTORNEY)') == (True, 'VOLUNTEER (ATTORNEY)')
    assert split_retired('RETIREMENT PLANNER') == (False, 'RETIREMENT PLANNER')
    assert split_retired('BOARD OF RETIRED') == (False, 'BOARD OF RETIRED')
    assert split_retired('ACME LLC') == (False, 'ACME LLC')


def test_settle_retired_fields_keeps_job_and_company():
    from fec.cleaning.work.retired import settle_retired_fields
    df = pd.DataFrame({
        'is_individual': [True] * 6,
        'contributor_employer': ['RETIRED FROM DAIICHI SANKYO', 'RETIRE', 'RETIRED TEACHER', "I'M RETIRED", 'RETIRED', 'ACME LLC'],
        'contributor_occupation': ['RETIRED', 'SOCIAL WORKER', 'RETIRED', 'RETIRED.', 'RETIRED ATTORNEY', 'ENGINEER'],
        'occupation_category': ['RETIRED', 'OTHER', 'RETIRED', 'RETIRED', 'RETIRED', 'TECHNOLOGY'],
    })

    assert settle_retired_fields(df)[1] == 5
    assert df['contributor_employer'].tolist() == ['RETIRED'] * 5 + ['ACME LLC']
    assert df['contributor_occupation'].tolist() == ['RETIRED', 'SOCIAL WORKER', 'TEACHER', 'RETIRED', 'ATTORNEY', 'ENGINEER']
    assert df['previous_employer'].fillna('').tolist() == ['DAIICHI SANKYO', '', '', '', '', '']


def test_split_self_marker_and_previous_employer():
    from fec.cleaning.work.previous import normalize_previous_employer_value, split_self_marker
    assert split_self_marker('SELF - ACME LLC') == (True, 'ACME LLC')
    assert split_self_marker('SELF; JACK A FRYDRYCH APLC') == (True, 'JACK A FRYDRYCH APLC')
    assert split_self_marker('SELF EMPLOYED AND TOTAL REALTY') == (True, 'TOTAL REALTY')
    assert split_self_marker('CONSULTANT (SELF-EMPLOYED)') == (True, 'CONSULTANT')
    assert split_self_marker('SELF') == (True, '')
    assert split_self_marker('ACME LLC') == (False, '')
    assert normalize_previous_employer_value('SELF; JACK A FRYDRYCH APLC') == 'JACK A FRYDRYCH APLC'
    assert normalize_previous_employer_value('SELF - ROUND ROCK PERIODONTICS') == 'ROUND ROCK PERIODONTICS'
    assert normalize_previous_employer_value('SELF EMPLOYED; DENTIST') == 'SELF-EMPLOYED'


def test_street_suffixes_abbreviate_only_at_the_end():
    from fec.cleaning.addresses.street_text import normalize_street
    assert normalize_street('100 Wolf Point') == '100 WOLF PT'
    assert normalize_street('313 W WOLF POINT PLAZA 3510') == '313 W WOLF POINT PLZ 3510'
    assert normalize_street('3401 EVERGREEN POINT RD') == '3401 EVERGREEN POINT RD'
    assert normalize_street('5200 TOWN CENTER CIR') == '5200 TOWN CENTER CIR'
    assert normalize_street('1 UNIVERSITY PLAZA STE 400') == '1 UNIVERSITY PLZ STE 400'
    assert normalize_street('21 SANDIA HEIGHTS DR NE') == '21 SANDIA HEIGHTS DR NE'
