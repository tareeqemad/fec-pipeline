"""JSON geocode cache; only transient failures retry."""

import json
import os

from fec.io import write_json_atomic


class GeoCache:

    # load the cache from disk into memory
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {}
        self._load()

    # read the JSON file if it exists
    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    # write cache to disk atomically
    def save(self):
        write_json_atomic(self.path, self.data, ensure_ascii=False)

    # look up a cached entry by key
    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    # true if key is uncached or transient-failed
    def needs_retry(self, key: str) -> bool:
        entry = self.data.get(key)
        if entry is None:
            return True
        return entry.get("source") == "transient_fail"

    # store a geocode hit; country is ISO-2
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

    # mark a cached point as town-checked
    def mark_town_checked(self, key: str):
        if key in self.data:
            self.data[key]["town_checked"] = True

    # mark address not geocodable; never retried
    def put_failed(self, key: str, validated: bool = False):
        self.data[key] = {
            "lat": None,
            "lng": None,
            "source": "not_found",
            "validated": validated,
        }

    # keep a temporary failure retryable
    def put_transient(self, key: str):
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
