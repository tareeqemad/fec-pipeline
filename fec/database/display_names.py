"""Store donor names as people read them: HAIM SABAN is stored as Haim Saban.

The FEC files names in capitals. Cleaning and matching keep working on those capitals; only the names written
to the database are cased, so the website and the chatbot show them as they are.
"""

from __future__ import annotations

import re

from fec.vocabulary.tables import CONFIG_DIR, read_map, words

DOTTED_INITIALS = re.compile(r"(?:[A-Z]\.)+[A-Z]?\.?")
SHORT_CAPITALS = re.compile(r"[A-Z][A-Z&.]{1,4}")


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
            cased.append("-".join(_name_word(part) for part in word.split("-")))
    return " ".join(cased)


def _name_word(word: str) -> str:
    """One word of a name: acronyms kept, ActBlue, McDonald, MacArthur, O'Brien."""
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
