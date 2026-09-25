"""Occupation leftovers of the 2026-09-23 audit.

- misspellings missing from the curated typo lists (SALED, FIANCE, ...);
- unambiguous job titles that fell into OTHER (EQUESTRIAN, INTERPRETER, ...),
  while ambiguous bare words stay OTHER;
- a role word in the employer box beside the company in the occupation box
  (VERON, JANE: CEO / TAP) is swapped back instead of becoming SELF-EMPLOYED;
- EXECUTIVE in the employer box is swapped back on a legal-entity word beside
  a name (NORTH INDUSTRIES), never onto an industry (EXECUTIVE / HEALTHCARE);
- a company in the occupation box no longer gets an occupation copied from
  other people who work there;
- the junk occupation BIG BOY is nulled so the same-employer fill can act.
"""
import csv
import warnings

import numpy as np
import pandas as pd
import pytest

from fec.cleaning.donor_consistency.occupation import _fill_occupation_from_donor
from fec.cleaning.manual_overrides import OVERRIDES_CSV
from fec.cleaning.occupations import _categorize_final
from fec.cleaning.occupations.normalize import _normalize_text
from fec.cleaning.occupations.style import normalize_occupation_style_step
from fec.cleaning.safety_nets.employer import _null_sector_as_employer
from fec.cleaning.safety_nets.employer_swaps import _fix_role_as_employer
from fec.cleaning.safety_nets.name_fields import _fix_company_name_as_occupation
from fec.cleaning.safety_nets.occupation import _fix_web_artifact_occupation
from fec.config.occupation_rules.category_overrides import CATEGORY_OVERRIDES
from fec.config.occupation_rules.fixes import OCCUPATION_TYPO_FIXES
from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE

ARTS = 'ARTS / ENTERTAINMENT'
EXEC = 'EXECUTIVE / C-SUITE'
FIN = 'FINANCE / INVESTMENT'
GOV = 'GOVERNMENT / MILITARY'
MED = 'MEDICAL / HEALTHCARE'


def _cat(occupation: str) -> str:
    return _categorize_final(pd.Series([occupation])).iloc[0]


def _normalized(raw: str) -> str:
    return _normalize_text(pd.Series([raw]), OCCUPATION_NORMALIZE, collapse_retire=True)[0].iloc[0]


# ---------------------------------------------------------------- typo lists

@pytest.mark.parametrize(('raw', 'cleaned', 'category'), [
    ('SALED', 'SALES', 'SALES / MARKETING'),          # ERNSTEIN: SALES 14x, same employer
    ('FIANCE', 'FINANCE', FIN),                       # MUSCHEL: FINANCE 57x
    ('OFFICER AT RICHKER METALS', 'OFFICER', EXEC),   # 'OFFICER OF ...' was already listed
    ('Account Mananger', 'ACCOUNT MANAGER', 'MANAGEMENT'),
    ('C.O.O.', 'COO', EXEC),                          # trailing period stripped first
    ('EXEC. DIR.', 'EXECUTIVE DIRECTOR', EXEC),
    ('HR / BENEFITS COSULTING FIRM', 'HR/BENEFITS CONSULTING FIRM', 'CONSULTING'),
    ('REIRED', 'RETIRED', 'RETIRED'),
    ('MOSTLY TRTIRED', 'RETIRED', 'RETIRED'),
    ('HOUSE WIFE', 'HOUSEWIFE', 'HOMEMAKER'),
    ('BANJET', 'BANKER', FIN),
    ('THEATRE PRODUCING', 'THEATER PRODUCER', ARTS),
    ('PROP MGMT', 'PROPERTY MANAGER', 'REAL ESTATE'),
    ('FERTIZLIER', 'FERTILIZER', 'AGRICULTURE'),
    ('SVP CORP DEV', 'SVP CORPORATE DEVELOPMENT', EXEC),
])
def test_verified_typos_are_fixed_at_the_first_step(raw, cleaned, category):
    assert _normalized(raw) == cleaned
    assert _cat(cleaned) == category


@pytest.mark.parametrize('ambiguous', ['F A', 'SLES', 'SPEAKER', 'MAGICIAN'])
def test_ambiguous_short_forms_are_not_globally_rewritten(ambiguous):
    """F A (financial advisor / flight attendant / fine arts), SLES (SALES or
    SUSE Linux at GOOGLE): per-row evidence only, never a global entry."""
    assert _normalized(ambiguous) == ambiguous
    assert ambiguous not in OCCUPATION_TYPO_FIXES


@pytest.mark.parametrize(('typo', 'category_before', 'category_after'), [
    ('COMMERCIAL REAL ESTAE OWNER', 'BUSINESS / ENTREPRENEUR', 'REAL ESTATE'),
    ('REAL ESTATE INVESTMENT & MANAGMENT', FIN, FIN),
    ('SENIOR ASSOCIATE, PUBIC ACCOUNTING', 'ACCOUNTING / TAX', 'ACCOUNTING / TAX'),
])
def test_misspelled_words_inside_titles(typo, category_before, category_after):
    fixed = OCCUPATION_TYPO_FIXES[typo]
    assert _cat(typo) == category_before
    assert _cat(fixed) == category_after


# -------------------------------------------------------------- categories

@pytest.mark.parametrize(('occupation', 'category'), [
    ('EQUESTRIAN', ARTS), ('HORSE TRAINER', ARTS),   # one donor files both
    ('INTERPRETER', ARTS), ('TRANSLATOR', ARTS),
    ('MATHEMATICIAN', 'SCIENCE / RESEARCH'),
    ('PRINTER', 'BUSINESS / ENTREPRENEUR'),
    ('BUYER', 'SALES / MARKETING'), ('CANDY & GIFT BUYER', 'SALES / MARKETING'),
    ('INS AGENCY', 'INSURANCE'),
    ('COURT COORDINATOR', GOV), ('POLICE OFFICER', GOV), ('CAPTAIN, COP UNIT', GOV),
    ('HEPATOLOGIST', MED), ('NURSING', MED), ('FITNESS TRAINER', MED),
    ('ADHD COACH', 'CONSULTING'), ('CORPORATE TRAINER', 'EDUCATION'),
    ('GM', 'MANAGEMENT'), ('CGO', EXEC),
    ('GLOBAL BROADCAST TALENT', ARTS),
    ('CLEANING SERVICE', 'BUSINESS / ENTREPRENEUR'),
    ('INFRASTRUCTURE DEVELOPMENT AND OPERATI', 'MANAGEMENT'),
    # bare VP / SVP / EVP titles still OTHER after the first pass
    ('VP OF STRATEGY', EXEC), ('CLINICAL DEVELOPMENT SVP', EXEC), ('EVP STRATEGY', EXEC),
])
def test_unambiguous_titles_get_a_category(occupation, category):
    assert _cat(occupation) == category


@pytest.mark.parametrize('ambiguous', [
    'INVESTIGATOR', 'COUNSELOR', 'REPRESENTATIVE', 'TRUSTEE', 'NEUTRAL',
    'SPEAKER', 'MAGICIAN', 'SUMMER ASSOCIATE', 'FRAMING', 'STRATEGY',
    'MVP',   # VP inside another token is not a VP title
])
def test_ambiguous_bare_words_stay_other(ambiguous):
    assert _cat(ambiguous) == 'OTHER'


def test_override_keys_are_not_rewritten_away_from_their_category():
    """An override keyed on a spelling an earlier map rewrites can never match
    (CLEANNG SERVICE sat unused while CLEANING SERVICE stayed OTHER). A dead key
    is harmless only if the rewritten value lands in the same category."""
    lost = {}
    for key, category in CATEGORY_OVERRIDES.items():
        value = OCCUPATION_NORMALIZE.get(key, key)
        value = OCCUPATION_TYPO_FIXES.get(value, value)
        if value != key and _cat(value) != category:
            lost[key] = (value, category)
    # pending owner decision (which category is meant), reported 2026-09-23
    assert set(lost) == {'PROP MNGMT', 'FUND-RAISING', 'FUND RAISING'}


# ------------------------------------------------------------------ frames

def _frame(rows, names=None):
    """rows = (employer, occupation)"""
    n = len(rows)
    return pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * n,
        'contributor_name': names or [f'DONOR {i}, PAT' for i in range(n)],
        'contributor_first_name': ['PAT'] * n,
        'contributor_last_name': [f'DONOR {i}' for i in range(n)],
        'contributor_employer': [r[0] for r in rows],
        'contributor_occupation': [r[1] for r in rows],
        'occupation_category': ['OTHER'] * n,
        'occupation_status': ['DISCLOSED'] * n,
    })


def test_ceo_beside_a_company_with_no_marker_is_swapped_back():
    df = _frame(
        [('CEO', 'TAP'), ('CEO', 'TAP'), ('CEO', 'KENILWORTH EQUITIES')],
        names=['VERON, JANE', 'VERON, JANE', 'MORROW, ROBERT'],
    )
    assert _fix_role_as_employer(df) == 3
    assert df['contributor_employer'].tolist() == ['TAP', 'TAP', 'KENILWORTH EQUITIES']
    assert df['contributor_occupation'].tolist() == ['CEO', 'CEO', 'CEO']
    assert df['occupation_category'].tolist() == [EXEC] * 3


def test_role_beside_a_job_or_line_of_work_stays_self_employed():
    df = _frame([
        ('OWNER', 'DISTRIBUTION'),        # self-employment word: not an org title
        ('INDEPENDENT', 'REPETITEUR'),    # a real (rare) job
        ('CEO', 'REAL ESTATE'),           # a line of work with a category
        ('CEO', 'NOT DISCLOSED'),         # a placeholder, not a company
        ('CEO', 'STRATEGY'),              # other filers use it as a job ...
        ('SELF-EMPLOYED', 'STRATEGY'),
        ('BURLINGTON STORES', 'STRATEGY'),
    ])
    assert _fix_role_as_employer(df) == 5
    assert df['contributor_employer'].tolist()[:5] == ['SELF-EMPLOYED'] * 5
    assert df['contributor_occupation'].tolist()[:5] == [
        'DISTRIBUTION', 'REPETITEUR', 'REAL ESTATE', 'NOT DISCLOSED', 'STRATEGY',
    ]


def test_same_unknown_word_from_two_filers_is_not_a_company():
    df = _frame([('CEO', 'GROWTHY'), ('PRESIDENT', 'GROWTHY')], names=['A, B', 'C, D'])
    _fix_role_as_employer(df)
    assert df['contributor_employer'].tolist() == ['SELF-EMPLOYED', 'SELF-EMPLOYED']


def test_marker_swap_still_works_for_any_role():
    df = _frame([('OWNER', 'SUMMIT HEALTH'), ('OWN BUSINESS', 'MENTAL HEALTH COUNSELOR')])
    _fix_role_as_employer(df)
    assert df['contributor_employer'].tolist() == ['SUMMIT HEALTH', 'SELF-EMPLOYED']


def test_executive_in_employer_box():
    df = _frame([
        ('EXECUTIVE', 'NORTH INDUSTRIES'),   # company marker: swapped back
        ('EXECUTIVE', 'COSMETICS'),          # a line of work: employer nulled, words kept
        ('EXECUTIVE', np.nan),               # title moved into the empty occupation box
        ('HEALTHCARE', 'ACME GROUP'),        # a real sector word never swaps
        ('EXECUTIVE', 'HEALTHCARE'),         # "healthcare executive": never a fake employer
        ('EXECUTIVE', 'ACME HOLDINGS LLC'),  # legal-entity word beside a name: swapped back
    ])
    assert _null_sector_as_employer(df) == 6
    assert df.loc[0, 'contributor_employer'] == 'NORTH INDUSTRIES'
    assert df.loc[0, 'contributor_occupation'] == 'EXECUTIVE'
    assert pd.isna(df.loc[1, 'contributor_employer'])
    assert df.loc[1, 'contributor_occupation'] == 'COSMETICS'
    assert pd.isna(df.loc[2, 'contributor_employer'])
    assert df.loc[2, 'contributor_occupation'] == 'EXECUTIVE'
    assert pd.isna(df.loc[3, 'contributor_employer'])
    assert df.loc[3, 'contributor_occupation'] == 'ACME GROUP'
    assert pd.isna(df.loc[4, 'contributor_employer'])
    assert df.loc[4, 'contributor_occupation'] == 'HEALTHCARE'
    assert df.loc[5, 'contributor_employer'] == 'ACME HOLDINGS LLC'
    assert df.loc[5, 'contributor_occupation'] == 'EXECUTIVE'


@pytest.mark.parametrize('industry', [
    # the broad company marker matches every one of these (industry words,
    # "X & Y", CO-); each reads "<industry> executive", so the employer is nulled
    'HEALTHCARE', 'INSURANCE', 'FINANCIAL SERVICES', 'MEDIA', 'PROPERTY MANAGEMENT',
    'INVESTMENT MANAGEMENT', 'FINANCIAL', 'REAL ESTATE SERVICES', 'TECHNOLOGY SOLUTIONS',
    'GLOBAL', 'HOSPITAL', 'OIL & GAS', 'CO-FOUNDER',
    # a legal-entity word beside an industry or a sector / job word, or alone
    'HEALTHCARE COMPANY', 'INSURANCE GROUP', 'INSURANCE COMPANY', 'INVESTMENT GROUP',
    'CONSULTING GROUP', 'TECHNOLOGY COMPANY', 'LLC',
    'STEALTH STARTUP INC', 'UNKNOWN LLC',   # name part is a non-company word
])
def test_executive_beside_an_industry_never_becomes_the_employer(industry):
    df = _frame([('EXECUTIVE', industry)])
    assert _null_sector_as_employer(df) == 1
    assert pd.isna(df.loc[0, 'contributor_employer'])
    assert df.loc[0, 'contributor_occupation'] == industry


def test_company_in_occupation_takes_no_occupation_from_coworkers():
    """FEINBERG, PETER: SELF / MASTERCARD became MASTERCARD / PRODUCT MANAGER,
    a job only his colleagues ever filed."""
    rows = [('SELF-EMPLOYED', 'MASTERCARD')] + [('MASTERCARD', 'PRODUCT MANAGER')] * 10
    df = _frame(rows, names=['FEINBERG, PETER'] + ['COLLEAGUE, X'] * 10)
    assert _fix_company_name_as_occupation(df) == 1
    assert df.loc[0, 'contributor_employer'] == 'MASTERCARD'
    assert pd.isna(df.loc[0, 'contributor_occupation'])
    assert df.loc[0, 'occupation_status'] == 'MISSING'


def test_donor_stage_refills_only_from_the_same_donor():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 4,
        'donor_key': ['brust', 'brust', 'other', 'feinberg'],
        'contributor_employer': ['CRESCENT REALTY ADVISORS'] * 3 + ['MASTERCARD'],
        'contributor_occupation': [np.nan, 'REAL ESTATE', 'ATTORNEY', np.nan],
        'occupation_category': [pd.NA, 'REAL ESTATE', 'LEGAL', pd.NA],
        'occupation_status': ['MISSING', 'DISCLOSED', 'DISCLOSED', 'MISSING'],
    })
    _fill_occupation_from_donor(df)
    assert df.loc[0, 'contributor_occupation'] == 'REAL ESTATE'
    assert pd.isna(df.loc[3, 'contributor_occupation'])


def test_big_boy_is_nulled_as_junk():
    df = _frame([('SANDHURST', 'BIG BOY'), ('BIG BOY RESTAURANTS', 'MANAGER')])
    assert _fix_web_artifact_occupation(df) == 1
    assert pd.isna(df.loc[0, 'contributor_occupation'])
    assert df.loc[1, 'contributor_employer'] == 'BIG BOY RESTAURANTS'


def test_style_step_does_not_warn_about_match_groups():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 3,
        'contributor_occupation': ['ADLER REAL ESTATE PARTNERS LLC', 'PRESIDENT & CEO', 'OWNERS'],
        'contributor_employer': ['X', 'Y', 'Z'],
    })
    with warnings.catch_warnings():
        warnings.simplefilter('error', UserWarning)
        df, n = normalize_occupation_style_step(df)
    assert n == 2
    assert df['contributor_occupation'].tolist() == [
        'ADLER REAL ESTATE PARTNERS LLC', 'PRESIDENT/CEO', 'OWNER',
    ]


def test_per_row_overrides_are_curated():
    with OVERRIDES_CSV.open(encoding='utf-8', newline='') as handle:
        rows = {row['sub_id']: row for row in csv.DictReader(handle)}
    richker = rows['4082720261585276373']
    assert (richker['contributor_employer'], richker['contributor_occupation']) == ('RICHKER METALS', 'OFFICER')
    assert rows['4080120261540162425']['contributor_occupation'] == 'FINANCIAL ADVISOR'
    dakar = rows['4110120231808001288']
    assert (dakar['contributor_employer'], dakar['contributor_occupation']) == ('', 'COSMETICS EXECUTIVE')
