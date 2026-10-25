import pandas as pd

from fec.cleaning.donors.steps import apply_donor_consistency
from fec.cleaning.donors.occupation import (
    _fill_occupation_from_donor,
    _rederive_occupation_category,
)
from fec.cleaning.donors.retired import (
    _fill_prev_employer_from_donor,
    _settle_retired_employer,
)
from fec.cleaning.reports.gates import run_quality_gates
from fec.cleaning.reports.trail import AuditTrail
from fec.cleaning.reports.occupation_scan import scan_uncategorized_occupations
from fec.cleaning.rules.addresses import _fix_foreign_addresses
from fec.cleaning.rules.employer import _fix_retired_typos
from fec.cleaning.rules.employer_swaps import (
    _swap_role_employer_with_known_company,
)
from fec.cleaning.rules.name_fields import _fix_occ_emp_both_swapped
from fec.cleaning.rules.occupation import _fix_web_artifact_occupation
from fec.cleaning.rules.occupation_category import (
    _fix_emp_occ_category_consistency,
)


def _apply_consistency(df):
    df['sub_id'] = [f'test-work-{index}' for index in df.index]
    df['contributor_name'] = 'DOE, JANE'
    df['contributor_street_1'] = '10 MAIN ST'
    df['contributor_street_2'] = ''
    df['contributor_city'] = 'NEW YORK'
    df['contributor_state'] = 'NY'
    df['contributor_zip'] = '10001'
    apply_donor_consistency(df, AuditTrail())


def test_retire_truncation_is_a_retired_status():
    df = pd.DataFrame({
        'contributor_employer': ['RETIRE'],
        'contributor_occupation': ['RETIRED'],
        'occupation_category': ['RETIRED'],
        'occupation_status': ['DISCLOSED'],
    })

    assert _fix_retired_typos(df, pd.Series([True])) == 1
    assert df.loc[0, 'contributor_employer'] == 'RETIRED'
    assert df.loc[0, 'occupation_status'] == 'NOT_APPLICABLE'


def test_explicit_retired_occupation_wins_over_not_employed_marker():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_employer': ['NOT EMPLOYED'],
        'contributor_occupation': ['RETIRED'],
        'occupation_category': ['RETIRED'],
        'occupation_status': ['NOT_APPLICABLE'],
    })

    assert _fix_emp_occ_category_consistency(df) == 1
    assert df.loc[0, 'contributor_employer'] == 'RETIRED'


def test_recovered_company_becomes_previous_employer_for_retiree():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_employer': ['FIT FOR LIFE', 'DR. AND MS.'],
        'contributor_occupation': ['RETIRED', 'RETIRED'],
        'occupation_category': ['RETIRED', 'RETIRED'],
        'occupation_status': ['DERIVED', 'DISCLOSED'],
        'previous_employer': [pd.NA, pd.NA],
    })

    assert _settle_retired_employer(df) == 2
    assert df['contributor_employer'].tolist() == ['RETIRED', 'RETIRED']
    assert df.loc[0, 'previous_employer'] == 'FIT FOR LIFE'
    assert pd.isna(df.loc[1, 'previous_employer'])


def test_retired_settlement_does_not_replace_existing_previous_employer():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_employer': ['NEWLY RECOVERED COMPANY'],
        'contributor_occupation': ['RETIRED'],
        'occupation_category': ['RETIRED'],
        'previous_employer': ['CURATED COMPANY'],
    })

    _settle_retired_employer(df)
    assert df.loc[0, 'previous_employer'] == 'CURATED COMPANY'


def test_not_disclosed_uses_other_without_losing_the_refusal():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_occupation': ['NOT DISCLOSED'],
        'contributor_employer': [pd.NA],
        'occupation_category': ['OTHER'],
        'occupation_status': ['MISSING'],
    })

    df.loc[0, 'occupation_category'] = pd.NA
    assert _rederive_occupation_category(df) == 1
    assert df.loc[0, 'occupation_category'] == 'OTHER'


def test_us_cities_with_foreign_names_are_kept_by_state():
    df = pd.DataFrame({
        'contributor_city': ['VANCOUVER', 'TORONTO', 'REHOVOT'],
        'contributor_state': ['WA', 'OH', 'CA'],
    })

    assert _fix_foreign_addresses(df) == 1
    assert df.loc[0, 'contributor_city'] == 'VANCOUVER'
    assert df.loc[1, 'contributor_city'] == 'TORONTO'
    assert pd.isna(df.loc[2, 'contributor_city'])


def test_quality_gates_reject_blank_category_and_retire_leak():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'occupation_category': [pd.NA],
        'contributor_employer': ['RETIRE'],
    })
    quality = run_quality_gates(df)

    assert not quality['checks']['valid_categories']['passed']
    assert quality['checks']['valid_categories']['blank'] == 1

    df.loc[0, 'occupation_category'] = 'RETIRED'
    quality = run_quality_gates(df)
    assert not quality['checks']['retired_employer_marker']['passed']


def test_quality_gates_reject_self_employed_status_mismatch():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_employer': ['SELF-EMPLOYED', 'ACME'],
        'contributor_occupation': ['CONSULTANT', 'SELF-EMPLOYED'],
        'occupation_category': ['CONSULTING', 'SELF-EMPLOYED'],
        'employer_status': ['active', 'active'],
    })

    check = run_quality_gates(df)['checks']['self_employed_status_consistency']
    assert not check['passed']
    assert check['marker_without_status'] == 2
    assert check['status_without_marker'] == 0

    df['employer_status'] = ['self_employed', 'self_employed']
    assert run_quality_gates(df)['checks']['self_employed_status_consistency']['passed']


def test_quality_gates_reject_not_employed_status_mismatch():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_employer': ['NOT EMPLOYED', 'NYU'],
        'contributor_occupation': ['NOT EMPLOYED', 'STUDENT'],
        'occupation_category': ['NOT EMPLOYED', 'STUDENT'],
        'employer_status': ['active', 'active'],
    })

    check = run_quality_gates(df)['checks']['not_employed_status_consistency']
    assert not check['passed']
    assert check['marker_without_status'] == 2
    assert check['status_without_marker'] == 0

    df['employer_status'] = ['not_employed', 'not_employed']
    assert run_quality_gates(df)['checks']['not_employed_status_consistency']['passed']


def test_quality_gates_reject_previous_employer_on_active_row():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'donor_key': ['same', 'same'],
        'contributor_employer': ['RETIRED', 'RETIRED'],
        'employer_status': ['retired', 'active'],
        'previous_employer': ['COMPANY A', 'COMPANY B'],
    })

    check = run_quality_gates(df)['checks']['previous_employer_scope']

    assert not check['passed']
    assert check['count'] == 1

    df.loc[1, 'previous_employer'] = ''
    check = run_quality_gates(df)['checks']['previous_employer_scope']
    assert check['passed']


def test_resolve_only_quality_gates_are_reported_before_resolve():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_employer': ['ACME'],
        'occupation_category': ['EXECUTIVE / C-SUITE'],
    })

    checks = run_quality_gates(df)['checks']
    for name in (
        'self_employed_status_consistency',
        'not_employed_status_consistency',
        'retired_active_sync',
    ):
        assert checks[name]['not_run']
        assert checks[name]['passed'] is None
        expected = ['employer_status']
        if name != 'retired_active_sync':
            expected = [
                'contributor_occupation', 'employer_status',
            ]
        assert checks[name]['missing_columns'] == expected

    previous = checks['previous_employer_scope']
    assert previous['not_run']
    assert previous['passed'] is None
    assert previous['missing_columns'] == [
        'employer_status', 'previous_employer',
    ]


def test_missing_employer_does_not_take_another_filings_company_or_job():
    df = pd.DataFrame({
        'donor_key': ['same', 'same', 'same'],
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': [pd.NA, pd.NA, 'ACME'],
        'contributor_occupation': ['ATTORNEY', pd.NA, 'CEO'],
        'occupation_category': ['LEGAL', 'OTHER', 'EXECUTIVE / C-SUITE'],
        'occupation_status': ['EMPLOYER_MISSING', 'MISSING', 'DISCLOSED'],
        'contribution_receipt_date': ['2024-01-01', '2024-02-01', '2024-03-01'],
    })

    _apply_consistency(df)
    assert df.loc[[0, 1], 'contributor_employer'].isna().all()
    assert df.loc[0, 'contributor_occupation'] == 'ATTORNEY'
    assert df.loc[0, 'occupation_category'] == 'LEGAL'
    assert pd.isna(df.loc[1, 'contributor_occupation'])
    assert df.loc[1, 'occupation_category'] == 'OTHER'


def test_missing_employer_keeps_reported_life_status_without_copying_a_company():
    df = pd.DataFrame({
        'donor_key': ['same'] * 4,
        'entity_type': ['INDIVIDUAL'] * 4,
        'contributor_employer': [pd.NA, pd.NA, pd.NA, 'ACME'],
        'contributor_occupation': ['RETIRED', 'NOT EMPLOYED', 'ATTORNEY', 'CEO'],
        'occupation_category': [
            'RETIRED', 'NOT EMPLOYED', 'LEGAL', 'EXECUTIVE / C-SUITE',
        ],
        'occupation_status': [
            'NOT_APPLICABLE', 'NOT_APPLICABLE', 'EMPLOYER_MISSING', 'DISCLOSED',
        ],
        'contribution_receipt_date': [
            '2022-01-01', '2023-01-01', '2023-06-01', '2024-01-01',
        ],
    })

    _apply_consistency(df)
    assert df.loc[[0, 1, 3], 'contributor_employer'].tolist() == ['RETIRED', 'NOT EMPLOYED', 'ACME']
    assert pd.isna(df.loc[2, 'contributor_employer'])


def test_previous_employer_uses_only_an_earlier_filing():
    df = pd.DataFrame({
        'donor_key': ['same'] * 3,
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': ['OLD COMPANY', 'RETIRED', 'FUTURE COMPANY'],
        'contributor_occupation': ['MANAGER', 'RETIRED', 'CEO'],
        'occupation_category': ['MANAGEMENT', 'RETIRED', 'EXECUTIVE / C-SUITE'],
        'contribution_receipt_date': ['2021-01-01', '2022-01-01', '2025-01-01'],
        'previous_employer': ['', '', ''],
    })

    assert _fill_prev_employer_from_donor(df) == 1
    assert df.loc[1, 'previous_employer'] == 'OLD COMPANY'


def test_future_employer_does_not_become_previous_employer():
    df = pd.DataFrame({
        'donor_key': ['same', 'same'],
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_employer': ['RETIRED', 'FUTURE COMPANY'],
        'contributor_occupation': ['RETIRED', 'CEO'],
        'occupation_category': ['RETIRED', 'EXECUTIVE / C-SUITE'],
        'contribution_receipt_date': ['2022-01-01', '2025-01-01'],
        'previous_employer': ['', ''],
    })

    assert _fill_prev_employer_from_donor(df) == 0
    assert df.loc[0, 'previous_employer'] == ''


def test_retired_row_is_not_previous_employer_evidence():
    df = pd.DataFrame({
        'donor_key': ['same', 'same'],
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_employer': ['DR. AND MS.', 'RETIRED'],
        'contributor_occupation': ['RETIRED', 'RETIRED'],
        'occupation_category': ['RETIRED', 'RETIRED'],
        'contribution_receipt_date': ['2021-01-01', '2022-01-01'],
        'previous_employer': ['', ''],
    })

    assert _fill_prev_employer_from_donor(df) == 0
    assert df.loc[1, 'previous_employer'] == ''


def test_occupation_fill_uses_same_employer_only():
    df = pd.DataFrame({
        'donor_key': ['same'] * 4,
        'entity_type': ['INDIVIDUAL'] * 4,
        'contributor_employer': ['ACME', 'ACME', 'SELF-EMPLOYED', 'ACME'],
        'contributor_occupation': [pd.NA, 'CEO', 'ATTORNEY', pd.NA],
        'occupation_category': [pd.NA, 'EXECUTIVE / C-SUITE', 'LEGAL', pd.NA],
        'occupation_status': ['MISSING', 'DISCLOSED', 'DISCLOSED', 'DERIVED'],
    })

    assert _fill_occupation_from_donor(df) == 2
    assert df.loc[0, 'contributor_occupation'] == 'CEO'
    assert df.loc[0, 'occupation_category'] == 'EXECUTIVE / C-SUITE'
    assert df.loc[3, 'contributor_occupation'] == 'CEO'


def test_self_employed_occupation_needs_one_confirmed_role_at_same_company():
    df = pd.DataFrame({
        'donor_key': ['clear'] * 3 + ['ambiguous'] * 3 + ['solo'],
        'entity_type': ['INDIVIDUAL'] * 7,
        'contributor_employer': ['ACME'] * 3 + ['BETA'] * 3 + ['GAMMA'],
        'contributor_occupation': [
            'SELF-EMPLOYED', 'ATTORNEY', 'ATTORNEY',
            'SELF-EMPLOYED', 'CEO', 'CONSULTANT',
            'SELF-EMPLOYED',
        ],
        'occupation_category': ['SELF-EMPLOYED'] * 7,
        'occupation_status': ['DISCLOSED'] * 7,
    })

    assert _fill_occupation_from_donor(df) == 1
    assert df.loc[0, 'contributor_occupation'] == 'ATTORNEY'
    assert df.loc[0, 'occupation_category'] == 'LEGAL'
    assert df.loc[0, 'occupation_status'] == 'DERIVED'
    assert df.loc[3, 'contributor_occupation'] == 'SELF-EMPLOYED'
    assert df.loc[6, 'contributor_occupation'] == 'SELF-EMPLOYED'


def test_company_name_in_occupation_needs_one_confirmed_role_at_same_company():
    df = pd.DataFrame({
        'donor_key': ['clear'] * 3 + ['ambiguous'] * 3,
        'entity_type': ['INDIVIDUAL'] * 6,
        'contributor_employer': [
            'KIRKLAND & ELLIS LLP', 'KIRKLAND & ELLIS LLP', 'KIRKLAND & ELLIS LLP',
            'BETA LLC', 'BETA LLC', 'BETA LLC',
        ],
        'contributor_occupation': [
            'KIRKLAND & ELLIS', 'ATTORNEY', 'ATTORNEY',
            'BETA', 'CEO', 'CONSULTANT',
        ],
        'occupation_category': ['OTHER'] * 6,
        'occupation_status': ['DISCLOSED'] * 6,
    })

    assert _fill_occupation_from_donor(df) == 1
    assert df.loc[0, 'contributor_occupation'] == 'ATTORNEY'
    assert df.loc[0, 'occupation_category'] == 'LEGAL'
    assert df.loc[0, 'occupation_status'] == 'DERIVED'
    assert df.loc[3, 'contributor_occupation'] == 'BETA'


def test_final_category_is_always_derived_from_final_occupation():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 4 + ['ORGANIZATION'],
        'contributor_occupation': [
            'ATTORNEY', 'COMPLIANCE', 'NOT DISCLOSED', pd.NA, pd.NA,
        ],
        'occupation_category': [
            'EXECUTIVE / C-SUITE', 'OTHER', 'LEGAL', pd.NA, 'ORGANIZATION',
        ],
    })

    quality = run_quality_gates(df)
    assert not quality['checks']['occupation_category_consistency']['passed']
    assert quality['checks']['occupation_category_consistency']['count'] == 4

    assert _rederive_occupation_category(df) == 4
    assert df['occupation_category'].tolist() == [
        'LEGAL', 'LEGAL', 'OTHER', 'OTHER', 'ORGANIZATION',
    ]

    quality = run_quality_gates(df)
    assert quality['checks']['occupation_category_consistency']['passed']


def test_quality_scan_surfaces_frequent_other_occupations():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 5,
        'contributor_occupation': ['CLAIMS', 'CLAIMS', '', 'NOT DISCLOSED', 'EMPLOYED'],
        'occupation_category': ['OTHER'] * 5,
    })

    report = scan_uncategorized_occupations(df)

    assert report == {
        'distinct': 1,
        'rows': 2,
        'examples': [{'value': 'CLAIMS', 'rows': 2}],
    }


def test_company_in_both_fields_is_not_blindly_swapped():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_occupation': ['AMERICAN EXPRESS', 'ARCH INSURANCE'],
        'contributor_employer': ['INDIGO CAPITAL LLC', 'CLAIMS'],
    })

    assert _fix_occ_emp_both_swapped(df) == 1

    assert df.loc[0, 'contributor_occupation'] == 'AMERICAN EXPRESS'
    assert df.loc[0, 'contributor_employer'] == 'INDIGO CAPITAL LLC'
    assert df.loc[1, 'contributor_occupation'] == 'CLAIMS'
    assert df.loc[1, 'contributor_employer'] == 'ARCH INSURANCE'


def test_swapped_job_is_canonicalized_after_company_detection():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL', 'INDIVIDUAL'],
        'contributor_occupation': ['JINSA', 'BANKER'],
        'contributor_employer': ['EXEC', 'JINSA'],
        'occupation_category': ['OTHER', 'FINANCE / INVESTMENT'],
    })

    assert _swap_role_employer_with_known_company(df) == 1
    assert df.loc[0, 'contributor_employer'] == 'JINSA'
    assert df.loc[0, 'contributor_occupation'] == 'EXECUTIVE'
    assert df.loc[0, 'occupation_category'] == 'EXECUTIVE / C-SUITE'


def test_garbled_web_artifact_is_removed():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_occupation': ['UOOO['],
        'occupation_category': ['OTHER'],
    })

    assert _fix_web_artifact_occupation(df) == 1
    assert pd.isna(df.loc[0, 'contributor_occupation'])


def test_missing_employer_stays_blank_between_different_filed_companies():
    df = pd.DataFrame({
        'donor_key': ['same'] * 4,
        'entity_type': ['INDIVIDUAL'] * 4,
        'contributor_employer': ['BLT RESTAURANT GROUP', pd.NA, 'JUNO INVESTMENTS', pd.NA],
        'contributor_occupation': ['CEO'] * 4,
        'occupation_category': ['EXECUTIVE / C-SUITE'] * 4,
        'occupation_status': ['DISCLOSED', 'EMPLOYER_MISSING', 'DISCLOSED', 'EMPLOYER_MISSING'],
        'contribution_receipt_date': ['2024-03-25', '2024-06-01', '2025-05-19', '2025-11-10'],
    })

    _apply_consistency(df)
    assert df.loc[[1, 3], 'contributor_employer'].isna().all()
    assert df.loc[[0, 2], 'contributor_employer'].tolist() == ['BLT RESTAURANT GROUP', 'JUNO INVESTMENTS']


def test_missing_employer_never_takes_a_later_filed_company():
    df = pd.DataFrame({
        'donor_key': ['same'] * 3,
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_employer': [pd.NA, 'FIRST CO', 'SECOND CO'],
        'contributor_occupation': ['CEO'] * 3,
        'occupation_category': ['EXECUTIVE / C-SUITE'] * 3,
        'occupation_status': ['EMPLOYER_MISSING', 'DISCLOSED', 'DISCLOSED'],
        'contribution_receipt_date': ['2022-01-01', '2023-01-01', '2024-01-01'],
    })

    _apply_consistency(df)
    assert pd.isna(df.loc[0, 'contributor_employer'])


def test_a_retiree_who_filed_no_as_employer_gets_no_previous_employer():
    # GOLDSMITH, JOANNE filed employer 'NO', occupation RETIRED
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'],
        'contributor_employer': ['NO'],
        'contributor_occupation': ['RETIRED'],
        'occupation_category': ['RETIRED'],
        'previous_employer': [pd.NA],
    })

    _settle_retired_employer(df)
    assert df.loc[0, 'contributor_employer'] == 'RETIRED'
    assert pd.isna(df.loc[0, 'previous_employer'])
