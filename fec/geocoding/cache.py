"""Persistent coordinates, including entries from older geocoding runs."""

import json
from pathlib import Path

from fec.cleaning.addresses.street_text import numbered_street
from fec.io import write_json_atomic


class GeoCache:
    def __init__(self, path: str):
        self.path = path
        self.data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}

    def save(self):
        write_json_atomic(self.path, self.data, ensure_ascii=False)

    def get(self, key: str) -> dict | None:
        if key in self.data:
            return self.data[key]
        # Older runs uppercased fields and numbered ordinals in keys.
        parts = [part.strip().upper() for part in key.split("|")]
        normalized = "|".join(parts)
        if normalized in self.data:
            return self.data[normalized]
        parts[0] = numbered_street(parts[0])
        return self.data.get("|".join(parts))

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

    def __len__(self):
        return len(self.data)
