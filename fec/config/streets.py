"""Address and street normalization patterns."""
import re

from fec.config.geography import US_STATES


# PO Box in any format
POBOX_RE = re.compile(
    r'\bP\.?\s*O\.?\s*BOX\b|\bPOST\s+OFFICE\s+BOX\b',
    re.IGNORECASE,
)

# unit at end of street_1 (APT 5)
UNIT_EXTRACT = re.compile(
    r'(?:\s+|,\s*)'
    r'((?:APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|DEPT|DEPARTMENT'
    # OFFICE PARK and similar are street names
    r'|(?:OFF|OFFICE)(?!\.?\s+(?:PARK|PLAZA|PLZ|CENTER|CENTRE|CTR|PKWY|PARKWAY|SQ|SQUARE'
    r'|CAMPUS|COMPLEX|TOWER|TWR|DR|DRIVE|BLVD|WAY|CT|COURT|PL|PLACE|RD|ROAD|ST|STREET'
    r'|AVE|AVENUE|LN|LANE|CIR|TER|BLDG|BUILDING)\b)'
    r'|FLOOR|RM|ROOM|PH|PENTHOUSE)'
    r'\.?\s+[\w\-\/\.]+\s*'
    r'|(?:FL|FLR)\.?\s+\d{1,2}\s*'  # 1-2 digits only; FL is also Florida
    r')$',
    re.IGNORECASE,
)

# a floor-only street_1 is a unit
FLOOR_ONLY_RE = re.compile(
    r'^(?:\d{1,3}(?:ST|ND|RD|TH)?\s+(?:FLOOR|FLR|FL)'
    r'|(?:FLOOR|FLR|FL)\s+\d{1,3}(?:ST|ND|RD|TH)?)\.?$',
    re.IGNORECASE,
)

# trailing hash unit; doubled hash keeps one
HASH_EXTRACT = re.compile(r'(?:\s+|(?<=\w))#*(#\s*[\w\-\/\.]+\s*)$')

# trailing state code; sorted keeps pattern stable
STATE_IN_CITY = re.compile(r'\s*,?\s+(' + '|'.join(sorted(US_STATES)) + r')\s*$')


# direction abbreviations

# street types, minus common names like PARK
_NAME_SUFFIXES = (
    'ALLEY|ALY|AVENUE|AVE|AV|BOULEVARD|BLVD|BYPASS|BYP|CAUSEWAY|CSWY'
    '|CIRCLE|CIR|COURT|CT|CRESCENT|CRES|DRIVE|DR|EXPRESSWAY|EXPY'
    '|FREEWAY|FWY|HIGHWAY|HWY|LANE|LN|LOOP|PARKWAY|PKWY|PLACE|PL'
    '|ROAD|RD|ROUTE|RTE|SQUARE|SQ|STREET|ST|TERRACE|TER|TRAIL|TRL'
    '|TURNPIKE|TPKE|WAY'
)
# types whose trailing number is the name
_NUMBERED_NAME_SUFFIXES = (
    'AVENUE|AVE|AV|ROAD|RD|LOOP|HIGHWAY|HWY|ROUTE|RTE|PIKE|TURNPIKE|TPKE'
    '|EXPRESSWAY|EXPY|FREEWAY|FWY|PARKWAY|PKWY'
)
_UNIT_WORDS = (
    'APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|DEPT|FL|FLR|FLOOR|RM|ROOM'
    '|PH|PENTHOUSE|OFFICE|OFC|LOT|SPC|SPACE|TRLR|PMB|NO'
)
_NAME_CLOSED = (
    r'\.?(?:\s*(?:,|;|#|$)|\s+-|\s+(?:' + _UNIT_WORDS + r')\b'
    r'|\s+\d+(?:ST|ND|RD|TH)\s+(?:FL|FLR|FLOOR)\b)'
)
# direction as street name: "650 WEST AVE"
_DIRECTION_IS_NAME = (
    r'\s+(?:' + _NAME_SUFFIXES + r')\b' + _NAME_CLOSED +
    r'|\s+(?!(?:' + _NUMBERED_NAME_SUFFIXES + r')\b)(?:' + _NAME_SUFFIXES + r')\b'
    r'\.?\s+\d+[A-Z]?\s*$'
)


# build a regex pair abbreviating one direction
def _direction_rule(word: str, abbr: str, prefix: bool = True) -> tuple:
    if prefix:
        return (re.compile(rf'^{word}\b(?!{_DIRECTION_IS_NAME})\s+', re.IGNORECASE), f'{abbr} ')
    return (re.compile(rf'\s+{word}\s*$', re.IGNORECASE), f' {abbr}')


DIRECTION_ABBREVIATIONS = [
    ('NORTHWEST', 'NW'), ('NORTHEAST', 'NE'),
    ('SOUTHWEST', 'SW'), ('SOUTHEAST', 'SE'),
    ('NORTH', 'N'), ('SOUTH', 'S'), ('EAST', 'E'), ('WEST', 'W'),
]

# direction words after a house number
DIR_MID = [
    (re.compile(rf'(\d\s+){word}\b(?!{_DIRECTION_IS_NAME})', re.IGNORECASE), rf'\g<1>{abbr}')
    for word, abbr in DIRECTION_ABBREVIATIONS
]

DIR_PREFIX = [
    _direction_rule(word, abbr) for word, abbr in DIRECTION_ABBREVIATIONS
]

DIR_SUFFIX = [
    _direction_rule(word, abbr, False) for word, abbr in DIRECTION_ABBREVIATIONS
]


# street type abbreviations

STREET_TYPE_ABBREVIATIONS = [
    ('STREET', 'ST'), ('AVENUE', 'AVE'), ('ROAD', 'RD'),
    ('BOULEVARD', 'BLVD'), ('DRIVE', 'DR'), ('LANE', 'LN'),
    ('COURT', 'CT'), ('CIRCLE', 'CIR'), ('PLACE', 'PL'),
    ('PARKWAY', 'PKWY'), ('HIGHWAY', 'HWY'), ('TERRACE', 'TER'),
    ('TURNPIKE', 'TPKE'), ('EXPRESSWAY', 'EXPY'), ('SQUARE', 'SQ'),
    ('TRAIL', 'TRL'), ('CROSSING', 'XING'),
    ('JUNCTION', 'JCT'), ('MOUNT', 'MT'),
]

STREET_TYPES = [
    (re.compile(rf'\b{full}\b', re.IGNORECASE), abbr)
    for full, abbr in STREET_TYPE_ABBREVIATIONS
]


# unit abbreviations

# Spelled ordinals ("SEVENTH FLOOR", "TWENTY-FIRST FLOOR")
ORDINAL_WORDS = {
    'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5, 'SIXTH': 6,
    'SEVENTH': 7, 'EIGHTH': 8, 'NINTH': 9, 'TENTH': 10, 'ELEVENTH': 11,
    'TWELFTH': 12, 'THIRTEENTH': 13, 'FOURTEENTH': 14, 'FIFTEENTH': 15,
    'SIXTEENTH': 16, 'SEVENTEENTH': 17, 'EIGHTEENTH': 18, 'NINETEENTH': 19,
    'TWENTIETH': 20, 'THIRTIETH': 30, 'FORTIETH': 40, 'FIFTIETH': 50,
    'SIXTIETH': 60, 'SEVENTIETH': 70, 'EIGHTIETH': 80, 'NINETIETH': 90,
}
ORDINAL_TENS = {
    'TWENTY': 20, 'THIRTY': 30, 'FORTY': 40, 'FIFTY': 50,
    'SIXTY': 60, 'SEVENTY': 70, 'EIGHTY': 80, 'NINETY': 90,
}

# USPS unit form: designator abbreviated, identifier kept
_NUMBERED_FLOOR = r'(\d{1,3})(?:(?:ST|ND|RD|TH)\s+(?:FLOOR|FLR|FL)|\s+(?:FLOOR|FLR))\b\.?'
_SPELLED_ORDINAL = rf"(?:({'|'.join(ORDINAL_TENS)})[\s-]+)?({'|'.join(ORDINAL_WORDS)})"
_SPELLED_FLOOR = _SPELLED_ORDINAL + r'\s+(?:FLOOR|FLR|FL)\b\.?'



# convert a spelled floor to FL form
def _spelled_floor(match: re.Match) -> str:
    tens = ORDINAL_TENS.get((match.group(1) or '').upper(), 0)
    return f'FL {tens + ORDINAL_WORDS[match.group(2).upper()]}'


# in-line designators; never the house number
_AFTER_TOKEN = r'(?<=\S\s)'
_AFTER_WORD = r'(?<=[A-Z.,\-]\s)'
_NOT_A_STREET_NAME = r'(?<!\s[NSEW]\s)(?<!\s[NS][EW]\s)'
USPS_UNIT_RULES = [
    (re.compile(_AFTER_TOKEN + r'(\d{1,3})(?:ST|ND|RD|TH)\s+(?:FLOOR|FLR)\b\.?(?=\s|$)', re.IGNORECASE),
     r'FL \1'),
    (re.compile(_AFTER_TOKEN + _NOT_A_STREET_NAME + r'(\d{1,3})(?:ST|ND|RD|TH)\s+FL\b\.?(?=\s|$)',
                re.IGNORECASE), r'FL \1'),
    (re.compile(_AFTER_WORD + r'(\d{1,3})\s+(?:FLOOR|FLR)\b\.?(?=\s|$)', re.IGNORECASE), r'FL \1'),
    (re.compile(_AFTER_WORD + _SPELLED_ORDINAL + r'\s+(?:FLOOR|FLR)\b\.?(?=\s|$)', re.IGNORECASE),
     _spelled_floor),
    (re.compile(_AFTER_WORD + _NOT_A_STREET_NAME + _SPELLED_ORDINAL + r'\s+FL\b\.?(?=\s|$)',
                re.IGNORECASE), _spelled_floor),
    (re.compile(r'(?<=\s)(?:FLOOR|FLR)\s*#?\s*(\d{1,3}[A-Z]?)(?=\s|$)', re.IGNORECASE), r'FL \1'),
    # '1 SUITE ST' is a street name
    (re.compile(r'(?<=\s)(?:SUITE|STE)\s*#\s*(?=\w)'
                r'|(?<=\s)SUITE\s+(?!(?:' + _NAME_SUFFIXES + r')\b)(?=\w)', re.IGNORECASE), 'STE '),
]

# street_2 units: leading designator, then in-line rules
UNIT_RULES = [
    (re.compile(rf'^{pattern}', re.IGNORECASE), abbr)
    for pattern, abbr in [
        (r'SUITE\b', 'STE'), (r'STE\.\s*', 'STE '), (r'STE\s*#\s*(?=\w)', 'STE '),
        (r'APARTMENT\b', 'APT'), (r'APT\.\s*', 'APT '),
        # a unit starting with a floor
        (_NUMBERED_FLOOR + r'(?=\s|$)', r'FL \1'), (_SPELLED_FLOOR + r'(?=\s|$)', _spelled_floor),
        (r'FLOOR\b', 'FL'), (r'FL\.\s*', 'FL '),
        (r'BUILDING\b', 'BLDG'), (r'BLDG\.?\s*', 'BLDG '),
        (r'DEPARTMENT\b', 'DEPT'), (r'DEPT\.?\s*', 'DEPT '),
        (r'OFFICE\b', 'OFF'), (r'OFF\.?\s*', 'OFF '),
        (r'#\s*', '# '),
    ]
] + USPS_UNIT_RULES


# abbreviate USPS floor/suite designators in a line
def usps_unit_designators(street: str) -> str:
    for pattern, replacement in USPS_UNIT_RULES:
        street = pattern.sub(replacement, street)
    return street


# human-verified exact street typos, never fuzzy
STREET_TYPO_RULES = [
    (re.compile(r'\bOLYMIC\b', re.IGNORECASE), 'OLYMPIC'),
    (re.compile(r'\bSUTE\b', re.IGNORECASE), 'SUITE'),
    # fixed first so WEST stays the name
    (re.compile(r'\bSREET\b', re.IGNORECASE), 'STREET'),
]
