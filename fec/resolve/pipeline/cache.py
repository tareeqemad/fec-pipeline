"""Simple JSON cache for resolved employer data."""

import json
import os


class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def discard(self, key):
        self.data.pop(key, None)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False)
        os.replace(temp_path, self.path)

    def __len__(self):
        return len(self.data)
