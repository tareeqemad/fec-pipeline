"""Persistent JSON cache: address key -> coords; not_found is final, transient_fail retries next run."""

import json
import os


class GeoCache:

    # load the cache from disk into memory
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        self._load()

    # load cached entries from the JSON file if present
    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    # write cache to disk atomically via a temp file
    def save(self):
        """Write cache to disk atomically via a temp file."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False)
        os.replace(temp_path, self.path)

    # look up a cached entry by key
    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    # true if the key is uncached or failed transiently
    def needs_retry(self, key: str) -> bool:
        """True if the key is uncached or failed transiently."""
        entry = self.data.get(key)
        if entry is None:
            return True
        return entry.get("source") == "transient_fail"

    # store a successful geocode hit
    def put(
        self,
        key: str,
        lat: float,
        lng: float,
        source: str,
        country: str = "US",
        validated: bool = False,
        zip_checked: bool = False,
        town_checked: bool = False,
    ):
        """Store a hit; country is ISO-2, old entries without it read as 'US'; zip_checked marks a result already checked against its filed ZIP and city, town_checked one from the settlement-only town search."""
        self.data[key] = {
            "lat": lat,
            "lng": lng,
            "source": source,
            "country": country,
            "validated": validated,
        }
        if zip_checked:
            self.data[key]["zip_checked"] = True
        if town_checked:
            self.data[key]["town_checked"] = True

    # keep a cached point the town search couldn't improve on
    def mark_town_checked(self, key: str):
        """Keep a cached point that the settlement-only town search could not improve on."""
        if key in self.data:
            self.data[key]["town_checked"] = True

    # mark an address as genuinely not geocodable
    def put_failed(self, key: str, validated: bool = False):
        """Mark an address as genuinely not geocodable (never retried)."""
        self.data[key] = {
            "lat": None,
            "lng": None,
            "source": "not_found",
            "validated": validated,
        }

    # keep a temporary failure retryable
    def put_transient(self, key: str):
        """Keep a temporary failure retryable."""
        self.data[key] = {"lat": None, "lng": None, "source": "transient_fail"}

    # number of cached entries
    def __len__(self):
        return len(self.data)

    # summarize cache hits, misses, and sources
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
