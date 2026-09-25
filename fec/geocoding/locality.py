"""One filing's place: filed city, state, ZIP and their reference points."""
import time

from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    city_level,
)
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.zip_checks import (
    _zip_area_point,
    _zip_point,
)


class _Locality:
    """The filed city/state/ZIP of one address; each city-level query runs at most once."""

    # store the filed city/state/ZIP and its zip-point lookups
    def __init__(self, city: str, state: str, zipcode: str):
        self.city = city
        self.state = state
        self.zipcode = zipcode
        self.zip_point = _zip_point(zipcode, state)
        self.zip_area = self.zip_point or _zip_area_point(zipcode, state)
        self._city_points: dict[str, tuple | None] = {}

    # look up and cache the filed town's own point
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
