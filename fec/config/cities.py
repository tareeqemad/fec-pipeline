"""City name abbreviations and verified typos."""
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

    # Saint/Fort/Mount forms are expanded later; typos only
    'ST LOUS': 'ST. LOUIS',

    # compass: the only city direction expansion
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
    # SCOTTDALE fixed for AZ only (real elsewhere)
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
    # NY and ambiguous initials are fixed elsewhere

    # variant spellings to the official form
    'MC LEAN': 'MCLEAN',
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


# fixed in one state; real cities elsewhere
CITY_STATE_NORMALIZE = {
    ('FAIRLAWN', 'NJ'): 'FAIR LAWN',
    ('EASTHAMPTON', 'NY'): 'EAST HAMPTON',
    ('DELMAR', 'CA'): 'DEL MAR',
    ('SCOTTDALE', 'AZ'): 'SCOTTSDALE',
}


# short city forms resolved by ZIP3
CITY_ZIP3_NORMALIZE = {
    ('NY', 'NY', '100'): 'NEW YORK',
    ('NY', 'NY', '101'): 'NEW YORK',
    ('NY', 'NY', '102'): 'NEW YORK',
    ('NY', 'NY', '103'): 'STATEN ISLAND',
    ('NY', 'NY', '104'): 'BRONX',
    ('NY', 'NY', '112'): 'BROOKLYN',
}


# Saint/Mount/Fort expansion; run last, keeps case
_ABBR_FULL = {'ST': ('SAINT', 'Saint'), 'STE': ('SAINTE', 'Sainte'),
              'MT': ('MOUNT', 'Mount'), 'FT': ('FORT', 'Fort')}
# STE before ST; \b avoids STERLING/STATEN
_ABBR_RE = re.compile(r'\b(STE|ST|MT|FT)\.?(?=\s)', re.IGNORECASE)


# expand Saint/Mount/Fort abbreviations in city names
def expand_city_abbreviations(value):
    if not isinstance(value, str) or not value:
        return value

    # replacement text, casing matched to the abbreviation
    def _repl(m):
        upper, title = _ABBR_FULL[m.group(1).upper()]
        return upper if m.group(1).isupper() else title

    return _ABBR_RE.sub(_repl, value)
