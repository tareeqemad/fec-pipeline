"""Persistent coordinates, keyed by the cleaned address: street|city|state|zip.

Older runs keyed entries differently (uppercased fields, numbered ordinals,
earlier street spellings); those entries were rewritten under the current
keys once, so a lookup is a plain lookup.
"""

from fec.io import JsonCache


class GeoCache(JsonCache):
    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def needs_retry(self, key: str) -> bool:
        entry = self.get(key)
        return entry is None or entry.get("lookup_status", entry.get("source")) == "transient_fail"

    def put(self, key: str, lat: float, lng: float, source: str, country: str = "US",
            matched_address: dict | None = None):
        self.data[key] = {"lat": lat, "lng": lng, "source": source, "country": country}
        if matched_address is not None:
            self.data[key]["matched_address"] = matched_address

    def _record_miss(self, key: str, status: str):
        entry = self.get(key)
        if entry and entry.get("lat") is not None:
            self.data[key] = {**entry, "lookup_status": status}
        else:
            self.data[key] = {"lat": None, "lng": None, "source": status}

    def put_failed(self, key: str):
        self._record_miss(key, "not_found")

    def put_transient(self, key: str):
        self._record_miss(key, "transient_fail")
