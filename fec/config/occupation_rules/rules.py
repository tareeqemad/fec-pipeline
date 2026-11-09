"""Occupation and employer spellings, categories and cross-field rules."""
from fec.config.constants import REFUSAL_EMPLOYERS as _REFUSAL_EMPLOYERS
from fec.config.not_employers import OCCUPATION_AS_EMPLOYER as _OCCUPATION_AS_EMPLOYER
from fec.config.tables import CONFIG_DIR, read_map
from fec.env import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_OVERRIDES_CSV

_nan = float("nan")

# No HOMEMAKER: _deep_clean_employer fixes it with occupation.
EMPLOYER_NORMALIZE = read_map(CONFIG_DIR / "employer_spellings.csv")

# Occupation corrections: a filed spelling, abbreviation or typo and the
# occupation it means; [CLEAR] marks a placeholder that means no occupation.
# One table, applied when the text is first read and again after each
# step that can expose a listed form.
CLEAR = "[CLEAR]"
OCCUPATION_CORRECTIONS = {
    written: (None if occupation == CLEAR else occupation)
    for written, occupation in read_map(CONFIG_DIR / "occupation_corrections.csv").items()
}
# The same corrections without the placeholders, for text replacement.
TEXT_CORRECTIONS = {written: occupation for written, occupation in OCCUPATION_CORRECTIONS.items() if occupation}
# Employer spellings first; occupation rows override them.
OCCUPATION_NORMALIZE = {
    **EMPLOYER_NORMALIZE,
    **{written: (_nan if occupation is None else occupation) for written, occupation in OCCUPATION_CORRECTIONS.items()},
}

# Each occupation's category: the model's answers, then the reviewed
# overrides on top. Unlisted texts are classified by the model when the
# pipeline runs (fec.cleaning.work.categorize); patterns are the fallback.
CATEGORY_OVERRIDES = {
    **(read_map(OCCUPATION_CATEGORY_CACHE_CSV) if OCCUPATION_CATEGORY_CACHE_CSV.exists() else {}),
    **(read_map(OCCUPATION_CATEGORY_OVERRIDES_CSV) if OCCUPATION_CATEGORY_OVERRIDES_CSV.exists() else {}),
}


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

