"""Simple JSON cache for resolved employer data."""

import json
import os

from fec.io import write_json_atomic


class Cache:
    # load cached data from path if it exists
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    # look up a cached value by key
    def get(self, key):
        return self.data.get(key)

    # store a value under key in the cache
    def put(self, key, value):
        self.data[key] = value

    # remove a key from the cache if present
    def discard(self, key):
        self.data.pop(key, None)

    # write the cache to disk atomically
    def save(self):
        write_json_atomic(self.path, self.data, ensure_ascii=False)

    # number of entries in the cache
    def __len__(self):
        return len(self.data)
