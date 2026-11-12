"""Read the plain-text rule tables kept as CSV files."""

import csv
from functools import cache
from pathlib import Path

from fec.common.settings import DATA_DIR

# Built-in spellings and word lists; reviewed decisions stay in data/rules.
CONFIG_DIR = DATA_DIR / "config"


def read_rows(path: Path) -> list[dict]:
    """Read a CSV file into a list of row dicts."""
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_map(path: Path) -> dict:
    """First column to second, in file order."""
    rows = read_rows(path)
    if not rows:
        return {}
    key, value = list(rows[0])[:2]
    return {row[key]: row[value] for row in rows}


def read_set(path: Path) -> frozenset:
    """Read the word column of a CSV file into a set."""
    return frozenset(row["word"] for row in read_rows(path))


@cache
def ordered_words(name: str) -> tuple:
    """One named word list from word_lists.csv, in file order."""
    rows = read_rows(CONFIG_DIR / "word_lists.csv")
    return tuple(row["word"] for row in rows if row["list"] == name)


def words(name: str) -> frozenset:
    """One named word list from word_lists.csv."""
    return frozenset(ordered_words(name))
