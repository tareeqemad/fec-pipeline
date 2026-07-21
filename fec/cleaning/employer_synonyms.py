"""
cleaning/employer_synonyms.py — Employer name normalization and synonym merging.

Extracted from enhancements.py. Contains:
  - EMPLOYER_SYNONYMS mapping (221 entries from donor-overlap analysis)
  - Employer canonical normalization (strip legal suffixes)
  - Employer synonym application
  - Occupation-as-employer fix
  - Post-enhancement employer re-canonicalization
  - Mid-string suffix cleanup
"""
import re
from collections import defaultdict
from typing import Tuple

import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.cleaning.occupations import _categorize
from fec.log import get_logger

logger = get_logger(__name__)


# Import centralized constants
from fec.config.constants import LEGAL_SUFFIX_RE as _LEGAL_SUFFIX_RE
from fec.config.constants import SKIP_EMPLOYERS as _SKIP_EMPLOYERS


def normalize_employer_canonical(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Add employer_name_normalized — strips legal suffixes, normalizes
    &/AND, collapses whitespace. For grouping/matching, not display.

    Examples:
        KIRKLAND & ELLIS LLP  → KIRKLAND AND ELLIS
        KIRKLAND&ELLIS         → KIRKLAND AND ELLIS
        KIRKLAND $ ELLIS       → KIRKLAND AND ELLIS
        GOLDMAN SACHS & CO    → GOLDMAN SACHS AND CO

    Returns: (df, n_normalized)
    """
    ii = _indiv_idx(df)
    emp = df.loc[ii, 'contributor_employer']

    # Skip NaN and status employers
    has_emp = emp.notna() & ~emp.isin(_SKIP_EMPLOYERS)
    target = ii[has_emp]

    if len(target) == 0:
        df['employer_name_normalized'] = pd.Series(dtype='object', index=df.index)
        return df, 0

    raw = emp[has_emp].copy()

    # Strip trailing trademark / ticker / marker parentheticals — (R), (TM),
    # (BEP), (ITCI), (SELF), (US)... Mirrors the same rule in
    # normalize_employer_display_name so the main pipeline strips them too.
    stripped = raw.str.replace(r'\s*\([A-Z]{1,5}\)\s*$', '', regex=True)
    # Strip legal suffixes
    stripped = stripped.str.replace(_LEGAL_SUFFIX_RE, '', regex=True)
    # Normalize & $ → AND (for non-brand forms; brand preservation handled
    # by the hybrid rule below via normalize_employer_display_name)
    stripped = stripped.str.replace(r'[&$]', ' AND ', regex=True)
    # Collapse whitespace
    stripped = stripped.str.replace(r'\s+', ' ', regex=True).str.strip()
    # Remove trailing comma/period
    stripped = stripped.str.rstrip('.,').str.strip()

    # Hybrid rule: when stripping reduces the name to a single generic
    # word (HOUSING etc. — see GENERIC_WORDS_PROTECTED below), keep the
    # original with its suffix. Without this, "HOUSING INC" would become
    # just "HOUSING" and read as a common noun instead of Andrew
    # Schwartzberg's firm. Applied in Python loop over the rows that
    # would otherwise collapse (only ~65 rows in current data — cheap).
    raw_upper = raw.str.upper().str.strip()
    needs_protection = (
        (stripped != raw_upper)
        & stripped.isin(GENERIC_WORDS_PROTECTED)
        & ~stripped.str.contains(r'\s', regex=True, na=True)
    )
    if needs_protection.any():
        for idx in raw[needs_protection].index:
            protected = normalize_employer_display_name(raw.loc[idx])
            if protected:
                stripped.loc[idx] = protected

    normed = stripped

    df['employer_name_normalized'] = pd.Series(dtype='object', index=df.index)
    changed = normed != raw
    df.loc[target, 'employer_name_normalized'] = normed

    # Preserve original employer name with LLC/INC for display (e.g. previous_employer)
    if 'contributor_employer_original' not in df.columns:
        df['contributor_employer_original'] = df['contributor_employer'].copy()

    # Write normalized name into contributor_employer for matching/synonyms
    overwrite = target[changed]
    df.loc[overwrite, 'contributor_employer'] = normed[changed]

    return df, int(changed.sum())


# Well-known brand names that use `&` in their official form.
# Normalization collapses `&` to ` AND ` for unknown firms, but for
# brands in this set we preserve the ampersand — "AT AND T" is
# unreadable, "AT&T" is the name. Hand-curated; do NOT auto-detect.
# Canonical form is the brand's own stylization (UPPER for consistency
# with the rest of the pipeline).
AMPERSAND_BRANDS = frozenset({
    # Tech / telecom
    'AT&T', 'AT&T WIRELESS', 'AT&T MOBILITY',
    # Retail / consumer
    'H&M', 'BATH & BODY WORKS', 'CRATE & BARREL', 'BARNES & NOBLE',
    'B&H PHOTO', 'DOLCE & GABBANA', 'TIFFANY & CO',
    'SMITH & WESSON', 'BLACK & DECKER',
    # Finance / accounting
    'S&P GLOBAL', 'S&P', 'J.P. MORGAN CHASE & CO',
    'ERNST & YOUNG', 'PRICEWATERHOUSECOOPERS', 'H&R BLOCK',
    'PROCTER & GAMBLE', 'P&G', 'JOHNSON & JOHNSON',
    'BROWN & BROWN',               # NYSE: BRO insurance
    'AETNA LIFE & CASUALTY',
    # Law firms — AmLaw top 100 + well-known boutiques
    'KIRKLAND & ELLIS',                            # AmLaw #1
    'LATHAM & WATKINS',                            # AmLaw top 3
    'SKADDEN ARPS SLATE MEAGHER & FLOM',           # AmLaw top 10
    'SULLIVAN & CROMWELL',                         # AmLaw top 20
    'SIMPSON THACHER & BARTLETT',                  # AmLaw top 20
    'DAVIS POLK & WARDWELL',                       # AmLaw top 20
    'WACHTELL LIPTON ROSEN & KATZ',                # AmLaw top 30
    'CLEARY GOTTLIEB STEEN & HAMILTON',            # AmLaw top 30
    'CRAVATH SWAINE & MOORE',                      # AmLaw top 30
    'DEBEVOISE & PLIMPTON',                        # AmLaw top 30
    'PAUL WEISS RIFKIND WHARTON & GARRISON',       # AmLaw top 30
    'ROPES & GRAY',                                # AmLaw top 30
    'GIBSON DUNN & CRUTCHER',                      # AmLaw top 20
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
    # Construction / equipment
    'H&E EQUIPMENT SERVICES', 'H&E DO IT YOURSELF CENTER',
})

# Common English nouns that become ambiguous when stripped of their
# legal suffix. `HOUSING INC` (Andrew Schwartzberg's firm) is a real
# US company, verified on his LinkedIn and Pennant Housing Group
# profile — but stripped to just "HOUSING" it reads as a common noun
# and the resolver (AI + addresses) treats it as such, hallucinating
# fake locations. Keeping the suffix preserves the reading as a
# company name. Hand-curated, evidence-driven — add a word here only
# when a real filer used it as a company name AND the stripped form
# is too ambiguous to carry that meaning alone.
GENERIC_WORDS_PROTECTED = frozenset({
    'HOUSING',       # Andrew Schwartzberg's "HOUSING INC" (Pennant Housing Group)
    'GOOD HEALTH',   # Stephen Samuel's "GOOD HEALTH INC" (Premier Pharmacy Services, Baldwin Park CA)
    'TERRA',         # Chad Rosenberg's "TERRA" (specific firm; ambiguous on its own)
})


def _brand_key(s: str) -> str:
    """Alphanumeric-only uppercase key for brand matching.

    Collapses every variant ("AT&T", "AT AND T", "AT & T", "at&t",
    "A.T.&T.") to the same key ("ATT"), so a single entry in
    AMPERSAND_BRANDS catches all the raw forms in the FEC data.
    """
    s = s.upper()
    s = re.sub(r'\s+AND\s+', '', s)
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s


# Pre-computed lookup: alphanumeric key -> canonical form
_AMPERSAND_BRAND_LOOKUP = {_brand_key(b): b for b in AMPERSAND_BRANDS}


def _preserve_brand(upper_value: str) -> str | None:
    """Return the canonical `&` form if `upper_value` matches a known brand."""
    return _AMPERSAND_BRAND_LOOKUP.get(_brand_key(upper_value))


# Values that must never end up in previous_employer — they are status
# words or artifacts, not company names. Callers should treat them as
# "no previous employer known" (i.e. return None).
_PREV_EMP_NOT_REAL = {
    'NOT-EMPLOYED', 'NOT EMPLOYED', 'UNEMPLOYED', 'RETIRED',
    'SELF-EMPLOYED', 'SELF EMPLOYED', 'STUDENT', 'HOMEMAKER',
    'NOT DISCLOSED', 'PRIVATE', 'NONE', 'N/A', 'N.A', 'N.A.', 'NA', 'NAN',
    'CAMPAIGN/COMMITTEE',
    # Short artifacts that are clearly not companies
    'RET', 'RET.', 'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.',
    'DR', 'DR.',
    # Status typos / non-answers seen only in previous_employer
    'RETIREDE', 'RETIREFT', 'NOT', 'NOT IN WORKFORCE', 'NO EMPLOYER',
    'NOTEMPLOYS', 'NAT EMPLOYED', 'XXN', 'AUDIOLOGIST',
    'COMPANY NAME', 'COMPANY NAME (OPTIONAL)', 'NOT SPECIFIED',
}


def normalize_employer_display_name(name) -> str | None:
    """Apply contributor_employer's normalization rules to an ad-hoc value.

    Same transforms as `normalize_employer_canonical` (uppercase, strip
    legal suffixes, `&`→` AND `, collapse whitespace), packaged for
    callers outside the main cleaning pipeline — notably the resolve
    step, where `previous_employer` values are written from a cache
    that never went through cleaning.

    Returns None when the value is missing, empty, or a status-word /
    artifact (callers should then leave `previous_employer` blank).

    Examples:
        "CHAPEL HAVEN, INC"    -> "CHAPEL HAVEN"
        "LATHAM & WATKINS LLP" -> "LATHAM AND WATKINS"
        "DuPont"               -> "DUPONT"
        "NOT-EMPLOYED"         -> None
        "RET."                 -> None
    """
    if name is None:
        return None
    s = str(name).strip()
    if not s or s.upper() in _PREV_EMP_NOT_REAL:
        return None

    s = s.upper()
    # 'RETIRED - ORTHOCAROLINA' style: the remainder IS the previous employer;
    # a bare profession word ('RETIRED PHYSICIAN') is not a company at all.
    m = re.match(r'^RETIRED\b[\s,-]+(.+)$', s)
    if m:
        s = m.group(1).strip()
        from fec.config.constants import OCCUPATION_AS_EMPLOYER
        if not s or s in OCCUPATION_AS_EMPLOYER or s == 'MILITARY':
            return None
    # Strip trailing parenthetical markers — trademark indicators,
    # stock tickers, and "self" markers that aren't part of the company
    # name itself. Examples:
    #   "SAVE THE DATE (R)" / "SAVE THE DATE"  -> SAVE THE DATE
    #   "BEST ENERGY POWER (BEP)" / "BEST ENERGY POWER"  -> BEST ENERGY POWER
    #   "SENIOR HOUSING GROUP LLC (SELF)" / "SENIOR HOUSING GROUP LLC"
    # Pattern: trailing ( + 1-5 letters + ) at end of string, possibly
    # preceded by whitespace. Keeps real disambiguating tags like
    # company-internal codes that are 6+ chars or contain digits.
    s = re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', s).strip()

    # Strip legal suffixes, possibly stacked (e.g. "CO., INC.")
    original = re.sub(r'\s+', ' ', s).strip()          # normalized original
    stripped = s
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = _LEGAL_SUFFIX_RE.sub('', stripped).strip()
    # Hybrid rule: if stripping reduces the name to a single generic
    # word (HOUSING etc.), keep the original with suffix — "HOUSING"
    # alone reads as a noun and confuses users / the resolver.
    if (stripped != original
            and len(stripped.split()) == 1
            and stripped in GENERIC_WORDS_PROTECTED):
        # Normalize the original: "HOUSING, INC" / "HOUSING INC." -> "HOUSING INC"
        clean = re.sub(r',\s+', ' ', original)
        clean = re.sub(r'\s+', ' ', clean).strip().rstrip('.').rstrip(',').strip()
        return clean
    s = stripped
    # Brand override: if the value matches a known `&`-brand (in any
    # form — `AT&T`, `AT AND T`, `AT & T` all reduce to the same key),
    # force the canonical brand form. Otherwise keep what the filer
    # wrote — the pipeline deliberately does NOT do a blanket
    # `&` -> `AND` substitution, because that mangles real brand names.
    brand = _preserve_brand(s)
    if brand:
        return brand
    # Collapse whitespace, trim stray punctuation
    s = re.sub(r'\s+', ' ', s).strip().rstrip(',').rstrip('.').strip()
    s = re.sub(r'^C/O\s+', '', s)
    s = _strip_trailing_connectors(s)
    # Converge on the curated canonical spelling (e.g. IBM -> IBM CORP) so the
    # previous_employer path can never re-split a company the dict merged.
    s = EMPLOYER_SYNONYMS.get(s, s)
    return s or None


# A name never legitimately ends in a bare connector — these are FEC 38-char
# truncation stumps ("SOUTHERN GLAZER'S WINE AND" -> "... WINE").
_TRAILING_CONNECTOR_RE = re.compile(
    r'\s+(?:AT THE|OF THE|ATTORNEYS AT|AT|OF|FOR|AND|&|-)$')


def _strip_trailing_connectors(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_CONNECTOR_RE.sub('', s).strip()
    return s


# Legal-suffix STYLE normalization for display names: ", INC" -> " INC" and
# de-dotted "P.C" -> "PC". Style only — never strips the suffix, so it can
# never merge two different companies. 'A.L.P' is a real company (a.l.p.
# Lighting), not a limited partnership.
# Tolerates the raw punctuation FEC filers type ("CO,, INC." / "CO ,INC") —
# the comma may repeat and a trailing period is stripped later in the pipeline,
# so both forms must still match here.
_SUFFIX_COMMA_RE = re.compile(
    r'\s*,[,\s]*(?=(?:PLLC|LLC|LLP|INC|CORP|LTD|PLC|LP|PC|PA'
    r'|L\.L\.C|L\.L\.P|L\.P|P\.C|P\.A'
    r'|INCORPORATED|CORPORATION|COMPANY|LIMITED)\.?$)')
_SUFFIX_DEDOT = [(re.compile(r'\bL\.L\.C\.?$'), 'LLC'), (re.compile(r'\bL\.L\.P\.?$'), 'LLP'),
                 (re.compile(r'\bP\.C\.?$'), 'PC'), (re.compile(r'\bP\.A\.?$'), 'PA'),
                 (re.compile(r'\bL\.P\.?$'), 'LP')]


def restyle_legal_suffix(name: str) -> str:
    """Style-only: ', INC' -> ' INC', 'P.C' -> 'PC'. Never strips a suffix, so
    it can never merge two different companies."""
    if name.rstrip('.').endswith('A.L.P'):   # a.l.p. Lighting, not a partnership
        return name
    s = _SUFFIX_COMMA_RE.sub(' ', name)
    for pat, rep in _SUFFIX_DEDOT:
        s = pat.sub(rep, s)
    return s


# ── Employer synonyms (same company, different spelling) ──
# Built from donor-overlap analysis on 183K records.
# Only safe merges: same donors use both spellings.

EMPLOYER_SYNONYMS = {
    # ── Embedded self-employment markers (audit 2026-07-20) ──
    'SENIOR HOUSING GROUP SELF-EMPLOYED':      'SENIOR HOUSING GROUP',
    'LMR ADVISORS (SELF EMPLOYED)':            'LMR ADVISORS',
    'BLVD INN AND BISTRO/ SELF EMPLOYED AT':   'BLVD INN AND BISTRO',
    'MORLEES/SELF EMPLOYED':                   'MORLEES',
    'C/O SCHUWARGER & ASSOCIATES':             'SCHUWARGER & ASSOCIATES',
    # ── Status words written with typos / extras → canonical status ──
    'NOT IN WORKFORCE':          'NOT EMPLOYED',
    'NO EMPLOYER':               'NOT EMPLOYED',
    'NOTEMPLOYS':                'NOT EMPLOYED',
    'NAT EMPLOYED':              'NOT EMPLOYED',
    'SE;F EMPLOYED':             'SELF-EMPLOYED',
    'SEND-EMPLOYED':             'SELF-EMPLOYED',
    'SEPT EMPLOYED':             'SELF-EMPLOYED',
    'SELF-EMPLOYED PROGRAM':     'SELF-EMPLOYED',
    'SELF-EMPLOYED, CONSULTANT FOR WITHIN H':  'SELF-EMPLOYED',
    'I AM RETIRED':              'RETIRED',
    'RETIREDE':                  'RETIRED',
    'RETIREFT':                  'RETIRED',
    'RETIRED SELF EMPLOYED':     'RETIRED',
    'SELF RETIRED':              'RETIRED',
    'RETIRED MILITARY':          'RETIRED',
    # ── Brand acronym vs expansion, donor-overlap verified (audit) ──
    'PRICEWATERHOUSECOOPERS':    'PWC',
    'PWC US':                    'PWC',
    'PWC US TAX':                'PWC',
    'PWC US TAX LLP':            'PWC',
    'MWE':                       'MCDERMOTT WILL & EMERY LLP',
    'JONES LANG LASALLE':        'JLL',
    'NEW YORK UNIVERSITY':       'NYU',
    'CITY UNIVERSITY OF NEW YORK': 'CUNY',
    'MMA':                       'MARSH MCLENNAN AGENCY',
    # ── Same-donor variants (user-verified 2026-07-20) ──
    # Michael Margolin (Westfield NJ physician) wrote both; GROUP is 6-of-7
    'ADVANCED GASTROENTEROLOGY ASSOCIATES': 'ADVANCED GASTROENTEROLOGY GROUP',

    # ── Verified spelling typos ──
    'IDEAL FASTNER CORPORATION': 'IDEAL FASTENER',
    'CENTRBASE':                 'CENTERBASE LLC',
    'MXGUIREWOODS':              'MCGUIREWOODS',
    'ENT AND ALLERGY OF DE':     'ENT AND ALLERGY OF DELAWARE',

    # ── User-curated case-by-case merges ──
    # FEC truncates contributor_employer at 38 chars, so the same union
    # appears twice when the renamed-form crosses that boundary too.
    # Both truncated forms map to the current legal name.
    'SOUTHWEST REGIONAL COUNCIL OF CARPENTE':  'SOUTHWEST MOUNTAIN STATES COUNCIL OF CARPENTERS',
    'SOUTHWEST MOUNTAIN STATES COUNCIL OF C':  'SOUTHWEST MOUNTAIN STATES COUNCIL OF CARPENTERS',
    # Law firm: with vs without "OFFICES" suffix — user picked WITH offices
    'OSTWALD LAW':                             'OSTWALD LAW OFFICES',
    # City abbreviation
    'ENT GROUP OF LA':                         'ENT GROUP OF LOS ANGELES',

    # ── HIGH-confidence cluster merges ──
    # Rule: longer / more-complete name wins. Exception: JLL (real co. name).
    'STEWARD':                  'STEWARD PARTNERS',
    'NOBLE':                    'NOBLE PROPERTIES',
    'PORTAGE':                  'PORTAGE PARTNERS',
    'COOPER':                   'COOPER MANAGEMENT',
    'REGENT':                   'REGENT PROPERTIES',
    'MCR':                      'MCR LLC',
    'JLL PARTNERS':             'JLL',                          # JLL is the real name
    'EDISON':                   'EDISON PROPERTIES',
    'METROPOLITAN':             'METROPOLITAN MANAGEMENT',
    'IBM':                      'IBM CORP',
    'ARDEA':                    'ARDEA PARTNERS',
    'ARGO':                     'ARGO PARTNERS',
    'ELEVATE ENT':              'ELEVATE ENT PARTNERS',
    'GROVE POINT':              'GROVE POINT PARTNERS',
    'HERON':                    'HERON CAPITAL',
    'BLOOMFIELD':               'BLOOMFIELD CAPITAL',
    'ADLER':                    'ADLER PROPERTIES',
    'MESA WEST':                'MESA WEST CAPITAL',
    'MOORE':                    'MOORE HOLDINGS',
    'DAYTONA STREET':           'DAYTONA STREET CAPITAL',
    'HORNSTEIN LAW':            'HORNSTEIN LAW OFFICES',
    'CHOICE NEW YORK':          'CHOICE NEW YORK MANAGEMENT',
    'SOUTH OCEAN CAPITAL':      'SOUTH OCEAN CAPITAL PARTNERS',
    'PORTAGE POINT':            'PORTAGE POINT PARTNERS',
    'CRUISER CAPITAL':          'CRUISER CAPITAL ADVISORS',
    'BRIGADE CAPITAL':          'BRIGADE CAPITAL MANAGEMENT',
    'VANTAGE CAPITAL':          'VANTAGE CAPITAL PARTNERS',
    'VANTAGE':                  'VANTAGE CAPITAL PARTNERS',    # collapsed chain
    'SYNERGY HEALTH':           'SYNERGY HEALTH PARTNERS',
    'FIVEW':                    'FIVEW CAPITAL',
    'SIXPOINT':                 'SIXPOINT PARTNERS',
    'HAGER PACIFIC':            'HAGER PACIFIC PROPERTIES',
    'SUNDAY':                   'SUNDAY CAPITAL',
    'GUMENICK':                 'GUMENICK PROPERTIES',
    'CSC':                      'CSC PROPERTIES',
    'NWC':                      'NWC PROPERTIES',
    'PATRON CAPITAL':           'PATRON CAPITAL MANAGEMENT',
    'AVIS':                     'AVIS MANAGEMENT',
    'CHOICE NY':                'CHOICE NY MANAGEMENT',
    'MILLENNIUM CAPITAL':       'MILLENNIUM CAPITAL MANAGEMENT',
    'FAIRFIELD':                'FAIRFIELD PROPERTIES',
    'GREEN MEADOW':             'GREEN MEADOW VENTURES',
    'NOVA':                     'NOVA CONSULTING',
    'GUGGENHEIM':               'GUGGENHEIM PARTNERS',
    'SAGE':                     'SAGE CAPITAL',
    'ENFIELD CAPITAL':          'ENFIELD CAPITAL PARTNERS',
    'UNIVERSAL RISK':           'UNIVERSAL RISK ASSOCIATES',
    'ICG':                      'ICG ADVISORS',
    'GOTHAM':                   'GOTHAM VENTURES',
    'SLS':                      'SLS GROUP',
    'STARWOOD':                 'STARWOOD CAPITAL',

    # ── Person-specific swap fixes (employer name was in the occupation field) ──
    # Ben Ohebshalom's 3 rows: REAL ESTATE MANAGEMENT → his actual employer
    # (occupation field had "SKY MANAGEMENT CORPORATION", the fields were swapped)
    'REAL ESTATE MANAGEMENT':   'SKY MANAGEMENT CORPORATION',
    # Eliot Weiner's 4 rows: LEVY → his employer's full name (Edward C. Levy Co.)
    'LEVY':                     'EDWARD C. LEVY COMPANY',

    # ── MEDIUM-confidence merges ──
    # Subsidiaries/divisions/variants → parent or canonical form.
    'BROWNSTEIN HYATT':                      'BROWNSTEIN HYATT FARBER SCHRECK',
    'WELLS FARGO ADVISORS FINANCIAL NETWORK': 'WELLS FARGO ADVISORS',
    'DLA PIPER LLP (US)':                    'DLA PIPER',
    'TALCOTT HOLDINGS':                      'TALCOTT HOLDINGS, INC',
    'PULL-A-PART RECYCLING':                 'PULL-A-PART',
    'SCHALL LAW':                            'SCHALL LAW FIRM',
    'GLASS GARDENS INC. SHOPRITES':          'GLASS GARDENS INC',
    'BROWN & BROWN':                         'BROWN & BROWN INSURANCE',
    'WILKES ARTIS':                          'WILKES ARTIS, CHARTERED',
    'OPTUM CARE':                            'OPTUM',
    "MO'S BAGELS":                           "MO'S BAGELS & DELI",
    'PREMIER REALTY':                        'PREMIER REALTY MICHIGAN, LLC',
    'TROUTMAN PEPPER LOCKE LLP':             'TROUTMAN PEPPER',
    'MARINA PACIFIC HOTEL':                  'MARINA PACIFIC HOTEL & SUITES',
    'ARENTFOX':                              'ARENTFOX SCHIFF',
    'PAYPAL ADS':                            'PAYPAL',
    'DENTONS SIROTE':                        'DENTONS',
    'DENTONS US LLP':                        'DENTONS',
    'JACKSON':                               'JACKSON LEWIS',
    'CITADEL EHS':                           'CITADEL',
    'THRIVE':                                'THRIVE FP',
    'MUCH':                                  'MUCH LAW',
    'KELLER WILLIAMS BEVERLY HILLS':         'KELLER WILLIAMS',
    'COMPASS RE':                            'COMPASS',

    # ── MEDIUM-confidence merges (cont.) ──
    'REDWOOD':                               'REDWOOD LIVING',
    'EMA INV':                               'EMA INV MGMT',
    'EMA INVEST':                            'EMA INV MGMT',
    'EMA INVEST,MENT':                       'EMA INV MGMT',
    'MTS HEALTH PARTNERS & ELEMENTS HEALTH': 'MTS HEALTH PARTNERS',
    'STATE OF TEXAS EMPLOYEE':               'STATE OF TEXAS',
    'MARVISTA':                              'MARVISTA ENT',
    'BLUE STAR':                             'BLUE STAR BENEFITS',
    'JEWISH COMMUNITY FOUNDATION':           'JEWISH COMMUNITY FOUNDATION GREATER ME',
    'IBT GROUP USA LLC':                     'IBT GROUP',
    'ALTAIR SIGN':                           'ALTAIR SIGN & LIGHT',
    'MCDERMOTT LAW':                         'MCDERMOTT',
    'UNICO':                                 'UNICO ITC',
    'HOGAN LOVELLS US LLP':                  'HOGAN LOVELLS',
    'TRADEWEB':                              'TRADEWEB MARKETS',
    'BEST ENERGY POWER (BEP)':               'BEST ENERGY POWER',
    'INDEPENDENT CONTRACTOR SATAUS':         'INDEPENDENT CONTRACTOR',
    'PHATHOM':                               'PHATHOM PHARMA',
    'LOEWY LAW':                             'LOEWY LAW FIRM',
    'WESTPAC':                               'WESTPAC WEALTH',
    'FIRSTSERVICE':                          'FIRSTSERVICE RESIDENTIAL',
    'TOWER VENTURES HOLDINGS, LLC':          'TOWER VENTURES',
    'SENIOR HOUSING GROUP LLC SELF-EMPLOYED':'SENIOR HOUSING GROUP LLC',

    # ── MEDIUM-confidence merges (cont.) ──
    'SENIOR HOUSING GROUP LLC (SELF-EMPLOYE': 'SENIOR HOUSING GROUP LLC',
    'SENIOR HOUSING GROUP LLC (SELF)':       'SENIOR HOUSING GROUP LLC',
    'BUSHWICK':                              'BUSHWICK POTATO',
    'PERKINS':                               'PERKINS COIE',
    'FLATIRON':                              'FLATIRON VENTURE',
    'CENTURY 21':                            'CENTURY 21 STORES',
    'NORTHERN VALLEY':                       'NORTHERN VALLEY MEDICAL',
    'METROPOLITAN REALTY':                   'METROPOLITAN REALTY GROUP LLC',
    'COLDWELL BANKER':                       'COLDWELL BANKER REALTY',
    'NYU LANGONE HEALTH HUNTINGTON':         'NYU LANGONE HEALTH HUNTINGTON MEDICAL',
    'SELECT PET':                            'SELECT PET PRODUCTS',
    'CRESCENT HEIGHTS':                      'CRESCENT HEIGHTS OF AMERICA',
    'FENIGSTEIN & KAUFMAN, A PROFESSIONAL C':'FENIGSTEIN & KAUFMAN',
    'SEPHARDIC INSTITUTE':                   'SEPHARDIC INSTITUTE & SYNAGOGUE',
    'EMPIRE VALUATION':                      'EMPIRE VALUATION CONSULTANTS',
    'PANTHEON SYTEMS':                       'PANTHEON SYSTEMS',
    'PANTHEON':                              'PANTHEON SYSTEMS',
    'STANHOPE FINANCIAL HOLDINGS LIMITED':   'STANHOPE FINANCIAL',
    'REICH AND TRUAX (SEMI RETIRED)':        'REICH AND TRUAX',
    'CLEVELAND CLINIC FLORIDA':              'CLEVELAND CLINIC',

    # ── MEDIUM-confidence merges, verified by donor-city check ──
    # Each verified by checking donor cities to ensure they're truly the same entity.
    # SKIPPED (would create false merges across states):
    #   - JEWISH FEDERATION → ... (donors in 3 different states, not Tulsa/Cleveland)
    #   - PLASTIC SURGERY GROUP → MEMPHIS (donor in NJ, not TN)
    'GEM':                                   'GEM RC',                          # MALKIN, BARRY Chicago IL
    "CHILDREN'S HOSPITAL":                   "CHILDREN'S HOSPITAL LOS ANGELES", # all 6 in LA
    'RICHARDS':                              'RICHARDS MFG',                    # BIER family NJ
    'ATLANTIC':                              'ATLANTIC ENT',                    # ROTHBAUM, DANIEL Orlando FL

    # ── Well-known 2-char company abbreviations → full name ──
    'EY':                           'ERNST AND YOUNG',
    'C3':                           'C3.AI',
    'GM':                           'GENERAL MOTORS',
    'HP':                           'HEWLETT-PACKARD',
    # ── Brands where the legal suffix is part of common usage ──
    # The normalizer strips LLC/INC by default; these mappings restore
    # the suffix for brands where the bare form reads wrong
    # (e.g. "WhatsApp" is the product, "WhatsApp LLC" the employer).
    'WHATSAPP':                     'WHATSAPP LLC',
    # ── Name abbreviation variants ──
    'EDW. C. LEVY CO':              'EDWARD C. LEVY CO',
    'EDW C LEVY CO':                'EDWARD C. LEVY CO',
    # ── Bank/company name variants ──
    'ZIONS BANK':                   'ZIONS BANCORPORATION',
    'CAPITAL GROUP COMPANIES':      'CAPITAL GROUP',
    'HILCO':                        'HILCO GLOBAL',
    'LETTUCE ENTERTAIN YOU':        'LETTUCE ENTERTAIN YOU ENTERPRISES',
    'CENTERVIEW':                   'CENTERVIEW PARTNERS',
    'CANYON':                       'CANYON PARTNERS',
    'S AND P':                      'S AND P GLOBAL',
    'SCULPTOR':                     'SCULPTOR CAPITAL MANAGEMENT',
    'SCULPTOR CAPITAL':             'SCULPTOR CAPITAL MANAGEMENT',
    'ROCKEFELLER CAPITAL':          'ROCKEFELLER CAPITAL MANAGEMENT',
    'DRW':                          'DRW HOLDINGS',
    'NEWMARK':                      'NEWMARK GROUP',
    'MILLENNIUM':                   'MILLENNIUM MANAGEMENT',
    'BLACKSTONE':                   'BLACKSTONE GROUP',
    'ARES':                         'ARES MANAGEMENT',
    'APOLLO':                       'APOLLO GLOBAL MANAGEMENT',
    'APOLLO MANAGEMENT':            'APOLLO GLOBAL MANAGEMENT',
    'AMERICAN EXPRESS COMPANY':     'AMERICAN EXPRESS',
    'PROFICIO':                     'PROFICIO CAPITAL PARTNERS',
    'PROFICIO CAPITAL':             'PROFICIO CAPITAL PARTNERS',
    # ── Confirmed same-person variant pairs ──
    'WELLS FARGO':                  'WELLS FARGO ADVISORS',
    'MESIROW':                      'MESIROW FINANCIAL',
    'KIMCO':                        'KIMCO REALTY',
    'BBX':                          'BBX CAPITAL',
    'ATLANTIC REALTY':              'ATLANTIC REALTY ASSOCIATES',
    'UBS FINANCIAL':                'UBS FINANCIAL SERVICES',
    'CHURCHILLFORGE':               'CHURCHILLFORGE PROPERTIES',
    'PENNANTPARK':                  'PENNANTPARK INVESTMENTS',
    'RST':                          'RST DEVELOPMENT',
    'VIKING':                       'VIKING PARTNERS',
    'TOTAL INSURANCE':              'TOTAL INSURANCE SERVICES',
    'LIGHTSTONE':                   'LIGHTSTONE GROUP',
    'RLF CAPITAL':                  'RLF CAPITAL ADVISORS',
    'CARMEL PARTNERS':              'CARMEL PARTNERS MANAGEMENT',
    'JES':                          'JES PROPERTIES',
    'TREGAN':                       'TREGAN PARTNERS',
    'PATHFINDER':                   'PATHFINDER PARTNERS',
    'SPANDREL':                     'SPANDREL DEVELOPMENT PARTNERS',
    'SPANDREL DEVELOPMENT':         'SPANDREL DEVELOPMENT PARTNERS',
    'THE KLEIN':                    'THE KLEIN GROUP',
    'OMNINET':                      'OMNINET CAPITAL',
    'ROSE REAL ESTATE':             'ROSE REAL ESTATE SERVICES',
    'STIFEL':                       'STIFEL FINANCIAL',
    'BNB':                          'BNB REALTY',
    'GATEHOUSE':                    'GATEHOUSE MANAGEMENT',
    'MGS':                          'MGS PARTNERS',
    # ── Auto-detected same-person variant pairs (97) ──
    'A AND R KATZ':                 'A AND R KATZ MANAGEMENT',
    'A PRIORI INVESTMENTS':         'A PRIORI',
    'ALJ REGIONAL HOLDINGS':        'ALJ REGIONAL',
    'AMERICAN CENTURY INVESTMENTS': 'AMERICAN CENTURY',
    'AMERICAN INDUSTRIAL':          'AMERICAN INDUSTRIAL PARTNERS',
    'AMROCK':                       'AMROCK HOLDINGS',
    'AND WEALTH':                   'AND WEALTH PARTNERS',
    'ARKHOUSE PARTNERS':            'ARKHOUSE',
    'ARONOV REALTY':                'ARONOV',
    'ATALAN':                       'ATALAN CAPITAL',
    'ATALAN CAPITAL PARTNERS':      'ATALAN CAPITAL',
    'AVANTI PROPERTIES':            'AVANTI PROPERTIES GROUP',
    'BAINBRIDGE':                   'BAINBRIDGE COMPANIES',
    'BALLARD':                      'BALLARD PARTNERS',
    'BDT AND MSD PARTNERS':         'BDT AND MSD',
    'BEAL PROPERTIES':              'BEAL',
    'BLUE ARCH':                    'BLUE ARCH CAPITAL',
    'BNB REALTY PARTNERS':          'BNB REALTY',
    'BOLD PARTNERS':                'BOLD',
    'BP DIVERSIFIED INVESTMENTS':   'BP DIVERSIFIED',
    'BRADDOCK':                     'BRADDOCK FINANCIAL',
    'BROAD STREET':                 'BROAD STREET DEVELOPMENT',
    'BRODIE GENERATIONAL CAPITAL':  'BRODIE GENERATIONAL CAPITAL PARTNERS',
    'CANAM':                        'CANAM ENTERPRISES',
    'CANVASBACK':                   'CANVASBACK MANAGEMENT',
    'CENTRE PARTNERS':              'CENTRE PARTNERS MANAGEMENT',
    'CRAYHILL CAPITAL':             'CRAYHILL CAPITAL MANAGEMENT',
    'CRESA GLOBAL':                 'CRESA',
    'CRESSET':                      'CRESSET CAPITAL',
    'CYMBAL DLT COMPANIES':         'CYMBAL DLT',
    'EDGEWOOD CONSULTING GROUP':    'EDGEWOOD CONSULTING',
    'F AND F CAPITAL':              'F AND F CAPITAL GROUP',
    'FEDWAY':                       'FEDWAY ASSOCIATES',
    'FIDELITY':                     'FIDELITY INVESTMENTS',
    'FINMARC MANAGEMENT':           'FINMARC',
    'FIRST RATE FINANCIAL':         'FIRST RATE FINANCIAL GROUP',
    'FLATIRON VENTURE PARTNERS':    'FLATIRON VENTURE',
    'FRIEDKIN PROPERTY GROUP':      'FRIEDKIN PROPERTY',
    'FULTON':                       'FULTON CAPITAL',
    'GINA GROUP':                   'GINA',
    'GLOBAL INFRASTRUCTURE':        'GLOBAL INFRASTRUCTURE PARTNERS',
    'GOLDER INVESTMENT MANAGEMENT': 'GOLDER INVESTMENT',
    'HAMMERMAN CAPITAL':            'HAMMERMAN CAPITAL MANAGEMENT',
    'HOFKIN CAPITAL':               'HOFKIN CAPITAL MANAGEMENT',
    'HORNROCK':                     'HORNROCK PROPERTIES',
    'IBM CONSULTING':               'IBM',
    'IRON':                         'IRON FINANCIAL',
    'JF CAPITAL ADVISORS':          'JF CAPITAL',
    'JUMP':                         'JUMP MANAGEMENT',
    'KELLER WILLIAMS REALTY':       'KELLER WILLIAMS',
    'KRG CAPITAL':                  'KRG CAPITAL PARTNERS',
    'LABOVICK LAW':                 'LABOVICK LAW GROUP',
    'LENORE ENTERTAINMENT':         'LENORE ENTERTAINMENT GROUP',
    'LMC ADVISORS':                 'LMC',
    'LONG AND FOSTER REALTY':       'LONG AND FOSTER',
    'LSN':                          'LSN PARTNERS',
    'MAGELLAN DEVELOPMENT GROUP':   'MAGELLAN DEVELOPMENT',
    'MAVEN VENTURES':               'MAVEN',
    'MCA FINANCIAL GROUP':          'MCA FINANCIAL',
    'MCGUIREWOODS CONSULTING':      'MCGUIREWOODS',
    'MERIDIAN CAPITAL':             'MERIDIAN CAPITAL GROUP',
    'MILL POND':                    'MILL POND CAPITAL',
    'NALPAK':                       'NALPAK CAPITAL',
    'NAVITAS':                      'NAVITAS CAPITAL',
    'NUCARE SERVICES GROUP':        'NUCARE SERVICES',
    'OMINET CAPITAL':               'OMINET',
    'PENSAM':                       'PENSAM CAPITAL',
    'PIERPOINT CAPITAL':            'PIERPOINT',
    'PLATINUM MILE VENTURES':       'PLATINUM MILE',
    'PROVIDENT REAL ESTATE VENTURES': 'PROVIDENT REAL ESTATE',
    'PWC US GROUP':                 'PWC US',
    'QUADRANT CAPITAL':             'QUADRANT CAPITAL ADVISORS',
    'RCC':                          'RCC CONSULTING',
    'RENSOP INVESTMENTS':           'RENSOP',
    'ROC CAPITAL':                  'ROC',
    'ROYAL MEDIA':                  'ROYAL MEDIA GROUP',
    'RPK DEVELOPMENT':              'RPK',
    'SABER REAL ESTATE':            'SABER REAL ESTATE ADVISORS',
    'SERVENCO':                     'SERVENCO MANAGEMENT',
    'SIGNATURE':                    'SIGNATURE BANK',
    'SINGER WEALTH':                'SINGER WEALTH ADVISORS',
    'SMARTY':                       'SMARTY MANAGEMENT',
    'STANBERY DEVELOPMENT GROUP':   'STANBERY DEVELOPMENT',
    'STATE STREET CAPITAL':         'STATE STREET CAPITAL PARTNERS',
    'STRATA EQUITY':                'STRATA EQUITY GROUP',
    'STRUCK':                       'STRUCK CAPITAL',
    'SUNVERA':                      'SUNVERA GROUP',
    'TKO GROUP':                    'TKO GROUP HOLDINGS',
    'TRIANGLE CAPITAL':             'TRIANGLE CAPITAL GROUP',
    'TRIUS LENDING':                'TRIUS LENDING PARTNERS',
    'TRYAX REALTY':                  'TRYAX REALTY MANAGEMENT',
    'UNGER REALTY SERVICES':        'UNGER REALTY',
    'USI INSURANCE':                'USI INSURANCE SERVICES',
    'VLEIGH':                       'VLEIGH MANAGEMENT',
    'WALLACHBETH CAPITAL':          'WALLACHBETH',
    'WESTPAC WEALTH PARTNERS':      'WESTPAC WEALTH',
    # ── Same-person cross-reference variants (16) ──
    'GIBSON DUNN':                  'GIBSON DUNN AND CRUTCHER',
    'CEDARS SINAI':                 'CEDARS-SINAI MEDICAL CENTER',
    'CEDARS-SINAI':                 'CEDARS-SINAI MEDICAL CENTER',
    'SHADER BROS':                  'SHADER BROTHERS',
    'WOLF, RIFKIN':                 'WOLF, RIFKIN, SHAPIRO, SCHULMAN AND RABK',
    'TFN':                          'TRAVEL FUNDERS NETWORK',
    'KIRKLAND':                     'KIRKLAND AND ELLIS',
    'KTBS':                         'KTBS LAW',
    'RUB PEDIATRICS':               'RUB PEDIATRICS MD',
    'ATRIUM':                       'ATRIUMHEALTH',
    'HARBERG + HUVARD':             'HARBERG AND HUVARD',
    'LION BRAND YARNS':             'LIONBRAND YARN COMPANY',
    'L.M. COHEN AND CO':           'LMC',
    'BRAMAN MANAGEMENT ASSOCIATION': 'BRAMAN MANAGEMENT',
    'NJB ADVISORS':                 'NJB INVESTMENTS',
    # ── JP Morgan family ──
    'JPMORGAN':                     'JPMORGAN CHASE',
    'JP MORGAN':                    'JPMORGAN CHASE',
    'JP MORGAN CHASE':              'JPMORGAN CHASE',
    # ── Goldman Sachs ──
    'GOLDMAN SACHS AND CO':         'GOLDMAN SACHS',
    'GOLDMAN, SACHS AND CO':        'GOLDMAN SACHS',
    "GOLDMAN'S SACHS":              'GOLDMAN SACHS',
    # Hand-picked from a rapidfuzz dry-run. The blanket fuzzy merge stays OFF
    # (it wanted ROCKET COMPANIES → ROCK COMPANIES and NORTHEASTERN →
    # NORTHWESTERN UNIVERSITY — different firms), but these three are
    # unambiguous typos of a name already in the data.
    'SL NUSBAUM REALY CO':          'S.L. NUSBAUM REALTY CO',
    'DHC REAL ESTATE SERVICE':      'DHC REAL ESTATE SERVICES',
    # Z->X slip on the law firm's own name. It arrived via previous_employer, so
    # it never met the contributor_employer cleaning and became a second,
    # address-less row for a firm already in the dimension at 1633 Broadway.
    'KASOWITX, BENSON':             'KASOWITZ BENSON TORRES',
    # ── Law firms — comma/space/punctuation variants ──
    'GIBSON, DUNN AND CRUTCHER':    'GIBSON DUNN AND CRUTCHER',
    'GIBSON, DUNN, AND CRUTCHER':   'GIBSON DUNN AND CRUTCHER',
    'GIBSON. DUNN AND CRUTCHER':    'GIBSON DUNN AND CRUTCHER',
    "COZEN O'CONNOR":               'COZEN OCONNOR',
    'MORRISONCOHEN':                'MORRISON COHEN',
    'GREENBERGTRAURIG':             'GREENBERG TRAURIG',
    'PACHULSKI, STANG, ZIEHL, AND JONES':  'PACHULSKI STANG ZIEHL AND JONES',
    'PACHULSKI, STANG, ZIEHL AND JONES':   'PACHULSKI STANG ZIEHL AND JONES',
    'HERRICK, FEINSTEIN':           'HERRICK FEINSTEIN',
    'GREENBERG, GLUSKER':           'GREENBERG GLUSKER',
    'FISHMAN, BLOCK, DIAMOND':      'FISHMAN BLOCK DIAMOND',
    'FISHMAN, BLOCK DIAMOND':       'FISHMAN BLOCK DIAMOND',
    'FISHMAN BLOCK AND DIAMOND':    'FISHMAN BLOCK DIAMOND',
    'FISHMAN, BLOCK AND DIAMOND':   'FISHMAN BLOCK DIAMOND',
    # ── Space/hyphen variants ──
    'GRAY ROBINSON':                'GRAYROBINSON',
    'STERLING RISK':                'STERLINGRISK',
    'GLENUNA INVESTMENTS':          'GLEN UNA INVESTMENTS',
    'COMPASSLEXECON':               'COMPASS LEXECON',
    'BOSTONLAND COMPANY':           'BOSTON LAND COMPANY',
    'B VISION':                     'BVISION',
    'PULL A PART':                  'PULL-A-PART',
    'BAKER-TILLY':                  'BAKER TILLY',
    'S-101 MANAGEMENT':             'S101 MANAGEMENT',
    'CEDARS SINAI MEDICAL CENTER':  'CEDARS-SINAI MEDICAL CENTER',
    # ── Punctuation variants ──
    'EDW C. LEVY CO':               'EDWARD C. LEVY CO',
    'EDW. C LEVY CO':               'EDWARD C. LEVY CO',
    'EDW, C. LEVY CO':              'EDWARD C. LEVY CO',
    'JSHELD':                       'JS HELD',
    'J.S. HELD':                    'JS HELD',
    'B.A.G. INVESTMENTS':           'BAG INVESTMENTS',
    'DANIEL J COSGROVE, MD':        'DANIEL J COSGROVE MD',
    'DANIEL J. COSGROVE, MD':       'DANIEL J COSGROVE MD',
    'RA COHEN AND ASSOCIATES':      'R.A. COHEN AND ASSOCIATES',
    'R.A. COHEN':                   'R.A. COHEN AND ASSOCIATES',
    # ── Short name → full name ──
    'HACKMAN CAPITAL':              'HACKMAN CAPITAL PARTNERS',
    'GRT CORPORATION':              'GRT',
    'OAKTREE CAPITAL MANAGEMENT':   'OAKTREE CAPITAL',
    'OAKTREE CAPITAL MGMT':         'OAKTREE CAPITAL',
    'SUTHERLAND CAPITAL MANAGEMENT':'SUTHERLAND CAPITAL',
    'SUTHERLAND CAPITAL MGMT':      'SUTHERLAND CAPITAL',
    'CENTERVIEW PARTNER':           'CENTERVIEW PARTNERS',
    'BBR':                          'BBR PARTNERS',
    'JEMB REALTY CORPORATION':      'JEMB REALTY',
    'JEMBREALTY':                    'JEMB REALTY',
    'IDEAL FASTENER CORPORATION':   'IDEAL FASTENER',
    'CORPAC':                       'CORPAC GROUP',
    'ODEON CAPITAL':                'ODEON CAPITAL GROUP',
    'SSP PARTNERS AND ASSOCIATES':  'SSP PARTNERS',
    'ACCESSO':                      'ACCESSO PARTNERS',
    'I AND M J GROSS CO':           'I AND MJ GROSS CO',
    'RUSH PROPERTIES':              'RUSH PROPERTIES MANAGEMENT',
    'ATLANTIC REALTY COMPANIES':     'ATLANTIC REALTY ASSOCIATES',
    'HEALTHSOURCE DISTRIBUTORS':    'HEALTH SOURCE DISTRIBUTORS',
    # ── Singular/plural variants ──
    'ACTION BEHAVIOR CENTER':       'ACTION BEHAVIOR CENTERS',
    'EMA INVESTMENT':               'EMA INVESTMENTS',
    'NA DESIGN BUILDER':            'NA DESIGN BUILDERS',
    'SANTA MONICA PARTNER':         'SANTA MONICA PARTNERS',
    'DELTA CHILDREN':               'DELTA CHILDRENS',
    # ── Typo variants ──
    'SALTZMAN MUGAN DUSHIFF':       'SALTZMAN MUGAN DUSHOFF',
    'ATLANTA PROPERTY GOUP':        'ATLANTA PROPERTY GROUP',
    'WHITE PINE CAPITAL MANAGAMENT':'WHITE PINE CAPITAL MANAGEMENT',
    'ALLADAPT IMMUNOTHERPEUTICS':   'ALLADAPT IMMUNOTHERAPEUTICS',
    'DELTA ENTERPRISSE':            'DELTA ENTERPRISE',
    'ATLANTIC REALTY CONSULTAMTS':   'ATLANTIC REALTY CONSULTANTS',
    'SUSSMAN EDUCATIONA':           'SUSSMAN EDUCATION',
    # ── Web/abbreviation variants ──
    'NORTH SQUARE INVEST.COM':      'NORTH SQUARE INVESTMENTS',
    # ── Division variants (same parent company) ──
    'BANK OF AMERICA/ MERRILL':     'BANK OF AMERICA/MERRILL LYNCH',
    'SOUTHERN GLAZERS W AND S':     "SOUTHERN GLAZER'S WINE AND SPIRITS",
    'SOUTHERN GLAZERS WINE AND SPIRITS': "SOUTHERN GLAZER'S WINE AND SPIRITS",
    'SOUTHERN GLAZERS':             "SOUTHERN GLAZER'S WINE AND SPIRITS",
    # ── With/without full name ──
    'LYNN PINKER HURST':            'LYNN PINKER HURST SCHWEGMANN',
    'STUART L DAITCH':              'STUART L DAITCH DMD',
    'BROWN AND BROWN':              'BROWN AND BROWN INSURANCE',
    # ── Same-donor typos (Levenshtein <= 2, ratio < 3x) ──
    "MOS BAGELS AND DELI":          "MO'S BAGELS AND DELI",
    'VALLEY HEALTH YSTEM':          'VALLEY HEALTH SYSTEM',
    'THE KIOD FROM DOWNUNDER':      'THE KID FROM DOWNUNDER',
    'FIR FOR LIFE':                 'FIT FOR LIFE',
    "MARY'S MERCY CENTER":          'MARYS MERCY CENTER',
    'GREEWICH HOSPITAL':            'GREENWICH HOSPITAL',
    'EATON STELL BAR COMPANY':      'EATON STEEL BAR COMPANY',
    'PREMIER ORTHOPEDICS':          'PREMIER ORTHOPAEDICS',
    'SHAMBER, JOHNSON AND BERGMAN': 'SHAMBERG, JOHNSON AND BERGMAN',
    'COREGRO':                      'CORGRO',
    'CHILVIS GRUBMAN WARNER AND BERRY': 'CHILIVIS GRUBMAN WARNER AND BERRY',
    'SCOYYHULSE':                   'SCOTTHULSE',
    'THE FUNDWOKRS':                'THE FUNDWORKS',
    'MALLORY AND STERN':            'MALLERY AND STERN',
    'MCGIREWOODS':                  'MCGUIREWOODS',
    'MONARCH CASINO AND RESORTS':   'MONARCH CASINO AND RESORT',
    'TTHE RON KAUFMAN COMPANIES':   'THE RON KAUFMAN COMPANIES',
    'RIVERSTONE COMMINITIES':       'RIVERSTONE COMMUNITIES',
    'CROWDSRIKE':                   'CROWDSTRIKE',
    'RHNE GROUP':                   'RHONE GROUP',
    'SANHURST':                     'SANDHURST',
    'BROWNSTEIN HYATT FARBER SCHREK': 'BROWNSTEIN HYATT FARBER SCHRECK',
    'SUTTONS SQUARE GROUP':         'SUTTON SQUARE GROUP',
    'CEDAR SINAI MEDICAL CENTER':   'CEDARS-SINAI MEDICAL CENTER',
    'FUNDAMENTAL ADVISOR LP':       'FUNDAMENTAL ADVISORS LP',
    'GURDIAN DENTISTRY PARTNERS':   'GUARDIAN DENTISTRY PARTNERS',
    'LIEBERT CASSIDY WHITORE':      'LIEBERT CASSIDY WHITMORE',
    'BEHAVIORIAL MEDICINE ASSOCIATES': 'BEHAVIORAL MEDICINE ASSOCIATES',
    'CRDIT AGRICOLE CIB':           'CREDIT AGRICOLE CIB',

    # ── Auto-added from scan_employer_aliases.py (conf >= 85) ──
    # Evidence: same donor_key used both the variant and canonical form.
    # Generated 2026-04-17 from scans/results/employer_aliases.csv.
    '3 ART ENTERTAINMENT':          '3 ARTS ENTERTAINMENT',
    'ABA':                          'AMERICAN BANKERS ASSOCIATION',
    'ACE':                          'ATLANTIC COAST ENTERPRISES',
    'AEI':                          'AMERICAN EDUCATIONAL INSTITUTE',
    'AGG':                          'ARNALL GOLDEN GREGORY',
    'AHS':                          'AMERICAN HOSPICE SYSTEMS',
    'AIP':                          'AMERICAN INDUSTRIAL PARTNERS',
    'AMERICAN EDUCATION INSTITUTE': 'AMERICAN EDUCATIONAL INSTITUTE',
    'AMERICAN ISRAEL PUBLIC AFFAIRS COMMITT': 'AIPAC',
    'APPIED INTUIITION':            'APPLIED INTUITION',
    'ARA':                          'AUSTIN RADIOLOGICAL ASSOCIATION',
    'ASG':                          'ASSESSMENT SOLUTIONS GROUP',
    'AWRE':                         'ALFRED WEISSMAN REAL ESTATE',
    'BELKIN BURDER GOLDMAN':        'BELKIN BURDEN GOLDMAN',
    'BELKIN*BURDEN*GOLDMAN':        'BELKIN BURDEN GOLDMAN',
    'BEP':                          'BEST ENERGY POWER',
    'BGCP':                         'BRODIE GENERATIONAL CAPITAL PARTNERS',
    'BHFS':                         'BROWNSTEIN HYATT FARBER SCHRECK',
    'BHS':                          'BROWN HARRIS STEVENS',
    'BMC':                          'BUSINESS MACHINES CONSULTANTS',
    'BROWN + BROWN':                'BROWN & BROWN',
    'CALIFORNIA STATE UNIVERSITY, NORTHRIDG': 'CSUN',
    'CARDIOVASCULAR CONSULTANT OF LONG ISLA': 'CARDIOVASCULAR CONSULTANTS OF LONG ISL',
    'CATALYST PHYSICIAN GROUP':     'CATALYST PHYSICIANS GROUP',
    'CCC':                          'CLEARWATER CARDIOVASC CONSULTANTS',
    'CCM':                          'COMPREHENSIVE COMMERCIAL MANAGEMENT',
    'CDG':                          'COMMUNITY DEVELOPMENT GROUP',
    'CFP':                          'CAROLINA FACIAL PLASTICS',
    'CIG':                          'CONSOLIDATED INVESTMENT GROUP',
    "COOK CHILDRENS HOSPITAL":      "COOK CHILDREN'S HOSPITAL",
    'CPG':                          'CATALYST PHYSICIANS GROUP',
    'CRES':                         'CLEVELAND REAL ESTATE SERVICES',
    'CRP':                          'CHESAPEAKE REALTY PARTNERS',
    'CSIMG':                        'CEDARS SINAI IMAGING MEDICAL GROUP',
    'CSUN':                         'CAL STATE UNIVERSITY NORTHRIDGE',
    'CTC':                          'CHICAGO TRADING COMPANY',
    'CTPO':                         'CENTRAL TEXAS PEDITRIC ORTHOPEDICS',
    'CUNEO HILBERT AND LADUCA':     'CUNEO GILBERT AND LADUCA',
    'DAVE PERRY-MILLER REAL ESTATE': 'DAVE PERRY MILLER REAL ESTATE',
    'DAYTONA STREET CAPJITAL':      'DAYTONA STREET CAPITAL',
    'DIC':                          'DANTO INVESTMENT CO',
    'DLPR':                         'DUKAS LINDEN PUBLIC RELATIONS',
    'DOCS':                         'DERMATOLOGISTS OF CENTRAL STATES',
    'DONE COMMUNITY BANK':          'DIME COMMUNITY BANK',
    'DOWN-TO-EARTH TECHNOLOGIES':   'DOWN TO EARTH TECHNOLOGIES',
    'EMAI INV':                     'EMA INV',
    'EMERALD//COHEN AND COMPANY':   'EMERALD/ COHEN AND COMPANY',
    'ETC':                          'EVEREDE TOOL COMPANY',
    'FCP':                          'FORTUNE CAPITAL PARTNERS',
    'FFL':                          'FIT FOR LIFE',
    'FOA':                          'FLORIDA ORTHOPAEDIC ASSOCIATES',
    'FOCUS PATNERS WEALTH':         'FOCUS PARTNERS WEALTH',
    'FUNDAMENTAL ADVISOR':          'FUNDAMENTAL ADVISORS',
    'GMU':                          'GEORGE MASON UNIVERSITY',
    'GRSM':                         'GORDON REES SCULLY MANSUKHANI',
    'GRUNBERG MANAGAEMENT':         'GRUNBERG MANAGEMENT',
    'HIGHCAPE':                     'HIGHCAPE CAPITAL',
    'HIJ':                          'HARRIS INVESTMENT GROUP',
    'IDEA':                         'INTERNATIONAL DEVELOPMENT ENTERPRISES ASSOC',
    'IRA KATZ CONSULTANT':          'IRA KATZ CONSULTING',
    'IRT':                          'INLAND REAL ESTATE TRUST',
    'JGDB':                         'JAFFA GROUP DESIGN BUILD',
    'JNF':                          'JEWISH NATIONAL FUND',
    'JTWM':                         'JAFFE TILCHIN WEALTH MANAGEMENT',
    'KOVITZ INVESTMENTS GROUP':     'KOVITZ INVESTMENT GROUP',
    'KKS':                          'KRAUSE KALFAYAN SMITH',
    'LEGAL AID SOCIETY OF PALM BEACH': 'LEGAL AID SOCIETY OF PALM BEACH COUNTY',
    'LOLV':                         'LEXUS OF LEHIGH VALLEY',
    'MARCH CAPITAL PARNTERS':       'MARCH CAPITAL PARTNERS',
    'MBOS':                         'MARINA BAY OPEN SPACE',
    'MCN':                          'MERCER CAPITAL',
    'MMT':                          'MASSACHUSETTS MATERIALS TECHNOLOGIES',
    'MOH':                          'MARYLAND ONCOLOGY HEMATOLOGY',
    'MPT':                          'MARINE POLYMER TECHNOLOGY',
    'NATIONAL COLLECTORS MINT':     "NATIONAL COLLECTOR'S MINT",
    'NGPC':                         'NORTH GEORGIA PAIN CLINIC',
    'NJCA':                         'NEW JERSEY CARDIOLOGY ASSOCIATES',
    'NLC':                          'NORTHSIDE LAW CENTER',
    'NSP':                          'NEOTA SOCIAL PERFORMANCE',
    'PATRIDGE':                     'PARTRIDGE',
    'PERCETVION COMMS':             'PERCEPTION COMMS',
    'PLG':                          'PESSAH LAW GROUP',
    'RADOLOGY ASSOCIATES OF CLEARWATER': 'RADIOLOGY ASSOCIATES OF CLEARWATER',
    'RAINBOW CROW CUSUTOM INTERIORS': 'RAINBOW CROW CUSTOM INTERIORS',
    'SAADIA GROUP LCC':             'SAADIA GROUP',
    'SAE INTERNATINAL':             'SAE INTERNATIONAL',
    'SGE':                          'SUSQUEHANNA GROWTH EQUITY',
    'SOUTH BEACH ESATES GROUP':     'SOUTH BEACH ESTATES GROUP',
    'STROOCK AND STROOCK AND LAVAN': 'STROOCK',   # keep the short form — they use it
    'TFQ':                          'THE FEMALE QUOTIENT',
    'UBER TECHNOLOGOIES':           'UBER TECHNOLOGIES',
    'VHP':                          'VILLAGE HEALTH PARTNERS',
    'WEST COAST ARBORIST':          'WEST COAST ARBORISTS',
    'WILLAM MORRIS AGENCY':         'WILLIAM MORRIS AGENCY',
    'YENKIN-MAJESTIC PAINT':        'YENKIN MAJESTIC PAINT',

    # ── Typo fixes verified manually from duplicate scan (A: 1-2 letter
    #    typos, B: abbreviation/truncation — direction always toward the
    #    correctly-spelled / fuller form) ──
    'ARTISTIC TILE INCV':           'ARTISTIC TILE, INC.',
    'SIXTY HOTLES':                 'SIXTY HOTELS',
    'STORAGE PROS MANAGENEBT':      'STORAGE PROS MANAGEMENT',
    '22C CAPTIAL':                  '22C CAPITAL',
    'AKIN GUMP STRAUSS HAVER & FELD': 'AKIN GUMP STRAUSS HAUER & FELD LLP',
    'QUINN EMANUEL URQUHARD & SULLIVAN': 'QUINN EMANUEL URQUHART & SULLIVAN LLP',
    'SOFTERWARE':                   'SOFTWARE',
    'SCHULTE ROTHE & ZABEL':        'SCHULTE ROTH & ZABEL LLP',
    'SANTA MONICA PARNTERS LP':     'SANTA MONICA PARTNERS',
    'KAMRAS AND POLANSKY MEDICAL CORPORATIO': 'KAMRAS AND POLANSKY MEDICAL CORP',
    'KIRKLAND & ELIS':              'KIRKLAND & ELLIS',
    'KIRKLAND & ELLS LLP':          'KIRKLAND & ELLIS LLP',
    'KIRKLAND & ELIS LLP':          'KIRKLAND & ELLIS LLP',
    'KIRKLAND & RELLIS LLP':        'KIRKLAND & ELLIS LLP',
    'SULIVAN & WORCESTER LLP':      'SULLIVAN & WORCESTER LLP',
    'CREATIVE ARTIST AGENCY':       'CREATIVE ARTISTS AGENCY',
    'UNIVERISTY OF CONNECTICUT':    'UNIVERSITY OF CONNECTICUT',
    'WAYNE STTAE UNIVERSITY':       'WAYNE STATE UNIVERSITY',
    'SETON HALL UNIVERSOTY':        'SETON HALL UNIVERSITY',
    'CHILVIS GRUBMAN WARNER & BERRY LLP': 'CHILIVIS GRUBMAN WARNER & BERRY LLP',
    'CONTINENTAL REALTY COORPORATION': 'CONTINENTAL REALTY CORPORATION',
    'STIFFEL':                      'STIFEL',
    'LAWRENCE BERKLEY NATIONAL LAB': 'LAWRENCE BERKELEY NATIONAL LAB',
    'CAMPBELL PROPERTY MANAGEMEN':  'CAMPBELL PROPERTY MANAGEMENT',
    'SHADER BROS CORP':             'SHADER BROTHERS CORP',
    'TAMAROFF MTS':                 'TAMAROFF MOTORS',
    'NEMAN BROS. & ASSOCIATES, INC': 'NEMAN BROTHERS & ASSOCIATES, INC.',
    'HUSCH EPPENBERG':              'HUSCH & EPPENBERGER',

    # ── B: Abbreviation/truncation ──
    'PROHEALTH PROFESSIONAL SVCS.': 'PROHEALTH PROFESSIONAL SERVICES',
    'MIDSTATE RADIOLOGY ASSOC':     'MIDSTATE RADIOLOGY ASSOCIATES',
    'NEMAN BROTHERS & ASSOC., INC.': 'NEMAN BROTHERS & ASSOCIATES, INC.',
    'UNIV OF KANSAS HEALTH SYSTEM': 'UNIVERSITY OF KANSAS HEALTH SYSTEM',
    'AMERICAN EDUCATIONAL INST':    'AMERICAN EDUCATIONAL INSTITUTE',
    'SILVER INVESTMENTS LTD':       'SILVER INVESTMENTS LIMITED',
    'LAWRENCE LIVERMORE NATIONAL LAB': 'LAWRENCE LIVERMORE NATIONAL LABORATORY',
    'JAFFE TILCHIN WEALTH MGMT':    'JAFFE TILCHIN WEALTH MANAGEMENT',
    'FOOTHILLS LAND AND DEVEL':     'FOOTHILLS LAND AND DEVELOPMENT',
    'BETH ISRAEL LAHEY HES':        'BETH ISRAEL LAHEY HEALTH',
    'ANESTHESIA SERVICES OF BHAM':  'ANESTHESIA SERVICES OF BIRMINGHAM',
    'RESOLUTION CAPITOL MGMT':      'RESOLUTION CAPITOL MANAGEMENT',
    'GERALD M. COHEN PA':           'GERALD COHEN PA',
    'PORTAGE TECHNOLOGY':           'PORTAGE TECHNOLOGIES',
    'HAIVISION NETWORK VIDEO':      'HAIVISION',
}


def _load_manual_overrides():
    """Load human-reviewed typo→canonical mappings from
    data/manual_typo_overrides.json and merge into EMPLOYER_SYNONYMS.

    This file is produced by `scans/apply_user_decisions.py` after a
    human triages the duplicates_to_review.csv. Keeping it as JSON
    (rather than baking entries into this module) means future review
    cycles update one file, not source code, and the diff is reviewable
    on its own."""
    import json as _json
    from pathlib import Path as _Path
    fp = _Path(__file__).resolve().parent.parent.parent / 'data' / 'manual_typo_overrides.json'
    if not fp.exists():
        return 0
    try:
        with open(fp, encoding='utf-8') as f:
            overrides = _json.load(f)
    except Exception:
        return 0
    n = 0
    for k, v in overrides.items():
        if isinstance(k, str) and isinstance(v, str):
            EMPLOYER_SYNONYMS[k.strip().upper()] = v.strip()
            n += 1
    return n


def _flatten_synonym_chains() -> int:
    """Collapse transitive chains so a single .map() reaches the final form.

    apply_employer_synonyms maps once, so a chain A→B, B→C would otherwise
    leave A stranded at the intermediate B. Rewrite each key to point at the
    end of its chain. A cycle is left at its one-hop target and logged."""
    n = 0
    for key in list(EMPLOYER_SYNONYMS):
        seen = {key}
        val = EMPLOYER_SYNONYMS[key]
        while val in EMPLOYER_SYNONYMS and val not in seen:
            seen.add(val)
            val = EMPLOYER_SYNONYMS[val]
        if val in EMPLOYER_SYNONYMS:
            logger.warning("Cycle in EMPLOYER_SYNONYMS — %r left at one hop", key)
            continue
        if val != EMPLOYER_SYNONYMS[key]:
            EMPLOYER_SYNONYMS[key] = val
            n += 1
    return n


def _canonicalize_for_match(s: str) -> str:
    """Reduce an employer string to the form apply_employer_synonyms sees
    at match time — i.e. after normalize_employer_canonical has stripped
    legal suffixes and converted &→AND. Mirrors the regex chain in
    normalize_employer_canonical; keep the two in sync."""
    s = (s or '').upper().strip()
    s = re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', s)
    s = _LEGAL_SUFFIX_RE.sub('', s)
    s = re.sub(r'[&$]', ' AND ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.rstrip('.,').strip()


def _expand_synonym_keys() -> int:
    """Register each key's post-normalize form as an alias.

    apply_employer_synonyms matches contributor_employer AFTER
    normalize_employer_canonical runs, so a key in raw display form
    ('GIBBONS PC', 'GRUBMAN SHIRE & MEISELAS') never matches. Add the
    normalized form alongside the original so either spelling resolves."""
    added = 0
    for key in list(EMPLOYER_SYNONYMS):
        target = EMPLOYER_SYNONYMS[key]
        nk = _canonicalize_for_match(key)
        # Skip identity (nk == key) and self-maps (nk == target): when the
        # normalized key already equals the canonical value, an employer in
        # that form is canonical and needs no synonym.
        if not nk or nk == key or nk == target:
            continue
        existing = EMPLOYER_SYNONYMS.get(nk)
        if existing is not None:
            if existing != target:
                # Benign: two raw keys normalize to the same alias with
                # different targets — we keep the first (existing) one. Logged
                # at DEBUG so it doesn't spam every import; raise the level if
                # you're auditing the synonym tables.
                logger.debug(
                    "Synonym alias collision on %r — keeping existing target", nk)
            continue
        EMPLOYER_SYNONYMS[nk] = target
        added += 1
    return added


# Apply manual review overrides on module import — once loaded, the rest
# of the cleaning pipeline sees them transparently as part of EMPLOYER_SYNONYMS.
# This runs at IMPORT time (before clean.py prints its banner), so keep it at
# DEBUG — it's internal bookkeeping, not part of the run's user-facing output.
_n_manual = _load_manual_overrides()
_n_alias = _expand_synonym_keys()
_n_flat = _flatten_synonym_chains()
logger.debug(
    "employer synonyms ready: %s manual overrides, %s key aliases, %s chains flattened",
    f"{_n_manual:,}", _n_alias, _n_flat,
)


# Occupation words used as employer (these people are self-employed)
_OCC_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'PHYSICIAN', 'CONSULTANT', 'PROFESSOR',
    'DENTIST', 'ACCOUNTANT', 'TEACHER', 'ENGINEER', 'REALTOR',
    'INVESTOR',
})


def apply_employer_synonyms(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Merge employer name variants into canonical forms.
    Only safe merges verified by donor-overlap analysis.

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    emp = df.loc[ii, 'contributor_employer']
    hits = ii[emp.isin(EMPLOYER_SYNONYMS)]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'contributor_employer'] = emp[hits].map(EMPLOYER_SYNONYMS)

    # Suffix style: ", INC" -> " INC", "P.C" -> "PC". Never strips a suffix.
    emp = df.loc[ii, 'contributor_employer'].dropna()
    restyled = emp.map(restyle_legal_suffix)
    changed = restyled != emp
    if changed.any():
        df.loc[changed[changed].index, 'contributor_employer'] = restyled[changed]
        n_fixed += int(changed.sum())

    return df, n_fixed


# Employer abbreviations → full word (whole-word, optional trailing period).
# Kept tight: each expansion is unambiguous for THIS dataset. INV was verified
# by hand — every "INV" employer here is an Investment firm (EMA / SSI / Feldman
# / Triple S Investment). ASSOC stays out: it splits between ASSOCIATES and
# ASSOCIATION (e.g. "BRAMAN MANAGEMENT ASSOCIATION") — left for review.
_EMPLOYER_ABBREV = [
    (re.compile(r'\bMGMT\b\.?'), 'MANAGEMENT'),
    (re.compile(r'\bMGMNT\b\.?'), 'MANAGEMENT'),
    (re.compile(r'\bMGT\b\.?'), 'MANAGEMENT'),
    (re.compile(r'\bINV\b\.?'), 'INVESTMENT'),
    (re.compile(r'\bINTL\b\.?'), 'INTERNATIONAL'),
    (re.compile(r'\bMFG\b\.?'), 'MANUFACTURING'),
    (re.compile(r'\bGRP\b\.?'), 'GROUP'),
    (re.compile(r'\bSVCS\b\.?'), 'SERVICES'),
    (re.compile(r'\bSVC\b\.?'), 'SERVICES'),
    # INFO verified by hand: the only whole-word "INFO" employer is CAMBRIDGE
    # INFO GROUP, whose full-spelling twin CAMBRIDGE INFORMATION GROUP is also
    # in the data — so the two were being counted as separate companies. The
    # \b guards keep INFOSYS / INFOSEC / INFORMA (INFO as a prefix) untouched.
    (re.compile(r'\bINFO\b\.?'), 'INFORMATION'),
]


def expand_employer_abbreviations(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Expand unambiguous abbreviations in contributor_employer (MGMT → MANAGEMENT,
    …). Runs LAST, after synonyms — so abbreviations that a synonym/override target
    reintroduces ("SUTHERLAND CAPITAL MGMT. INC.") get expanded too, and variants
    that differed only by the abbreviation collapse to one canonical name.
    """
    ii = _indiv_idx(df)
    emp = df.loc[ii, 'contributor_employer']
    has = emp.notna() & ~emp.isin(_SKIP_EMPLOYERS)
    target = ii[has]
    if len(target) == 0:
        return df, 0

    vals = emp[has]
    new = vals
    for rx, repl in _EMPLOYER_ABBREV:
        new = new.str.replace(rx, repl, regex=True)
    new = new.str.replace(r'\s+', ' ', regex=True).str.strip().str.rstrip('.,').str.strip()

    changed = new != vals
    if changed.any():
        df.loc[target[changed], 'contributor_employer'] = new[changed]
    return df, int(changed.sum())


_ASSOC_RX = re.compile(r'\bASSOCS?\b\.?')
_ASSOCIATION_RX = re.compile(r'\bASSOCIATION\b')


def expand_employer_associates(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Normalize the abbreviation ASSOC contextually: a real ASSOCIATION (e.g.
    "BRAMAN MANAGEMENT ASSOCIATION", "ASSOC FOR WOMENS HEALTH CARE") → ASSOCIATION;
    everything else (law/medical/realty groups, "& ASSOC") → ASSOCIATES — keeping
    the firm's identity rather than dropping the word. Association is detected from
    the cleaned OR the as-filed original, so a truncated "...ASSOC" is still caught.
    """
    ii = _indiv_idx(df)
    emp = df.loc[ii, 'contributor_employer']
    has = (emp.notna() & ~emp.isin(_SKIP_EMPLOYERS)
           & emp.str.upper().str.contains(r'\bASSOCS?\b', regex=True, na=False))
    target = ii[has]
    if len(target) == 0:
        return df, 0

    cur = emp[has].str.upper()
    if 'contributor_employer_original' in df.columns:
        o = df.loc[target, 'contributor_employer_original'].astype(str).str.upper()
    else:
        o = cur
    is_association = (cur.str.contains(_ASSOCIATION_RX, na=False)
                      | o.str.contains(_ASSOCIATION_RX, na=False)
                      | cur.str.match(r'ASSOC\s+FOR\b'))

    as_assn = cur.str.replace(_ASSOC_RX, 'ASSOCIATION', regex=True)
    as_ates = cur.str.replace(_ASSOC_RX, 'ASSOCIATES', regex=True)
    new = as_assn.where(is_association, as_ates)
    new = new.str.replace(r'\s+', ' ', regex=True).str.strip().str.rstrip('.,').str.strip()

    changed = new != emp[has]
    if changed.any():
        df.loc[target[changed], 'contributor_employer'] = new[changed]
    return df, int(changed.sum())


def fix_occupation_as_employer(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Fix records where employer is an occupation word (ATTORNEY, CONSULTANT, etc.)
    and the person has a real occupation → they're self-employed.

    e.g. emp='ATTORNEY', occ='RETIRED' → leave alone (was an attorney)
         emp='CONSULTANT', occ='PROJECT MANAGEMENT CONSULTANT' → emp=SELF-EMPLOYED

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    emp = _norm(df.loc[ii, 'contributor_employer'])
    occ = _norm(df.loc[ii, 'contributor_occupation'])

    # Only fix when employer IS the occupation word AND
    # the person has a different real occupation
    is_occ_emp = emp.isin(_OCC_AS_EMPLOYER)
    has_diff_occ = (occ != '') & (occ != emp)  # has a different occupation value
    # Don't touch RETIRED people — "ATTORNEY" might be their previous employer
    not_retired = ~occ.isin({'RETIRED', 'RETIRE', 'RETIREE'})

    hits = ii[is_occ_emp & has_diff_occ & not_retired]
    n_fixed = len(hits)

    if n_fixed:
        # Move employer word to occupation if current occ is SELF-EMPLOYED
        se_mask = hits[occ[hits] == 'SELF-EMPLOYED']
        if len(se_mask):
            df.loc[se_mask, 'contributor_occupation'] = df.loc[se_mask, 'contributor_employer']
            df.loc[se_mask, 'occupation_category'] = _categorize(
                df.loc[se_mask, 'contributor_occupation']
            )

        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'

    return df, n_fixed


_MID_SUFFIX_RE = re.compile(
    r'\b(LLC|LLP|INC\.?|CORP\.?|LTD\.?)[\s,./]+',
    re.IGNORECASE,
)

def fix_normalized_mid_suffix(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Strip LLC/LLP/INC that appear mid-string in employer_name_normalized.
    e.g. 'GLASS GARDENS INC. SHOPRITES' → 'GLASS GARDENS SHOPRITES'
         'SENIOR HOUSING GROUP LLC SELF-EMPLOYED' → 'SENIOR HOUSING GROUP SELF-EMPLOYED'
         'DLA PIPER LLP (US)' → 'DLA PIPER (US)'

    Returns: (df, n_fixed)
    """
    col = 'employer_name_normalized'
    if col not in df.columns:
        return df, 0

    vals = df[col].fillna('')
    has_mid = vals.str.contains(r'\bLLC\b|\bLLP\b|\bINC\b|\bCORP\b|\bLTD\b', case=False, regex=True, na=False)
    target = df.index[has_mid]

    if len(target) == 0:
        return df, 0

    cleaned = vals[has_mid].str.replace(_MID_SUFFIX_RE, ' ', regex=True)
    # Also strip trailing suffix (already handled but double-check)
    cleaned = cleaned.str.replace(_LEGAL_SUFFIX_RE, '', regex=True)
    cleaned = cleaned.str.replace(r'\s+', ' ', regex=True).str.strip()

    changed = cleaned != vals[has_mid]
    n_fixed = int(changed.sum())
    if n_fixed:
        df.loc[target[changed], col] = cleaned[changed]
        # Also update contributor_employer (since we merge normalized back)
        df.loc[target[changed], 'contributor_employer'] = cleaned[changed]

    return df, n_fixed


# NOTE: list COMPANY before CO so iteration strips "COMPANY" first when both
# could match (avoids leaving "MPANY" residue if CO was tried first).
# INCORPORATED first for the same reason relative to INC.
_CORP_SUFFIXES = ('INCORPORATED', 'CORPORATION', 'COMPANY', 'ENTERPRISES',
                  'PLLC', 'CORP', 'GROUP', 'LLC', 'LLP', 'INC', 'LTD', 'CO',
                  'PC', 'PA', 'LP')

# Whole-token abbreviations folded for grouping only (never display).
# ASSOC is deliberately absent: ASSOCIATES vs ASSOCIATION is contextual.
_KEY_TOKEN_EXPANSIONS = {'UNIV': 'UNIVERSITY', 'MT': 'MOUNT',
                         'ASSOCS': 'ASSOCIATES'}


def canonical_key(name: str) -> str:
    """
    Strict canonical grouping key for an employer name.

    Variants that collapse to the same key are treated as the same company.
    Catches whitespace, punctuation, TLD (".com"), legal-suffix, "THE",
    and ampersand/AND variants in one consistent pass.

    Examples:
        KIRKLAND & ELLIS LLP        → KIRKLANDELLIS
        KIRKLAND AND ELLIS           → KIRKLANDELLIS
        PRICEMDS.COM                  → PRICEMDS
        FISHMAN, BLOCK + DIAMOND     → FISHMANBLOCKDIAMOND
        THE CARLYLE GROUP             → CARLYLE
    """
    s = name.strip().upper()
    if not s:
        return ''
    # Strip TLD-like trailing segments before we kill the dot
    s = re.sub(r'\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b', '', s)
    # `&` and `$` (FEC typo) → AND  — then drop the word AND entirely for
    # grouping so "X AND Y" == "X & Y" == "XY".
    # IMPORTANT: do word-boundary substitutions BEFORE collapsing whitespace,
    # otherwise `AND` inside KIRKLAND would be eaten.
    s = re.sub(r'[&$]', ' AND ', s)
    s = re.sub(r'^\s*THE\b', '', s)
    s = re.sub(r'\bAND\b', ' ', s)
    s = ' '.join(_KEY_TOKEN_EXPANSIONS.get(t, t) for t in s.split())
    # Collapse all non-alphanumeric to nothing (whitespace, commas, dots,
    # plus/minus/slash, etc.). Catches "FISHMAN, BLOCK + DIAMOND" ==
    # "FISHMAN BLOCK DIAMOND" etc.
    s = re.sub(r'[^A-Z0-9]+', '', s)
    # Iteratively strip trailing corporate suffixes until stable
    while True:
        for suf in _CORP_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 3:
                s = s[:-len(suf)]
                break
        else:
            break
    return s


def merge_typo_variants(df: pd.DataFrame, threshold: int = 92) -> int:
    """
    Detect and merge employer name pairs that are typos of each other.

    Uses rapidfuzz token_sort_ratio for similarity. Bucketed by the first
    4 alphanumeric characters so we only compare names with the same
    starting letters — keeps O(n^2) within small buckets, not 11K^2.

    Examples it catches (which canonical_key + recanonicalize miss because
    the typo changes a letter, not whitespace/suffix):

        WAYNE STTAE UNIVERSITY       → WAYNE STATE UNIVERSITY
        LAWRENCE BERKLEY NATIONAL    → LAWRENCE BERKELEY NATIONAL
        SETON HALL UNIVERSOTY        → SETON HALL UNIVERSITY
        STIFFEL                      → STIFEL
        CONTINENTAL REALTY COORP…    → CONTINENTAL REALTY CORP…

    Canonical winner = the variant donors most often wrote (frequency).
    Ties broken by longer name, then alphabetical.

    Returns: number of rows changed.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        logger.warning("    rapidfuzz not installed — skipping typo merge")
        return 0

    ii = _indiv_idx(df)
    # Count frequencies across BOTH contributor_employer and previous_employer
    # so retiree-only names are weighed too.
    cols = [c for c in ('contributor_employer', 'previous_employer') if c in df.columns]
    series_list = []
    for c in cols:
        s = df.loc[ii, c].astype(str).str.strip().str.upper()
        s = s[(s != '') & (s != 'NAN')]
        series_list.append(s)
    if not series_list:
        return 0
    all_names = pd.concat(series_list, ignore_index=True)
    counts = all_names.value_counts()
    if counts.empty:
        return 0
    names = list(counts.index)

    # Bucket by first 4 alphanumeric chars for fast pairing
    buckets: dict[str, list[str]] = {}
    for n in names:
        key = re.sub(r'[^A-Z0-9]', '', n)[:4]
        if not key:
            continue
        buckets.setdefault(key, []).append(n)

    # Union-find so that A↔B and B↔C cluster A,B,C together
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        parent.setdefault(a, a); parent.setdefault(b, b)
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for bucket_names in buckets.values():
        if len(bucket_names) < 2:
            continue
        for i in range(len(bucket_names)):
            for j in range(i + 1, len(bucket_names)):
                a, b = bucket_names[i], bucket_names[j]
                if fuzz.token_sort_ratio(a, b) >= threshold:
                    union(a, b)

    # Build {variant -> canonical} mapping. canonical = most-frequent in
    # cluster; tie-break by longer name, then alpha.
    clusters: dict[str, list[str]] = {}
    for name in parent:
        clusters.setdefault(find(name), []).append(name)

    mapping = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = sorted(
            members,
            key=lambda v: (-counts.get(v, 0), -len(v), v)
        )[0]
        for m in members:
            if m != canonical:
                mapping[m] = canonical

    if not mapping:
        return 0

    n = 0
    # Apply to BOTH columns so retiree previous_employer typos
    # (e.g. "BEHAVIORIAL MEDICINE" via prev_cache) also get merged.
    for col in ('contributor_employer', 'previous_employer'):
        if col not in df.columns:
            continue
        col_u = df[col].astype(str).str.strip().str.upper()
        to_fix = col_u.isin(mapping)
        n_col = int(to_fix.sum())
        if n_col:
            df.loc[to_fix, col] = col_u[to_fix].map(mapping)
            n += n_col
    return n


def restore_display_suffixes(df: pd.DataFrame, raw_csv_path) -> int:
    """
    End-of-cleaning pass: restore the most-common original suffix-bearing
    form of each employer name from the raw FEC data.

    Earlier passes strip legal suffixes (INC, LLC, LLP, …) to merge variants
    under one canonical name. That's correct for matching/grouping, but the
    display value loses readability:

        HOUSING INC   (real-estate company)     →  HOUSING       (looks like a noun)
        GOOD HEALTH INC                          →  GOOD HEALTH
        VENSURE EMPLOYER SERVICES                →  VENSURE

    This pass re-introduces the most-common original form (per canonical
    grouping) so the saved CSV — and downstream DB — reads naturally:

        HOUSING       →  HOUSING INC
        GOOD HEALTH   →  GOOD HEALTH INC
        VENSURE       →  VENSURE EMPLOYER SERVICES (if it was the most common
                                                    raw form for that group)

    Idempotent. Only touches rows where the current cleaned name differs
    from the most-common raw form sharing the same canonical_key.

    Returns: number of rows changed.
    """
    from pathlib import Path
    if not Path(raw_csv_path).exists():
        return 0

    # Read raw employer column only — cheap
    raw = pd.read_csv(raw_csv_path, usecols=['contributor_employer'],
                      dtype=str, keep_default_na=False, low_memory=False)
    # Light cleanup: uppercase, strip, collapse multi-space and `+` to single
    # space. Catches typos like "FISHMAN, BLOCK + DIAMOND" → "FISHMAN, BLOCK
    # DIAMOND".
    raw['emp'] = (raw['contributor_employer'].str.strip().str.upper()
                  .str.replace(r'[\s+]+', ' ', regex=True)
                  # collapse a stray trailing dot/space tail ("INC. ." → "INC")
                  # so the restored display form doesn't carry FEC keying junk
                  .str.replace(r'(?:\s*\.)+\s*$', '', regex=True)
                  .str.strip())
    raw = raw[raw['emp'] != '']
    raw['key'] = raw['emp'].map(canonical_key)
    raw = raw[raw['key'] != '']

    if raw.empty:
        return 0

    # For each canonical key, pick the most-frequent original form
    most_common_form = (
        raw.groupby('key')['emp']
        .agg(lambda s: s.mode().iloc[0])
        .to_dict()
    )

    ii = _indiv_idx(df)
    current = df.loc[ii, 'contributor_employer']
    has_emp = current.notna() & (current.astype(str).str.strip() != '')
    target = ii[has_emp]
    if len(target) == 0:
        return 0

    cur_clean = current[has_emp].astype(str).str.strip().str.upper()
    keys = cur_clean.map(canonical_key)
    new_vals = keys.map(most_common_form).fillna(cur_clean)

    changed_mask = new_vals != cur_clean
    n_changed = int(changed_mask.sum())
    if n_changed:
        df.loc[target[changed_mask], 'contributor_employer'] = new_vals[changed_mask]
    return n_changed


def _recanonicalize_employers(df: pd.DataFrame) -> int:
    """
    Final employer re-canonicalization after all enhancements.

    The pipeline's _canonicalize_employers runs before enhancements, but
    enhancements convert & → AND, strip suffixes, and normalize names —
    creating new variant groups. This pass catches them.

    Returns: number of rows changed.
    """

    ii = _indiv_idx(df)
    # Collect frequencies across BOTH contributor_employer AND previous_employer
    # so retiree-only variants (e.g. "MCDERMOTT WILL & EMERY" coming only from
    # a retiree's previous job) merge under the same canonical as the current
    # employer's spelling ("MCDERMOTT WILL & EMERY LLP"). Without this, the
    # loader builds two `employers` rows for what is the same firm.
    cols = [c for c in ('contributor_employer', 'previous_employer') if c in df.columns]
    series = []
    for c in cols:
        s = df.loc[ii, c]
        s = s[s.notna() & (s.astype(str).str.strip() != '')]
        series.append(s)
    if not series:
        return 0
    all_names = pd.concat(series, ignore_index=True)
    counts = all_names.value_counts()
    if counts.empty:
        return 0

    groups = defaultdict(list)
    for name in counts.index:
        k = canonical_key(name)
        if k:
            groups[k].append(name)

    mapping = {}
    for k, variants in groups.items():
        if len(variants) < 2:
            continue
        canonical = sorted(variants,
                           key=lambda v: (-counts.get(v, 0), -len(v), v))[0]
        for v in variants:
            if v != canonical:
                mapping[v] = canonical

    if not mapping:
        return 0

    n = 0
    for c in cols:
        to_fix = df[c].isin(mapping)
        n_col = int(to_fix.sum())
        if n_col:
            df.loc[to_fix, c] = df.loc[to_fix, c].map(mapping)
            n += n_col

    return n

