"""Occupation and employer spellings, categories and cross-field rules."""
from fec.config.constants import REFUSAL_EMPLOYERS as _REFUSAL_EMPLOYERS
from fec.config.not_employers import OCCUPATION_AS_EMPLOYER as _OCCUPATION_AS_EMPLOYER
from fec.config.tables import CONFIG_DIR, read_map, read_rows

# No HOMEMAKER: _deep_clean_employer fixes it with occupation.
EMPLOYER_NORMALIZE = read_map(CONFIG_DIR / "employer_spellings.csv")

# Employer spellings first; occupation rows override them.
OCCUPATION_NORMALIZE = {**EMPLOYER_NORMALIZE, **read_map(CONFIG_DIR / "occupation_spellings.csv")}

# Safe spellings applied by the record rules.
OCCUPATION_CANONICAL = read_map(CONFIG_DIR / "occupation_canonical.csv")

# Raw occupation fixes that also assign a category.
# A blank category means the fix sets no category.
OCCUPATION_FIXES = {row["filed"]: (row["occupation"], row["category"] or None)
                    for row in read_rows(CONFIG_DIR / "occupation_fixes.csv")}

# Curated occupation typo fixes, keyed after the style step.
OCCUPATION_TYPO_FIXES = read_map(CONFIG_DIR / "occupation_typos.csv")

# Each occupation's category; patterns categorize unlisted ones.
CATEGORY_OVERRIDES = read_map(CONFIG_DIR / "occupation_categories.csv")


# Refusals keep NOT DISCLOSED; other fixes to it are junk.
OCCUPATION_REFUSAL_INPUTS = frozenset({
    'PRIVATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'DECLINED TO ANSWER', 'MYOB', 'TMI',
})

# Employer status -> occupation/category when occupation is missing.
OCCUPATION_FROM_EMPLOYER = {
    'RETIRED': ('RETIRED', 'RETIRED'),
    'SELF-EMPLOYED': ('SELF-EMPLOYED', 'SELF-EMPLOYED'),
    'NOT EMPLOYED': ('NOT EMPLOYED', 'NOT EMPLOYED'),
}

# Occupation status -> employer during the field-level pass.
EMPLOYER_FROM_OCCUPATION = {
    'RETIRED': 'RETIRED',
    'NOT EMPLOYED': 'NOT EMPLOYED',
    'HOMEMAKER': 'NOT EMPLOYED',
    'SELF-EMPLOYED': 'SELF-EMPLOYED',
    'STUDENT': 'STUDENT',
}

# Donor consistency keeps homemaker distinct and handles aliases.
FINAL_EMPLOYER_FROM_OCCUPATION = {
    **EMPLOYER_FROM_OCCUPATION,
    'HOMEMAKER': 'HOMEMAKER',
    'HOUSEWIFE': 'HOMEMAKER',
    'UNEMPLOYED': 'NOT EMPLOYED',
}
EMPLOYER_FROM_CATEGORY = {
    category: FINAL_EMPLOYER_FROM_OCCUPATION[category]
    for category in ('RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'STUDENT', 'SELF-EMPLOYED')
}

SWAP_JOB_TITLES = _OCCUPATION_AS_EMPLOYER | frozenset({
    'MANAGER', 'PARTNER', 'ADMINISTRATOR',
    'PRESIDENT', 'VICE PRESIDENT', 'VP', 'DIRECTOR', 'EXECUTIVE',
    'OFFICER', 'CEO', 'CFO', 'COO', 'CTO', 'CMO',
})

OCCUPATION_KEYWORDS = _OCCUPATION_AS_EMPLOYER | frozenset({
    'ATTORNEY', 'LAWYER', 'PARTNER', 'MANAGER', 'ADMINISTRATOR',
    'PRESIDENT', 'DIRECTOR', 'EXECUTIVE', 'PHYSICIAN', 'ACCOUNTANT',
    'CONSULTANT', 'ENGINEER', 'OFFICER', 'ANALYST', 'ADVISOR',
    'COUNSEL', 'DENTIST', 'SURGEON', 'BROKER', 'AGENT',
    'CLAIMS', 'CPA', 'OWNER', 'MANAGING PARTNER', 'MANAGING DIRECTOR',
    'PAC MANAGER', 'INSURANCE BROKER', 'INSURANCE AGENT', 'REAL ESTATE',
    'INVESTMENT MANAGEMENT', 'FINANCE', 'MARKETING', 'MANAGEMENT',
    'SALES', 'CASHIER', 'DESIGNER', 'CATER', 'CATERER', 'WRITER',
    'ARTIST', 'PSYCHOLOGIST', 'THERAPIST', 'PROFESSOR',
})

FINAL_SHORT_OCCUPATIONS = frozenset({
    'MD', 'VP', 'PA', 'RN', 'IT', 'PR', 'HR', 'AI', 'DJ', 'DO', 'GP', 'GM',
})
FINAL_JUNK_EMPLOYERS = frozenset({
    'VARIOUS', 'JOB', 'A', 'EMP', 'N', 'MULTIPLE', 'OTHER',
})
FINAL_NULL_EMPLOYERS = _REFUSAL_EMPLOYERS | frozenset({
    'N/A', 'NA', 'N A', 'NONE', 'NOT APPLICABLE', 'NOT APPLICAABLE',
    'NOT DISCLOSED', 'INFORMATION REQUESTED',
    'INFORMATION REQUESTED PER BEST EFFORTS', 'PHYSICAN', 'SELP EMPLOYED',
})
WEB_ARTIFACT_OCCUPATIONS = frozenset({
    'LOADING', 'ACMIO', 'REMD', 'SWAP MEET', 'UOOO[',
    # A joke with no job; the employer fill restores it.
    'BIG BOY',
})

KNOWN_COMPANY_OCCUPATIONS = frozenset({
    'ARCH INSURANCE', 'BROWN & BROWN', 'DANZIGER & DE LLANO LLP',
    'NNA SERVICES LLC', 'BAKER TILLY', 'AMERICAN EXPRESS',
    'NFI INDUSTRIES', 'SKYRISE PROPERTIES', 'PAYROLL COMPANY',
})

HOMEMAKER_EMPLOYER_VALUES = frozenset({
    'HOMEMAKER', 'HOUSEWIFE', 'HOUSWIFE', 'HOUSEWIVES',
    'STAY AT HOME MOM', 'STAY AT HOME DAD',
})

