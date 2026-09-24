"""Address geocoding: the single-address engine chain plus the contributor and employer dataframe passes."""

import math
import re
import statistics
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from fec.log import get_logger
from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS
from fec.cleaning.foreign_addresses import foreign_address_mask

from .cache import GeoCache
from .engines import (
    CensusUnavailable,
    NOMINATIM_DELAY,
    NominatimUnavailable,
    census,
    city_level,
    nominatim,
    nominatim_international,
    nominatim_within,
)
from .places import distance_km as _distance_km
from .places import in_us_bounds as _in_us_bounds
from .places import valid_for_state as _valid_for_state
from .reviewed_points import (
    REVIEWED_WRONG_POINTS,
    is_reviewed_wrong,
    is_reviewed_zip_typo,
    reviewed_point,
)

logger = get_logger(__name__)

_ZIP_CENTROIDS = Path(__file__).resolve().parents[2] / "data" / "database" / "zip_centroids.csv"
# a city-level point and the filed ZIP farther apart than this contradict each other
_ZIP_OUTLIER_KM = 50

# Street-level results must sit inside the filed ZIP. A ZIP's size is measured as
# the distance from its centroid to the 3rd-nearest other ZIP centroid (the 1st and
# 2nd are often single-building or campus ZCTAs: 10112 Rockefeller Center, 08544
# Princeton University). On the 17,770 street-level results in the 2026-09-23 data
# the distance to the filed ZIP centroid is at most 1.98x that size for 99.5% of
# them; the 48 beyond 2.5x (0.27%) are same-named streets in another town (1 BRYANT
# PARK NY on Long Island, N LA SALLE ST Chicago on the South Side, 281 WILSHIRE AVE
# LA in Fullerton, '3 RD FLOOR' 103 km away) plus ZIP typos. Rural ZIPs (Kamas UT,
# Aspen, Casper) stay under 2.5x because their neighbours are far apart too. The
# 5 km floor keeps a dense downtown ZIP from rejecting a result a few blocks off.
# A result outside the filed ZIP is not wrong by that alone: when the same street
# cannot be found inside the filed ZIP and Census and Nominatim agree on the point,
# the ZIP is the typo and the point is kept if it lies in the filed city (see
# _resolve_outside_zip). Hand-checked typos are listed in reviewed_points.py.
STREET_LEVEL_SOURCES = frozenset({"census", "nominatim", "google"})
_REVIEWED_STREET_SOURCES = STREET_LEVEL_SOURCES | {"manual_census"}
_STREET_ZIP_SPREAD = 2.5
_STREET_ZIP_FLOOR_KM = 5.0
_ZIP_NEIGHBOUR_RANK = 3
# a ZIP without a centroid is placed by this many nearest-numbered ZIPs of its 3-digit area
_ZIP_AREA_NEIGHBOURS = 4
# two engines' points for one address this close together are the same place
# (Census address ranges vs OSM: 0.01-0.7 km apart for the 2026-09-23 typos)
_ENGINES_AGREE_KM = 1.0

_US_ZIP_RE = re.compile(r"\d{5}(?:-?\d{4})?")
# postcodes that can never be a mangled US ZIP: Canada (M5V 3L9) and the UK (NW1 5DX)
_FOREIGN_POSTCODE_RE = re.compile(
    r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d"
    r"|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}"
)

_PO_BOX_RE = re.compile(r"^PO\s+BOX", re.IGNORECASE)

# every REVIEWED_POINTS point is a copied Google street result
_REVIEWED_POINT_LEVEL = "google"

# require whitespace before the keyword and a word boundary after, so short
# abbreviations (FL, STE, RM, APT) never match inside street names like FLANDERS
_SUITE_RE = re.compile(
    r',?\s+(?:Suite|Ste|Floor|Fl|Unit|Apt|Apartment|Room|Rm|Bldg|PH)\b\.?\s*\S+.*$'
    r'|,?\s*#\s*\S+.*$',
    re.IGNORECASE,
)
_FLOOR_RE = re.compile(
    r',?\s*\d+(?:st|nd|rd|th)\s+Floor.*$',
    re.IGNORECASE,
)
# a building's name before its street address ('ONE WILLIAMS CENTER 101 E 2ND ST',
# 'CIRA CENTRE, 2929 ARCH ST'): the name ends in a building word and is followed by
# a house number and a street; 'ONE KENDALL SQ BUILDING 600 STE 380' names no street
_BUILDING_NAME_RE = re.compile(
    r"^[A-Z][A-Z'&.\- ]*?\b(?:CENTER|CENTRE|TOWERS?|PLAZA|BUILDING|BLDG|HALL|HOUSE|COMPLEX"
    r"|CAMPUS|PAVILION|ATRIUM)\s*,?\s+"
    r"(?=\d+[A-Z]?\s+(?!(?:STE|SUITE|FL|FLOOR|UNIT|APT|RM|ROOM|BLDG|BUILDING)\b)[A-Z0-9])",
    re.IGNORECASE,
)

# Spelled-out ordinal streets ("777 THIRD AVE") geocode to the wrong place far more
# often than the numbered form Census/TIGER and OSM use ("777 3RD AVE"), so geocode
# keys, and with them the queries, use numbers. Only before a street type, so named
# streets ("SECOND LAKE RD") keep their words.
_ORDINAL_WORDS = {
    "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
    "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11,
    "TWELFTH": 12, "THIRTEENTH": 13, "FOURTEENTH": 14, "FIFTEENTH": 15,
    "SIXTEENTH": 16, "SEVENTEENTH": 17, "EIGHTEENTH": 18, "NINETEENTH": 19,
    "TWENTIETH": 20, "THIRTIETH": 30, "FORTIETH": 40, "FIFTIETH": 50,
    "SIXTIETH": 60, "SEVENTIETH": 70, "EIGHTIETH": 80, "NINETIETH": 90,
}
_ORDINAL_TENS = {
    "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50,
    "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90,
}
_ORDINAL_STREET_RE = re.compile(
    rf"\b(?:({'|'.join(_ORDINAL_TENS)})[\s-]+)?({'|'.join(_ORDINAL_WORDS)})\b"
    r"(?=\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT"
    r"|CIR|CIRCLE|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|TER|TERRACE|SQ|SQUARE|TRL|TRAIL|WAY)\b)",
    re.IGNORECASE,
)
# A building named after its street ("120 FIFTH AVENUE PLACE") is found by that name,
# so such addresses keep their words.
_ORDINAL_BUILDING_RE = re.compile(
    rf"\b(?:{'|'.join(_ORDINAL_WORDS)})\s+(?:AVE|AVENUE|ST|STREET)"
    r"\s+(?:PLACE|PLAZA|TOWER|TOWERS|CENTER|CENTRE|BUILDING)\b",
    re.IGNORECASE,
)


def _ordinal_number(match: re.Match) -> str:
    number = (_ORDINAL_TENS.get((match.group(1) or "").upper(), 0)
              + _ORDINAL_WORDS[match.group(2).upper()])
    suffix = "TH" if 10 <= number % 100 <= 20 else {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


def numbered_street(street: str) -> str:
    """'777 THIRD AVE' -> '777 3RD AVE'; any other street is returned unchanged."""
    if _ORDINAL_BUILDING_RE.search(street):
        return street
    return _ORDINAL_STREET_RE.sub(_ordinal_number, street)


def _numbered_streets(streets: pd.Series) -> pd.Series:
    return streets.map(numbered_street)


def is_po_box(street: str) -> bool:
    return bool(_PO_BOX_RE.match(street.strip()))


def is_foreign_address(state: str, zipcode: str) -> bool:
    """True for a non-US state or province (CUNDINAMARCA), or no state with a non-US postcode (NW1 5DX, 6744316); a US state with a malformed ZIP ('MA', '2138') stays US."""
    state = str(state or "").strip().upper()
    zipcode = str(zipcode or "").strip().upper()
    # old cache keys hold ZIPs read as floats ('10007.0', 'NAN'): still US ZIPs / no ZIP
    float_zip = re.fullmatch(r"(\d{3,5})\.0", zipcode)
    if zipcode in {"NAN", "NONE"}:
        zipcode = ""
    elif float_zip:
        zipcode = float_zip.group(1).zfill(5)
    if state:
        if state not in _STATE_BOUNDS:
            return True
        # a US state decides, unless the postcode is one no US ZIP typo can produce
        return bool(_FOREIGN_POSTCODE_RE.fullmatch(zipcode))
    # no state at all: only a US ZIP makes it a US address with the state missing
    return not _US_ZIP_RE.fullmatch(zipcode)


def is_foreign_key(key: str) -> bool:
    """is_foreign_address for a STREET|CITY|STATE|ZIP cache key."""
    parts = key.split("|")
    if len(parts) != 4:
        return False
    return is_foreign_address(parts[2], parts[3])


def _contributor_keys(df: pd.DataFrame) -> pd.Series:
    """Cache key per row: street|city|state|zip, NaN as empty, ordinal streets numbered."""
    cols = df[['contributor_street_1', 'contributor_city',
               'contributor_state', 'contributor_zip']].fillna('')
    return (_numbered_streets(cols['contributor_street_1']) + '|' +
            cols['contributor_city'] + '|' +
            cols['contributor_state'] + '|' +
            cols['contributor_zip'])


def _employer_keys(frame: pd.DataFrame) -> pd.Series:
    """Cache key per row: STREET|CITY|STATE|ZIP, stripped, uppercased, ordinal streets numbered."""
    cols = frame[['employer_address', 'employer_city',
                  'employer_state', 'employer_zip']].fillna('')
    return (_numbered_streets(cols['employer_address'].str.strip().str.upper()) + '|' +
            cols['employer_city'].str.strip().str.upper() + '|' +
            cols['employer_state'].str.strip().str.upper() + '|' +
            cols['employer_zip'].str.strip().str.upper())


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    if not _ZIP_CENTROIDS.exists():
        return {}
    rows = pd.read_csv(_ZIP_CENTROIDS, dtype={"zip": str})
    return {
        str(row.zip).zfill(5): (float(row.lat), float(row.lng))
        for row in rows.itertuples()
    }


def _far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    if not re.fullmatch(r"\d{5}", zipcode):
        return False
    centroid = _zip_centroids().get(zipcode)
    return bool(centroid and _distance_km((lat, lng), centroid) > _ZIP_OUTLIER_KM)


@lru_cache(maxsize=1)
def _centroid_radians() -> np.ndarray:
    centroids = _zip_centroids()
    if not centroids:
        return np.empty((0, 2))
    return np.radians(np.array(list(centroids.values()), dtype=float))


@lru_cache(maxsize=None)
def _zip_neighbour_km(zipcode: str) -> float | None:
    """Distance from the ZIP's centroid to its 3rd-nearest other ZIP centroid (the ZIP's size)."""
    centroid = _zip_centroids().get(zipcode)
    points = _centroid_radians()
    if centroid is None or len(points) <= _ZIP_NEIGHBOUR_RANK:
        return None
    lat, lng = np.radians(centroid)
    value = (np.sin((points[:, 0] - lat) / 2) ** 2
             + np.cos(lat) * np.cos(points[:, 0]) * np.sin((points[:, 1] - lng) / 2) ** 2)
    distances = 6371 * 2 * np.arcsin(np.sqrt(np.clip(value, 0, 1)))
    distances = np.sort(distances[distances > 0.01])  # drop the ZIP itself
    if len(distances) < _ZIP_NEIGHBOUR_RANK:
        return None
    return float(distances[_ZIP_NEIGHBOUR_RANK - 1])


def street_zip_limit_km(zipcode: str) -> float | None:
    """Farthest a street-level result may sit from the filed ZIP's centroid; None when the ZIP has no centroid."""
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    size = _zip_neighbour_km(zipcode)
    if size is None:
        return None
    return max(_STREET_ZIP_FLOOR_KM, _STREET_ZIP_SPREAD * size)


def _street_far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    """A street-level result outside its filed ZIP (threshold explained at STREET_LEVEL_SOURCES)."""
    centroid = _zip_centroids().get(zipcode) if re.fullmatch(r"\d{5}", zipcode or "") else None
    if centroid is None:
        return False
    distance = _distance_km((lat, lng), centroid)
    if distance <= _STREET_ZIP_FLOOR_KM:  # never beyond the limit; skips the neighbour search
        return False
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit


def _zip_point(zipcode: str, state: str) -> tuple[float, float] | None:
    """The filed ZIP's centroid, when it is a known 5-digit ZIP inside the filed state."""
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    point = _zip_centroids().get(zipcode)
    if point is None or not _valid_for_state(point[0], point[1], state):
        return None
    return point


def _zip_area_point(zipcode: str, state: str) -> tuple[float, float] | None:
    """Where the filed ZIP lies, to tell same-name towns apart: its centroid, or for a ZIP
    without one (PO-box-only and unique ZIPs: 94141, 78711, 20859) the median of the
    nearest-numbered ZIP centroids of its 3-digit area inside the filed state (20859's
    neighbours are Potomac and Rockville, not Potomac in Allegany County). None without a ZIP.
    """
    point = _zip_point(zipcode, state)
    if point is not None or not re.fullmatch(r"\d{5}", zipcode or ""):
        return point
    number = int(zipcode)
    neighbours = sorted(
        (abs(int(other) - number), lat, lng)
        for other, (lat, lng) in _zip_centroids().items()
        if other[:3] == zipcode[:3] and _valid_for_state(lat, lng, state)
    )[:_ZIP_AREA_NEIGHBOURS]
    if not neighbours:
        return None
    return (statistics.median(lat for _gap, lat, _lng in neighbours),
            statistics.median(lng for _gap, _lat, lng in neighbours))


def _zip_replaces_city(city_point: tuple[float, float], zip_point: tuple[float, float],
                       zipcode: str, po_box: bool) -> bool:
    """Whether the filed ZIP's centroid is a better approximate pin than the filed city's point.

    Never when the two contradict each other (over 50 km apart). A street address
    lies somewhere in its ZIP, so the ZIP's centroid wins. A PO box sits at the post
    office, in the named town, so the town's point stays unless it lies outside the
    filed ZIP altogether (downtown St. Louis for a Webster Groves 63119 box). A rural
    ZIP's centroid can be 20-30 km from its town (PO BOX 3530 SAN ANGELO 76902).
    """
    distance = _distance_km(city_point, zip_point)
    if distance > _ZIP_OUTLIER_KM:
        return False
    if not po_box:
        return True
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit


def accepted_coordinates(key: str, entry: dict | None) -> tuple[float | None, float | None, str]:
    """(lat, lng, level) a cached result may publish, or (None, None, reason) when the key's own address rules it out.

    A hand-checked point (reviewed_points.REVIEWED_POINTS) is published whatever the
    cache holds; a street point shown wrong (REVIEWED_WRONG_POINTS) never is."""
    reviewed = reviewed_point(key)
    if reviewed is not None:
        return reviewed[0], reviewed[1], _REVIEWED_POINT_LEVEL
    if not entry or entry.get("lat") is None:
        return None, None, "not_found"
    lat, lng = float(entry["lat"]), float(entry["lng"])
    source = entry.get("source") or ""
    country = entry.get("country") or "US"
    parts = key.split("|")
    if len(parts) != 4:
        return lat, lng, source
    _street, _city, state, zipcode = parts
    if is_foreign_address(state, zipcode):
        # a foreign office is published only with a foreign match (KIFO London sat in Connecticut)
        if country == "US":
            return None, None, "rejected_us_match_for_foreign"
        return lat, lng, source
    if country != "US":
        return lat, lng, source
    if not _valid_for_state(lat, lng, state):
        return None, None, "rejected_out_of_us"
    if _reviewed_wrong_street(key, entry, lat, lng):
        return None, None, "rejected_reviewed_wrong"
    if _unchecked_outside_zip(key, entry, lat, lng, zipcode):
        return None, None, "rejected_far_from_zip"
    return lat, lng, source


def _reviewed_wrong_street(key: str, entry: dict, lat: float, lng: float) -> bool:
    """A cached street-level point at the spot reviewed_points shows wrong for the key.

    A ZIP centroid or town point there is honest about its precision and stands."""
    return entry.get("source") in _REVIEWED_STREET_SOURCES and is_reviewed_wrong(key, lat, lng)


def _unchecked_outside_zip(key: str, entry: dict, lat: float, lng: float, zipcode: str) -> bool:
    """A cached street-level result outside its filed ZIP that no run and no reviewer has checked yet."""
    return (entry.get("source") in STREET_LEVEL_SOURCES
            and not entry.get("zip_checked")
            and not is_reviewed_zip_typo(key)
            and _street_far_from_zip(lat, lng, zipcode))


def prefer_zip_centroids(keys, cache: GeoCache) -> int:
    """Replace cached city-centroid fallbacks with the filed ZIP's centroid where _zip_replaces_city allows; returns entries changed."""
    changed = 0
    for key in keys:
        entry = cache.get(key)
        if (not entry or entry.get("source") != "nominatim_city"
                or entry.get("lat") is None or (entry.get("country") or "US") != "US"):
            continue
        parts = key.split("|")
        if len(parts) != 4 or is_foreign_address(parts[2], parts[3]):
            continue
        point = _zip_point(parts[3], parts[2])
        if point is None:
            continue
        city_point = (float(entry["lat"]), float(entry["lng"]))
        if not _zip_replaces_city(city_point, point, parts[3], is_po_box(parts[0])):
            continue
        cache.put(key, point[0], point[1], "zip_centroid", "US",
                  validated=True, zip_checked=True)
        changed += 1
    return changed


# _lookup_reason value for a cached town pin that only needs the one-time town re-check
_TOWN_CHECK = "town_check"


def _needs_lookup(key: str, cache: GeoCache) -> bool:
    return _lookup_reason(key, cache) is not None


def _lookup_reason(key: str, cache: GeoCache) -> str | None:
    """Why a key is looked up (again) this run, or None when its cached entry stands."""
    if reviewed_point(key) is not None:
        return None  # checked by hand: the reviewed point is published, no lookup can beat it
    if cache.needs_retry(key):
        return "uncached"

    entry = cache.get(key) or {}
    source = entry.get("source")
    if (entry.get("lat") is not None
            and _reviewed_wrong_street(key, entry, float(entry["lat"]), float(entry["lng"]))):
        return "reviewed_wrong"
    if source == "manual_census":
        return None
    if is_reviewed_zip_typo(key) and source in STREET_LEVEL_SOURCES and entry.get("lat") is not None:
        return None  # checked by hand: the point is right, the filed ZIP is the typo
    if source == "not_found":
        return None if entry.get("validated") else "unvalidated_not_found"
    if is_foreign_key(key):
        # a foreign office cached with a US match is looked up again, internationally
        if entry.get("lat") is not None and (entry.get("country") or "US") == "US":
            return "us_match_for_foreign"
        return None
    if entry.get("country", "US") != "US" or entry.get("lat") is None:
        return None
    if accepted_coordinates(key, entry)[0] is None:
        # a cached point its own address rules out (another state, far outside
        # its ZIP) is never published, so it must be looked up again
        return "rejected"

    street, _city, state, zipcode = key.split("|")
    if state not in _STATE_BOUNDS:
        return None
    lat, lng = float(entry["lat"]), float(entry["lng"])
    # a street-level result outside its ZIP that no run has checked against the
    # filed ZIP and city yet (validated results from before the ZIP check included)
    if _unchecked_outside_zip(key, entry, lat, lng, zipcode):
        return "outside_zip"
    if source == "census":
        return None
    far_from_zip = _far_from_zip(lat, lng, zipcode)
    if entry.get("validated"):
        if source == "nominatim_city" and not is_po_box(street) and far_from_zip:
            return "far_from_zip"
    elif far_from_zip:
        return "far_from_zip"
    if _town_check_due(entry, state, zipcode):
        return _TOWN_CHECK
    return None


def _town_check_due(entry: dict, state: str, zipcode: str) -> bool:
    """A town-level pin cached before the settlement-only town search, in a ZIP without a centroid to hold it.

    The old free-text query put these on a POI named '... USA', a county, a road or
    the next city (Lego Miniland in Carlsbad for SAN FRANCISCO 94141, North Little
    Rock for LITTLE ROCK 72217), and nothing checked them: a PO-box-only or unique
    ZIP (or none filed) has no centroid for the ZIP rules to compare with. Each is
    looked up once more; the answer is stored with town_checked. A pin in a ZIP with
    a centroid is already held inside that ZIP by prefer_zip_centroids and the
    fallback's ZIP rules, so it is not re-checked.
    """
    return (entry.get("source") == "nominatim_city"
            and not entry.get("town_checked")
            and _zip_point(zipcode, state) is None)


def geocode_addresses(df: pd.DataFrame, cache: GeoCache,
                      batch_size: int = 50):
    """Geocode every unique address in df, skipping cached keys and saving the cache every batch_size lookups."""
    keys_series = _contributor_keys(df)
    keys_series = keys_series[~foreign_address_mask(df)]
    all_keys = set(keys_series[keys_series != '|||'].unique())
    _prefer_zip_centroids_logged(all_keys, cache)

    todo = [key for key in all_keys if _needs_lookup(key, cache)]

    logger.info(f"\n  Unique addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:    {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:         {len(todo):,}")
    if not todo:
        logger.info("  All addresses cached")
        return

    estimated_seconds = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Estimated time:    ~{int(estimated_seconds//3600)}h {int((estimated_seconds%3600)//60)}m")

    _geocode_todo(todo, cache, batch_size)


def _prefer_zip_centroids_logged(keys, cache: GeoCache) -> None:
    changed = prefer_zip_centroids(keys, cache)
    if changed:
        cache.save()
        logger.info(f"  City centroids replaced by the filed ZIP's centroid: {changed:,}")


def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Map cached results onto latitude/longitude/geocode_level (vectorized)."""
    keys = _contributor_keys(df)

    # re-validate cached coords against the key's own address (state box, ZIP,
    # foreign office) so a stale wrong cache entry is dropped
    def _lookup(key):
        entry = cache.get(key)
        lat, lng, level = accepted_coordinates(key, entry)
        if lat is None:
            return np.nan, np.nan, "US", level
        return lat, lng, (entry or {}).get("country", "US"), level

    unique_keys = keys.unique()
    key_results = {key: _lookup(key) for key in unique_keys}

    results = keys.map(key_results)
    df["latitude"] = results.apply(lambda result: result[0])
    df["longitude"] = results.apply(lambda result: result[1])
    df["geocode_level"] = results.apply(lambda result: result[3])
    # a foreign filing (REHOVOT / CA, JERUSALEM, ISRAEL / NY) has no US coordinate
    foreign = foreign_address_mask(df)
    if foreign.any():
        df.loc[foreign, ["latitude", "longitude"]] = np.nan
        df.loc[foreign, "geocode_level"] = "foreign_not_geocoded"
    # x[2] (country) was dropped from the schema; used above only to keep foreign coords

    return df


def geocode_employer_addresses(df: pd.DataFrame, cache: GeoCache,
                               batch_size: int = 50):
    """Geocode employer addresses; skips empty rows, but RETIRED with a previous employer IS geocoded at the company address."""
    mask = (df['employer_address'].notna() &
            (df['employer_address'] != ''))
    keys = _employer_keys(df.loc[mask])
    all_keys = set(keys[keys != '|||'].unique())
    _prefer_zip_centroids_logged(all_keys, cache)

    todo = [key for key in all_keys if _needs_lookup(key, cache)]

    logger.info(f"\n  Employer addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:      {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:           {len(todo):,}")
    if not todo:
        logger.info("  All employer addresses cached")
        return

    estimated_seconds = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Estimated time:      ~{int(estimated_seconds//3600)}h {int((estimated_seconds%3600)//60)}m")

    _geocode_todo(todo, cache, batch_size)


def apply_employer_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Map cached employer geocoding onto employer_latitude/longitude/geocode_level."""
    method = df.get(
        "resolve_method",
        pd.Series("", index=df.index),
    ).fillna("")

    df["employer_latitude"] = np.nan
    df["employer_longitude"] = np.nan
    df["employer_geocode_level"] = "no_address"

    # fec_not_found rows keep no_address
    mask_skip = method == "fec_not_found"

    employer_address = df['employer_address'].fillna('')
    mask_real = (~mask_skip) & (employer_address != '')

    if mask_real.any():
        keys = _employer_keys(df.loc[mask_real])

        unique_keys = keys.unique()
        key_results = {}
        for key in unique_keys:
            lat, lng, level = accepted_coordinates(key, cache.get(key))
            if lat is None:
                key_results[key] = (np.nan, np.nan, level)
            else:
                key_results[key] = (lat, lng, level)

        results = keys.map(key_results)
        df.loc[mask_real, "employer_latitude"] = results.apply(lambda result: result[0]).values
        df.loc[mask_real, "employer_longitude"] = results.apply(lambda result: result[1]).values
        df.loc[mask_real, "employer_geocode_level"] = results.apply(lambda result: result[2]).values

    return df


def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    """Run the engine chain over todo keys, saving the cache and logging an ETA every batch_size lookups."""
    stats = {
        "found": 0,
        "failed": 0,
        "transient": 0,
        "city": 0,
        "town_kept": 0,
    }
    start = time.time()
    # the one-time town re-check replaces a working pin only with a better one
    town_rechecks = {key for key in todo if _lookup_reason(key, cache) == _TOWN_CHECK}

    for done, key in enumerate(todo, 1):
        street, city, state, zipcode = key.split("|")
        recheck = key in town_rechecks

        try:
            lat, lng, country, source = _geocode_one(
                street, city, state, zipcode,
            )
        except (NominatimUnavailable, CensusUnavailable) as error:
            if not recheck:  # a re-checked pin stays as it is and is re-checked next run
                cache.put_transient(key)
            stats["transient"] += 1
            logger.debug("Geocoding unavailable for %s: %s", key, error)
            continue

        if lat is not None and not (recheck and (country or "US") != "US"):
            # every result written here passed the ZIP / foreign checks in _geocode_one
            cache.put(key, lat, lng, source, country, validated=True, zip_checked=True,
                      town_checked=True)
            stats["found"] += 1
            if source in {"nominatim_city", "zip_centroid"}:
                stats["city"] += 1
        elif recheck:
            # no town of that name found (JAMAICA NY is no OSM settlement): keep the
            # old pin rather than erase it or move a US filing abroad
            cache.mark_town_checked(key)
            stats["town_kept"] += 1
            logger.debug("Town re-check found nothing better for %s: cached pin kept", key)
        else:
            cache.put_failed(key, validated=True)
            stats["failed"] += 1

        if done % batch_size == 0 or done == len(todo):
            cache.save()
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 1
            eta = (len(todo) - done) / rate
            logger.info(
                "  [%s/%s] found: %s  failed: %s  ETA: %dm %ds",
                f"{done:,}", f"{len(todo):,}",
                f"{stats['found']:,}", f"{stats['failed']:,}",
                int(eta // 60), int(eta % 60),
            )

    cache.save()
    _print_summary(todo, stats, time.time() - start)


def _clean_street_for_geocoding(street: str) -> str:
    """Strip a leading building name and suite/floor/unit suffixes that confuse Nominatim and Census."""
    cleaned = _BUILDING_NAME_RE.sub('', street)
    cleaned = _FLOOR_RE.sub('', cleaned)
    cleaned = _SUITE_RE.sub('', cleaned)
    return cleaned.strip().rstrip(',')


class _Locality:
    """The filed city/state/ZIP of one address; each city-level query runs at most once."""

    def __init__(self, city: str, state: str, zipcode: str):
        self.city = city
        self.state = state
        self.zipcode = zipcode
        self.zip_point = _zip_point(zipcode, state)
        self.zip_area = self.zip_point or _zip_area_point(zipcode, state)
        self._city_points: dict[str, tuple | None] = {}

    def city_point(self, zipcode: str = "") -> tuple | None:
        """(lat, lng, country) of the filed town itself (a settlement of that name, inside the filed state; see engines.city_level), optionally searched with the filed ZIP."""
        if zipcode not in self._city_points:
            point = None
            if self.city:
                lat, lng, country = city_level(self.city, self.state, zipcode, self.zip_area)
                time.sleep(NOMINATIM_DELAY)
                if lat is not None and _valid_for_state(lat, lng, self.state):
                    point = (lat, lng, country)
            self._city_points[zipcode] = point
        return self._city_points[zipcode]


def _inside_filed_zip(lat: float, lng: float, locality: _Locality) -> bool:
    """A street-level result inside the filed ZIP (always true when the ZIP has no centroid to test against)."""
    if locality.zip_point is None:
        return True
    limit = street_zip_limit_km(locality.zipcode)
    return limit is None or _distance_km((lat, lng), locality.zip_point) <= limit


def _accept_street(lat: float, lng: float, locality: _Locality) -> bool:
    """Keep a street-level result inside the filed ZIP, or outside it when the filed ZIP contradicts the filed city (a ZIP typo 50+ km off) and the result lies in that city."""
    if _inside_filed_zip(lat, lng, locality):
        return True
    city = locality.city_point() if locality.city else None
    if city is None:
        return False
    city_point = city[:2]
    return (_distance_km(city_point, locality.zip_point) > _ZIP_OUTLIER_KM
            and _distance_km((lat, lng), city_point) <= _ZIP_OUTLIER_KM)


def _zip_search_box(locality: _Locality) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) around the filed ZIP's centroid, as wide as the street-inside-ZIP limit."""
    limit = street_zip_limit_km(locality.zipcode) if locality.zip_point else None
    if limit is None:
        return None
    lat, lng = locality.zip_point
    dlat = limit / 111.0
    dlng = limit / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng


def _street_in_filed_zip(street: str, locality: _Locality) -> tuple | None:
    """The same street in the filed city, searched only around the filed ZIP; a hit inside the ZIP, or None when the ZIP has no such street.

    The city is part of the query: without it a same-named street in the next town
    at the edge of the search circle counts (474 CENTRAL AVE PASSAIC matched in
    Hackensack)."""
    box = _zip_search_box(locality)
    if box is None or not street:
        return None
    lat, lng, country_code = nominatim_within(street, locality.city, locality.state, box)
    time.sleep(NOMINATIM_DELAY)
    if lat is None or not _valid_for_state(lat, lng, locality.state):
        return None
    if not _inside_filed_zip(lat, lng, locality):
        return None
    return lat, lng, country_code or "US", "nominatim"


def _in_filed_city(lat: float, lng: float, locality: _Locality) -> bool:
    """A point within 50 km of the filed city's point; of the filed ZIP's centroid when the city is missing or unknown."""
    city = locality.city_point() if locality.city else None
    anchor = city[:2] if city else locality.zip_point
    return anchor is not None and _distance_km((lat, lng), anchor) <= _ZIP_OUTLIER_KM


def _resolve_outside_zip(street: str, locality: _Locality, outside: list[tuple]) -> tuple | None:
    """Street results that landed outside the filed ZIP (engine order: census, nominatim).

    If the street exists inside the filed ZIP, the outside result was a same-named
    street in another town (10 S LA SALLE ST CHICAGO 60603 matched on the South
    Side): the hit inside the ZIP wins. If the filed ZIP has no such street, the ZIP
    may be the typo (2160 GOLD ST SAN JOSE filed with 95112 is in Alviso, 95002).
    The outside point is kept only when Census and Nominatim independently put the
    address at the same spot, in the filed city. One engine alone proves nothing:
    Nominatim put 1 BRYANT PARK in Yonkers and Census put 1 FOUNTAIN SQUARE
    CHATTANOOGA in East Ridge, while neither street could be found inside its ZIP.
    Otherwise None, and the caller falls back to the filed ZIP.
    """
    inside = _street_in_filed_zip(street, locality)
    if inside is not None:
        return inside
    if len(outside) >= 2:
        first, second = outside[0], outside[1]
        if (_distance_km(first[:2], second[:2]) <= _ENGINES_AGREE_KM
                and _in_filed_city(first[0], first[1], locality)):
            return first
    logger.debug("Street match outside ZIP %s not confirmed (%s, %s): left to the ZIP/city fallback",
                 locality.zipcode, street, locality.city)
    return None


def _fallback(locality: _Locality, require_zip_match: bool = False,
              po_box: bool = False) -> tuple | None:
    """City-level fallback: the filed ZIP's centroid when _zip_replaces_city allows, else the town's point; None on a city/ZIP conflict when require_zip_match.

    The ZIP is part of the town search only when it has a centroid: OSM does not know
    PO-box-only and unique ZIPs (94141, 78711). With no centroid to test against, the
    town's own settlement point is the pin (a PO box sits at the post office, in the town).
    """
    zip_point = locality.zip_point
    conflicting = None
    for zip_code in ([locality.zipcode, ""] if zip_point is not None else [""]):
        point = locality.city_point(zip_code)
        if point is None:
            continue
        lat, lng, country = point
        if zip_point is None:
            return lat, lng, country or "US", "nominatim_city"
        if _distance_km((lat, lng), zip_point) <= _ZIP_OUTLIER_KM:
            if _zip_replaces_city((lat, lng), zip_point, locality.zipcode, po_box):
                return zip_point[0], zip_point[1], "US", "zip_centroid"
            return lat, lng, country or "US", "nominatim_city"  # a PO box in its own town
        conflicting = conflicting or point
    if zip_point is not None and conflicting is None:
        # the city is unknown to Nominatim: the filed ZIP is the only evidence left
        return zip_point[0], zip_point[1], "US", "zip_centroid"
    if conflicting is not None and not require_zip_match:
        lat, lng, country = conflicting
        return lat, lng, country or "US", "nominatim_city"
    return None


def _city_fallback(city: str, state: str, zipcode: str,
                   require_zip_match: bool = False) -> tuple:
    """(lat, lng, country) of the city-level fallback, or Nones."""
    result = _fallback(_Locality(city, state, zipcode), require_zip_match)
    if result is None:
        return None, None, None
    return result[:3]


def _geocode_one(street, city, state, zipcode):
    """Geocode one address; a foreign address only ever uses the international engine."""
    if is_foreign_address(state, zipcode):
        return _geocode_foreign(street, city, state, zipcode)

    if not city and not state:
        return None, None, None, "not_found"

    locality = _Locality(city, state, zipcode)
    raw_street = street
    if is_po_box(street):
        result = _fallback(locality, po_box=True)
        if result:
            return result
        return _geocode_abroad_unless_us_zip(street, city, locality)

    if street:
        street = _clean_street_for_geocoding(street)

    if f"{raw_street}|{city}|{state}|{zipcode}" in REVIEWED_WRONG_POINTS:
        return _geocode_reviewed_wrong(raw_street, street, locality)

    outside = []  # street-level results that landed outside the filed ZIP
    if street and state in _STATE_BOUNDS:
        try:
            lat, lng, country_code = census(street, city, state, zipcode)
        except CensusUnavailable as error:
            logger.debug("Census Geocoder unavailable for %s: %s", street, error)
        else:
            if lat and _valid_for_state(lat, lng, state):
                if _accept_street(lat, lng, locality):
                    return lat, lng, country_code or "US", "census"
                outside.append((lat, lng, country_code or "US", "census"))

    if street:
        lat, lng, country_code = nominatim(street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat and _valid_for_state(lat, lng, state):
            if _accept_street(lat, lng, locality):
                return lat, lng, country_code or "US", "nominatim"
            logger.debug("Nominatim result lat=%.4f lng=%.4f outside ZIP %s", lat, lng, zipcode)
            outside.append((lat, lng, country_code or "US", "nominatim"))
        elif lat:
            logger.debug("Nominatim result lat=%.4f lng=%.4f rejected - outside %s", lat, lng, state)

    if outside:
        result = _resolve_outside_zip(street, locality, outside)
        if result:
            return result

    result = _fallback(locality, require_zip_match=True)
    if result:
        return result

    # a foreign address mislabeled with a US state is kept and flagged, not dropped
    return _geocode_abroad_unless_us_zip(street, city, locality)


def _geocode_reviewed_wrong(key_street: str, street: str, locality: _Locality) -> tuple:
    """A key whose cached street point was shown wrong: Census inside the filed ZIP, else the ZIP's centroid.

    Nominatim is skipped: it gave most of these points (the same-name street inside
    the city of the postal name) and would give them again. A Census answer at the
    rejected spot is refused. CensusUnavailable is raised, so an outage is retried
    next run instead of settling for the centroid."""
    key = f"{key_street}|{locality.city}|{locality.state}|{locality.zipcode}"
    if street and locality.state in _STATE_BOUNDS:
        lat, lng, country_code = census(street, locality.city, locality.state, locality.zipcode)
        if (lat is not None and _valid_for_state(lat, lng, locality.state)
                and _inside_filed_zip(lat, lng, locality) and not is_reviewed_wrong(key, lat, lng)):
            return lat, lng, country_code or "US", "census"
    if locality.zip_point is not None:
        return locality.zip_point[0], locality.zip_point[1], "US", "zip_centroid"
    result = _fallback(locality, require_zip_match=True)
    return result or (None, None, None, "not_found")


def _geocode_foreign(street, city, state, zipcode):
    """A foreign address (no US state, or a non-US postal code): international engine only, and only a non-US result is accepted."""
    queries = []
    if street:
        queries.append((street, "nominatim_intl"))
    if city:
        queries.append(("", "nominatim_intl_city"))
    for query_street, level in queries:
        lat, lng, country_code = nominatim_international(query_street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat is not None and country_code and country_code != "US":
            return lat, lng, country_code, level
    return None, None, None, "not_found"


def _geocode_abroad_unless_us_zip(street, city, locality: _Locality):
    """The international last resort, skipped when the filed ZIP lies in the filed state.

    Such a filing is in the US even when its town is not found: JAMAICA NY is no OSM
    settlement, and 'PO BOX 5, JAMAICA' searched abroad is the island."""
    if locality.zip_area is not None:
        return None, None, None, "not_found"
    return _geocode_international(street, city)


def _geocode_international(street, city):
    """Last resort without a US restriction; accept only a confidently foreign result (non-US country AND coords outside the US), else not_found."""
    if not street and not city:
        return None, None, None, "not_found"
    # the suspected-wrong US state is dropped so it doesn't bias the lookup back into the US
    lat, lng, country_code = nominatim_international(street, city, "", "")
    time.sleep(NOMINATIM_DELAY)
    if lat and country_code and country_code != "US" and not _in_us_bounds(lat, lng):
        return lat, lng, country_code, "nominatim_intl"
    return None, None, None, "not_found"


def _print_summary(todo: list, stats: dict, elapsed: float) -> None:
    logger.info("\n\n  -- Geocoding Done --")
    logger.info(f"  Processed:   {len(todo):,} in {int(elapsed//60)}m {int(elapsed%60)}s")
    logger.info(f"  Found:       {stats['found']:,}")
    logger.info(f"  Failed:      {stats['failed']:,}")
    if stats.get("transient"):
        logger.info(f"  Retry later: {stats['transient']:,}")
    if stats["city"]:
        logger.info(f"  City-level:  {stats['city']:,}")
    if stats.get("town_kept"):
        logger.info(f"  Town re-check kept the cached pin: {stats['town_kept']:,}")
