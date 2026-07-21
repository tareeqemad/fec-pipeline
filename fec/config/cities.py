"""
config/cities.py — City name normalization mappings.

Standard abbreviations (FT. → FORT, ST → SAINT, etc.)
and verified typos found in real FEC data.
"""

import re

CITY_NORMALIZE = {
    # --- Abbreviations ---
    'NYC': 'NEW YORK',
    'NEW YORK CITY': 'NEW YORK',
    'NEWYORK': 'NEW YORK',
    'PHILA': 'PHILADELPHIA',
    'PHILLY': 'PHILADELPHIA',
    'LOS ANGELS': 'LOS ANGELES',
    'GREAT NCK PLZ': 'GREAT NECK PLAZA',
    'GREAT NCK': 'GREAT NECK',
    'WASHINGTON DC': 'WASHINGTON',
    'WASHINGTON, DC': 'WASHINGTON',

    # Saint / St
    'SAINT LOUIS': 'ST. LOUIS',     'ST LOUIS': 'ST. LOUIS',
    'SAINT PAUL': 'ST. PAUL',       'ST PAUL': 'ST. PAUL',
    'SAINT PETERSBURG': 'ST. PETERSBURG',
    'ST PETERSBURG': 'ST. PETERSBURG',
    'ST LOUS': 'ST. LOUIS',

    # Fort / Ft
    'FT LAUDERDALE': 'FORT LAUDERDALE',   'FT. LAUDERDALE': 'FORT LAUDERDALE',
    'FT WORTH': 'FORT WORTH',             'FT. WORTH': 'FORT WORTH',
    'FT WASHINGTON': 'FORT WASHINGTON',    'FT. WASHINGTON': 'FORT WASHINGTON',
    'FT MYERS': 'FORT MYERS',             'FT. MYERS': 'FORT MYERS',
    'FT LEE': 'FORT LEE',                 'FT. LEE': 'FORT LEE',

    # Mount / Mt
    'MT VERNON': 'MOUNT VERNON',     'MT. VERNON': 'MOUNT VERNON',
    'MT LAUREL': 'MOUNT LAUREL',     'MT. LAUREL': 'MOUNT LAUREL',
    'MT KISCO': 'MOUNT KISCO',       'MT. KISCO': 'MOUNT KISCO',

    # Compass abbreviations
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

    # --- Verified typos ---
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
    'ATLANTA ': 'ATLANTA',
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
    'LOS W': 'LOS ANGELES',
    'MPLS': 'MINNEAPOLIS',
    'TARZANA, CALIFORNIA': 'TARZANA',
    'AUSTIN/TEXAS': 'AUSTIN',
    'HILTON HEAD': 'HILTON HEAD ISLAND',
    'HOWARD COUNTY': 'COLUMBIA',  # MD ZIP 21044

    # --- Short abbreviations (verified by ZIP codes) ---
    'LA': 'LOS ANGELES',
    'SM': 'SANTA MONICA',
    'PB': 'PALM BEACH',
    'GV': 'GREENWOOD VILLAGE',
    'KP': 'KINGS POINT',
    'WPB': 'WEST PALM BEACH',
    'PBG': 'PALM BEACH GARDENS',
    'BRIDGEY': 'BRIDGEHAMPTON',
    'KCMO': 'KANSAS CITY',            # MO — Kansas City, Missouri
    'NPB': 'NORTH PALM BEACH',        # FL ZIP 33408
    'SLC': 'SALT LAKE CITY',          # UT ZIP 84103
    'LBTS': 'LAUDERDALE BY THE SEA',  # FL ZIP 33062
    # 'NY' handled by 'NYC' → 'NEW YORK' — also add direct
    'NY': 'NEW YORK',
    # State codes used as city names (verified by ZIP)
    'GA': 'ATLANTA',      # ZIP 30342 = Atlanta area
    'A': 'DENVER',        # ZIP 80209 = Denver (data entry garbage)

    # --- Variant spellings (verified by state/ZIP) ---
    'FAIRLAWN': 'FAIR LAWN',            # NJ — two-word is official
    'MC LEAN': 'MCLEAN',                # VA — one word is official
    'ST LOUIS PARK': 'ST. LOUIS PARK',  # MN — period is official
    'EASTHAMPTON': 'EAST HAMPTON',      # NY — two words is official
    'DELMAR': 'DEL MAR',                # CA — two words is official
    'WATERMILL': 'WATER MILL',          # NY — two words is official

    # --- Typos missed by fuzzy matching ---
    'BEVERLY HLLLS': 'BEVERLY HILLS',          # CA 90210
    'BROOKLINEMIAMI': 'MIAMI',                  # FL 33133 — garbled entry
    'HALNDLE BCH': 'HALLANDALE BEACH',          # FL 33009

    # --- Abbreviated cities (verified by state/ZIP) ---
    'HUNTINGTN BCH': 'HUNTINGTON BEACH',        # CA
    'SHAKER HTS': 'SHAKER HEIGHTS',             # OH
    'PARADISE VLY': 'PARADISE VALLEY',          # AZ
    'CHERRY HL VLG': 'CHERRY HILLS VILLAGE',    # CO 80113
    'MENDOTA HTS': 'MENDOTA HEIGHTS',           # MN
    'CLEVELAND HTS': 'CLEVELAND HEIGHTS',       # OH
    'MAYFIELD HTS': 'MAYFIELD HEIGHTS',         # OH
    'PALM BCH GDNS': 'PALM BEACH GARDENS',      # FL
    'HASBROUCK HTS': 'HASBROUCK HEIGHTS',        # NJ
    'COMMERCE TWP': 'COMMERCE TOWNSHIP',         # MI
    'MADISON HTS': 'MADISON HEIGHTS',            # MI

    # --- Additional city variants (verified from data analysis) ---
    'FOXBOROUGH': 'FOXBORO',
    'NEWTON CENTRE': 'NEWTON CENTER',
    'E FALMOUTH': 'EAST FALMOUTH',
    'MERION STA': 'MERION STATION',
    'NEWTOWN SQ': 'NEWTOWN SQUARE',
    'W CNSHOHOCKEN': 'WEST CONSHOHOCKEN',
    'GREEN COVE SPRING': 'GREEN COVE SPRINGS',
    'GOLDEN BAECH': 'GOLDEN BEACH',
    'N PALM BEACH': 'NORTH PALM BEACH',
    'PORT SAINT LUCIE': 'PORT ST LUCIE',
    'MOUNTAIN BRK': 'MOUNTAIN BROOK',
    'N ROYALTON': 'NORTH ROYALTON',
    'HUNTINGTN WDS': 'HUNTINGTON WOODS',
    'W BLOOMFIELD': 'WEST BLOOMFIELD',
    'SAINT LOUIS PARK': 'ST LOUIS PARK',
    'MOUNT PROSPECT': 'MT PROSPECT',
    'LIS ANGELES': 'LOS ANGELES',
    'N. HOLLYWOOD': 'NORTH HOLLYWOOD',
}


# City-name abbreviation expansion — run LAST in city cleaning so no
# abbreviation survives. The directional prefixes (N/S/E/W) are already
# expanded upstream; this covers Saint/Sainte/Mount/Fort, which FEC filers
# write inconsistently ("ST. LOUIS" vs "SAINT LOUIS"). The abbreviation
# period is dropped. Case of the rest of the name is preserved so it works
# on both UPPER donor cities and Title-case employer cities.
_ABBR_FULL = {'ST': ('SAINT', 'Saint'), 'STE': ('SAINTE', 'Sainte'),
              'MT': ('MOUNT', 'Mount'), 'FT': ('FORT', 'Fort')}
# A standalone Saint/Mount/Fort token (optionally with a period) followed by a
# space — anywhere in the name. The lookahead keeps the space, and the \b plus
# the longer STE-before-ST ordering stop false hits like STERLING / STATEN.
_ABBR_RE = re.compile(r'\b(STE|ST|MT|FT)\.?(?=\s)', re.I)


def expand_city_abbreviations(value):
    """Expand Saint/Sainte/Mount/Fort abbreviations ANYWHERE in a city name so
    none survive: "ST. LOUIS"→"SAINT LOUIS", "PORT ST LUCIE"→"PORT SAINT LUCIE",
    "West St. Paul"→"West Saint Paul". The abbreviation period is dropped and
    each token keeps its own case. No-op on blanks / non-strings."""
    if not isinstance(value, str) or not value:
        return value

    def _repl(m):
        upper, title = _ABBR_FULL[m.group(1).upper()]
        return upper if m.group(1).isupper() else title

    return _ABBR_RE.sub(_repl, value)
