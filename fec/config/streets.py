"""
config/streets.py — Address / street normalization patterns.

PO Box, unit extraction, direction abbreviations,
street type abbreviations, and unit abbreviation rules.
"""
import re

from fec.config.geography import US_STATES


# PO Box (various formats → "PO BOX")
POBOX_RE = re.compile(r'\bP\.?\s*O\.?\s*BOX\b|\bPOST\s+OFFICE\s+BOX\b', re.I)

# Extract unit info from end of street_1 (APT 5, STE 200, UNIT B, etc.)
UNIT_EXTRACT = re.compile(
    r'(?:\s+|,\s*)'
    r'((?:APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|DEPT|DEPARTMENT'
    r'|OFF|OFFICE|FLOOR|RM|ROOM|PH|PENTHOUSE)'
    r'\.?\s+[\w\-\/\.]+\s*'
    r'|(?:FL|FLR)\.?\s+\d{1,2}\s*'  # FL/FLR only with 1-2 digit floor (avoids FL=Florida+ZIP)
    r')$',
    re.I,
)

# Extract # units from end of street_1 (#5A, # 200, BLVD#300, etc.)
HASH_EXTRACT = re.compile(r'(?:\s+|(?<=\w))(#\s*[\w\-\/\.]+\s*)$')

# State code stuck at end of city name ("ENCINO, CA" → "ENCINO")
STATE_IN_CITY = re.compile(r'\s*,?\s+(' + '|'.join(US_STATES) + r')\s*$')


# --- Direction abbreviations (NORTH → N, SOUTHWEST → SW, etc.) ---

def _direction_rule(word: str, abbr: str, prefix: bool = True) -> tuple:
    """Build a (regex, replacement) pair for direction abbreviation."""
    if prefix:
        return (re.compile(rf'^{word}\b\s+', re.I), f'{abbr} ')
    return (re.compile(rf'\s+{word}\s*$', re.I), f' {abbr}')

# Additional: direction words after house number (e.g. "123 NORTH MAIN ST")
DIR_MID = [
    (re.compile(rf'(\d\s+){w}\b', re.I), rf'\g<1>{a}')
    for w, a in [
        ('NORTHWEST', 'NW'), ('NORTHEAST', 'NE'),
        ('SOUTHWEST', 'SW'), ('SOUTHEAST', 'SE'),
        ('NORTH', 'N'), ('SOUTH', 'S'),
        ('EAST', 'E'), ('WEST', 'W'),
    ]
]

DIR_PREFIX = [
    _direction_rule('NORTHWEST', 'NW'), _direction_rule('NORTHEAST', 'NE'),
    _direction_rule('SOUTHWEST', 'SW'), _direction_rule('SOUTHEAST', 'SE'),
    _direction_rule('NORTH', 'N'),      _direction_rule('SOUTH', 'S'),
    _direction_rule('EAST', 'E'),       _direction_rule('WEST', 'W'),
]

DIR_SUFFIX = [
    _direction_rule('NORTHWEST', 'NW', False), _direction_rule('NORTHEAST', 'NE', False),
    _direction_rule('SOUTHWEST', 'SW', False), _direction_rule('SOUTHEAST', 'SE', False),
    _direction_rule('NORTH', 'N', False),      _direction_rule('SOUTH', 'S', False),
    _direction_rule('EAST', 'E', False),       _direction_rule('WEST', 'W', False),
]


# --- Street type abbreviations (STREET → ST, AVENUE → AVE, etc.) ---

STREET_TYPES = [
    (re.compile(rf'\b{full}\b', re.I), abbr)
    for full, abbr in [
        ('STREET', 'ST'),       ('AVENUE', 'AVE'),      ('ROAD', 'RD'),
        ('BOULEVARD', 'BLVD'),  ('DRIVE', 'DR'),        ('LANE', 'LN'),
        ('COURT', 'CT'),        ('CIRCLE', 'CIR'),      ('PLACE', 'PL'),
        ('PARKWAY', 'PKWY'),    ('HIGHWAY', 'HWY'),     ('TERRACE', 'TER'),
        ('TURNPIKE', 'TPKE'),   ('EXPRESSWAY', 'EXPY'), ('SQUARE', 'SQ'),
        ('WAY', 'WAY'),         ('TRAIL', 'TRL'),       ('CROSSING', 'XING'),
        ('JUNCTION', 'JCT'),    ('MOUNT', 'MT'),
    ]
]


# --- Unit abbreviations (SUITE → STE, APARTMENT → APT, etc.) ---

UNIT_RULES = [
    (re.compile(rf'^{pattern}', re.I), abbr)
    for pattern, abbr in [
        (r'SUITE\b',      'STE'),      (r'STE\.\s*',     'STE '),
        (r'APARTMENT\b',  'APT'),      (r'APT\.\s*',     'APT '),
        (r'FLOOR\b',      'FL'),       (r'FL\.\s*',      'FL '),
        (r'BUILDING\b',   'BLDG'),     (r'BLDG\.?\s*',   'BLDG '),
        (r'DEPARTMENT\b', 'DEPT'),     (r'DEPT\.?\s*',   'DEPT '),
        (r'OFFICE\b',     'OFF'),      (r'OFF\.?\s*',    'OFF '),
        (r'#\s*',         '# '),
    ]
]
