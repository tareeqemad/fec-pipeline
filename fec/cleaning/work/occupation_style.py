"""Occupation style: separators, joiners, CO- prefix, compounds, plurals."""

import re

from fec.vocabulary.tables import CONFIG_DIR, ordered_words, read_rows, words

# Titles combined with "/"; longest first so VICE PRESIDENT wins.
_TITLES = sorted(ordered_words("occupation_titles"), key=len, reverse=True)
_TITLE_ALT = "|".join(re.escape(title) for title in _TITLES)
# whole string = TITLE (& | AND | -) TITLE
_TITLE_PAIR_RE = re.compile(rf"^({_TITLE_ALT})\s*(?:&|\bAND\b|-)\s*({_TITLE_ALT})$")
# CO before a role word is the co- prefix.
_CO_PREFIX_RE = re.compile(r"\bCO\s*-?\s*(?=(?:" + "|".join(ordered_words("co_prefix_roles")) + r")\b)")
# Compound spellings of the same words, anywhere.
_COMPOUNDS = [
    (re.compile(row["pattern"]), row["replacement"]) for row in read_rows(CONFIG_DIR / "occupation_compounds.csv")
]
# Role nouns de-pluralized as the last word, never after OF.
_ROLE_NOUNS = words("role_nouns")
# Last word ending in S, without the S.
_LAST_WORD_RE = re.compile(r"([A-Z]+)S$")


def _depluralize_last_word(value: str) -> str:
    if " OF " in f" {value} ":
        return value
    match = _LAST_WORD_RE.search(value)
    if not match:
        return value
    singular = match.group(1)
    if singular not in _ROLE_NOUNS:
        return value
    # The plural must be the whole last word.
    start = match.start(1)
    if start and value[start - 1].isalpha():
        return value
    return value[:start] + singular


def normalize_occupation_style(value: str) -> str:
    """Apply every style rule to one occupation string."""
    # Separator spacing; short abbreviations like R&D stay tight.
    text = re.sub(r"\s*/+\s*", "/", value)
    text = re.sub(r"\s*&\s*", " & ", text)
    text = re.sub(r"\b([A-Z]{1,2}) & ([A-Z]{1,2})\b", r"\1&\2", text)
    for regex, replacement in _COMPOUNDS:
        text = regex.sub(replacement, text)
    text = _CO_PREFIX_RE.sub("CO-", text)
    text = _TITLE_PAIR_RE.sub(r"\1/\2", text)
    text = _depluralize_last_word(text)
    return re.sub(r"\s{2,}", " ", text).strip()
