"""Persistent coordinates, keyed by the cleaned address: street|city|state|zip.

Older runs keyed entries differently (uppercased fields, numbered ordinals,
earlier street spellings); those entries were rewritten under the current
keys once, so a lookup is a plain lookup.
"""

from fec.common.files import JsonCache


class GeoCache(JsonCache):
    """Store looked-up coordinates by cleaned address."""

    def needs_retry(self, key: str) -> bool:
        """Return whether the address is new or its last lookup failed for a temporary reason."""
        entry = self.get(key)
        return entry is None or entry.get("lookup_status", entry.get("source")) == "transient_fail"

    def put(self, key: str, lat: float, lng: float, source: str, country: str = "US") -> None:
        """Store the coordinates found for an address."""
        self.data[key] = {"lat": lat, "lng": lng, "source": source, "country": country}

    def _record_miss(self, key: str, status: str) -> None:
        """Record a failed lookup, keeping any coordinates found before."""
        entry = self.get(key)
        if entry and entry.get("lat") is not None:
            self.data[key] = {**entry, "lookup_status": status}
        else:
            self.data[key] = {"lat": None, "lng": None, "source": status}

    def put_failed(self, key: str) -> None:
        """Record that the address was not found."""
        self._record_miss(key, "not_found")

    def put_transient(self, key: str) -> None:
        """Record that the lookup failed for a temporary reason."""
        self._record_miss(key, "transient_fail")
