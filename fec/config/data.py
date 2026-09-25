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

# Title -> occupation when occupation is empty; only titles that strongly imply one.
TITLE_TO_OCCUPATION = {
    'DR': ('DOCTOR', 'MEDICAL / HEALTHCARE'),
    'RABBI': ('RABBI', 'RELIGIOUS'),
    'CANTOR': ('CANTOR', 'RELIGIOUS'),
    'PASTOR': ('PASTOR', 'RELIGIOUS'),
    'DEACON': ('DEACON', 'RELIGIOUS'),
    'BISHOP': ('BISHOP', 'RELIGIOUS'),
    'FATHER': ('PRIEST', 'RELIGIOUS'),
    'SISTER': ('RELIGIOUS SISTER', 'RELIGIOUS'),
    'IMAM': ('IMAM', 'RELIGIOUS'),
    'REV': ('CLERGY', 'RELIGIOUS'),
    'REVEREND': ('CLERGY', 'RELIGIOUS'),
    'JUDGE': ('JUDGE', 'LEGAL'),
    'HON': ('JUDGE', 'LEGAL'),
    'HONORABLE': ('JUDGE', 'LEGAL'),
    'PROF': ('PROFESSOR', 'EDUCATION'),
    'PROFESSOR': ('PROFESSOR', 'EDUCATION'),
    'AMB': ('AMBASSADOR', 'GOVERNMENT'),
    'AMBASSADOR': ('AMBASSADOR', 'GOVERNMENT'),
}

# Suffixes stripped from last names (SMITH JR. -> SMITH). V/IV need the comma
# prefix so names like RAVIV or ones ending in IV survive.
SUFFIX_RE = re.compile(
    r',?\s+(JR\.?|SR\.?|III|II|ESQ\.?)\s*$|,\s*(IV|V)\s*$',
    re.IGNORECASE,
)

# Professional suffixes in last name (MILLER MD -> MILLER); handled separately.
PRO_SUFFIX_RE = re.compile(
    r'\s+(MD|M\.D\.?|DDS|D\.D\.S\.?|PHD|PH\.D\.?|DO|D\.O\.?|FACS|FAAOS)\s*$',
    re.IGNORECASE,
)

# Keywords that indicate an organization, not an individual; every token has a
# proven hit on the real raw names (per-token census, 2026-07)
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

# Individual names: "LASTNAME, F..." (FEC standard). Spaces/parens allowed in
# the last name; `*` after the first letter so single-letter surnames match
# ("Y, IVAN"); backticks and semicolons are tolerated as stray punctuation.
INDIV_NAME_RE = re.compile(r"^[A-Z][\w\s.'\-()`;]*,\s*[A-Z]")

# Trailing junk on committee names (", SOMETOWN", appended candidate name) but
# not a legal suffix (", INC"). 30-char cap covers "FIRST MIDDLE LAST TITLE".
COMM_TAIL_RE = re.compile(r',\s+(?!INC|LLC|LLP|CORP|JR|SR|PA\s*$)[A-Z][A-Z.\s]{0,30}$')

RETIRE_RE = re.compile(r'RETIRE', re.IGNORECASE)

# Output column order. recipient_committee is the PAC that RECEIVED the money,
# resolved from the FEC committee_id via data/database/committees.csv.
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
    # work; contributor_employer is the per-donor unified name — the raw
    # per-filing value (contributor_employer_original) is internal-only
    'contributor_employer',
    'contributor_occupation', 'occupation_category',
    # internal working fields read by later safety nets; dropped at save
    'occupation_status', 'committee_type', '_generational_suffix',
    # contribution; contributor_year deliberately absent (DB derives it), as
    # are is_refund / is_zero_amount (amount alone suffices; refunds < 0).
    # Refund rows are almost all COMMITTEE/PAC, but the rare INDIVIDUAL refund
    # exists and is load-bearing: dropping it would overstate that donor's net
    'contribution_receipt_date',
    'contribution_receipt_amount',
]

# Working/provenance columns that must never reach the cleaned CSV — every
# writer (clean / geocode / resolve) drops these before to_csv. Coordinates
# are kept; only the *_level "how it was geocoded" markers are dropped.
INTERNAL_OUTPUT_COLUMNS = [
    'is_individual',  # redundant with entity_type
    'contributor_employer_original',  # raw per-filing employer (for prev-employer)
    'committee_type',  # overloaded with non-committee sentinels
    'occupation_status',  # ~99% derivable; not in DB
    '_generational_suffix',  # identity evidence from the raw contributor name
    'resolve_method',  # how the employer address was resolved
    'resolve_confidence',  # confidence of the above
    'geocode_level',  # how the donor coordinate was derived
    'employer_geocode_level',  # how the employer coordinate was derived
]

# Final cleaned CSV contract.
FINAL_OUTPUT_COLUMNS = [
    column for column in OUTPUT_COLUMNS
    if column not in INTERNAL_OUTPUT_COLUMNS
] + [
    'previous_employer',
    'donor_key',
    'latitude',
    'longitude',
    'employer_status',
]
