"""
config/employers.py — Employer name normalization mappings.

Maps raw FEC employer strings → canonical forms.
Self-employed, retired, not-employed variants.
"""

EMPLOYER_NORMALIZE = {
    # Self-employed variants
    'SELF': 'SELF-EMPLOYED',
    'SELF EMPLOYED': 'SELF-EMPLOYED',
    'SELF-EMPLOYED': 'SELF-EMPLOYED',
    'SELF EMPLOYEED': 'SELF-EMPLOYED',
    'SELF EMPLYED': 'SELF-EMPLOYED',
    'SELF EMPLOYEE': 'SELF-EMPLOYED',
    'SELF EMLPOYED': 'SELF-EMPLOYED',
    'SELF EMPLOYES': 'SELF-EMPLOYED',
    'SELF EMPLOYYED': 'SELF-EMPLOYED',
    'SELF-EMPLOYER': 'SELF-EMPLOYED',
    'SELF EMP': 'SELF-EMPLOYED',
    'SELF -EMPLOYED': 'SELF-EMPLOYED',
    # ── Added: missed variants found in data ──
    'MYSELF': 'SELF-EMPLOYED',
    'SELFF': 'SELF-EMPLOYED',
    'SELF-EMPOYED': 'SELF-EMPLOYED',
    'SELF- EMPLOYED': 'SELF-EMPLOYED',
    'SELF  EMPLOYED': 'SELF-EMPLOYED',
    'SELF WMPLOYED': 'SELF-EMPLOYED',
    'SELF EMPLOYEF': 'SELF-EMPLOYED',
    'SELF-EMPLOYEED': 'SELF-EMPLOYED',
    'SELF EMPLOYED COMPANY OWNER': 'SELF-EMPLOYED',

    # Retired variants in employer field
    'RETIREE': 'RETIRED',
    'MOSTLY RETIRED': 'RETIRED',
    'UCLA RETIRED': 'RETIRED',

    # Not employed variants
    'NOT EMPLOYED': 'NOT EMPLOYED',
    'UNEMPLOYED': 'NOT EMPLOYED',
    'NOT WORKING': 'NOT EMPLOYED',
    'JOBLESS': 'NOT EMPLOYED',
    'I AM UNEMPLOYED': 'NOT EMPLOYED',
    'UNEMPLOYMENT': 'NOT EMPLOYED',

    # Homemaker in employer field — handled by _deep_clean_employer
    # to properly sync both employer and occupation fields
}
