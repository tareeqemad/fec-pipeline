"""Missing-value sentinels, committee/name patterns, and output column contract."""
import re

# FEC junk values that all mean "missing"
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
    'AN EMPLOYER',
    'AN OCCUPATION',
    # FEC admin notes / form placeholders that leaked as "employers"
    'BEST EFFORT', 'BEST EFFORT SENT', 'BEST EFFORTS',
    '2ND REQUEST MADE', '2ND LETTER MAILED', '2ND REQUEST LETTER MAILED',
    'REQUESTING VIA MAIL',
    'NONE PROVIDED', 'REQUESTED', 'INFORMATION REQUESTED PER BEST EFFO',
    '-SELECT-', 'SELECT', 'PLEASE SELECT', 'TBD', 'EMPLOYED',
    '.', '-', '',
})

# committee classification, first match wins
COMM_PATTERNS = [
    ('CONGRESSIONAL CAMPAIGN', re.compile(r'FOR CONGRESS|REP\.', re.IGNORECASE)),
    ('SENATE CAMPAIGN', re.compile(r'FOR SENATE|SEN\.', re.IGNORECASE)),
    ('POLITICAL ACTION COMMITTEE', re.compile(r'\bPAC\b', re.IGNORECASE)),
    (
        'PARTY ORGANIZATION',
        re.compile(r'PARTY|NRSC|NRCC|DCCC|DSCC|DNC|RNC', re.IGNORECASE),
    ),
    ('POLITICAL COMMITTEE', re.compile(r'COMMITTEE', re.IGNORECASE)),
]

# Titles stripped from first names (DR. JOHN -> JOHN)
TITLE_RE = re.compile(
    r'^(DR\.?|MR\.?|MRS\.?|MS\.?|REV\.?|REVEREND|'
    r'RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|IMAM|'
    r'JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|'
    r'SGT\.?|CAPT\.?|COL\.?|MAJ\.?|GEN\.?|LT\.?|'
    r'CAPTAIN|COLONEL|MAJOR|GENERAL|LIEUTENANT|SERGEANT|'
    r'AMBASSADOR|AMB\.?|COMMISSIONER|MAYOR)\s+',
    re.IGNORECASE,
)

# Last-name suffixes stripped; V/IV need a comma before.
SUFFIX_RE = re.compile(
    r',?\s+(JR\.?|SR\.?|III|II|ESQ\.?)\s*$|,\s*(IV|V)\s*$',
    re.IGNORECASE,
)

# Professional suffixes in last names (MILLER MD), handled separately.
PRO_SUFFIX_RE = re.compile(
    r'\s+(MD|M\.D\.?|DDS|D\.D\.S\.?|PHD|PH\.D\.?|DO|D\.O\.?|FACS|FAAOS)\s*$',
    re.IGNORECASE,
)

# Organization keywords, each proven on real raw names.
ORG_KEYWORDS = re.compile(
    r'FOR CONGRESS|FOR SENATE|FOR AMERICA'
    r'|,\s+[\w\s]+\s+(?:REP|SEN)\.'                     # ", MIKE REP." / ", SHELLEY MOORE SEN."
    r'| FOR [A-Z]{2,},\s'                              # "FOR LOUISIANA, " "FOR NH, "
    r'|COMMITTEE|PAC |PAC$'
    r'|\b(?:NRSC|NRCC|DCCC|DSCC|RNC)\b'
    r'| (?:INC\.?|LLC|LLP|LP\b|CORP|ASSOC|FUND|TRUST|FOUNDATION'
    r'|HOLDINGS|INVESTMENT|PARTNERS|PARTNERSHIP|COMPANY|COUNCIL'
    r'|SERVICES|MEDIA|PROPERTY|PROPERTIES|TRADES|GROUP|VENTURES)'
    r'|(?:DEMOCRATIC|REPUBLICAN) '
    r'| (?:BROTHERS|EQUITIES|STEEL|LENDING|CONGREGATION)',
    re.IGNORECASE,
)

# Individual names in FEC "LASTNAME, F..." form.
INDIV_NAME_RE = re.compile(r"^[A-Z][\w\s.'\-()`;]*,\s*[A-Z]")

# Trailing junk on committee names, but not legal suffixes.
COMM_TAIL_RE = re.compile(r',\s+(?!INC|LLC|LLP|CORP|JR|SR|PA\s*$)[A-Z][A-Z.\s]{0,30}$')

# the substring "RETIRE" anywhere, case-insensitive: "RETIRED", "RETIREE"
RETIRE_RE = re.compile(r'RETIRE', re.IGNORECASE)

# Output column order; recipient_committee is the receiving PAC.
OUTPUT_COLUMNS = [
    # ids
    'sub_id', 'transaction_id', 'two_year_transaction_period', 'recipient_committee',
    # entity (is_individual removed — entity_type is canonical)
    'entity_type',
    # name
    'contributor_name', 'contributor_first_name', 'contributor_last_name',
    # address
    'contributor_street_1', 'contributor_street_2',
    'contributor_city', 'contributor_state', 'contributor_zip',
    # Work; contributor_employer is the per-donor unified name.
    'contributor_employer',
    'contributor_occupation', 'occupation_category',
    # Internal working fields for later safety nets; dropped at save.
    'occupation_status', 'committee_type', '_generational_suffix',
    # Contribution; amounts alone mark refunds (below zero).
    'contribution_receipt_date',
    'contribution_receipt_amount',
    # What FEC itself says about the contributor.
    'fec_entity_type', 'fec_contributor_id', 'fec_image_number',
]

# Working columns no writer may save; coordinates are kept.
INTERNAL_OUTPUT_COLUMNS = [
    'is_individual',  # redundant with entity_type
    'contributor_employer_original',  # raw per-filing employer (for prev-employer)
    'committee_type',  # overloaded with non-committee sentinels
    'occupation_status',  # ~99% derivable; not in DB
    'employment_source',  # kept in the cleaning audit only
    '_generational_suffix',  # identity evidence from the raw contributor name
    'geocode_level',  # how the donor coordinate was derived
    # FEC's own type and id, read while cleaning, never written.
    'fec_entity_type',
    'fec_contributor_id',
    'fec_image_number',
]
