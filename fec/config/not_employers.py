"""Occupations, roles, sectors and titles that are not employers."""

import re

from fec.config.constants import (
    REFUSAL_EMPLOYERS,
    SKIP_EMPLOYERS,
    STATUS_SLIPS_BEYOND_RULE,
)
from fec.config.tables import CONFIG_DIR, read_set, words

# Occupation words as employer: probably self-employed.
OCCUPATION_AS_EMPLOYER = words("occupation_as_employer")


# Role words as employer; the company is often in occupation.
ROLE_AS_EMPLOYER = read_set(CONFIG_DIR / "roles.csv")


# Industry words instead of a company: employer nulled.
SECTOR_AS_EMPLOYER = read_set(CONFIG_DIR / "sectors.csv")


# Job titles that appear as employer (swap candidates).
JOB_TITLE_AS_EMPLOYER = words("job_title_as_employer")


# Self-employed occupation words found in the employer field.
SELF_EMPLOYED_OCC_AS_EMP = words("self_employed_occ_as_emp")


# Employer words that themselves say the filer works for themselves.
SELF_EMPLOYMENT_WORDS = words("self_employment_words")


# Legal suffixes stripped from employer names for matching.
LEGAL_SUFFIX_RE = re.compile(
    r",?\s*\b"  # word boundary — don't strip inside words
    r"(LLC|LLP|L\.L\.C\.?|L\.L\.P\.?"
    r"|INC\.?|CORP\.?|LTD\.?"
    r"|LP|PLC"
    r"|P\.?C\.?|P\.?A\.?|P\.?L\.?L\.?C\.?|APLC|APC)"
    r"\s*\.?\s*$",
    re.IGNORECASE,
)


# Professions in employer boxes, each checked: none names a company.
OCCUPATION_TITLE_EMPLOYERS = read_set(CONFIG_DIR / "job_titles.csv")


# The shared answer to "is this string a company?".
NOT_REAL_EMPLOYER = words("not_real_employer_own") | (
    SKIP_EMPLOYERS
    | REFUSAL_EMPLOYERS
    | STATUS_SLIPS_BEYOND_RULE
    | OCCUPATION_TITLE_EMPLOYERS
    | OCCUPATION_AS_EMPLOYER
    | ROLE_AS_EMPLOYER
    | SECTOR_AS_EMPLOYER
    | JOB_TITLE_AS_EMPLOYER
    | SELF_EMPLOYED_OCC_AS_EMP
)


# A job title replacing a company: only title words.
JOB_TITLE_HEADS = read_set(CONFIG_DIR / "job_title_main_words.csv")
JOB_TITLE_MODIFIERS = read_set(CONFIG_DIR / "job_title_extra_words.csv")
