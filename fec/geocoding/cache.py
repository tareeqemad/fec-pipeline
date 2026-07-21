"""
geocode/cache.py — Persistent JSON cache for geocoding results.

Each entry maps an address key to its coordinates:
    "123 MAIN ST|NEW YORK|NY|10001" → {"lat": 40.71, "lng": -74.00, "source": "nominatim"}

Failed lookups stored as:
    source="not_found" — genuinely not found (don't retry)
    source="transient_fail" — timeout/rate-limit (retry on next run)
"""

import json
import os


class GeoCache:

    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        self._load()

    # ── Read / Write ──

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        """Write cache to disk atomically (via temp file)."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)          # atomic write

    # ── Lookup ──

    def get(self, key: str) -> dict | None:
        """Return cached result for key, or None if not cached."""
        return self.data.get(key)

    def needs_retry(self, key: str) -> bool:
        """Return True if this key should be retried (transient failure or not cached)."""
        entry = self.data.get(key)
        if entry is None:
            return True
        return entry.get("source") == "transient_fail"

    def put(self, key: str, lat: float, lng: float, source: str, country: str = "US"):
        """Store a successful geocoding result. country is ISO-2 ('US', 'IL', …);
        old entries without it are read as 'US' via .get(\"country\", \"US\")."""
        self.data[key] = {"lat": lat, "lng": lng, "source": source, "country": country}

    def put_failed(self, key: str):
        """Mark an address as not geocodable (genuinely not found — don't retry)."""
        self.data[key] = {"lat": None, "lng": None, "source": "not_found"}

    def __len__(self):
        return len(self.data)

    # ── Stats ──

    def stats(self) -> dict:
        found = sum(1 for v in self.data.values() if v["lat"] is not None)
        by_source: dict[str, int] = {}
        for v in self.data.values():
            src = v.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1

        return {
            "total":     len(self.data),
            "found":     found,
            "failed":    len(self.data) - found,
            "by_source": by_source,
        }
