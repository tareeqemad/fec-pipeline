"""
config/data.py — Remaining constants, patterns, and mappings for FEC cleaning.

Split into submodules:
  - geography.py  — US_STATES, STATE_NAMES
  - cities.py     — CITY_NORMALIZE
  - employers.py  — EMPLOYER_NORMALIZE
  - occupations.py — OCCUPATION_NORMALIZE, OCCUPATION_FIXES, CATEGORY_*
  - streets.py    — POBOX_RE, UNIT_EXTRACT, DIR_*, STREET_TYPES, UNIT_RULES

This file contains:
  - MISSING_VALUES
  - Committee patterns (COMM_PATTERNS)
  - Name patterns (TITLE_RE, SUFFIX_RE, etc.)
  - ORG_KEYWORDS, INDIV_NAME_RE, COMM_TAIL_RE, RETIRE_RE
  - OUTPUT_COLUMNS
  - FIRST_NAME_FIXES
"""
import re



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FEC junk values — these all mean "missing"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MISSING_VALUES = frozenset({
    'INFORMATION REQUESTED',
    'INFO REQUESTED',
    'INFORMATION REQUESTED PER BEST EFFORTS',
    'NONE', '--NONE--',
    'N/A', 'NA',
    'UNKNOWN',
    'NOT AVAILABLE',
    'NONEB',
    'REFUSED',
    'NONE OF YOUR BUSINESS',
    'NOT PROVIDED',
    'NOT APPLICABLE',
    # Verified from real FEC data
    'AN EMPLOYER',    # placeholder employer
    'AN OCCUPATION',  # placeholder occupation
    # FEC administrative notes / form placeholders that leaked as "employers"
    'BEST EFFORT', 'BEST EFFORT SENT', 'BEST EFFORTS',
    '2ND REQUEST MADE', '2ND LETTER MAILED', '2ND REQUEST LETTER MAILED',
    'REQUESTING VIA MAIL',
    'NONE PROVIDED', 'REQUESTED', 'INFORMATION REQUESTED PER BEST EFFO',
    '-SELECT-', 'SELECT', 'PLEASE SELECT', 'TBD', 'EMPLOYED',
    '.', '-', '',
})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Committee classification patterns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMM_PATTERNS = [
    ('CONGRESSIONAL CAMPAIGN',     re.compile(r'FOR CONGRESS|REP\.', re.I)),
    ('SENATE CAMPAIGN',            re.compile(r'FOR SENATE|SEN\.', re.I)),
    ('POLITICAL ACTION COMMITTEE', re.compile(r'\bPAC\b', re.I)),
    ('PARTY ORGANIZATION',         re.compile(r'PARTY|NRSC|NRCC|DCCC|DSCC|DNC|RNC', re.I)),
    ('POLITICAL COMMITTEE',        re.compile(r'COMMITTEE', re.I)),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Name patterns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Titles to strip from first names (DR. JOHN → JOHN)
TITLE_RE = re.compile(
    r'^(DR\.?|MR\.?|MRS\.?|MS\.?|REV\.?|REVEREND|'
    r'RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|IMAM|'
    r'JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|'
    r'SGT\.?|CAPT\.?|COL\.?|MAJ\.?|GEN\.?|LT\.?|'
    r'CAPTAIN|COLONEL|MAJOR|GENERAL|LIEUTENANT|SERGEANT|'
    r'AMBASSADOR|AMB\.?|COMMISSIONER|MAYOR)\s+',
    re.I,
)


# Map professional/religious titles → occupation (used when occupation is empty).
# Only includes titles that strongly imply a specific occupation.
TITLE_TO_OCCUPATION = {
    'DR':        ('DOCTOR',          'MEDICAL / HEALTHCARE'),
    'RABBI':     ('RABBI',           'RELIGIOUS'),
    'CANTOR':    ('CANTOR',          'RELIGIOUS'),
    'PASTOR':    ('PASTOR',          'RELIGIOUS'),
    'DEACON':    ('DEACON',          'RELIGIOUS'),
    'BISHOP':    ('BISHOP',          'RELIGIOUS'),
    'FATHER':    ('PRIEST',          'RELIGIOUS'),
    'SISTER':    ('RELIGIOUS SISTER','RELIGIOUS'),
    'IMAM':      ('IMAM',            'RELIGIOUS'),
    'REV':       ('CLERGY',          'RELIGIOUS'),
    'REVEREND':  ('CLERGY',          'RELIGIOUS'),
    'JUDGE':     ('JUDGE',           'LEGAL'),
    'HON':       ('JUDGE',           'LEGAL'),
    'HONORABLE': ('JUDGE',           'LEGAL'),
    'PROF':      ('PROFESSOR',       'EDUCATION'),
    'PROFESSOR': ('PROFESSOR',       'EDUCATION'),
    'AMB':       ('AMBASSADOR',      'GOVERNMENT'),
    'AMBASSADOR':('AMBASSADOR',      'GOVERNMENT'),
}


# Suffixes to strip from last names (SMITH JR. → SMITH)
# NOTE: V and IV require word boundary + comma/space prefix to avoid
# damaging names like RAVIV or ending in IV naturally
SUFFIX_RE = re.compile(r',?\s+(JR\.?|SR\.?|III|II|ESQ\.?)\s*$|,\s*(IV|V)\s*$', re.I)

# Professional suffixes in last name (MILLER MD → MILLER)
# Applied separately because they need different handling
PRO_SUFFIX_RE = re.compile(r'\s+(MD|M\.D\.?|DDS|D\.D\.S\.?|PHD|PH\.D\.?|DO|D\.O\.?|FACS|FAAOS)\s*$', re.I)

# Keywords that indicate an organization, not an individual
ORG_KEYWORDS = re.compile(
    r'FOR CONGRESS|FOR SENATE|FOR AMERICA|FOR GOVERNOR|FOR PRESIDENT'
    r'|,\s+[\w\s]+\s+(?:REP|SEN)\.'                     # ", MIKE REP." / ", SHELLEY MOORE SEN."
    r'| FOR [A-Z]{2,},\s'                              # "FOR LOUISIANA, " "FOR NH, "
    r'|COMMITTEE|PAC |PAC$|PARTY'
    r'|NRSC|NRCC|DCCC|DSCC|DNC|RNC'
    r'| INC\.?| LLC| LLP| LP\b| CORP| ASSOC| FUND| TRUST| FOUNDATION'
    r'| HOLDINGS| INVESTMENT| PARTNERS| PARTNERSHIP| COMPANY| COUNCIL'
    r'| SERVICES| MEDIA| PROPERTY| PROPERTIES| TRADES| GROUP| VENTURES'
    r'|DEMOCRATIC |REPUBLICAN '
    r'| BROTHERS| EQUITIES| STEEL| LENDING| CONGREGATION',  # business entity patterns
    re.I,
)


# Pattern for individual names: "LASTNAME, F..." (FEC standard format)
# Allows spaces in last name for compound names like "MILLER MD, PHILIP"
# and parenthetical names like "TAUBER (OPPENHEIMER), KENNETH".
# `*` (not `+`) after the first letter so single-letter surnames match
# ("Y, IVAN", "U, MATTHEW" — real people FEC mis-flagged as non-individual).
# Backtick tolerated for stray-punctuation names like "GORDON`, STEVE`".
INDIV_NAME_RE = re.compile(r"^[A-Z][\w\s.'\-()`]*,\s*[A-Z]")

# Trailing junk on committee names (e.g. ", SOMETOWN" or a candidate name FEC
# appended like ", MARIE GLUESENKAMP REP.") but not a legal suffix (", INC").
# Length cap is 30 to cover a full "FIRST MIDDLE LAST TITLE" tail.
COMM_TAIL_RE = re.compile(r',\s+(?!INC|LLC|LLP|CORP|JR|SR|PA\s*$)[A-Z][A-Z.\s]{0,30}$')

# Exact-name corrections for committees that arrive mangled in raw FEC data
# (e.g. the filer dropped the spaces). Applied after tail-stripping — add a
# new row here as more mangled committee names surface.
COMMITTEE_NAME_FIXES = {
    'BELLFORMISSOURI':  'BELL FOR MISSOURI',
    # jewelry firm mis-filed as a committee — keyed on both the raw form
    # ("...INC") and the post-suffix-strip form, so the merge lands either way
    'SOLOW AND CO':     'SOLOW & CO.',
    'SOLOW AND CO INC': 'SOLOW & CO.',
    # same FEC committee C00502575 (Dr. Raul Ruiz, CA) — some filings drop
    # the "DR" the candidate registered with
    'RAUL RUIZ FOR CONGRESS': 'DR RAUL RUIZ FOR CONGRESS',
}

# Matches any form of "RETIRE" (RETIRED, RETIREE, etc.)
RETIRE_RE = re.compile(r'RETIRE', re.I)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Output column order
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT_COLUMNS = [
    # IDs
    # recipient_committee is the human-readable name of the PAC that RECEIVED
    # the contribution (AIPAC / DMFI / UDP), resolved from the raw FEC
    # committee_id via data/database/committees.csv — the single source of
    # truth. The cryptic FEC number is not exported.
    'sub_id', 'transaction_id', 'two_year_transaction_period', 'recipient_committee',

    # Entity
    'entity_type',
    # NOTE: is_individual removed — entity_type is the canonical field.
    # is_individual is used internally during cleaning but not in output.

    # Name
    'contributor_name', 'contributor_first_name', 'contributor_last_name',

    # Address
    # state_name flows through the pipeline (some steps read it) but is in
    # INTERNAL_OUTPUT_COLUMNS, so it's dropped at save — a redundant translation
    # of contributor_state (NY → New York); the DB derives it from us_states.
    'contributor_street_1', 'contributor_street_2',
    'contributor_city', 'contributor_state', 'state_name', 'contributor_zip',

    # Work
    # contributor_employer is the clean, per-donor unified company name — the
    # single source of truth in the output. The raw per-filing value
    # (contributor_employer_original) is kept internally for previous_employer
    # resolution but intentionally NOT exported: two employer columns that
    # diverge (e.g. RETIRED vs NOT EMPLOYED, suffix-stripped vs raw) confuse
    # anyone reading the cleaned file.
    'contributor_employer',
    'contributor_occupation', 'occupation_category',
    # committee_type and employer_change_type are working columns that later
    # cleaning steps (safety nets) read, so they must flow through the pipeline —
    # but they're in INTERNAL_OUTPUT_COLUMNS and dropped at save. entity_type
    # already says whether a row is a committee (committee_type was overloaded
    # with non-committee sentinels); employer_change_type is an internal QA flag.
    'occupation_status', 'committee_type', 'employer_change_type',

    # Contribution
    # contributor_year is intentionally NOT exported — it's just the year of
    # contribution_receipt_date (the DB derives it in a view via EXTRACT), so a
    # standalone column would be redundant in the cleaned file.
    'contribution_receipt_date',
    'contribution_receipt_amount',
    # Note: is_refund / is_zero_amount are intentionally excluded.
    # All refunds (amount < 0) belong to COMMITTEE/PAC — not individuals.
    # The amount field itself is sufficient; no separate flag needed.
]


# Internal columns that must never reach the cleaned CSV. They are working
# state (recreated after the output-column selection) or pipeline provenance
# that describes HOW a value was derived — never loaded into the DB and pure
# noise to anyone reading the file. Every writer (clean / geocode / resolve)
# drops these before to_csv. Coordinates (latitude/longitude) are kept; only
# the *_level "how it was geocoded" markers are dropped.
INTERNAL_OUTPUT_COLUMNS = [
    'is_individual',                   # redundant with entity_type
    'contributor_employer_original',   # raw per-filing employer (for prev-employer)
    'committee_type',         # overloaded with non-committee sentinels
    'occupation_status',      # ~99% derivable (committee / occupation-presence); not in DB
    'state_name',             # redundant translation of contributor_state
    'employer_change_type',   # internal QA flag
    'resolve_method',         # how the employer address was resolved
    'resolve_confidence',     # confidence of the above
    'geocode_level',          # how the donor coordinate was derived
    'employer_geocode_level', # how the employer coordinate was derived
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Garbled first name corrections
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Keyboard/data-entry errors verified against the dataset:
# each wrong form appears at exactly one address with one last name,
# where the correct form is the dominant spelling (3x+ more common).
# Real names (MORTY, CHERIE, SIG, MILT, RODDY, GABRIELE, etc.) are excluded.
FIRST_NAME_FIXES = {
    'JEFREY':    'JEFFREY',    # missing F
    'ROBERTB':   'ROBERT',     # trailing B key
    'PETGER':    'PETER',      # G for T swap
    'DORUS':     'DORIS',      # U for I swap
    'RICHAR':    'RICHARD',    # truncated D
    'RUSELL':    'RUSSELL',    # missing S
    'MARRISSA':  'MARISSA',    # double R
    'STWART':    'STEWART',    # missing E
    'MQRY':      'MARY',       # Q for A key
    'RHONDS':    'RHONDA',     # S for A key
    'YEHDUI':    'YEHUDI',     # transposed U/D
    'ARLEBE':    'ARLENE',     # B for N key
    'ERVI':      'ERVIN',      # truncated N
    'BRIA':      'BRIAN',      # truncated N
    'CECIIA':    'CECILIA',    # missing L
    'WILIAM':    'WILLIAM',    # missing L
    'MATTHWE':   'MATTHEW',    # transposed W/E
    'MATTHW':    'MATTHEW',    # truncated + transposed
    'DWNNIS':    'DENNIS',     # W for E key
    'CAROLL':    'CAROL',      # extra L
    'ERR':       'ERRAN',      # truncated AN
    'AURI':      'AURIEL',     # truncated EL
    'JOIN':      'JON',        # extra I key
    'ISSAC':     'ISAAC',      # transposed S
    'JONAATHAN': 'JONATHAN',   # extra A
    'JILLIN':    'JILLIAN',    # missing A
    'LARYL':     'LARRY',      # trailing L
    'JUDITY':    'JUDITH',     # Y for H swap
    'PHILIP.':   'PHILIP',     # trailing period
}

