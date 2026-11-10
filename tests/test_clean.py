import pandas as pd
import pytest
import clean as cli
from fec.cleaning.donors.clean import identify_donors
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names


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
    assert split_retired('RETIREDPHYSICIAN') == (True, 'PHYSICIAN')
    # RETIRED inside one part qualifies that part only: the role before it is current.
    assert split_retired('OFFICE MANAGER, RETIRED NURSE') == (False, 'OFFICE MANAGER')
    assert split_retired('VOLUNTEER (RETIRED ATTORNEY)') == (False, 'VOLUNTEER')
    assert split_retired('ARBITRATOR/RETIRED JUDGE') == (False, 'ARBITRATOR')
    assert split_retired('HOMEMAKER/WIFE OF RETIRED MILITARY MEM') == (False, 'HOMEMAKER')
    assert split_retired('SELF, RETIRED TEACHER') == (True, 'SELF, TEACHER')
    assert split_retired('TEACHER NOW RETIRED CONSULTANT') == (True, 'TEACHER NOW CONSULTANT')
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


def test_settle_retired_fields_keeps_a_current_role_before_a_retired_one():
    from fec.cleaning.work.retired import settle_retired_fields
    df = pd.DataFrame({
        'is_individual': [True] * 3,
        'contributor_employer': ['STEVEN TENNENBAUM', '', 'JAMS'],
        'contributor_occupation': ['OFFICE MANAGER, RETIRED NURSE', 'VOLUNTEER (RETIRED ATTORNEY)', 'RETIRED JUDGE MEDIATOR'],
        'occupation_category': ['MEDICAL / HEALTHCARE', 'LEGAL', 'LEGAL'],
    })

    assert settle_retired_fields(df)[1] == 3
    # JAMS stays here; the donor step moves a retiree's company to previous_employer.
    assert df['contributor_employer'].tolist() == ['STEVEN TENNENBAUM', '', 'JAMS']
    assert df['contributor_occupation'].tolist() == ['OFFICE MANAGER', 'VOLUNTEER', 'JUDGE MEDIATOR']


def test_split_self_marker_and_previous_employer():
    from fec.cleaning.work.self_employed import split_self_marker
    from fec.cleaning.work.read import company_named
    assert split_self_marker('SELF - ACME LLC') == (True, 'ACME LLC')
    assert split_self_marker('SELF; JACK A FRYDRYCH APLC') == (True, 'JACK A FRYDRYCH APLC')
    assert split_self_marker('SELF EMPLOYED AND TOTAL REALTY') == (True, 'TOTAL REALTY')
    assert split_self_marker('CONSULTANT (SELF-EMPLOYED)') == (True, 'CONSULTANT')
    assert split_self_marker('SELF') == (True, '')
    assert split_self_marker('ACME LLC') == (False, '')
    assert company_named('SELF; JACK A FRYDRYCH APLC') == 'JACK A FRYDRYCH APLC'
    assert company_named('SELF - ROUND ROCK PERIODONTICS') == 'ROUND ROCK PERIODONTICS'
    assert company_named('SELF EMPLOYED; DENTIST') == ''          # a trade, no company



def test_address_rule_can_clear_the_street(monkeypatch, tmp_path):
    from fec.cleaning.addresses.fixes import verified
    rules = tmp_path / 'rules.csv'
    rules.write_text(
        'match_street,match_state,match_zip,street,unit,city,state,zip,source,match_unit,match_sub_id\n'
        'CALIFORNIA SUPPLY,CA,90247,[CLEAR],,,,,the street field held a company name,,\n'
        '1150 KANE CONCOURSE,FL,33154,1150 KANE CONCOURSE,,BAY HARBOR ISLANDS,,,county records,,\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(verified, 'ADDRESS_RULES_CSV', rules)
    df = pd.DataFrame({
        'sub_id': ['1', '2'],
        'contributor_street_1': ['CALIFORNIA SUPPLY', '1150 KANE CONCOURSE'],
        'contributor_street_2': [None, 'FL 2'],
        'contributor_city': ['GARDENA', 'SURFSIDE'],
        'contributor_state': ['CA', 'FL'],
        'contributor_zip': ['90247', '33154'],
    })

    assert verified.apply_verified_address_fixes(df, city_only=False) == 2
    assert df['contributor_street_1'].isna().tolist() == [True, False]
    assert df['contributor_city'].tolist() == ['GARDENA', 'BAY HARBOR ISLANDS']
    # The city-only rule also runs in the second pass; the clearing rule does not.
    df.loc[1, 'contributor_city'] = 'SURFSIDE'
    assert verified.apply_verified_address_fixes(df, city_only=True) == 1
    assert df['contributor_city'].tolist() == ['GARDENA', 'BAY HARBOR ISLANDS']


def test_truncated_first_name_matches_with_an_anchor():
    # TIM / TIMOTHY at one street are one donor; without an anchor they stay apart.
    rows = [
        _row('601', 'WULIGER, TIMOTHY', 'TIMOTHY', 'WULIGER', 'SOLON', 'OH', '44139', '7 OAK LN'),
        _row('602', 'WULIGER, TIM', 'TIM', 'WULIGER', 'SOLON', 'OH', '44139', '7 OAK LN'),
        _row('603', 'WULIGER, TIM', 'TIM', 'WULIGER', 'AUSTIN', 'TX', '78701', '90 ELM ST', employer='DELL'),
    ]
    keys = _keys(_identify(rows))
    assert keys['601'] == keys['602'] != keys['603']


def test_short_form_of_two_names_matches_neither():
    # MARI beside MARIANNE and MARISOL names nobody in particular.
    rows = [
        _row('611', 'HALE, MARIANNE', 'MARIANNE', 'HALE', 'SOLON', 'OH', '44139', '7 OAK LN'),
        _row('612', 'HALE, MARISOL', 'MARISOL', 'HALE', 'SOLON', 'OH', '44139', '7 OAK LN'),
        _row('613', 'HALE, MARI', 'MARI', 'HALE', 'SOLON', 'OH', '44139', '7 OAK LN'),
    ]
    keys = _keys(_identify(rows))
    assert len({keys['611'], keys['612'], keys['613']}) == 3


def test_same_words_in_another_order_are_one_name():
    rows = [
        _row('621', 'GAHFI, ALBERT', 'ALBERT', 'GAHFI', 'BAL HARBOUR', 'FL', '33154', '10201 COLLINS AVE'),
        _row('622', 'GAHFI, MENACHEM ALBERT', 'MENACHEM ALBERT', 'GAHFI', 'BAL HARBOUR', 'FL', '33154', '10201 COLLINS AVE'),
        _row('623', 'GAHFI, ALBERT MENACHEM', 'ALBERT MENACHEM', 'GAHFI', 'BAL HARBOUR', 'FL', '33154', '10201 COLLINS AVE'),
        _row('624', 'FARAHI, DAVID-JACQUES', 'DAVID-JACQUES', 'FARAHI', 'DENVER', 'CO', '80206', '373 GARFIELD ST'),
        _row('625', 'FARAHI, DAVID JACQUES', 'DAVID JACQUES', 'FARAHI', 'DENVER', 'CO', '80206', '373 GARFIELD ST'),
    ]
    keys = _keys(_identify(rows))
    assert keys['621'] == keys['622'] == keys['623']
    assert keys['624'] == keys['625']


def test_joint_filings_do_not_join_the_two_people():
    # STUART and SARA each file alone; their joint filings get one key of their own.
    rows = [
        _row('631', 'MEYERS, STUART', 'STUART', 'MEYERS', 'DUNWOODY', 'GA', '30338', '1841 VERMACK CT'),
        _row('632', 'MEYERS, SARA', 'SARA', 'MEYERS', 'DUNWOODY', 'GA', '30338', '1841 VERMACK CT'),
        _row('633', 'MEYERS, STUART SARA', 'STUART SARA', 'MEYERS', 'DUNWOODY', 'GA', '30338', '1841 VERMACK CT'),
        _row('634', 'MEYERS, SARA STUART', 'SARA STUART', 'MEYERS', 'DUNWOODY', 'GA', '30338', '1841 VERMACK CT'),
    ]
    keys = _keys(_identify(rows))
    assert keys['631'] != keys['632']
    assert keys['633'] == keys['634'] not in (keys['631'], keys['632'])


def test_learn_typos_reads_one_slip_from_one_listed_occupation():
    from fec.cleaning.work.typos import learn_typos
    listed = ['ATTORNEY', 'CONSULTANT', 'PHYSICIAN', 'PARTNER', 'ARCHITECT', 'CEO OWNER', 'CO-OWNER', 'RETIRED']
    texts = pd.Series([
        'ATTORNEYY', 'CONSULTATN', 'PHYSICAIN ', 'ARCHITECTS',   # added, transposed, swapped, plural
        'GARTNER',           # one slip from PARTNER: read, which is why only the occupation box is learnt from
        'ATTORNEY',          # listed already
        'CO OWNER',          # CO / CEO: a word under six letters
        'ATTORNEY AT LAW',   # two words more, not a slip
        'XX',                # junk
        'RETIRE',            # a status word: the RETIRED reader owns it
    ])
    assert learn_typos(texts, listed=listed) == {
        'ATTORNEYY': 'ATTORNEY', 'CONSULTATN': 'CONSULTANT', 'PHYSICAIN': 'PHYSICIAN',
        'ARCHITECTS': 'ARCHITECT', 'GARTNER': 'PARTNER',
    }


def test_extend_corrections_adds_learnt_typos_to_the_table():
    from fec.cleaning.work.typos import extend_corrections
    from fec.config.occupation_rules.rules import OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS
    df = pd.DataFrame({'contributor_occupation': ['ORTHODONTISTT', 'ATTORNEY'], 'contributor_employer': ['', '']})
    try:
        assert extend_corrections(df) == 1
        assert OCCUPATION_CORRECTIONS['ORTHODONTISTT'] == 'ORTHODONTIST' and TEXT_CORRECTIONS['ORTHODONTISTT'] == 'ORTHODONTIST'
    finally:
        for table in (OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS):
            table.pop('ORTHODONTISTT', None)


def test_learn_categories_asks_only_for_unlisted_texts_and_remembers(tmp_path, monkeypatch):
    from fec.cleaning.work import categorize
    from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES
    monkeypatch.setattr(categorize, "OCCUPATION_CATEGORY_CACHE_CSV", tmp_path / "cache.csv")
    asked = []

    def fake_model(texts):
        asked.extend(sorted(texts))
        return {t: "SCIENCE / RESEARCH" for t in texts}

    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL', 'COMMITTEE/PAC', 'INDIVIDUAL'],
        'contributor_occupation': ['ATTORNEY', 'QUANTUM TOPOLOGIST', 'QUANTUM TOPOLOGIST', ''],
        'occupation_category': ['LEGAL', 'OTHER', 'POLITICAL COMMITTEE', None],
    })
    try:
        assert categorize.learn_categories(df, ask=fake_model) == 1
        assert asked == ['QUANTUM TOPOLOGIST']           # ATTORNEY is listed, the committee is not a person
        assert df['occupation_category'].fillna('').tolist() == ['LEGAL', 'SCIENCE / RESEARCH', 'POLITICAL COMMITTEE', '']
        assert CATEGORY_OVERRIDES['QUANTUM TOPOLOGIST'] == 'SCIENCE / RESEARCH'
        assert (tmp_path / "cache.csv").read_text().splitlines() == [
            'occupation,category,model', f'QUANTUM TOPOLOGIST,SCIENCE / RESEARCH,{categorize.DEFAULT_MODEL}',
        ]
        assert categorize.learn_categories(df, ask=fake_model) == 0      # remembered: not asked again
    finally:
        CATEGORY_OVERRIDES.pop('QUANTUM TOPOLOGIST', None)


def test_classify_without_a_key_leaves_texts_to_the_patterns(monkeypatch):
    from fec.cleaning.work.categorize import classify
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert classify(['QUANTUM TOPOLOGIST']) == {}


def test_reviewed_category_overrides_win_over_the_model():
    import csv
    from fec.env import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_OVERRIDES_CSV
    from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES
    from fec.config.occupation_rules.categories import VALID_CATEGORIES
    cache = {r['occupation']: r['category'] for r in csv.DictReader(open(OCCUPATION_CATEGORY_CACHE_CSV))}
    overrides = {r['occupation']: r['category'] for r in csv.DictReader(open(OCCUPATION_CATEGORY_OVERRIDES_CSV))}
    assert set(cache.values()) <= VALID_CATEGORIES and set(overrides.values()) <= VALID_CATEGORIES
    assert all(CATEGORY_OVERRIDES[k] == v for k, v in overrides.items())
    assert all(CATEGORY_OVERRIDES[k] == v for k, v in cache.items() if k not in overrides)


def test_status_word_reads_a_slip_and_leaves_other_words():
    from fec.cleaning.work.status_words import learn_status_typos, status_word
    assert status_word('RETIERD') == 'RETIRED'
    assert status_word('RETRIED') == 'RETIRED'                 # transposition
    assert status_word('SE;F EMPLOYED') == 'SELF-EMPLOYED'     # a wrong key
    assert status_word('SELF-EMPLO') == 'SELF-EMPLOYED'        # truncated
    assert status_word('NOT EMOLOYED') == 'NOT EMPLOYED'
    assert status_word('HOUSE WIFE') == 'HOUSEWIFE'
    assert status_word('RETIRED') == 'RETIRED'                 # itself
    for word in ('PRESIDENT', 'RESIDENT', 'RETAILER', 'HOMEOWNER', 'STUDIO', 'RET', 'SELF', 'RETIREMENT PLANNER'):
        assert status_word(word) is None, word
    assert learn_status_typos(['Self Employeed', 'UN EMPLOYED', 'ACME LLC', 'RETIRED'], canonical={'UNEMPLOYED': 'NOT EMPLOYED'}) == {
        'SELF EMPLOYEED': 'SELF-EMPLOYED', 'UN EMPLOYED': 'NOT EMPLOYED',
    }


def test_standard_address_follows_usps_with_no_table_of_ours():
    from fec.cleaning.addresses.standardize import standard_address, standardize_addresses

    def std(street_1, street_2='', city='SAINT LOUIS', zipcode='63141'):
        return standard_address(street_1, street_2, city, 'MO', zipcode)[:3]

    assert std('50 EAST 79TH STREET, 16D') == ('50 E 79TH ST', '# 16D', 'SAINT LOUIS')
    assert std('123 Town Square Place #515'.upper()) == ('123 TOWN SQUARE PL', '# 515', 'SAINT LOUIS')   # SQUARE is the name
    assert std('750 SOUTH ST') == ('750 SOUTH ST', '', 'SAINT LOUIS')                      # SOUTH is the name
    assert std('374 N COAST HIGHWAY 101 SUITE 2') == ('374 N COAST HWY 101', 'STE 2', 'SAINT LOUIS')
    assert std('9 W 57 ST', 'PMB #333') == ('9 W 57TH ST', 'PMB 333', 'SAINT LOUIS')
    assert std('123 MAIN ST', '5TH FLOOR') == ('123 MAIN ST', 'FL 5', 'SAINT LOUIS')
    assert std('P.O. BOX 68033') == ('PO BOX 68033', '', 'SAINT LOUIS')
    assert std('C/O RED CURVE SOLUTIONS LLC', '138 CONANT ST STE 401') == ('138 CONANT ST', 'STE 401', 'SAINT LOUIS')
    assert std('SOMEONE@GMAIL.COM')[0] == ''
    assert std('1 MAIN ST', '# 539')[1] == '# 539'                                       # a unit is never a place
    assert std('1 MAIN ST', 'SAIN MO')[1] == ''                                          # the city and state cut short
    assert std('22649 SHAKER BLVD SAINT LOUIS MO 631')[0] == '22649 SHAKER BLVD'         # the place typed after the street
    assert std('103 STANTON AVE, 103 STANTON AVENU')[0] == '103 STANTON AVE'             # the street typed twice
    assert std('UNIT 8606 7485 VICTORY LANE')[:2] == ('7485 VICTORY LN', 'UNIT 8606')
    assert std('13764RIVOLI DRIVE')[0] == '13764 RIVOLI DR'
    assert std('BOX 137')[0] == 'PO BOX 137'
    assert std('1 MAIN ST', city='ST LOUIS')[2] == 'SAINT LOUIS'
    assert std('1 MAIN ST', city='ST LOUS')[2] == 'SAINT LOUIS'            # one slip, read through the ZIP
    assert std('1 MAIN ST', city='CREVE COEUR')[2] == 'CREVE COEUR'        # a USPS name of the ZIP stays
    assert std('1 MAIN ST', city='SPRINGFIELD')[2] == 'SPRINGFIELD'        # another city is not rewritten

    df = pd.DataFrame({
        'sub_id': ['1', '2'],
        'contributor_street_1': ['123 East 13th Street Apt 4', '10 Downing St'],
        'contributor_street_2': ['', ''],
        'contributor_city': ['Anniston', 'London'],
        'contributor_state': ['AL', 'ZZ'],
        'contributor_zip': ['362011234', 'SW1A 2AA'],
    })
    assert standardize_addresses(df) == 1
    assert df.loc[0, ['contributor_street_1', 'contributor_street_2']].tolist() == ['123 E 13TH ST', 'APT 4']
    assert df.loc[1, 'contributor_street_1'] == '10 Downing St'           # foreign: as filed
    assert standardize_addresses(df) == 0                                 # the standard form is stable


def _read(rows):
    """(employer, occupation) as read for filings given as (name, filed employer, filed occupation)."""
    from fec.cleaning.work.read import read_work
    df = pd.DataFrame(rows, columns=['contributor_name', 'filed_employer', 'filed_occupation'])
    df['entity_type'] = 'INDIVIDUAL'
    df['contributor_last_name'] = df['contributor_name'].str.split(', ').str[0]
    df['contributor_first_name'] = df['contributor_name'].str.split(', ').str[1]
    df['contributor_employer'] = df['contributor_occupation'] = df['occupation_category'] = None
    read_work(df, df['entity_type'].eq('INDIVIDUAL'), df['filed_employer'], df['filed_occupation'])
    return list(zip(df['contributor_employer'].fillna(''), df['contributor_occupation'].fillna('')))


def test_work_fields_are_read_from_the_filed_pair():
    cases = {
        # each field on its own
        ('A, B', 'SELF', 'ATTORNEY'): ('SELF-EMPLOYED', 'ATTORNEY'),
        ('A, B', 'ACME LLC', 'ATTY'): ('ACME LLC', 'ATTORNEY'),
        ('A, B', 'ACME LLC', 'XXX'): ('ACME LLC', ''),
        ('A, B', '', 'RETIRED TEACHER'): ('RETIRED', 'TEACHER'),
        ('A, B', 'UCLA RETIRED', 'PROFESSOR'): ('RETIRED', 'PROFESSOR'),
        # a status in one box fills the other
        ('A, B', 'HOUSEWIFE', ''): ('NOT EMPLOYED', 'HOMEMAKER'),
        ('A, B', 'NONE', 'HOMEMAKER'): ('NOT EMPLOYED', 'HOMEMAKER'),
        ('A, B', 'SELF EMPLOYEED', ''): ('SELF-EMPLOYED', 'SELF-EMPLOYED'),
        # an employer that names none
        ('A, B', 'NOTYOURBUSINESS', 'NOTYOURBUSINESS'): ('', ''),
        ('A, B', 'VOLUNTEER', 'VOLUNTEER'): ('', 'VOLUNTEER'),
        ('A, B', '43209876', 'LIBRARIAN'): ('', 'LIBRARIAN'),
        ('A, B', 'A@B.COM', 'ENGINEER'): ('', 'ENGINEER'),
        ('A, B', 'GE', 'ENGINEER'): ('GE', 'ENGINEER'),
        # a job in the employer box: swapped with a company, never a guessed SELF-EMPLOYED
        ('A, B', 'ATTORNEY', 'WOLF, RIFKIN'): ('WOLF RIFKIN', 'ATTORNEY'),
        ('A, B', 'CARDIOLOGIST', 'CARDIOLOGY PC'): ('CARDIOLOGY PC', 'CARDIOLOGIST'),
        ('A, B', 'DESIGNER', 'SELF-EMPLOYED'): ('SELF-EMPLOYED', 'DESIGNER'),
        ('A, B', 'ATTORNEY', ''): ('', 'ATTORNEY'),
        ('A, B', 'OWNER', ''): ('SELF-EMPLOYED', 'OWNER'),
        ('A, B', 'ATTORNEY', 'RETIRED'): ('RETIRED', 'ATTORNEY'),
        ('A, B', 'CONSULTANT', 'INVESTOR'): ('', 'INVESTOR'),
        ('A, B', 'CONSULTANT', 'PROJECT MANAGEMENT CONSULTANT'): ('', 'PROJECT MANAGEMENT CONSULTANT'),
        ('A, B', 'ATTORNEY', 'ATTORNEY'): ('', 'ATTORNEY'),
        # a company in the occupation box: the role stays, the company moves to an open employer
        ('A, B', '', 'PRESIDENT, RELAY HUMAN CLOUD, INC'): ('RELAY HUMAN CLOUD INC', 'PRESIDENT'),
        ('A, B', 'BOXER PROPERTY CORP', 'CEO AT ACME LLC'): ('BOXER PROPERTY CORP', 'CEO'),
        ('A, B', '', 'MD ANDERSON CANCER CENTER INC'): ('MD ANDERSON CANCER CENTER INC', ''),
        ('A, B', 'ACME LLC', 'PRESIDENT OF HEALTH TECH COMPANY'): ('ACME LLC', 'PRESIDENT OF HEALTH TECH COMPANY'),
        ('A, B', 'SELF-EMPLOYED', 'COMPANY'): ('SELF-EMPLOYED', 'SELF-EMPLOYED'),
        # the donor's own name is no employer, unless it carries a legal form
        ('DOE, JANE', 'JANE DOE', 'WRITER'): ('SELF-EMPLOYED', 'WRITER'),
        ('DOE, JANE', 'JANE DOE LLC', 'WRITER'): ('JANE DOE LLC', 'WRITER'),
        # the occupation is a name this donor files as their employer elsewhere
        ('BRUST, RANDY', 'SELF-EMPLOYED', 'CRESCENT REALTY ADVISORS'): ('CRESCENT REALTY ADVISORS', ''),
        ('BRUST, RANDY', 'CRESCENT REALTY ADVISORS', 'REAL ESTATE'): ('CRESCENT REALTY ADVISORS', 'REAL ESTATE'),
    }
    assert dict(zip(cases, _read(list(cases)))) == cases


def test_placeholder_employers_are_one_list():
    from fec.cleaning.work.read import names_no_employer
    values = ['43209876', 'A@B.COM', 'XXX', 'NM', 'GE', 'N A', 'PRIVATE', 'ACME LLC']
    assert [names_no_employer(v, also_words=False) for v in values] == [True, True, True, True, False, False, False, False]
    assert [names_no_employer(v) for v in values] == [True, True, True, True, False, True, True, False]


def test_occupation_corrections_are_one_table():
    from fec.config.occupation_rules.rules import OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS
    # No correction leads to another listed form: a filed value is corrected in one step.
    assert not [k for k, v in OCCUPATION_CORRECTIONS.items() if v in OCCUPATION_CORRECTIONS and OCCUPATION_CORRECTIONS[v] != v]
    assert OCCUPATION_CORRECTIONS['ATTY'] == 'ATTORNEY' and 'ATTY' in TEXT_CORRECTIONS
    assert OCCUPATION_CORRECTIONS['XXX'] is None and 'XXX' not in TEXT_CORRECTIONS
