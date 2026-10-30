"""Read the plain-text rule tables kept as CSV files."""
import csv

from fec.env import DATA_DIR

# Built-in spellings and word lists; reviewed decisions stay in data/rules.
CONFIG_DIR = DATA_DIR / "config"


def read_rows(path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# First column to second, in file order.
def read_map(path) -> dict:
    rows = read_rows(path)
    if not rows:
        return {}
    key, value = list(rows[0])[:2]
    return {row[key]: row[value] for row in rows}


def read_set(path) -> frozenset:
    return frozenset(row["value"] for row in read_rows(path))

