"""City normalization: standard abbreviations plus verified FEC typos."""
import re
from fec.config.tables import CONFIG_DIR, read_map

CITY_NORMALIZE = read_map(CONFIG_DIR / "city_spellings.csv")


# These spellings name different real cities in different states.
CITY_STATE_NORMALIZE = {
    ('FAIRLAWN', 'NJ'): 'FAIR LAWN',
    ('EASTHAMPTON', 'NY'): 'EAST HAMPTON',
    ('DELMAR', 'CA'): 'DEL MAR',
    ('SCOTTDALE', 'AZ'): 'SCOTTSDALE',
}


# Match (city, state, ZIP3); Queens needs a neighbourhood rule.
CITY_ZIP3_NORMALIZE = {
    ('NY', 'NY', '100'): 'NEW YORK',
    ('NY', 'NY', '101'): 'NEW YORK',
    ('NY', 'NY', '102'): 'NEW YORK',
    ('NY', 'NY', '103'): 'STATEN ISLAND',
    ('NY', 'NY', '104'): 'BRONX',
    ('NY', 'NY', '112'): 'BROOKLYN',
}


# Expand these last, preserving the abbreviation's case.
_ABBR_FULL = {'ST': ('SAINT', 'Saint'), 'STE': ('SAINTE', 'Sainte'),
              'MT': ('MOUNT', 'Mount'), 'FT': ('FORT', 'Fort')}
# Whole tokens followed by a space, not STERLING or STATEN.
_ABBR_RE = re.compile(r'\b(STE|ST|MT|FT)\.?(?=\s)', re.IGNORECASE)


# Expand city abbreviations; leave blanks and non-strings unchanged.
def expand_city_abbreviations(value):
    if not isinstance(value, str) or not value:
        return value

    # replacement text, casing matched to the abbreviation
    def _repl(m):
        upper, title = _ABBR_FULL[m.group(1).upper()]
        return upper if m.group(1).isupper() else title

    return _ABBR_RE.sub(_repl, value)
