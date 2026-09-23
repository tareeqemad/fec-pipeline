"""Employer name normalization for grouping/matching and for display."""
import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.constants import LEGAL_SUFFIX_RE
from fec.config.constants import OCCUPATION_AS_EMPLOYER
from fec.config.constants import SKIP_EMPLOYERS

from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS

# Trailing trademark/ticker/marker parentheticals - (R), (TM), (BEP), (SELF).
# 1-5 letters only: longer or digit-bearing tags are kept as disambiguators.
_TRAILING_PAREN_RE = re.compile(r'\s*\([A-Z]{1,5}\)\s*$')

# Status words/artifacts that must never end up in previous_employer;
# callers treat a match as "no previous employer known". The canonical status
# set plus display-stage extras (punctuated variants, honorifics, stumps).
_PREV_EMP_NOT_REAL = EMPLOYER_STATUS_VALUES | {
    'NOT-EMPLOYED', 'PRIVATE', 'N.A', 'N.A.',
    'RET', 'RET.', 'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.',
    'DR', 'DR.',
    'RETIREDE', 'RETIREFT', 'NOT', 'NOT IN WORKFORCE', 'NO EMPLOYER',
    'NOTEMPLOYS', 'NAT EMPLOYED', 'XXN', 'AUDIOLOGIST',
    'COMPANY NAME', 'COMPANY NAME (OPTIONAL)', 'NOT SPECIFIED',
}

# A name never legitimately ends in a bare connector - these are FEC 38-char
# truncation stumps ("SOUTHERN GLAZER'S WINE AND" -> "... WINE").
_TRAILING_CONNECTOR_RE = re.compile(
    r'\s+(?:AT THE|OF THE|ATTORNEYS AT|AT|OF|FOR|AND|&|-)$')

# Suffix STYLE normalization only (", INC" -> " INC", "P.C" -> "PC") - never
# strips, so it can never merge two companies. Tolerates raw FEC punctuation
# ("CO,, INC." / "CO ,INC"): the comma may repeat and a trailing period is
# stripped later in the pipeline, so both forms must still match here.
_SUFFIX_COMMA_RE = re.compile(
    r'\s*,[,\s]*(?=(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA'
    r'|L\.L\.C|L\.L\.P|L\.P|P\.C|P\.A'
    r'|INCORPORATED|CORPORATION|COMPANY|LIMITED)\.?$)')
# "CO." keeps its period only when it is a mid-name abbreviation nobody styles
# ("CO. INC" / "CO. LLP" / trailing "CO."): dotted initials such as R.A. or U.S. stay.
_SUFFIX_DEDOT = [(re.compile(r'\bL\.L\.C\.?$'), 'LLC'), (re.compile(r'\bL\.L\.P\.?$'), 'LLP'),
                 (re.compile(r'\bP\.C\.?$'), 'PC'), (re.compile(r'\bP\.A\.?$'), 'PA'),
                 (re.compile(r'\bL\.P\.?$'), 'LP'),
                 (re.compile(r'\bCO\.(?=\s+(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA|LPA)\b)'), 'CO'),
                 (re.compile(r'\bCO\.$'), 'CO')]

# Brands whose official form uses `&`. Normalization turns `&` into ` AND `
# for unknown firms; for these we keep the ampersand ("AT AND T" is
# unreadable). Hand-curated - do NOT auto-detect.
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

# Common nouns that are real company names once suffix-stripped ("HOUSING INC"
# stripped to "HOUSING" reads as a noun and makes the resolver hallucinate).
# Evidence-driven: add a word only when a real filer used it as a company name
# and the stripped form is too ambiguous to carry that meaning alone.
GENERIC_WORDS_PROTECTED = frozenset({
    'HOUSING',
    'GOOD HEALTH',
    'TERRA',
})


def _brand_key(s: str) -> str:
    """Alphanumeric-only uppercase key so every raw variant of a brand matches one AMPERSAND_BRANDS entry."""
    s = s.upper()
    s = re.sub(r'\s+AND\s+', '', s)
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s


_AMPERSAND_BRAND_LOOKUP = {_brand_key(brand): brand for brand in AMPERSAND_BRANDS}


def _preserve_brand(upper_value: str) -> str | None:
    """Return the canonical `&` form if `upper_value` matches a known brand."""
    return _AMPERSAND_BRAND_LOOKUP.get(_brand_key(upper_value))


def _strip_trailing_connectors(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_CONNECTOR_RE.sub('', s).strip()
    return s


def normalize_employer_canonical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Add employer_name_normalized (legal suffixes stripped, &/AND and whitespace normalized) for grouping, not display; returns (df, n_normalized)."""
    indiv_idx = _indiv_idx(df)
    emp = df.loc[indiv_idx, 'contributor_employer']

    has_emp = emp.notna() & ~emp.isin(SKIP_EMPLOYERS)
    target = indiv_idx[has_emp]

    if len(target) == 0:
        df['employer_name_normalized'] = pd.Series(dtype='object', index=df.index)
        return df, 0

    raw = emp[has_emp]

    stripped = raw.str.replace(_TRAILING_PAREN_RE, '', regex=True)
    stripped = stripped.str.replace(LEGAL_SUFFIX_RE, '', regex=True)
    # & $ -> AND for non-brand forms; brand preservation happens via the
    # hybrid rule below through normalize_employer_display_name
    stripped = stripped.str.replace(r'[&$]', ' AND ', regex=True)
    stripped = stripped.str.replace(r'\s+', ' ', regex=True).str.strip()
    stripped = stripped.str.rstrip('.,').str.strip()

    # Keep the suffix when stripping leaves an ambiguous generic or <=2 chars.
    # "JB INC" is a company; "JB" would be mistaken for short junk.
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

    # keep the original suffix-bearing name for display (e.g. previous_employer)
    if 'contributor_employer_original' not in df.columns:
        df['contributor_employer_original'] = df['contributor_employer'].copy()

    overwrite = target[changed]
    df.loc[overwrite, 'contributor_employer'] = stripped[changed]

    return df, int(changed.sum())


def normalize_employer_display_name(name) -> str | None:
    """Apply contributor_employer's normalization to an ad-hoc value (e.g. resolve-stage previous_employer); None for missing/status values."""
    if name is None:
        return None
    text = str(name).strip()
    if not text or text.upper() in _PREV_EMP_NOT_REAL:
        return None

    text = text.upper()
    # 'RETIRED - ORTHOCAROLINA': the remainder IS the previous employer;
    # a bare profession word ('RETIRED PHYSICIAN') is not a company
    match = re.match(r'^RETIRED\b[\s,-]+(.+)$', text)
    if match:
        text = match.group(1).strip()
        if not text or text in OCCUPATION_AS_EMPLOYER or text == 'MILITARY':
            return None
    text = _TRAILING_PAREN_RE.sub('', text).strip()

    # strip legal suffixes, possibly stacked ("CO., INC.")
    original = re.sub(r'\s+', ' ', text).strip()
    stripped = text
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = LEGAL_SUFFIX_RE.sub('', stripped).strip()
    # Ambiguous generics and very short names keep their suffix.
    if (stripped != original
            and (stripped in GENERIC_WORDS_PROTECTED or len(stripped) <= 2)):
        clean = re.sub(r',\s+', ' ', original)
        clean = re.sub(r'\s+', ' ', clean).strip().rstrip('.').rstrip(',').strip()
        return clean
    text = stripped
    # Brand override: known `&`-brands are forced to canonical form; otherwise
    # keep the filer's spelling - deliberately NO blanket `&` -> `AND` here,
    # because that mangles real brand names.
    brand = _preserve_brand(text)
    if brand:
        return brand
    text = re.sub(r'\s+', ' ', text).strip().rstrip(',').rstrip('.').strip()
    text = re.sub(r'^C/O\s+', '', text)
    text = _strip_trailing_connectors(text)
    # converge on the curated canonical spelling so the previous_employer
    # path can never re-split a company the dict merged
    text = EMPLOYER_SYNONYMS.get(text, text)
    return text or None


def restyle_legal_suffix(name: str) -> str:
    """Style-only suffix cleanup (', INC' -> ' INC', 'P.C' -> 'PC'); never strips, so it can never merge two companies."""
    if name.rstrip('.').endswith('A.L.P'):   # a.l.p. Lighting, not a partnership
        return name
    text = _SUFFIX_COMMA_RE.sub(' ', name)
    for pattern, replacement in _SUFFIX_DEDOT:
        text = pattern.sub(replacement, text)
    return text
