"""Employer/occupation misplacement fixes: swapped fields and self-employment consistency."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.work.normalize import (
    _categorize,
    categorize_final,
    swap_employer_and_occupation,
)
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS, STATUS_WORDS
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP,
    SELF_EMPLOYMENT_WORDS,
)
from fec.cleaning.work.clean import occupation_is_company
from fec.cleaning.work.status import is_job_title

# HEALTH names a company unless a person noun follows.
_HEALTH_JOB_TAIL = (
    r'\s+(?:CARE\s+)?(?:INDUSTRY\s+)?'
    r'(?:COUNSELOR|THERAPIST|CLINICIAN|PROFESSIONAL|COACH|AIDE|NURSE|WORKER'
    r'|PRACTITIONER|PROVIDER|CONSULTANT|ADVISOR|EDUCATOR'
    r'|ADMINISTRATOR|ADM|EXECUTIVE|EXEC|OWNER|INVESTOR|SPECIALIST|TECHNICIAN'
    r'|ASSISTANT|MANAGER|DIRECTOR|OFFICER|ANALYST|STRATEGIST)\b'
)

# Markers of a real legal entity name, not an industry.
_COMPANY_NAME_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C\.?|L\.L\.P\.?|INC\.?|CORP\.?|CO\.?|'
    r'LTD\.?|LP|PLC|P\.?C\.?|P\.?A\.?|COMPANY|CORPORATION|'
    r'HOLDINGS|GROUP|PARTNERS|VENTURES|FUND|CAPITAL|'
    r'ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY|ADVISORS|'
    r'INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|'
    rf'HEALTH(?:CARE)?(?!{_HEALTH_JOB_TAIL})|SOLUTIONS|SERVICES|SYSTEMS|TECHNOLOGIES|'
    r'MANAGEMENT|FOUNDATION|UNIVERSITY|COLLEGE|HOSPITAL|CENTER|'
    r'INTERNATIONAL|GLOBAL|TRUST)\b'
    # OR any ampersand between two words (BROWN & BROWN):
    r'|\b\w+\s*&\s*\w+\b',
    re.IGNORECASE,
)

_SKIP_OCC = SKIP_OCCUPATIONS | {'OWNER', 'CEO', 'PRESIDENT'}
_KNOWN_OCCUPATIONS = (
    OCCUPATION_AS_EMPLOYER
    | ROLE_AS_EMPLOYER
    | JOB_TITLE_AS_EMPLOYER
    | SELF_EMPLOYED_OCC_AS_EMP
    | {"ADVOCATE", "SCULPTOR"}
)

# Organisation titles ("CEO of X") filed alone mean swapped boxes.
ORG_TITLE_ROLES = frozenset({
    'CEO', 'COO', 'PRESIDENT', 'PRESIDENT CEO', 'VICE PRESIDENT', 'VP',
    'CHAIRMAN', 'FOUNDER', 'DEPUTY CEO', 'SENIOR DIRECTOR',
    'MANAGING DIRECTOR', 'MANAGING PARTNER', 'EXEC DIRECTOR',
})
# placeholders that categorize OTHER without naming anything
_NO_INFO_OCCUPATIONS = frozenset({'EMPLOYED', 'NOT DISCLOSED', 'OTHER'})


# build each row's filer name as written on the filing
def _filer_names(df: pd.DataFrame) -> pd.Series:
    """The filer's name as written on each row."""
    names = df['contributor_name']
    return names.fillna('').astype(str).str.strip().str.upper()


# find candidates whose occupation text is really an organization name
def _occupation_names_no_job(df: pd.DataFrame, occ: pd.Series, candidates: pd.Series) -> pd.Series:
    """Rows whose occupation text is no job, so an organisation."""
    if not candidates.any():
        return candidates
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    names = _filer_names(df)
    filed = is_indiv & occ.ne('')
    filers = names[filed].groupby(occ[filed]).nunique()
    only_on_candidates = ~occ.isin(set(occ[filed & ~candidates]))
    one_filer = occ.map(filers).fillna(0).eq(1)

    uncategorised = pd.Series(False, index=occ.index)
    cand_occ = occ[candidates]
    uncategorised.loc[cand_occ.index] = categorize_final(cand_occ).eq('OTHER').to_numpy()
    upper = occ.str.upper()
    return (
        candidates
        & occ.ne('')
        & uncategorised
        & only_on_candidates
        & one_filer
        & ~upper.isin(_KNOWN_OCCUPATIONS)
        & ~upper.isin(SKIP_OCCUPATIONS)
        & ~upper.isin(NOT_REAL_EMPLOYER)
        & ~upper.isin(_NO_INFO_OCCUPATIONS)
    )


_JOBS_IN_EMPLOYER = (
    OCCUPATION_AS_EMPLOYER | ROLE_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER | SELF_EMPLOYED_OCC_AS_EMP
)
_STATUS_OCCUPATIONS = frozenset({'RETIRED', 'NOT EMPLOYED'})


def settle_job_in_employer(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """A job word in the employer box, decided in one place.

    - The occupation names a company: swap the two.
    - The occupation is empty or SELF-EMPLOYED: the word becomes the occupation.
    - The occupation is RETIRED or NOT EMPLOYED: the word becomes the
      occupation and the employer takes the status.
    - Otherwise the filed occupation stays.
    The employer becomes SELF-EMPLOYED only when the filing says so: a
    SELF-EMPLOYED occupation or a self-employment word such as OWNER.
    Without that the employer is left unknown.
    """
    rows = df.index[df['entity_type'].eq('INDIVIDUAL')]
    emp = df.loc[rows, 'contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    occ = df.loc[rows, 'contributor_occupation'].fillna('').astype(str).str.strip().str.upper()
    job = emp.isin(_JOBS_IN_EMPLOYER)
    if not job.any():
        return df, 0

    full_occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()
    org_title = df.index.isin(rows[job & emp.isin(ORG_TITLE_ROLES)])
    named_org = _occupation_names_no_job(df, full_occ, pd.Series(org_title, index=df.index))
    occ_is_job = occ.isin(_JOBS_IN_EMPLOYER) | occ.map(is_job_title)
    company = job & (
        occupation_is_company(emp, occ, _JOBS_IN_EMPLOYER)
        | (occ.str.contains(_COMPANY_NAME_RE, na=False) & ~occ_is_job)
        | named_org.loc[rows]
    )
    says_self = (
        occ.eq('SELF-EMPLOYED') | occ.isin(SELF_EMPLOYMENT_WORDS)
        | emp.isin(SELF_EMPLOYMENT_WORDS) | _categorize(emp).eq('SELF-EMPLOYED')
    )
    empty = occ.isin({'', 'SELF-EMPLOYED'}) | (occ.isin(SKIP_EMPLOYERS) & ~occ.isin(STATUS_WORDS))
    status = occ.isin(_STATUS_OCCUPATIONS)
    move = job & ~company & (empty | status)
    other = job & ~company & ~move

    if company.any():
        swap_employer_and_occupation(df, rows[company])
    if move.any():
        moved = rows[move]
        words = df.loc[moved, 'contributor_employer'].copy()
        df.loc[moved, 'contributor_occupation'] = words
        df.loc[moved, 'occupation_category'] = _categorize(words)
        df.loc[moved, 'contributor_employer'] = occ[move].where(status[move], np.nan)
        df.loc[rows[move & ~status & says_self], 'contributor_employer'] = 'SELF-EMPLOYED'
    if other.any():
        df.loc[rows[other], 'contributor_employer'] = np.nan
        df.loc[rows[other & says_self], 'contributor_employer'] = 'SELF-EMPLOYED'
    return df, int(job.sum())
