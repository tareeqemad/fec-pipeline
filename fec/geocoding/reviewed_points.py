"""Street-level geocodes checked by hand.

REVIEWED_ZIP_TYPO_KEYS: the point is right although it lies outside the filed ZIP.
In each of these the filed ZIP is the typo, not the point. The street-inside-ZIP
check (fec/geocoding/pipeline.py) cannot prove that on its own, so these keys keep
their cached street point and are never looked up again. Add a key here only after
checking that the street exists where the point is and not in the filed ZIP; say
how in the note. Never research a donor's home address to fill this list.

REVIEWED_POINTS: the right point for a key whose cached point is wrong, copied from
a paid Google street result the cache holds under another spelling of the same
address (no ZIP, 'NAN', '33019.0', a suite). Those spellings are no longer used by
the data and are pruned from the cache, so the coordinates live here; the note
names the key they were copied from (for 2711 S OCEAN DR, the neighbouring 3101 on
the same stretch). Only public places (a committee's mailbox store, an office, a
campus) or a point the data itself proves (the house numbers of one street in
order). Published instead of the cached point; never looked up.

REVIEWED_WRONG_POINTS: a cached street point shown to be wrong (another town under
the same postal name, the street's centre, the wrong end of a long road). The
point is withheld, the key is looked up again with Census only (the free engine
with address ranges; Nominatim gave most of these points), a Census answer within
REVIEWED_WRONG_KM of the rejected point is refused, and a miss falls back to the
filed ZIP's centroid, which lies in the right town. Unresolved conflicts (two
points, neither shown wrong) do not belong here.
"""

from fec.geocoding.places import distance_km

REVIEWED_ZIP_TYPO_KEYS: dict[str, str] = {
    # the 2026-09-23 audit verdict (finding 8) lists these as ZIP typos with correct coordinates
    "791 HWY 77 N|WAXAHACHIE|TX|75164": "audit: the point is Waxahachie; 75164 is Josephine, 89 km away",
    "3750 S DIXIE HWY|MIAMI|FL|33154": "audit: S Dixie Hwy runs through Coconut Grove (33133), not Bal Harbour (33154)",
    "4 CHESTNUT DR|NEW YORK|NY|10112": "audit: the point is Great Neck 11021; 10112 is one Rockefeller Center building",
    # street names that exist in one place only, or the city's own street grid
    "201 ROUSE BLVD|PHILADELPHIA|PA|19122": "Rouse Blvd exists only in the Navy Yard (19112)",
    "1875 CENTURY PARK EAST||CA|91605": "Century Park East exists only in Century City (90067); 91605 is North Hollywood",
    "740 SW 19TH AVE|MIAMI|FL|33174": "Miami street grid: 740 SW 19th Ave is Little Havana (33135); 33174 lies 13 km west",
    "3135 HUTTON DR|BEVERLY HILLS|CA|90212": "Hutton Dr is in the 90210 hills; the same filing with 90210 has this point",
}

# 2026-09-24 cache audit, area street-wrong-town (finding and verifier's web checks)
REVIEWED_POINTS: dict[str, tuple[float, float, str]] = {
    "5130 S FORT APACHE RD|LAS VEGAS|NV|89148": (
        36.0957486, -115.2964452,
        "from '5130 S FORT APACHE RD|LAS VEGAS|NV|' (google): UPS Store at Tropicana & Fort Apache;"
        " the nominatim point was on Desert Inn Rd"),
    "8020 S RAINBOW BLVD|LAS VEGAS|NV|89139": (
        36.043206, -115.2418169,
        "from '8020 S RAINBOW BLVD|LAS VEGAS|NV|' (google): UPS Store, Southwest Marketplace;"
        " the nominatim point was on the Sahara Ave line"),
    "1636 N CEDAR CREST BLVD|ALLENTOWN|PA|18104": (
        40.6131429, -75.534401,
        "from '1636 N CEDAR CREST BLVD|ALLENTOWN|PA|' (google): UPS Store, Crest Plaza at Walbert Ave"),
    "810 N ST|ANCHORAGE|AK|99501": (
        61.2144849, -149.908594,
        "from '810 N ST|ANCHORAGE|AK|' (google): office building at 8th Ave & N St downtown;"
        " the nominatim point was 5.6 km east"),
    "3980 BROADWAY ST|BOULDER|CO|80304": (
        40.0477533, -105.2807602,
        "from '3980 BROADWAY ST STE 103|BOULDER|CO|80304' (google): North Boulder Shipping Store;"
        " the nominatim point was downtown near Pearl St"),
    "3101 S OCEAN DR|HOLLYWOOD|FL|33019": (
        25.9938336, -80.1178208,
        "from '3101 S OCEAN DR|HOLLYWOOD|FL|33019.0' (google): lies between 2401 (25.9985) and"
        " 4010 (25.9875) S Ocean Dr in the same data; the nominatim point 26.0121 was north of both"),
    "2711 S OCEAN DR|HOLLYWOOD|FL|33019": (
        25.9938336, -80.1178208,
        "from '3101 S OCEAN DR|HOLLYWOOD|FL|33019.0' (google), the nearest reviewed number of that"
        " stretch: 2711 lies between 2401 (25.9985) and 3101; its nominatim point was 3101's wrong one"),
    "4075 LINGLESTOWN RD|HARRISBURG|PA|17112": (
        40.3348186, -76.8369814,
        "from '4075 LINGLESTOWN RD|HARRISBURG|PA|' (google): UPS Store, Dauphin parcel 35-129-042"
        " (Lower Paxton Twp); the nominatim point was by the 17110 centroid"),
    "1 HOFSTRA UNIVERSITY|HEMPSTEAD|NY|11549": (
        40.7167787, -73.6014234,
        "from '114 HOFSTRA UNIVERSITY|HEMPSTEAD|NJ|' (google): Hofstra campus; the nominatim"
        " point was 6.5 km north"),
}

REVIEWED_WRONG_KM = 0.5

_CITY_CLIP = "the same-name street inside the city named in the postal address"
_STREET_ONLY = "the street's centre, not the house number"
REVIEWED_WRONG_POINTS: dict[str, tuple[float, float, str]] = {
    # round 2 and the 2026-09-24 audit (area street-wrong-town), confirmed by its verifier
    "2335 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5671767, -80.0405852, "in Manalapan; 2500 S Ocean Blvd is at 26.618, 2778 at 26.635"),
    "2660 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5568015, -80.0418709, "in Manalapan; 2500 S Ocean Blvd is at 26.618, 2778 at 26.635"),
    "2770 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5532174, -80.0423938, "in Manalapan; 2778 S Ocean Blvd is at 26.635"),
    "750 PARK AVE NE|ATLANTA|GA|30326": (
        33.7621478, -84.3769628, "9.8 km south of Buckhead, at 0.99 of the 30326 limit"),
    "7972 CRANES PT WAY|WEST PALM BEACH|FL|33412": (
        26.9105305, -80.1337901,
        "google; the same donor's CRANES POINTE/POINT WAY keys are at 26.793, 13 km south-west"),
    "7000 N PHOENIX ST SUITE 1|PHOENIX|AZ|85020": (
        33.4476025, -112.0288927, "google, downtown; the same donor's 7000 N 16TH ST is at 33.540"),
    "6655 S EASTERN AVE|LAS VEGAS|NV|89119": (
        36.1442943, -115.1189055, f"{_CITY_CLIP}: on the Sahara Ave line, 8 km north of the 6600 block"),
    "3750 S LAS VEGAS BLVD|LAS VEGAS|NV|89158": (
        36.1436683, -115.1574861, f"{_CITY_CLIP}: Sahara Ave line; 89158 is the CityCenter ZIP 4 km south"),
    "3750 LAS VEGAS BLVD SOUTH|LAS VEGAS|NV|89158": (
        36.1436652, -115.1574865, f"{_CITY_CLIP}: Sahara Ave line; 89158 is the CityCenter ZIP 4 km south"),
    "11559 MEMORIAL DR|HOUSTON|TX|77024": (
        29.7624253, -95.4109326, f"{_CITY_CLIP}: by Memorial Park; the 11500 block is in the Memorial Villages"),
    "12505 46TH AVE N|MINNEAPOLIS|MN|55442": (
        45.0385135, -93.3087719, f"{_CITY_CLIP}: north Minneapolis; 55442 is Plymouth"),
    "4440 W 25TH ST|MINNEAPOLIS|MN|55416": (
        44.957979, -93.3012753, f"{_CITY_CLIP}: the same address filed as SAINT LOUIS PARK is at -93.338"),
    "28899 S WOODLAND RD|CLEVELAND|OH|44124": (
        41.4792238, -81.5863815, f"{_STREET_ONLY}: west of 17700 S Woodland (Shaker Heights); 28000+ is Pepper Pike"),
    "31549 S WOODLAND RD|CLEVELAND|OH|44124": (
        41.4792238, -81.5863815, f"{_STREET_ONLY}: west of 17700 S Woodland (Shaker Heights); 31000+ is Hunting Valley"),
    "8220 DELMAR BLVD|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, f"{_STREET_ONLY}: east of 7365 Delmar (-90.328) in the same data"),
    "8300 DELMAR BLVD|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, f"{_STREET_ONLY}: east of 7365 Delmar (-90.328) in the same data"),
    "8000 MARYLAND AVE|SAINT LOUIS|MO|63105": (
        38.6443077, -90.2594943, f"{_CITY_CLIP}: Central West End; 8000 Maryland Ave is in Clayton"),
    "7442 VALENCIA DR|BOCA RATON|FL|33433": (
        26.3216127, -80.0785086, f"{_STREET_ONLY}: on the Deerfield border, 8.5 km from the West Boca ZIP"),
    "7081 VALENCIA DR|BOCA RATON|FL|33433": (
        26.3216127, -80.0785086, f"{_STREET_ONLY}: on the Deerfield border, 8.5 km from the West Boca ZIP"),
    "5200 NORTHSIDE DR|ATLANTA|GA|30327": (
        33.7851924, -84.4075235, "Georgia Tech; 4711 Northside Dr in the same data is at 33.882"),
    "1800 PEACHTREE ST NW|ATLANTA|GA|30309": (
        33.754012, -84.390034, "downtown; the same donor's 1800 PEACHTREE ST NE is at 33.804"),
    "3400 PEACHTREE RD NW|ATLANTA|GA|30326": (
        33.8302174, -84.3867636, "2500 Peachtree Rd NW's point; 3400 PEACHTREE RD NE is at 33.850"),
    "4253 LOWER ROSWELL RD|MARIETTA|GA|30068": (
        33.9509024, -84.4957569, "6.3 km west of 4220 Lower Roswell Rd (33.961, -84.429) in the same data"),
    "88 W PACES FERRY RD|ATLANTA|GA|30305": (
        33.8480401, -84.4092557, f"{_STREET_ONLY}: 2.6 km west of 88 W PACES FERRY RD NW (33.840, -84.383)"),
    "7222 137TH ST|FLUSHING|NY|11367": (
        40.766575, -73.830273, "near downtown Flushing; 7233 137TH ST in the same data is at 40.724"),
    "1010 S JOLIET ST|DENVER|CO|80012": (
        39.7558215, -104.8613063, f"{_CITY_CLIP}: 80012 is Aurora; the same address filed as AURORA is at 39.698"),
    "4601 N PARK AVE|CHEVY CHASE|MD|20815": (
        38.990075, -77.091846, "by NIH (20894); 4550 N PARK AVE on the same short street is at 38.963"),
    "11500 SAN VICENTE BLVD|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, f"{_STREET_ONLY}: Santa Monica border; 11661-11693 are at -118.465"),
    "11726 SAN VICENTE BLVD|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, f"{_STREET_ONLY}: Santa Monica border; 11661-11693 are at -118.465"),
    # employer offices (public buildings), under the resolved-address keys the office pass looks up
    "11726 SAN VICENTE BLVD STE 650|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, f"{_STREET_ONLY}: Santa Monica border; 11661-11693 are at -118.465"),
    "8630 DELMAR BLVD, SUITE 100|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, f"SPETNER ASSOCIATES; {_STREET_ONLY} of Delmar (the 8220/8300 point)"),
    "8630 DELMAR BLVD, SUITE 100|ST. LOUIS|MO|63124": (
        38.6557551, -90.3027869, f"SPETNER ASSOCIATES; {_STREET_ONLY} of Delmar (the 8220/8300 point)"),
    "6365 MONTESSOURI STREET|LAS VEGAS|NV|89113": (
        36.1404295, -115.2474218, f"POKERGO, Park West Business Center (89113); {_CITY_CLIP} by Sahara Ave"),
    "1 COPLEY PL, SUITE 105|BOSTON|MA|02116": (
        42.316545, -71.100891, "THE UROLOGY GROUP; the point is Jamaica Plain, Copley Place is in Back Bay"),
    "66 HUDSON BOULEVARD|NEW YORK|NY|10001": (
        40.718403330373, -74.008784846222,
        "DEBEVOISE (census): Hudson St in Tribeca; 66 Hudson Blvd is The Spiral at Hudson Yards"),
}


# true for a key whose outside-ZIP point was hand-verified right
def is_reviewed_zip_typo(key: str) -> bool:
    """True for a cache key whose outside-ZIP street point was checked by hand and is right."""
    return key in REVIEWED_ZIP_TYPO_KEYS


# the hand-checked (lat, lng) published for key, or None
def reviewed_point(key: str) -> tuple[float, float] | None:
    """The hand-checked (lat, lng) published for key, or None."""
    point = REVIEWED_POINTS.get(key)
    return None if point is None else (point[0], point[1])


# true when the point is near a known-wrong one
def is_reviewed_wrong(key: str, lat: float, lng: float) -> bool:
    """True when (lat, lng) is, or lies within REVIEWED_WRONG_KM of, the point shown wrong for key."""
    wrong = REVIEWED_WRONG_POINTS.get(key)
    return wrong is not None and distance_km((lat, lng), wrong[:2]) <= REVIEWED_WRONG_KM
