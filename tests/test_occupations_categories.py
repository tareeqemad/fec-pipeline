"""Occupation categories: rule order and whole-word anchoring (audit findings 20 / 27).

The first matching CATEGORY_RULES pattern wins, so an unanchored word inside a
longer title decided the category: COUNSEL inside COUNSELOR (LEGAL), INVEST
inside INVESTIGATOR (FINANCE), CEO/COO/CTO inside INSTRUCTOR / COORDINATOR /
CONTRACTOR (EXECUTIVE), CHIEF inside "ANCHOR AND CHIEF ... CORRESPONDENT",
SECRETARY inside DEPUTY SECRETARY OF DEFENSE.
"""
import pandas as pd
import pytest

from fec.cleaning.occupations.normalize import categorize_final
from fec.config.occupation_rules.categories import (
    CATEGORY_RULES,
    RECLASSIFY_CATEGORY_RULES,
    VALID_CATEGORIES,
)
from fec.config.occupation_rules.category_overrides import CATEGORY_OVERRIDES

LEGAL = 'LEGAL'
MED = 'MEDICAL / HEALTHCARE'
EDU = 'EDUCATION'
GOV = 'GOVERNMENT / MILITARY'
ARTS = 'ARTS / ENTERTAINMENT'
FIN = 'FINANCE / INVESTMENT'
EXEC = 'EXECUTIVE / C-SUITE'


def _cat(occupation: str) -> str:
    return categorize_final(pd.Series([occupation])).iloc[0]


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
    ('REAL ESTATE INVESTOR', 'REAL ESTATE'),
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


# 27-Sep occupation review: a broad word no longer beats a narrower title,
# and a title that does not say its field is not given one
@pytest.mark.parametrize('occupation, category', [
    ('REAL ESTATE BROKER', 'REAL ESTATE'),
    ('COMMERCIAL REAL ESTATE BROKER', 'REAL ESTATE'),
    ('MORTGAGE BROKER', FIN),
    ('DATA ANALYST', 'TECHNOLOGY'),
    ('DATA ANALYST MANAGER', 'TECHNOLOGY'),
    ('WEB ANALYST', 'TECHNOLOGY'),
    ('IT ANALYST', 'TECHNOLOGY'),
    ('FINANCIAL ANALYST', FIN),
    ('SENIOR WEALTH ADVISOR', FIN),
    ('ASSOCIATE WEALTH ADVISOR', FIN),
    ('WEALTH ADVISORY', FIN),
    ('HEDGE FUND MANAGER', FIN),
    ('PUBLIC ADJUSTER', 'INSURANCE'),
    ('PACKAGE HANDLER', 'OTHER'),
    ('SENIOR ASSOCIATE', 'OTHER'),
])
def test_occupation_review_categories(occupation, category):
    assert _cat(occupation) == category


# a word that does not say the field gets no field: general or OTHER, title kept
@pytest.mark.parametrize('occupation, category', [
    ('CLERK', 'OTHER'),
    ('OFFICE STAFF', 'OTHER'),
    ('PROGRAM SPECIALIST', 'OTHER'),
    ('LAW CLERK', LEGAL),
    ('TREATMENT TECHNICIAN', 'OTHER'),
    ('TECHNOLOGIST', 'OTHER'),
    ('TECHNICAL WRITER', ARTS),
    ('RANGE OPS TECH', 'OTHER'),
    ('PHARMACY TECHNICIAN', MED),
    ('COMPUTER TECHNICIAN', 'TECHNOLOGY'),
    ('IT TECHNICIAN', 'TECHNOLOGY'),
    ('TECH SALES', 'TECHNOLOGY'),
    ('BIOTECH', 'TECHNOLOGY'),
    ('INFORMATION TECHNOLOGY', 'TECHNOLOGY'),
])
def test_general_words_do_not_decide_the_field(occupation, category):
    assert _cat(occupation) == category


# EXECUTIVE as a rank is C-suite; followed by the job itself it is that job
@pytest.mark.parametrize('occupation, category', [
    ('EXECUTIVE', EXEC),
    ('EXECUTIVE DIRECTOR', EXEC),
    ('EXECUTIVE VICE PRESIDENT', EXEC),
    ('CPO', EXEC),
    ('EXECUTIVE ASSISTANT', 'MANAGEMENT'),
    ('EXECUTIVE COACH', 'OTHER'),
    ('EXECUTIVE SEARCH', 'SALES / MARKETING'),
    ('EXECUTIVE CHEF', 'FOOD / HOSPITALITY'),
    ('EXECUTIVE PRODUCER', ARTS),
    ('COMMUNITY BUILDING', 'OTHER'),
    ('COMMUNITY BUILDER', 'OTHER'),
    ('BUILDER', 'CONSTRUCTION / TRADES'),
])
def test_executive_is_a_rank_not_every_title_containing_it(occupation, category):
    assert _cat(occupation) == category


# 27-Sep clean review: a narrower title decides before BROKER, ANALYST, DIRECTOR, PRINCIPAL
@pytest.mark.parametrize('occupation, category', [
    ('FUNERAL DIRECTOR', 'OTHER'),
    ('FUNERAL DIRECTOR/EMBALMER', 'OTHER'),
    ('PRINCIPAL SOLUTIONS ENGINEER', 'TECHNOLOGY'),
    ('SCREENWRITER - DIRECTOR', 'ARTS / ENTERTAINMENT'),
    ('STOCK ANALYST', 'FINANCE / INVESTMENT'),
    ('CREDIT RESEARCH ANALYST', 'FINANCE / INVESTMENT'),
    ('PRINCIPAL STAFF ANALYST', 'MANAGEMENT'),
    ('ASSOCIATE STAFF ANALYST', 'MANAGEMENT'),
    ('PRINCIPAL', 'EXECUTIVE / C-SUITE'),
    ('TAX PRINCIPAL', 'ACCOUNTING / TAX'),
    ('TAX PARTNER', 'ACCOUNTING / TAX'),
    ('TAX ATTORNEY', 'LEGAL'),
    ('PROFESSOR/CHAIR', 'EDUCATION'),
    ('HOMEMAKER & HOMESCHOOL MOM', 'HOMEMAKER'),
    ('MANUFACTURING ENGINEER', 'TECHNOLOGY'),
    ('PRINCIPAL', EXEC),
    ('TV COMMERCIAL DIRECTOR', ARTS),
    ('MUSIC DIRECTOR', ARTS),
    ('DIRECTOR/EDITOR', ARTS),
    ('AUDIOBOOK PRODUCER/DIRECTOR, EDITOR', ARTS),
    ('COMMERCIAL DIRECTOR', EXEC),
    ('SALES EXECUTIVE', 'SALES / MARKETING'),
    ('NATIONAL ACCOUNT SALES EXECUTIVE', 'SALES / MARKETING'),
    ('INSURANCE ADVISOR & BROKER', 'INSURANCE'),
    ('HEALTH INSURANCE BROKER', 'INSURANCE'),
    ('REAL ESTATE OWNER/BROKER', 'REAL ESTATE'),
    ('ASSOCIATE BROKER', 'REAL ESTATE'),
    ('MORTGAGE BROKER', FIN),
    ('ANALYST', 'OTHER'),
    ('BUSINESS ANALYST', 'OTHER'),
    ('RESEARCH ANALYST', 'OTHER'),
    ('FINANCIAL ANALYST', FIN),
    ('EQUITY RESEARCH ANALYST', FIN),
    ('BUY SIDE RESEARCH ANALYST', FIN),
    ('DIRECTOR OF ENGINEERING', EXEC),
])
def test_a_narrower_title_decides_before_a_broad_word(occupation, category):
    assert _cat(occupation) == category


def test_executive_asiatant_is_an_assistant():
    from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE
    assert OCCUPATION_NORMALIZE['EXECUTIVE ASIATANT'] == 'EXECUTIVE ASSISTANT'


@pytest.mark.parametrize('title, category', [
    ('REAL ESTATE FINANCE', 'REAL ESTATE'),
    ('PRIVATE EQUITY REAL ESTATE', 'REAL ESTATE'),
    ('REAL ESTATE LENDING', 'REAL ESTATE'),
    ('FINANCE AND REAL ESTATE', 'FINANCE / INVESTMENT'),
    ('ATTORNEY AND REAL ESTATE INVESTMENTS', 'LEGAL'),
    ('INVESTOR', 'FINANCE / INVESTMENT'),
])
def test_investing_in_real_estate_is_real_estate(title, category):
    """The field names the domain; two roles joined by AND still go to the first."""
    assert categorize_final(pd.Series([title])).iloc[0] == category
