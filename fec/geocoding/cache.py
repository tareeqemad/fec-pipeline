"""Persistent JSON cache: address key -> coords; not_found is final, transient_fail retries next run."""

import json
import os


class GeoCache:

    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    def save(self):
        """Write cache to disk atomically via a temp file."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False)
        os.replace(temp_path, self.path)

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def needs_retry(self, key: str) -> bool:
        """True if the key is uncached or failed transiently."""
        entry = self.data.get(key)
        if entry is None:
            return True
        return entry.get("source") == "transient_fail"

    def put(self, key: str, lat: float, lng: float, source: str, country: str = "US"):
        """Store a hit; country is ISO-2, old entries without it read as 'US'."""
        self.data[key] = {"lat": lat, "lng": lng, "source": source, "country": country}

    def put_failed(self, key: str):
        """Mark an address as genuinely not geocodable (never retried)."""
        self.data[key] = {"lat": None, "lng": None, "source": "not_found"}

    def __len__(self):
        return len(self.data)

    def stats(self) -> dict:
        found = sum(1 for entry in self.data.values() if entry["lat"] is not None)
        by_source: dict[str, int] = {}
        for entry in self.data.values():
            source = entry["source"]
            by_source[source] = by_source.get(source, 0) + 1

        return {
            "total": len(self.data),
            "found": found,
            "failed": len(self.data) - found,
            "by_source": by_source,
        }
