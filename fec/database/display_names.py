"""Store names as people read them: HAIM SABAN is stored as Haim Saban, 251 H ST NW as 251 H St NW.

The FEC files names in capitals. Cleaning, matching and loading keep working on those capitals; the last load
step rewrites donor names, streets, cities, company names, jobs and job categories in the database, so the
website and the chatbot show them as they are. Only the capitals change: a job stays the words the donor filed.
"""

from __future__ import annotations

import re
from typing import Any

from psycopg2.extras import execute_values

from fec.vocabulary.tables import CONFIG_DIR, read_map, words

DOTTED_INITIALS = re.compile(r"(?:[A-Z]\.)+[A-Z]?\.?")
SHORT_CAPITALS = re.compile(r"[A-Z][A-Z&.]{1,4}")
# Each table, its key and the text columns people read.
CASED_COLUMNS = {
    "donors": ("donor_id", ("first_name", "last_name")),
    "addresses": ("address_id", ("street_1", "street_2", "city")),
    "employers": ("employer_id", ("name",)),
    "donor_employments": ("donor_employment_id", ("occupation",)),
    "occupation_categories": ("occupation_category_id", ("name",)),
}


def case_stored_text(cur: Any) -> None:
    """Rewrite stored names, places and work in normal capitals, once every row is linked."""
    for table, (key, columns) in CASED_COLUMNS.items():
        cur.execute(f"SELECT {key}, {', '.join(columns)} FROM {table}")
        rows = [(row[0], *(cased_name(value) for value in row[1:])) for row in cur.fetchall()]
        assignments = ", ".join(f"{column} = new.{column}" for column in columns)
        execute_values(
            cur,
            f"UPDATE {table} SET {assignments} FROM (VALUES %s) AS new({key}, {', '.join(columns)}) "
            f"WHERE {table}.{key} = new.{key}",
            rows,
            page_size=5000,
        )


def cased_name(name: object) -> object:
    """One first or last name in normal capitals; an empty value stays as it is."""
    if not isinstance(name, str) or not name.strip():
        return name
    # A whole name with a reviewed spelling, like DSCC written out in full.
    spelling = read_map(CONFIG_DIR / "name_spellings.csv").get(name.upper())
    if spelling:
        return spelling
    small_words = words("name_small_words")
    cased = []
    for index, word in enumerate(name.split()):
        if index and word.lower() in small_words:
            cased.append(word.lower())
        else:
            # Each part around a hyphen or slash is cased on its own: Smith-Jones, N/A.
            cased.append("".join(_name_word(part) for part in re.split(r"([-/])", word)))
    return " ".join(cased)


def _name_word(word: str) -> str:
    """One word with its commas and brackets: (VP), CEO, and VP, keep their capitals."""
    head, core, tail = re.fullmatch(r"([(\"]*)(.*?)([,;:)\"]*)", word).groups()
    return head + _cased_word(core) + tail


def _cased_word(word: str) -> str:
    """One word of a name: acronyms kept, ActBlue, McDonald, MacArthur, O'Brien, 31st, 4B."""
    if word[:1].isdigit():
        return re.sub(r"^(\d+)(ST|ND|RD|TH)$", lambda match: match[1] + match[2].lower(), word)
    spelling = read_map(CONFIG_DIR / "name_spellings.csv").get(word.upper())
    if spelling:
        return spelling
    if not word or word in words("name_acronyms") or DOTTED_INITIALS.fullmatch(word):
        return word
    if _reads_as_acronym(word):
        return word
    lower = word.lower()
    if re.match(r"mc[a-z]", lower):
        return "Mc" + lower[2].upper() + lower[3:]
    if re.fullmatch(r"mac[a-z]{2,}", lower):
        return "Mac" + lower[3].upper() + lower[4:]
    cased = lower[:1].upper() + lower[1:]
    return re.sub(r"^([ODL])'([a-z])", lambda match: f"{match[1]}'{match[2].upper()}", cased)


def _reads_as_acronym(word: str) -> bool:
    """Short, all capitals and without a vowel, like LLC or MDC; Y counts as a vowel in LYNN."""
    if word.rstrip(".") in words("name_abbreviations") or not SHORT_CAPITALS.fullmatch(word):
        return False
    letters = re.sub(r"[^A-Z]", "", word)
    vowels = "AEIOUY" if len(letters) > 3 else "AEIOU"
    return not any(letter in vowels for letter in letters)
