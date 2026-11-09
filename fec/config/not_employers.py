"""Occupations, roles, sectors and titles that are not employers."""
import re

from fec.config.constants import (
    REFUSAL_EMPLOYERS,
    RETIRED_TYPO_EMPLOYERS,
    SKIP_EMPLOYERS,
)
from fec.config.tables import CONFIG_DIR, read_set

# Occupation words as employer: probably self-employed.
OCCUPATION_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'DOCTOR', 'PHYSICIAN', 'DENTIST',
    'ACCOUNTANT', 'CONSULTANT', 'ARCHITECT', 'ENGINEER',
    'PROFESSOR', 'TEACHER', 'NURSE', 'PSYCHOLOGIST',
    'THERAPIST', 'SURGEON', 'RADIOLOGIST', 'DERMATOLOGIST',
    'REALTOR', 'PHARMACY OWNER', 'CARDIOLOGIST',
    'CPA', 'DESIGNER',
})


# Role words as employer; the company is often in occupation.
ROLE_AS_EMPLOYER = read_set(CONFIG_DIR / "roles.csv")


# Industry words instead of a company: employer nulled.
SECTOR_AS_EMPLOYER = read_set(CONFIG_DIR / "sectors.csv")


# Job titles that appear as employer (swap candidates).
JOB_TITLE_AS_EMPLOYER = frozenset({
    'REAL ESTATE', 'INVESTMENT MANAGEMENT', 'INSURANCE',
    'CONSULTING', 'FINANCE', 'MARKETING', 'MANAGEMENT',
    'CONSTRUCTION', 'EDUCATION',
})


# Self-employed occupation words found in the employer field.
SELF_EMPLOYED_OCC_AS_EMP = frozenset({
    'DESIGNER', 'CATER', 'CATERER', 'WRITER', 'ARTIST', 'SALES',
    'REAL ESTATE', 'BUSINESS', 'FARMING', 'TRADER',
    # Generic phrase; verified company filings use their legal name.
    'TRUST DEED INVESTMENTS',
})


# Employer words that themselves say the filer works for themselves.
SELF_EMPLOYMENT_WORDS = frozenset({
    'OWNER', 'BUSINESS OWNER', 'BUSINESS OWNER/OPERATOR', 'OWN BUSINESS',
    'PRIVATE PRACTICE', 'FREELANCE', 'FREELANCER', 'INDEPENDENT',
    'INDEPENDENT CONTRACTOR', 'SELF (LANDSCAPE DESIGNER)', 'PERSONAL',
})


# Legal suffixes stripped from employer names for matching.
LEGAL_SUFFIX_RE = re.compile(
    r',?\s*\b'  # word boundary — don't strip inside words
    r'(LLC|LLP|L\.L\.C\.?|L\.L\.P\.?'
    r'|INC\.?|CORP\.?|LTD\.?'
    r'|LP|PLC'
    r'|P\.?C\.?|P\.?A\.?|P\.?L\.?L\.?C\.?|APLC|APC)'
    r'\s*\.?\s*$',
    re.IGNORECASE,
)


# Professions in employer boxes, each checked: none names a company.
OCCUPATION_TITLE_EMPLOYERS = read_set(CONFIG_DIR / "job_titles.csv")


# The shared answer to "is this string a company?".
NOT_REAL_EMPLOYER = frozenset({
    "NO",
    "SELF",
    "SELF EMPL.", "SELF EMPL", "SELF-EMP", "SELF EMP",
    "UNKNOWN",
    "MR AND MRS", "MR. AND MRS.", "DR AND MS", "DR. AND MS", "DR. AND MS.",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    # a request for the donor's details, not a company
    "MORE INFO NEEDED", "MORE INFORMATION NEEDED", "MORE INFO REQUESTED", "INFO NEEDED", "NEED MORE INFO",
    "DISABLED", "CHOOSE NOT TO DISCLOSE", "MANY ORGANIZATIONS",
    # status / housewife variants (the status typos come from the sets below)
    "RETIRE", "HOUSEWIFE", "HOUSWIFE", "HOUSE WIFE", "HOME MANAGER",
    "STAY-AT-HOME MOM",
    # A job title in the employer box; the rest come from the sets below.
    "PHILANTHROPIST",
}) | (
    SKIP_EMPLOYERS | REFUSAL_EMPLOYERS | RETIRED_TYPO_EMPLOYERS
    | OCCUPATION_TITLE_EMPLOYERS | OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER
    | SECTOR_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
)


# A job title replacing a company: only title words.
JOB_TITLE_HEADS = read_set(CONFIG_DIR / "job_title_main_words.csv")
JOB_TITLE_MODIFIERS = read_set(CONFIG_DIR / "job_title_extra_words.csv")
