"""Occupation categories: rule order and whole-word anchoring (audit findings 20 / 27).

The first matching CATEGORY_RULES pattern wins, so an unanchored word inside a
longer title decided the category: COUNSEL inside COUNSELOR (LEGAL), INVEST
inside INVESTIGATOR (FINANCE), CEO/COO/CTO inside INSTRUCTOR / COORDINATOR /
CONTRACTOR (EXECUTIVE), CHIEF inside "ANCHOR AND CHIEF ... CORRESPONDENT",
SECRETARY inside DEPUTY SECRETARY OF DEFENSE.
"""
import pandas as pd
import pytest

from fec.cleaning.occupations import _categorize_final
from fec.config.occupation_rules.categories import (
    CATEGORY_OVERRIDES,
    CATEGORY_RULES,
    RECLASSIFY_CATEGORY_RULES,
    VALID_CATEGORIES,
)

LEGAL = 'LEGAL'
MED = 'MEDICAL / HEALTHCARE'
EDU = 'EDUCATION'
GOV = 'GOVERNMENT / MILITARY'
ARTS = 'ARTS / ENTERTAINMENT'
FIN = 'FINANCE / INVESTMENT'
EXEC = 'EXECUTIVE / C-SUITE'


def _cat(occupation: str) -> str:
    return _categorize_final(pd.Series([occupation])).iloc[0]


@pytest.mark.parametrize(('occupation', 'category'), [
    # COUNSEL (lawyer) stays LEGAL
    ('COUNSEL', LEGAL),
    ('GENERAL COUNSEL', LEGAL),
    ('OF COUNSEL', LEGAL),
    ('SENIOR COUNSEL', LEGAL),
    ('ATTORNEY (GENERAL COUNSEL)', LEGAL),
    ('GENERAL COUNSEL/COO', LEGAL),
    ('CHIEF STRATEGY OFFICER & COUNSEL', LEGAL),
    ('LEG ASST - COUNSEL', LEGAL),
    ('COUNSELOR AT LAW', LEGAL),
    # school / college / career counselors are education
    ('COLLEGE COUNSELOR', EDU),
    ('CAREER COUNSELOR', EDU),
    ('SCHOOL COUNSELOR', EDU),
    ('GUIDANCE COUNSELOR', EDU),
    ('POLICY ADVISOR - SCHOOL COUNSELOR', EDU),
    # clinical counselors are health care, including the FEC's 38-char cut
    ('MENTAL HEALTH COUNSELOR', MED),
    ('CLINICAL MENTAL HEALTH COUNSELOR', MED),
    ('LICENSED MENTAL HEALTH COUNSELOR', MED),
    ('CLINICAL COUNSELOR', MED),
    ('GENETIC COUNSELOR', MED),
    ('ADDICTION COUNSELOR', MED),
    ('LICENSED PROFESSIONAL COUNSELOR', MED),
    ('LICENSED PROFESSIONAL CLINICAL COUNSEL', MED),
    ('LICENSED CLINICAL PROFESSIONAL COUNSEL', MED),
    # a bare COUNSELOR is ambiguous (counselor-at-law vs therapist): no guess
    ('COUNSELOR', 'OTHER'),
    # INVESTIGATOR is not an investor, and a bare one is not proof of a
    # government job either (the only filer works for a private university)
    ('INVESTIGATOR', 'OTHER'),
    ('PRIVATE INVESTIGATOR', 'OTHER'),
    ('POLICE INVESTIGATOR', GOV),
    ('COUNTY INVESTIGATOR', GOV),
    ('FEDERAL INVESTIGATOR', GOV),
    ('INSPECTOR GENERAL INVESTIGATOR', GOV),
    ('INSURANCE INVESTIGATOR', 'INSURANCE'),
    ('PRINCIPAL INVESTIGATOR', 'SCIENCE / RESEARCH'),
    ('INVESTIGATIVE JOURNALIST', ARTS),
    ('INVESTIGATION', 'OTHER'),
    ('INVESTOR', FIN),
    ('INVESTMENTS', FIN),
    ('INVESTMENT BANKER', FIN),
    ('REAL ESTATE INVESTOR', FIN),
    ('CHIEF INVESTMENT OFFICER', FIN),
    # public offices, one category however they are written
    ('SENATOR', GOV),
    ('STATE SENATOR', GOV),
    ('US SENATOR', GOV),
    ('U.S. SENATOR', GOV),
    ('US REPRESENTATIVE', GOV),
    ('STATE REPRESENTATIVE', GOV),
    ('LEGISLATOR', GOV),
    ('US AMBASSADOR TO ISRAEL', GOV),
    ('AMBASSADOR TO FRANCE', GOV),
    ('SPECIAL ENVOY FOR PEACE MISSIONS', GOV),
    ('DEPUTY SECRETARY OF DEFENSE', GOV),
    # ... but a bare AMBASSADOR / REPRESENTATIVE is a brand or sales one too
    ('AMBASSADOR', 'OTHER'),
    ('REPRESENTATIVE', 'OTHER'),
    ('CUSTOMER SERVICE REPRESENTATIVE', 'OTHER'),
    ('SECRETARY', 'MANAGEMENT'),
    ('LEGAL SECRETARY', LEGAL),
    # journalists: the category JOURNALIST already had
    ('JOURNALIST', ARTS),
    ('ANCHOR AND CHIEF POLITICAL CORRESPONDENT', ARTS),
    ('ANCHOR AND CHIEF WASHINGTON CORRESPONDENT', ARTS),
    ('NEWS ANCHOR', ARTS),
    ('CORRESPONDENT BANKING', FIN),
    # whole-word C-titles
    ('CEO', EXEC),
    ('CO-CEO', EXEC),
    ('CFO', EXEC),
    ('COO', EXEC),
    ('EXECUTIVE CHAIRMAN AND CTO', EXEC),
    ('CHIEF OF STAFF', EXEC),
    ('VCIO', EXEC),
    ('INSTRUCTOR', EDU),
    ('YOGA INSTRUCTOR', EDU),
    ('GYROTONIC INSTRUCTOR', EDU),
    ('PROFESSOR OF SOCIOLOGY', EDU),
    ('ACADEMIC COORDINATOR', EDU),
    ('CUSTOMER ENGAGEMENT COORDINATOR', 'SALES / MARKETING'),
    ('RECRUITING COORDINATOR', 'SALES / MARKETING'),
    ('NURSING COORDINATOR', MED),
    ('COORDINATOR', 'OTHER'),
    # COO inside COORDINATOR used to give an AIPAC staffer the same category
    # as a company VICE CHAIRMAN, which was the +15 that merged two GOLDBERG,
    # JOSHUA profiles (disjoint employers) into one donor
    ('REGIONAL POLITICAL COORDINATOR', GOV),
    ('POLITICAL COORDINATOR', GOV),
    ('MECHANICAL CONTRACTOR', 'CONSTRUCTION / TRADES'),
    ('SWIMMING POOL CONTRACTOR', 'CONSTRUCTION / TRADES'),
    ('CONCRETE CONSTRUCTOR', 'CONSTRUCTION / TRADES'),
    ('INDEPENDENT CONTRACTOR', 'SELF-EMPLOYED'),
    ('HUMAN FACTORS ENGINEER', 'TECHNOLOGY'),
    ('DCEO EOT IV', 'TECHNOLOGY'),
    ('VOICEOVER/AUDO PRODUCTION', ARTS),
    ('ACTOR', ARTS),
    ('LIV AND PRECIOUS JEWELRY', 'BUSINESS / ENTREPRENEUR'),
    # roster title from the Heritage Foundation, a think-tank fellow
    ('SENIOR COUNSELOR TO THE PRESIDENT AND E.W. RICHARDSON FELLOW', EDU),
])
def test_final_category(occupation, category):
    assert _cat(occupation) == category


def test_every_rule_and_override_writes_a_known_category():
    written = (
        {category for category, _ in CATEGORY_RULES}
        | set(CATEGORY_OVERRIDES.values())
        | {category for _, category in RECLASSIFY_CATEGORY_RULES}
    )
    assert written <= VALID_CATEGORIES, sorted(written - VALID_CATEGORIES)


def test_counselor_second_pass_no_longer_guesses_medical():
    """The old second-pass COUNSELOR -> MEDICAL rule would have caught every
    counselor the LEGAL substring used to claim; kinds are decided in pass 1."""
    patterns = ' '.join(pattern for pattern, _ in RECLASSIFY_CATEGORY_RULES)
    assert 'COUNSELOR' not in patterns
