"""Raw FEC employer names to canonical forms."""

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

    # no HOMEMAKER: employer and occupation sync together
}


# abbreviation tokens; None means never auto-expand
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
    'INFO': 'INFORMATION',  # verified; \b guards protect INFOSYS/INFOSEC
    # ASSOC decided by context; rest lack expansions
    'ASSOC': None,
    'ASSOCS': None,
    'INTNL': None,
    'MKTG': None,
    'CONSTR': None,
    'DEVELOP': None,
    'TECHS': None,
}
