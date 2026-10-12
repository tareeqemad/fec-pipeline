"""A filing's city, state, ZIP and points."""
import time

from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    city_level,
)
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.zip_checks import (
    zip_area_point,
    zip_center_point,
)


class Locality:
    """Filed city/state/ZIP; city queries run once."""

    # store filed place and its ZIP points
    def __init__(self, city: str, state: str, zipcode: str):
        self.city = city
        self.state = state
        self.zipcode = zipcode
        self.zip_point = zip_center_point(zipcode, state)
        self.zip_area = self.zip_point or zip_area_point(zipcode, state)
        self._city_points: dict[str, tuple | None] = {}

    # look up and cache the town's point
    def city_point(self, zipcode: str = "") -> tuple | None:
        if zipcode not in self._city_points:
            point = None
            if self.city:
                lat, lng, country = city_level(self.city, self.state, zipcode, self.zip_area)
                time.sleep(NOMINATIM_DELAY)
                if lat is not None and _valid_for_state(lat, lng, self.state):
                    point = (lat, lng, country)
            self._city_points[zipcode] = point
        return self._city_points[zipcode]
