"""Address and street normalization patterns."""
import re

from fec.config.geography import US_STATES


# PO Box in any format
POBOX_RE = re.compile(
    r'\bP\.?\s*O\.?\s*BOX\b|\bPOST\s+OFFICE\s+BOX\b',
    re.IGNORECASE,
)

# Unit info at end of street_1 (APT 5, STE 200, UNIT B, ...)
UNIT_EXTRACT = re.compile(
    r'(?:\s+|,\s*)'
    r'((?:APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|DEPT|DEPARTMENT'
    r'|OFF|OFFICE|FLOOR|RM|ROOM|PH|PENTHOUSE)'
    r'\.?\s+[\w\-\/\.]+\s*'
    r'|(?:FL|FLR)\.?\s+\d{1,2}\s*'  # FL/FLR only with 1-2 digit floor (avoids FL=Florida+ZIP)
    r')$',
    re.IGNORECASE,
)

# Hash units at end of street_1 (#5A, # 200, BLVD#300, ...)
HASH_EXTRACT = re.compile(r'(?:\s+|(?<=\w))(#\s*[\w\-\/\.]+\s*)$')

# State code stuck at end of city name ("ENCINO, CA"); sorted so the compiled
# pattern is identical across interpreter runs (frozenset order is not)
STATE_IN_CITY = re.compile(r'\s*,?\s+(' + '|'.join(sorted(US_STATES)) + r')\s*$')


# direction abbreviations

def _direction_rule(word: str, abbr: str, prefix: bool = True) -> tuple:
    """Build a (regex, replacement) pair for direction abbreviation."""
    if prefix:
        return (re.compile(rf'^{word}\b\s+', re.IGNORECASE), f'{abbr} ')
    return (re.compile(rf'\s+{word}\s*$', re.IGNORECASE), f' {abbr}')


_DIRECTIONS = [
    ('NORTHWEST', 'NW'), ('NORTHEAST', 'NE'),
    ('SOUTHWEST', 'SW'), ('SOUTHEAST', 'SE'),
    ('NORTH', 'N'), ('SOUTH', 'S'), ('EAST', 'E'), ('WEST', 'W'),
]

# Direction words after a house number ("123 NORTH MAIN ST")
DIR_MID = [
    (re.compile(rf'(\d\s+){word}\b', re.IGNORECASE), rf'\g<1>{abbr}')
    for word, abbr in _DIRECTIONS
]

DIR_PREFIX = [
    _direction_rule(word, abbr) for word, abbr in _DIRECTIONS
]

DIR_SUFFIX = [
    _direction_rule(word, abbr, False) for word, abbr in _DIRECTIONS
]


# street type abbreviations

STREET_TYPES = [
    (re.compile(rf'\b{full}\b', re.IGNORECASE), abbr)
    for full, abbr in [
        ('STREET', 'ST'), ('AVENUE', 'AVE'), ('ROAD', 'RD'),
        ('BOULEVARD', 'BLVD'), ('DRIVE', 'DR'), ('LANE', 'LN'),
        ('COURT', 'CT'), ('CIRCLE', 'CIR'), ('PLACE', 'PL'),
        ('PARKWAY', 'PKWY'), ('HIGHWAY', 'HWY'), ('TERRACE', 'TER'),
        ('TURNPIKE', 'TPKE'), ('EXPRESSWAY', 'EXPY'), ('SQUARE', 'SQ'),
        ('TRAIL', 'TRL'), ('CROSSING', 'XING'),
        ('JUNCTION', 'JCT'), ('MOUNT', 'MT'),
    ]
]


# unit abbreviations

UNIT_RULES = [
    (re.compile(rf'^{pattern}', re.IGNORECASE), abbr)
    for pattern, abbr in [
        (r'SUITE\b', 'STE'), (r'STE\.\s*', 'STE '),
        (r'APARTMENT\b', 'APT'), (r'APT\.\s*', 'APT '),
        (r'FLOOR\b', 'FL'), (r'FL\.\s*', 'FL '),
        (r'BUILDING\b', 'BLDG'), (r'BLDG\.?\s*', 'BLDG '),
        (r'DEPARTMENT\b', 'DEPT'), (r'DEPT\.?\s*', 'DEPT '),
        (r'OFFICE\b', 'OFF'), (r'OFF\.?\s*', 'OFF '),
        (r'#\s*', '# '),
    ]
]


# Human-verified FEC street typos. These are exact word replacements, not
# fuzzy matching: a near-looking street can still be a real different place.
STREET_TYPO_RULES = [
    (re.compile(r'\bOLYMIC\b', re.IGNORECASE), 'OLYMPIC'),
    (re.compile(r'\bSUTE\b', re.IGNORECASE), 'SUITE'),
]


# Exact corrections verified against Census or the donor's repeated filings.
# Values: street, unit, city, state, ZIP. None preserves the current value.
VERIFIED_ADDRESS_FIXES = {
    ('1100 SUPERIOR AVE E', 'OH', '44114'): ('1100 E SUPERIOR AVE', None, None, None, None),
    ('112 PINEBROOK DR W', 'AL', '36608'): ('112 W PINEBROOK DR', None, None, None, None),
    ('1742 GOLF RIDGE DR S', 'MI', '48302'): ('1742 S GOLF RIDGE DR', None, None, None, None),
    ('17800 LAUREL PARK DR N', 'MI', '48152'): ('17800 N LAUREL PARK DR', None, None, None, None),
    ('3750 LAS VEGAS BLVD S', 'NV', '89158'): ('3750 S LAS VEGAS BLVD', None, None, None, None),
    ('60 COMMONWEALTH PARK W', 'MA', '02459'): ('60 W COMMONWEALTH PARK', None, None, None, None),
    ('672 LAKEWOODE CIR E', 'FL', '33445'): ('672 E LAKEWOODE CIR', None, None, None, None),
    ('7 RIDGEVIEW RD N', 'FL', '34996'): ('7 N RIDGEVIEW RD', None, None, None, None),
    ('78 COMMONWEALTH PARK W', 'MA', '02459'): ('78 W COMMONWEALTH PARK', None, None, None, None),
    ('840 HOWELL ST N', 'MN', '55104'): ('840 N HOWELL ST', None, None, None, None),
    ('704C 13TH ST E', 'MT', '59937'): ('704C E 13TH ST', 'STE 260', None, None, None),
    ('42 W E 48TH ST', 'NY', '10017'): ('42 W 48TH ST', 'STE 706-707', None, None, '10036'),
    ('159 W 159 W', 'NY', '10023'): ('159 W 74TH ST', 'APT GR', None, None, None),
    ('200 W SREET', 'NY', '10282'): ('200 W ST', None, None, None, None),
    ('235 DEERCROFT DR', 'VA', '24069'): ('235 DEERCROFT DR', None, None, None, '24060'),
    ('240 MPARK LN', 'CA', '92027'): ('240 PARK LN', None, None, None, '94027'),
    ('437 MADISON AVE ST', 'NY', '10022'): ('437 MADISON AVE', None, None, None, None),
    ('9 W 57 ST', 'NY', '10506'): ('9 W 57TH ST', None, None, None, '10019'),
    ('928 BROADWAY AVE', 'NY', '10010'): ('928 BROADWAY', None, None, None, None),
    ('1101 IVEAN PEARSON RD', 'CA', '90292'): ('1101 IVEAN PEARSON RD', None, 'LAGO VISTA', 'TX', '78645'),
    ('268 CHESTNUT ST', 'NY', '11963'): ('268 CHESTNUT ST', None, 'ENGLEWOOD', 'NJ', '07631'),
}
