"""Employer name normalization for grouping and display."""
import re

import pandas as pd

from fec.cleaning._helpers import individual_index
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.config.not_employers import OCCUPATION_AS_EMPLOYER
from fec.config.constants import SKIP_EMPLOYERS

from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS

# trailing 1-5 letter tags; longer ones kept
TRAILING_PAREN_RE = re.compile(r'\s*\([A-Z]{1,5}\)\s*$')

# status words never kept as previous_employer
_PREV_EMP_NOT_REAL = EMPLOYER_STATUS_VALUES | {
    'NOT-EMPLOYED', 'PRIVATE', 'N.A', 'N.A.',
    'RET', 'RET.', 'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.',
    'DR', 'DR.',
    'RETIREDE', 'RETIREFT', 'NOT', 'NOT IN WORKFORCE', 'NO EMPLOYER',
    'NOTEMPLOYS', 'NAT EMPLOYED', 'XXN', 'AUDIOLOGIST',
    'COMPANY NAME', 'COMPANY NAME (OPTIONAL)', 'NOT SPECIFIED',
}

# bare trailing connector means a truncation stump
_TRAILING_CONNECTOR_RE = re.compile(
    r'\s+(?:AT THE|OF THE|ATTORNEYS AT|AT|OF|FOR|AND|&|-)$')

# style only, never strips; tolerates raw punctuation
_SUFFIX_COMMA_RE = re.compile(
    r'\s*,[,\s]*(?=(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA'
    r'|L\.L\.C|L\.L\.P|L\.P|P\.C|P\.A'
    r'|INCORPORATED|CORPORATION|COMPANY|LIMITED)\.?$)')
# dedot suffixes; initials like U.S. stay
_SUFFIX_DEDOT = [(re.compile(r'\bL\.L\.C\.?$'), 'LLC'), (re.compile(r'\bL\.L\.P\.?$'), 'LLP'),
                 (re.compile(r'\bP\.C\.?$'), 'PC'), (re.compile(r'\bP\.A\.?$'), 'PA'),
                 (re.compile(r'\bL\.P\.?$'), 'LP'),
                 (re.compile(r'\bCO\.(?=\s+(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA)\b)'), 'CO'),
                 (re.compile(r'\bCO\.$'), 'CO')]

# brands that keep `&`; hand-curated, never auto-detect
AMPERSAND_BRANDS = frozenset({
    # tech/telecom
    'AT&T', 'AT&T WIRELESS', 'AT&T MOBILITY',
    # retail/consumer
    'H&M', 'BATH & BODY WORKS', 'CRATE & BARREL', 'BARNES & NOBLE',
    'B&H PHOTO', 'DOLCE & GABBANA', 'TIFFANY & CO',
    'SMITH & WESSON', 'BLACK & DECKER',
    # finance/accounting
    'S&P GLOBAL', 'S&P', 'J.P. MORGAN CHASE & CO',
    'ERNST & YOUNG', 'PRICEWATERHOUSECOOPERS', 'H&R BLOCK',
    'PROCTER & GAMBLE', 'P&G', 'JOHNSON & JOHNSON',
    'BROWN & BROWN',
    'AETNA LIFE & CASUALTY',
    # law firms
    'KIRKLAND & ELLIS',
    'LATHAM & WATKINS',
    'SKADDEN ARPS SLATE MEAGHER & FLOM',
    'SULLIVAN & CROMWELL',
    'SIMPSON THACHER & BARTLETT',
    'DAVIS POLK & WARDWELL',
    'WACHTELL LIPTON ROSEN & KATZ',
    'CLEARY GOTTLIEB STEEN & HAMILTON',
    'CRAVATH SWAINE & MOORE',
    'DEBEVOISE & PLIMPTON',
    'PAUL WEISS RIFKIND WHARTON & GARRISON',
    'ROPES & GRAY',
    'GIBSON DUNN & CRUTCHER',
    'MCDERMOTT WILL & EMERY',
    'COVINGTON & BURLING',
    'VINSON & ELKINS',
    'WINSTON & STRAWN',
    'KILPATRICK TOWNSEND & STOCKTON',
    'WILLKIE FARR & GALLAGHER',
    'BAKER & HOSTETLER',
    'BAKER & MCKENZIE',
    'K&L GATES',
    'IRELL & MANELLA',
    'STROOCK & STROOCK & LAVAN',
    'ARNOLD & PORTER',
    'JACOBY & MEYERS',
    'RICHARDS LAYTON & FINGER',
    # construction/equipment
    'H&E EQUIPMENT SERVICES', 'H&E DO IT YOURSELF CENTER',
})

# stripped, these read as nouns; keep suffix
GENERIC_WORDS_PROTECTED = frozenset({
    'HOUSING',
    'GOOD HEALTH',
    'TERRA',
})


# build alphanumeric-only uppercase key for brand matching
def _brand_key(s: str) -> str:
    s = s.upper()
    s = re.sub(r'\s+AND\s+', '', s)
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s


_AMPERSAND_BRAND_LOOKUP = {_brand_key(brand): brand for brand in AMPERSAND_BRANDS}


# canonical ampersand form of a known brand
def _preserve_brand(upper_value: str) -> str | None:
    return _AMPERSAND_BRAND_LOOKUP.get(_brand_key(upper_value))


# repeatedly strip trailing truncation-stump connector words
def _strip_trailing_connectors(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_CONNECTOR_RE.sub('', s).strip()
    return s


# build employer_name_normalized for grouping, not display
def normalize_employer_canonical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    emp = df.loc[indiv_idx, 'contributor_employer']

    has_emp = emp.notna() & ~emp.isin(SKIP_EMPLOYERS)
    target = indiv_idx[has_emp]

    if len(target) == 0:
        df['employer_name_normalized'] = pd.Series(dtype='object', index=df.index)
        return df, 0

    raw = emp[has_emp]

    stripped = raw.str.replace(TRAILING_PAREN_RE, '', regex=True)
    stripped = stripped.str.replace(LEGAL_SUFFIX_RE, '', regex=True)
    stripped = stripped.str.replace(r'[&$]', ' AND ', regex=True)
    stripped = stripped.str.replace(r'\s+', ' ', regex=True).str.strip()
    stripped = stripped.str.rstrip('.,').str.strip()

    # ambiguous generic or <=2 chars keeps suffix
    raw_upper = raw.str.upper().str.strip()
    needs_protection = (
        (stripped != raw_upper)
        & (stripped.isin(GENERIC_WORDS_PROTECTED) | stripped.str.len().le(2))
    )
    if needs_protection.any():
        for idx in raw[needs_protection].index:
            protected = normalize_employer_display_name(raw.loc[idx])
            if protected:
                stripped.loc[idx] = protected

    df['employer_name_normalized'] = pd.Series(dtype='object', index=df.index)
    changed = stripped != raw
    df.loc[target, 'employer_name_normalized'] = stripped

    # keep suffix-bearing original for display
    if 'contributor_employer_original' not in df.columns:
        df['contributor_employer_original'] = df['contributor_employer'].copy()

    overwrite = target[changed]
    df.loc[overwrite, 'contributor_employer'] = stripped[changed]

    return df, int(changed.sum())


# normalize one employer value; None for status
def normalize_employer_display_name(name) -> str | None:
    if name is None:
        return None
    text = str(name).strip()
    if not text or text.upper() in _PREV_EMP_NOT_REAL:
        return None

    text = text.upper()
    # remainder after RETIRED is the previous employer
    match = re.match(r'^RETIRED\b[\s,-]+(.+)$', text)
    if match:
        text = match.group(1).strip()
        if not text or text in OCCUPATION_AS_EMPLOYER or text == 'MILITARY':
            return None
    text = TRAILING_PAREN_RE.sub('', text).strip()

    # strip legal suffixes, possibly stacked ("CO., INC.")
    original = re.sub(r'\s+', ' ', text).strip()
    stripped = text
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = LEGAL_SUFFIX_RE.sub('', stripped).strip()
    # ambiguous generics and short names keep suffix
    if (stripped != original
            and (stripped in GENERIC_WORDS_PROTECTED or len(stripped) <= 2)):
        clean = re.sub(r',\s+', ' ', original)
        clean = re.sub(r'\s+', ' ', clean).strip().rstrip('.').rstrip(',').strip()
        return clean
    text = stripped
    # no blanket AND swap; it mangles brands
    brand = _preserve_brand(text)
    if brand:
        return brand
    text = re.sub(r'\s+', ' ', text).strip().rstrip(',').rstrip('.').strip()
    text = re.sub(r'^C/O\s+', '', text)
    text = _strip_trailing_connectors(text)
    # use synonyms so previous_employer never re-splits companies
    text = EMPLOYER_SYNONYMS.get(text, text)
    return text or None


# restyle suffix punctuation; never strips, never merges
def restyle_legal_suffix(name: str) -> str:
    if name.rstrip('.').endswith('A.L.P'):   # a.l.p. Lighting, not a partnership
        return name
    text = _SUFFIX_COMMA_RE.sub(' ', name)
    for pattern, replacement in _SUFFIX_DEDOT:
        text = pattern.sub(replacement, text)
    return text
