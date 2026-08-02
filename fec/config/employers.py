"""Employer name normalization: raw FEC employer strings to canonical forms."""

EMPLOYER_NORMALIZE = {
    # self-employed
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
    'MYSELF': 'SELF-EMPLOYED',
    'SELFF': 'SELF-EMPLOYED',
    'SELF-EMPOYED': 'SELF-EMPLOYED',
    'SELF- EMPLOYED': 'SELF-EMPLOYED',
    'SELF  EMPLOYED': 'SELF-EMPLOYED',
    'SELF WMPLOYED': 'SELF-EMPLOYED',
    'SELF EMPLOYEF': 'SELF-EMPLOYED',
    'SELF-EMPLOYEED': 'SELF-EMPLOYED',
    'SELF EMPLOYED COMPANY OWNER': 'SELF-EMPLOYED',
    'SOLE PROPRIETOR': 'SELF-EMPLOYED',

    # retired
    'RETIREE': 'RETIRED',
    'MOSTLY RETIRED': 'RETIRED',
    'UCLA RETIRED': 'RETIRED',

    # not employed
    'NOT EMPLOYED': 'NOT EMPLOYED',
    'UNEMPLOYED': 'NOT EMPLOYED',
    'NOT WORKING': 'NOT EMPLOYED',
    'JOBLESS': 'NOT EMPLOYED',
    'I AM UNEMPLOYED': 'NOT EMPLOYED',
    'UNEMPLOYMENT': 'NOT EMPLOYED',

    # Homemaker deliberately absent — _deep_clean_employer syncs employer + occupation together.
}


# Employer abbreviation tokens - THE one table, three consumers: the cleaner
# auto-expands tokens that have an expansion, quality_scan surfaces every
# token for human review, and the DB healthcheck warns on names still
# carrying one. expansion=None marks a token as ambiguous or unverified -
# surfaced and warned about, but never auto-expanded.
EMPLOYER_ABBREVIATIONS = {
    'MGMT': 'MANAGEMENT',
    'MGMNT': 'MANAGEMENT',
    'MGT': 'MANAGEMENT',
    'INV': 'INVESTMENT',    # verified by hand for this dataset
    'INTL': 'INTERNATIONAL',
    'MFG': 'MANUFACTURING',
    'GRP': 'GROUP',
    'SVCS': 'SERVICES',
    'SVC': 'SERVICES',
    'INFO': 'INFORMATION',  # verified by hand; the \b guards protect INFOSYS/INFOSEC
    # ASSOC splits between ASSOCIATES and ASSOCIATION - decided contextually
    # in expand_employer_associates; the rest lack a verified single expansion
    'ASSOC': None,
    'ASSOCS': None,
    'INTNL': None,
    'MKTG': None,
    'CONSTR': None,
    'DEVELOP': None,
    'TECHS': None,
}
