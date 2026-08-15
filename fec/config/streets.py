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
]
