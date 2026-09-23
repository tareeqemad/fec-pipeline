"""The ZIP centroid reference file must agree with the ZIP's own state and with our verified street-level geocodes.

Both checks can fail on real data: 99503 (Anchorage) sat 447 km away in the
middle of Alaska until 2026-09-22.
"""
import json
import re
import statistics
from collections import defaultdict

import pytest

from fec.cleaning.pipeline.address_fixes.state_zip import _load_zcta_to_state
from fec.env import DATA_DIR
from fec.geocoding.pipeline import (
    _STATE_BOUNDS,
    _ZIP_CENTROIDS,
    _distance_km,
    _zip_centroids,
)

_MARGIN_DEG = 1.0          # coarse state bounding boxes: border ZIPs may sit just outside
_MAX_KM_FROM_GEOCODES = 50  # same threshold the geocoder uses for its ZIP-distance retry rule
_MIN_GEOCODES = 3


@pytest.fixture(scope='module')
def centroids():
    if not _ZIP_CENTROIDS.exists():
        pytest.skip('zip_centroids.csv not present')
    return _zip_centroids()


def test_every_centroid_lies_in_its_zip_state(centroids):
    # the ZCTA crosswalk only: the ZIP3 fallback is a guess (967xx is Hawaii OR American Samoa)
    zcta_to_state = _load_zcta_to_state()
    if not zcta_to_state:
        pytest.skip('ZCTA crosswalk not present')

    outside = []
    for zipcode, (lat, lng) in centroids.items():
        state = zcta_to_state.get(zipcode)
        if not state or state not in _STATE_BOUNDS:
            continue
        lat_min, lat_max, lng_min, lng_max = _STATE_BOUNDS[state]
        if not (lat_min - _MARGIN_DEG <= lat <= lat_max + _MARGIN_DEG
                and lng_min - _MARGIN_DEG <= lng <= lng_max + _MARGIN_DEG):
            outside.append((zipcode, state, lat, lng))
    assert not outside, f'centroids outside their state: {outside[:10]}'


def test_centroids_agree_with_verified_street_geocodes(centroids):
    cache_path = DATA_DIR / 'geocode_cache.json'
    if not cache_path.exists():
        pytest.skip('geocode_cache.json not present')
    cache = json.loads(cache_path.read_text(encoding='utf-8'))

    points = defaultdict(list)
    for key, entry in cache.items():
        # only street-level hits: city-level fallbacks and misses say nothing about the ZIP
        if entry.get('lat') is None or entry.get('source') in {'nominatim_city', 'not_found'}:
            continue
        parts = key.split('|')
        if len(parts) != 4 or not re.fullmatch(r'\d{5}', parts[3]):
            continue
        points[parts[3]].append((float(entry['lat']), float(entry['lng'])))

    far = []
    for zipcode, pts in points.items():
        if len(pts) < _MIN_GEOCODES or zipcode not in centroids:
            continue
        median = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
        km = _distance_km(median, centroids[zipcode])
        if km > _MAX_KM_FROM_GEOCODES:
            far.append((zipcode, round(km), len(pts)))
    assert not far, f'centroids >{_MAX_KM_FROM_GEOCODES} km from their own verified geocodes: {far[:10]}'


def test_centroids_agree_with_authoritative_rooftop_geocodes(centroids):
    """A single Census-Bureau rooftop hit is authoritative: the ZIP centroid cannot be 100+ km from it (99503 was 447 km off)."""
    cache_path = DATA_DIR / 'geocode_cache.json'
    if not cache_path.exists():
        pytest.skip('geocode_cache.json not present')
    cache = json.loads(cache_path.read_text(encoding='utf-8'))

    far = []
    for key, entry in cache.items():
        if entry.get('lat') is None or entry.get('source') not in {'census', 'manual_census'}:
            continue
        parts = key.split('|')
        if len(parts) != 4 or parts[3] not in centroids:
            continue
        km = _distance_km((float(entry['lat']), float(entry['lng'])), centroids[parts[3]])
        if km > 100:
            far.append((parts[3], round(km), key))
    assert not far, f'centroids >100 km from a Census rooftop geocode: {far[:10]}'
