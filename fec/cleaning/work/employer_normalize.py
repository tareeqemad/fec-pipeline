"""Employer name normalization for grouping/matching and for display."""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.not_employers import OCCUPATION_AS_EMPLOYER
from fec.config.constants import SKIP_EMPLOYERS

from fec.cleaning.work.employer_aliases import reviewed_employer_name

# Status words that never become previous_employer.
_PREV_EMP_NOT_REAL = EMPLOYER_STATUS_VALUES | {
    'NOT-EMPLOYED', 'PRIVATE', 'N.A', 'N.A.',
    'RET', 'RET.', 'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.',
    'DR', 'DR.',
    'RETIREDE', 'RETIREFT', 'NOT', 'NOT IN WORKFORCE', 'NO EMPLOYER',
    'NOTEMPLOYS', 'NAT EMPLOYED', 'XXN', 'AUDIOLOGIST',
    'COMPANY NAME', 'COMPANY NAME (OPTIONAL)', 'NOT SPECIFIED',
}

# Suffix style only (", INC" -> " INC"); never strips.
_SUFFIX_COMMA_RE = re.compile(
    r'\s*,[,\s]*(?=(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA'
    r'|L\.L\.C|L\.L\.P|L\.P|P\.C|P\.A'
    r'|INCORPORATED|CORPORATION|COMPANY|LIMITED)\.?$)')
# "CO." keeps its period only as a mid-name abbreviation.
_SUFFIX_DEDOT = [(re.compile(r'\bL\.L\.C\.?$'), 'LLC'), (re.compile(r'\bL\.L\.P\.?$'), 'LLP'),
                 (re.compile(r'\bP\.C\.?$'), 'PC'), (re.compile(r'\bP\.A\.?$'), 'PA'),
                 (re.compile(r'\bL\.P\.?$'), 'LP'),
                 (re.compile(r'\bCO\.(?=\s+(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA)\b)'), 'CO'),
                 (re.compile(r'\bCO\.$'), 'CO')]

# Apply reviewed names and keep the original employer.
def normalize_employer_canonical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = _indiv_idx(df)
    emp = df.loc[indiv_idx, 'contributor_employer']
    values = emp[emp.notna() & ~emp.isin(SKIP_EMPLOYERS)]
    if 'contributor_employer_original' not in df:
        df['contributor_employer_original'] = df['contributor_employer'].copy()
    names = values.map(reviewed_employer_name)
    changed = names.ne(values)
    df.loc[values.index[changed], 'contributor_employer'] = names[changed]
    return df, int(changed.sum())


def normalize_employer_display_name(name) -> str | None:
    """Remove work-status text; keep the company's filed legal form."""
    if name is None:
        return None
    text = str(name).strip().upper()
    if not text or text in _PREV_EMP_NOT_REAL:
        return None
    match = re.match(r'^RETIRED\b[\s,-]+(.+)$', text)
    if match:
        text = match.group(1).strip()
        if not text or text in OCCUPATION_AS_EMPLOYER or text == 'MILITARY':
            return None
    text = re.sub(r'^C/O\s+', '', text)
    text = restyle_legal_suffix(text).rstrip('.,').strip()
    return reviewed_employer_name(text) or None


# clean up legal-suffix punctuation style without stripping it
def restyle_legal_suffix(name: str) -> str:
    """Style-only suffix cleanup; never strips, so never merges companies."""
    if name.rstrip('.').endswith('A.L.P'):   # a.l.p. Lighting, not a partnership
        return name
    text = _SUFFIX_COMMA_RE.sub(' ', name)
    for pattern, replacement in _SUFFIX_DEDOT:
        text = pattern.sub(replacement, text)
    return text
