"""Accept address coordinates and keep the existing reviewed corrections."""

import math
import re
from urllib.parse import urlsplit

from fec.cleaning.addresses.street_text import numbered_street
from fec.config.geography import US_STATES, US_STATE_BBOX

EARTH_RADIUS_MILES = 3958.8
STREET_LEVEL_SOURCES = {"nominatim", "census", "google", "manual_census", "manual_verified", "nominatim_intl"}
US_TERRITORIES = {"AS", "GU", "MP", "PR", "VI"}
REVIEWED_WRONG_KM = 0.5


def is_po_box(street: str) -> bool:
    return bool(re.match(r"^P[.\s]*O[.\s]*BOX\b", street.strip(), re.IGNORECASE))


def unclear_house_number(street: str) -> bool:
    return bool(re.match(r"^[^A-Z0-9\s]+\d", street.strip(), re.IGNORECASE))


def is_foreign_address(state: str, zipcode: str) -> bool:
    state, zipcode = str(state or "").strip().upper(), str(zipcode or "").strip().upper()
    if state:
        return state not in US_STATES or bool(re.fullmatch(
            r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}", zipcode))
    if zipcode.endswith(".0"):
        zipcode = zipcode[:-2].zfill(5)
    return not bool(re.fullmatch(r"\d{5}(?:-?\d{4})?", zipcode))


def great_circle(first: tuple, second: tuple, radius: float) -> float:
    lat1, lng1 = map(math.radians, first)
    lat2, lng2 = map(math.radians, second)
    value = math.sin((lat2 - lat1) / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(value)))


def distance_km(first: tuple, second: tuple) -> float:
    return great_circle(first, second, 6371)


def valid_for_state(lat: float, lng: float, state: str) -> bool:
    state = state.strip().upper()
    bounds = [US_STATE_BBOX[state]] if state in US_STATE_BBOX else US_STATE_BBOX.values()
    return any(south - 1 <= lat <= north + 1 and west - 1 <= lng <= east + 1
               for south, north, west, east in bounds)


def _review_key(key: str) -> str:
    parts = [part.strip().upper() for part in key.split("|")]
    parts[0] = numbered_street(parts[0])
    return "|".join(parts)


def reviewed_point(key: str) -> tuple | None:
    point = REVIEWED_POINTS.get(_review_key(key))
    return tuple(point[:2]) if point else None


def is_reviewed_wrong(key: str, lat: float, lng: float) -> bool:
    wrong = REVIEWED_WRONG_POINTS.get(_review_key(key))
    return wrong is not None and distance_km((lat, lng), wrong[:2]) <= REVIEWED_WRONG_KM


def accepted_coordinates(key: str, entry: dict | None) -> tuple:
    reviewed = reviewed_point(key)
    if reviewed is not None:
        return *reviewed, "google"
    if not entry or entry.get("lat") is None or entry.get("lng") is None:
        return None, None, "not_found"
    source = entry.get("source", "")
    if source not in STREET_LEVEL_SOURCES:
        return None, None, "not_found"
    try:
        lat, lng = float(entry["lat"]), float(entry["lng"])
    except (TypeError, ValueError):
        return None, None, "rejected_coordinates"
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, "rejected_coordinates"
    parts = key.split("|")
    if len(parts) != 4:
        return None, None, "rejected_address"
    street, _, state, zipcode = parts
    state = state.strip().upper()
    if not street.strip() or is_po_box(street) or unclear_house_number(street):
        return None, None, "not_found"
    foreign = is_foreign_address(state, zipcode)
    country = (entry.get("country") or ("US" if not foreign else "")).upper()
    if foreign:
        if not country or country == "US":
            return None, None, "rejected_us_match_for_foreign"
    else:
        territory_country = state in US_TERRITORIES and country == state
        if (country != "US" and not territory_country) or not valid_for_state(lat, lng, state):
            return None, None, "rejected_out_of_us"
    if is_reviewed_wrong(key, lat, lng):
        return None, None, "rejected_reviewed_wrong"
    return lat, lng, source


_CITY_HOUSE_RE = re.compile(r"^(\d+[A-Z]?(?:[-/]\d+[A-Z]?)?(?: \d+/[1-9]\d*| ?[¼½¾])?) (.+)$")
_CITY_DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
_CITY_STREET_TYPES = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "PLACE": "PL",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TERRACE": "TER", "SQUARE": "SQ",
    "TRAIL": "TRL", "PLAZA": "PLZ", "TURNPIKE": "TPKE", "EXPRESSWAY": "EXPY",
    "ALLEY": "ALY", "CRESCENT": "CRES", "POINT": "PT", "HEIGHTS": "HTS",
    "JUNCTION": "JCT", "RIDGE": "RDG", "ROUTE": "RTE", "EXTENSION": "EXT",
}


# Keep road words in order; only format directions and a street suffix.
def _city_road_tokens(street: str) -> tuple[str, ...]:
    tokens = street.upper().split()
    if not tokens:
        return ()
    directions = set(_CITY_DIRECTIONS.values())
    if len(tokens) > 2:
        for index in (0, len(tokens) - 1):
            word = tokens[index].replace(".", "")
            if word in _CITY_DIRECTIONS or word in directions:
                tokens[index] = _CITY_DIRECTIONS.get(word, word)
    suffix = len(tokens) - 2 if len(tokens) > 1 and tokens[-1] in directions else len(tokens) - 1
    word = tokens[suffix].rstrip(".")
    if word in _CITY_STREET_TYPES or word in _CITY_STREET_TYPES.values():
        tokens[suffix] = _CITY_STREET_TYPES.get(word, word)
    return tuple(tokens)


# A provider city is evidence only when every other address part agrees.
def confirmed_city(key: str, entry: dict | None) -> str | None:
    if not isinstance(entry, dict) or entry.get("source") not in {"nominatim", "census"}:
        return None
    parts = key.split("|")
    if len(parts) != 4:
        return None
    street, city, state, zipcode = (" ".join(part.upper().split()) for part in parts)
    if state not in US_STATES or not re.fullmatch(r"\d{5}(?:-\d{4})?", zipcode):
        return None
    country = str(entry.get("country") or "").upper()
    if country != "US" and not (state in US_TERRITORIES and country == state):
        return None
    matched = entry.get("matched_address")
    fields = ("text", "house_number", "street", "city", "state", "zip", "source_url")
    if not isinstance(matched, dict) or any(
        not isinstance(matched.get(field), str) or not matched[field].strip() for field in fields
    ):
        return None
    try:
        source_url = urlsplit(matched["source_url"].strip())
        if source_url.scheme not in {"http", "https"} or not source_url.hostname:
            return None
    except ValueError:
        return None
    if any(char.isspace() for char in matched["source_url"].strip()):
        return None
    evidence = {field: " ".join(matched[field].upper().split()) for field in fields}
    if evidence["state"].removeprefix("US-") != state:
        return None
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", evidence["zip"]) or evidence["zip"][:5] != zipcode[:5]:
        return None
    house = _CITY_HOUSE_RE.fullmatch(street)
    if not house or house.group(1) != evidence["house_number"]:
        return None
    if _city_road_tokens(house.group(2)) != _city_road_tokens(evidence["street"]):
        return None
    lat, lng, source = accepted_coordinates(key, entry)
    if lat is None or lng is None or source not in {"nominatim", "census"}:
        return None
    actual = evidence["city"]
    return actual if actual != city and any(char.isalpha() for char in actual) else None


# Same-address corrections retained from the earlier cache review.
REVIEWED_POINTS = {
    "5130 S FORT APACHE RD|LAS VEGAS|NV|89148": (
        36.0957486, -115.2964452, "from '5130 S FORT APACHE RD|LAS VEGAS|NV|' (google): UPS Store at Tropicana & Fort Apache; the nominatim point was on Desert Inn Rd"),
    "8020 S RAINBOW BLVD|LAS VEGAS|NV|89139": (
        36.043206, -115.2418169, "from '8020 S RAINBOW BLVD|LAS VEGAS|NV|' (google): UPS Store, Southwest Marketplace; the nominatim point was on the Sahara Ave line"),
    "1636 N CEDAR CREST BLVD|ALLENTOWN|PA|18104": (
        40.6131429, -75.534401, "from '1636 N CEDAR CREST BLVD|ALLENTOWN|PA|' (google): UPS Store, Crest Plaza at Walbert Ave"),
    "810 N ST|ANCHORAGE|AK|99501": (
        61.2144849, -149.908594, "from '810 N ST|ANCHORAGE|AK|' (google): office building at 8th Ave & N St downtown; the nominatim point was 5.6 km east"),
    "3980 BROADWAY ST|BOULDER|CO|80304": (
        40.0477533, -105.2807602, "from '3980 BROADWAY ST STE 103|BOULDER|CO|80304' (google): North Boulder Shipping Store; the nominatim point was downtown near Pearl St"),
    "3101 S OCEAN DR|HOLLYWOOD|FL|33019": (
        25.9938336, -80.1178208, "from '3101 S OCEAN DR|HOLLYWOOD|FL|33019.0' (google): lies between 2401 (25.9985) and 4010 (25.9875) S Ocean Dr in the same data; the nominatim point 26.0121 was north of both"),
    "4075 LINGLESTOWN RD|HARRISBURG|PA|17112": (
        40.3348186, -76.8369814, "from '4075 LINGLESTOWN RD|HARRISBURG|PA|' (google): UPS Store, Dauphin parcel 35-129-042 (Lower Paxton Twp); the nominatim point was by the 17110 centroid"),
}

# Previously reviewed bad points must not reappear as address matches.
REVIEWED_WRONG_POINTS = {
    # Old cache entries already withheld for address/ZIP conflicts; still pending review.
    "101 S TRYON ST|CHARLOTTE|NC|28280": (
        35.0600842, -80.910337, "Previous address check rejected this cached point."),
    "100 PARK AVE FL 16|NEW YORK|NY|10017": (
        40.9352886, -73.8557474, "Previous address check rejected this cached point."),
    "305 MADISON AVE|NEW YORK|NY|10165": (
        42.0924417, -76.7999904, "Previous address check rejected this cached point."),
    "2335 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5671767, -80.0405852, "in Manalapan; 2500 S Ocean Blvd is at 26.618, 2778 at 26.635"),
    "2660 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5568015, -80.0418709, "in Manalapan; 2500 S Ocean Blvd is at 26.618, 2778 at 26.635"),
    "2770 S OCEAN BLVD|PALM BEACH|FL|33480": (
        26.5532174, -80.0423938, "in Manalapan; 2778 S Ocean Blvd is at 26.635"),
    "750 PARK AVE NE|ATLANTA|GA|30326": (
        33.7621478, -84.3769628, "9.8 km south of Buckhead, at 0.99 of the 30326 limit"),
    "7972 CRANES PT WAY|WEST PALM BEACH|FL|33412": (
        26.9105305, -80.1337901, "google; the same donor's CRANES POINTE/POINT WAY keys are at 26.793, 13 km south-west"),
    "7000 N PHOENIX ST SUITE 1|PHOENIX|AZ|85020": (
        33.4476025, -112.0288927, "google, downtown; the same donor's 7000 N 16TH ST is at 33.540"),
    "6655 S EASTERN AVE|LAS VEGAS|NV|89119": (
        36.1442943, -115.1189055, "the same-name street inside the city named in the postal address: on the Sahara Ave line, 8 km north of the 6600 block"),
    "3750 S LAS VEGAS BLVD|LAS VEGAS|NV|89158": (
        36.1436683, -115.1574861, "the same-name street inside the city named in the postal address: Sahara Ave line; 89158 is the CityCenter ZIP 4 km south"),
    "3750 LAS VEGAS BLVD SOUTH|LAS VEGAS|NV|89158": (
        36.1436652, -115.1574865, "the same-name street inside the city named in the postal address: Sahara Ave line; 89158 is the CityCenter ZIP 4 km south"),
    "11559 MEMORIAL DR|HOUSTON|TX|77024": (
        29.7624253, -95.4109326, "the same-name street inside the city named in the postal address: by Memorial Park; the 11500 block is in the Memorial Villages"),
    "12505 46TH AVE N|MINNEAPOLIS|MN|55442": (
        45.0385135, -93.3087719, "the same-name street inside the city named in the postal address: north Minneapolis; 55442 is Plymouth"),
    "4440 W 25TH ST|MINNEAPOLIS|MN|55416": (
        44.957979, -93.3012753, "the same-name street inside the city named in the postal address: the same address filed as SAINT LOUIS PARK is at -93.338"),
    "28899 S WOODLAND RD|CLEVELAND|OH|44124": (
        41.4792238, -81.5863815, "the street's centre, not the house number: west of 17700 S Woodland (Shaker Heights); 28000+ is Pepper Pike"),
    "31549 S WOODLAND RD|CLEVELAND|OH|44124": (
        41.4792238, -81.5863815, "the street's centre, not the house number: west of 17700 S Woodland (Shaker Heights); 31000+ is Hunting Valley"),
    "8220 DELMAR BLVD|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, "the street's centre, not the house number: east of 7365 Delmar (-90.328) in the same data"),
    "8300 DELMAR BLVD|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, "the street's centre, not the house number: east of 7365 Delmar (-90.328) in the same data"),
    "8000 MARYLAND AVE|SAINT LOUIS|MO|63105": (
        38.6443077, -90.2594943, "the same-name street inside the city named in the postal address: Central West End; 8000 Maryland Ave is in Clayton"),
    "7442 VALENCIA DR|BOCA RATON|FL|33433": (
        26.3216127, -80.0785086, "the street's centre, not the house number: on the Deerfield border, 8.5 km from the West Boca ZIP"),
    "7081 VALENCIA DR|BOCA RATON|FL|33433": (
        26.3216127, -80.0785086, "the street's centre, not the house number: on the Deerfield border, 8.5 km from the West Boca ZIP"),
    "5200 NORTHSIDE DR|ATLANTA|GA|30327": (
        33.7851924, -84.4075235, "Georgia Tech; 4711 Northside Dr in the same data is at 33.882"),
    "1800 PEACHTREE ST NW|ATLANTA|GA|30309": (
        33.754012, -84.390034, "downtown; the same donor's 1800 PEACHTREE ST NE is at 33.804"),
    "3400 PEACHTREE RD NW|ATLANTA|GA|30326": (
        33.8302174, -84.3867636, "2500 Peachtree Rd NW's point; 3400 PEACHTREE RD NE is at 33.850"),
    "4253 LOWER ROSWELL RD|MARIETTA|GA|30068": (
        33.9509024, -84.4957569, "6.3 km west of 4220 Lower Roswell Rd (33.961, -84.429) in the same data"),
    "88 W PACES FERRY RD|ATLANTA|GA|30305": (
        33.8480401, -84.4092557, "the street's centre, not the house number: 2.6 km west of 88 W PACES FERRY RD NW (33.840, -84.383)"),
    "7222 137TH ST|FLUSHING|NY|11367": (
        40.766575, -73.830273, "near downtown Flushing; 7233 137TH ST in the same data is at 40.724"),
    "1010 S JOLIET ST|DENVER|CO|80012": (
        39.7558215, -104.8613063, "the same-name street inside the city named in the postal address: 80012 is Aurora; the same address filed as AURORA is at 39.698"),
    "4601 N PARK AVE|CHEVY CHASE|MD|20815": (
        38.990075, -77.091846, "by NIH (20894); 4550 N PARK AVE on the same short street is at 38.963"),
    "11500 SAN VICENTE BLVD|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, "the street's centre, not the house number: Santa Monica border; 11661-11693 are at -118.465"),
    "11726 SAN VICENTE BLVD|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, "the street's centre, not the house number: Santa Monica border; 11661-11693 are at -118.465"),
    "11726 SAN VICENTE BLVD STE 650|LOS ANGELES|CA|90049": (
        34.0444042, -118.4981905, "the street's centre, not the house number: Santa Monica border; 11661-11693 are at -118.465"),
    "8630 DELMAR BLVD, SUITE 100|SAINT LOUIS|MO|63124": (
        38.6557551, -90.3027869, "SPETNER ASSOCIATES; the street's centre, not the house number of Delmar (the 8220/8300 point)"),
    "8630 DELMAR BLVD, SUITE 100|ST. LOUIS|MO|63124": (
        38.6557551, -90.3027869, "SPETNER ASSOCIATES; the street's centre, not the house number of Delmar (the 8220/8300 point)"),
    "6365 MONTESSOURI STREET|LAS VEGAS|NV|89113": (
        36.1404295, -115.2474218, "POKERGO, Park West Business Center (89113); the same-name street inside the city named in the postal address by Sahara Ave"),
    "1 COPLEY PL, SUITE 105|BOSTON|MA|02116": (
        42.316545, -71.100891, "THE UROLOGY GROUP; the point is Jamaica Plain, Copley Place is in Back Bay"),
    "66 HUDSON BOULEVARD|NEW YORK|NY|10001": (
        40.718403330373, -74.008784846222, "DEBEVOISE (census): Hudson St in Tribeca; 66 Hudson Blvd is The Spiral at Hudson Yards"),
    "2711 S OCEAN DR|HOLLYWOOD|FL|33019": (
        26.0120606, -80.1175272, "Old match rejected; the replacement was borrowed from another street number."),
    "1 HOFSTRA UNIVERSITY|HEMPSTEAD|NY|11549": (
        40.7730672, -73.6110909, "Old match rejected; the replacement was borrowed from another street number."),
}

