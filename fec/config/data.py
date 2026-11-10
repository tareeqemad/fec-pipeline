"""Missing-value sentinels, committee/name patterns, and output column contract."""
import re
from fec.config.tables import words

# FEC junk values that all mean "missing"
MISSING_VALUES = words("missing_values")

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

# Individual names in FEC "LASTNAME, F..." form.
INDIV_NAME_RE = re.compile(r"^[A-Z][\w\s.'\-()`;]*,\s*[A-Z]")

# Trailing junk on committee names, but not legal suffixes.
COMM_TAIL_RE = re.compile(r',\s+(?!INC|LLC|LLP|CORP|JR|SR|PA\s*$)[A-Z][A-Z.\s]{0,30}$')

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
    '_generational_suffix',
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
    '_generational_suffix',  # identity evidence from the raw contributor name
    'geocode_level',  # how the donor coordinate was derived
    # FEC's own type and id, read while cleaning, never written.
    'fec_entity_type',
    'fec_contributor_id',
    'fec_image_number',
]
