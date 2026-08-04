import pandas as pd

from fec.cleaning.quality import run_quality_gates
from fec.cleaning.safety_nets.addresses import _fix_foreign_addresses
from fec.cleaning.safety_nets.employer import _fix_retired_typos
from fec.cleaning.safety_nets.occupation import (
    _fix_emp_occ_category_consistency,
    _fix_not_disclosed_in_other,
)
from fec.post_merge_fixes.occupation import _rederive_occupation_category
from fec.post_merge_fixes.retired import _settle_retired_employer


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
        'contributor_employer': ['FIT FOR LIFE', 'MR AND MRS'],
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

    assert _fix_not_disclosed_in_other(df) == 1
    assert df.loc[0, 'occupation_status'] == 'NOT_DISCLOSED'
    assert df.loc[0, 'occupation_category'] == 'OTHER'

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
