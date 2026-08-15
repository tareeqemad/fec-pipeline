"""City name normalization: standard abbreviations plus typos verified in real FEC data."""
import re

CITY_NORMALIZE = {
    # abbreviations
    'NYC': 'NEW YORK',
    'NEW YORK CITY': 'NEW YORK',
    'NEWYORK': 'NEW YORK',
    'PHILA': 'PHILADELPHIA',
    'PHILLY': 'PHILADELPHIA',
    'LOS ANGELS': 'LOS ANGELES',
    'GREAT NCK PLZ': 'GREAT NECK PLAZA',
    'GREAT NCK': 'GREAT NECK',

    # Saint/Fort/Mount abbreviation forms need no entries here --
    # expand_city_abbreviations (runs last) already unifies them; only real
    # typos in those names belong in the typo list.
    'ST LOUS': 'ST. LOUIS',

    # compass -- the ONLY directional expansion for cities; nothing upstream
    # expands N/S/E/W
    'N MIAMI BEACH': 'NORTH MIAMI BEACH',
    'N MIAMI': 'NORTH MIAMI',
    'N HOLLYWOOD': 'NORTH HOLLYWOOD',
    'N CAMBRIDGE': 'NORTH CAMBRIDGE',
    'N BETHESDA': 'NORTH BETHESDA',
    'S MIAMI': 'SOUTH MIAMI',
    'SO ORANGE': 'SOUTH ORANGE',
    'S ORANGE': 'SOUTH ORANGE',
    'W HOLLYWOOD': 'WEST HOLLYWOOD',
    'E BRUNSWICK': 'EAST BRUNSWICK',

    # verified typos
    'STAMFOTD': 'STAMFORD',
    'BEVERLY HILLLS': 'BEVERLY HILLS',
    'BEVERLY HILS': 'BEVERLY HILLS',
    'CALABASA': 'CALABASAS',
    'CHATSWOTH': 'CHATSWORTH',
    'ENCINOI': 'ENCINO',
    'HUNTIONGTON BEACH': 'HUNTINGTON BEACH',
    'LA JOLLLA': 'LA JOLLA',
    'LOS ANGELSS': 'LOS ANGELES',
    'PACIFIC PLSDS': 'PACIFIC PALISADES',
    'PALM DESEERT': 'PALM DESERT',
    'SHERMAN OAK': 'SHERMAN OAKS',
    'SHERWOOD FORREST': 'SHERWOOD FOREST',
    'SUN JOSE': 'SAN JOSE',
    'COLORADO SPGS': 'COLORADO SPRINGS',
    'GREENWCIH': 'GREENWICH',
    'FORT LAUDERDAKLE': 'FORT LAUDERDALE',
    'HOLLYWODD': 'HOLLYWOOD',
    'MIAMI BEAC': 'MIAMI BEACH',
    'ATLANTS': 'ATLANTA',
    'BIRMNGHAM': 'BIRMINGHAM',
    'SKOKIIE': 'SKOKIE',
    'PHONIX': 'PHOENIX',
    'PARADISE VSLLEY': 'PARADISE VALLEY',
    'SCOTTDALE': 'SCOTTSDALE',
    'BROOKKYN': 'BROOKLYN',
    'BRROOKLYN': 'BROOKLYN',
    'JAMIACA': 'JAMAICA',
    'VALLEYSTREAM': 'VALLEY STREAM',
    'ATLANTIC BCH': 'ATLANTIC BEACH',
    'PLEASENTVILLE': 'PLEASANTVILLE',
    'BALA CYNWOOD': 'BALA CYNWYD',
    'BRYN MWR': 'BRYN MAWR',
    'CHADDSFORD': 'CHADDS FORD',
    'GLADYWNE': 'GLADWYNE',
    'WFORTWASHINGTON': 'FORT WASHINGTON',
    'BELLARE': 'BELLAIRE',
    'FALLS CHURC': 'FALLS CHURCH',
    'VIRGINIA BCH': 'VIRGINIA BEACH',
    'BATIMORE': 'BALTIMORE',
    'OWINS MILLS': 'OWINGS MILLS',
    'ROCVILLE': 'ROCKVILLE',
    'PRINCETON JUCGTION': 'PRINCETON JUNCTION',
    'TUCSOB': 'TUCSON',
    'NEW YROK': 'NEW YORK',
    'NREW YORK': 'NEW YORK',
    'LOS ANGLES': 'LOS ANGELES',
    'LOS ANGELE': 'LOS ANGELES',
    'MPLS': 'MINNEAPOLIS',
    'HILTON HEAD': 'HILTON HEAD ISLAND',

    # short abbreviations, each verified by ZIP
    'LA': 'LOS ANGELES',
    'WPB': 'WEST PALM BEACH',
    'PBG': 'PALM BEACH GARDENS',
    'BRIDGEY': 'BRIDGEHAMPTON',
    'KCMO': 'KANSAS CITY',
    'NPB': 'NORTH PALM BEACH',
    'SLC': 'SALT LAKE CITY',
    'LBTS': 'LAUDERDALE BY THE SEA',
    'NY': 'NEW YORK',
    # single-record garbage cities ('A', 'GA', 'HOWARD COUNTY', 'LOS W') and
    # ambiguous two-letter initials ('SM', 'PB', 'GV', 'KP') are fixed per
    # sub_id in data/manual_employer_overrides.csv, not here: a global rule
    # would silently misfix other donors in future pulls

    # variant spellings — target is the official form (verified by state/ZIP)
    'FAIRLAWN': 'FAIR LAWN',
    'MC LEAN': 'MCLEAN',
    'EASTHAMPTON': 'EAST HAMPTON',
    'DELMAR': 'DEL MAR',
    'WATERMILL': 'WATER MILL',

    # typos missed by fuzzy matching
    'BEVERLY HLLLS': 'BEVERLY HILLS',
    'BROOKLINEMIAMI': 'MIAMI',  # FL 33133, garbled entry
    'HALNDLE BCH': 'HALLANDALE BEACH',

    # abbreviated cities (verified by state/ZIP)
    'HUNTINGTN BCH': 'HUNTINGTON BEACH',
    'SHAKER HTS': 'SHAKER HEIGHTS',
    'PARADISE VLY': 'PARADISE VALLEY',
    'CHERRY HL VLG': 'CHERRY HILLS VILLAGE',
    'MENDOTA HTS': 'MENDOTA HEIGHTS',
    'CLEVELAND HTS': 'CLEVELAND HEIGHTS',
    'MAYFIELD HTS': 'MAYFIELD HEIGHTS',
    'PALM BCH GDNS': 'PALM BEACH GARDENS',
    'HASBROUCK HTS': 'HASBROUCK HEIGHTS',
    'COMMERCE TWP': 'COMMERCE TOWNSHIP',
    'MADISON HTS': 'MADISON HEIGHTS',

    # additional variants from data analysis
    'FOXBOROUGH': 'FOXBORO',
    'NEWTON CENTRE': 'NEWTON CENTER',
    'E FALMOUTH': 'EAST FALMOUTH',
    'MERION STA': 'MERION STATION',
    'NEWTOWN SQ': 'NEWTOWN SQUARE',
    'W CNSHOHOCKEN': 'WEST CONSHOHOCKEN',
    'GREEN COVE SPRING': 'GREEN COVE SPRINGS',
    'GOLDEN BAECH': 'GOLDEN BEACH',
    'N PALM BEACH': 'NORTH PALM BEACH',
    'MOUNTAIN BRK': 'MOUNTAIN BROOK',
    'N ROYALTON': 'NORTH ROYALTON',
    'HUNTINGTN WDS': 'HUNTINGTON WOODS',
    'W BLOOMFIELD': 'WEST BLOOMFIELD',
    'LIS ANGELES': 'LOS ANGELES',
    'N. HOLLYWOOD': 'NORTH HOLLYWOOD',
}


# Saint/Sainte/Mount/Fort expansion — run LAST in city cleaning so no
# abbreviation survives. Period dropped; case of the rest preserved (works on
# UPPER donor cities and Title-case employer cities).
_ABBR_FULL = {'ST': ('SAINT', 'Saint'), 'STE': ('SAINTE', 'Sainte'),
              'MT': ('MOUNT', 'Mount'), 'FT': ('FORT', 'Fort')}
# Standalone token followed by a space, anywhere in the name. STE before ST
# plus \b stops false hits like STERLING / STATEN.
_ABBR_RE = re.compile(r'\b(STE|ST|MT|FT)\.?(?=\s)', re.IGNORECASE)


def expand_city_abbreviations(value):
    """Expand Saint/Sainte/Mount/Fort anywhere in a city name; no-op on blanks / non-strings."""
    if not isinstance(value, str) or not value:
        return value

    def _repl(m):
        upper, title = _ABBR_FULL[m.group(1).upper()]
        return upper if m.group(1).isupper() else title

    return _ABBR_RE.sub(_repl, value)
