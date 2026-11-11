"""Loose keys for comparing employer text."""

import re

from fec.vocabulary.tables import words

_CORP_SUFFIXES = (
    "INCORPORATED",
    "CORPORATION",
    "COMPANY",
    "ENTERPRISES",
    "PLLC",
    "CORP",
    "GROUP",
    "LLC",
    "LLP",
    "INC",
    "LTD",
    "CO",
    "PC",
    "PA",
    "LP",
)

# Short suffixes glued onto one word belong to the brand.
_GLUED_BRAND_SUFFIXES = words("glued_brand_suffixes")

# Abbreviations folded for grouping only; ASSOC is deliberately absent.
_KEY_TOKEN_EXPANSIONS = {"UNIV": "UNIVERSITY", "MT": "MOUNT", "ASSOCS": "ASSOCIATES"}


def _strip_one_suffix(key: str, brand_length: int) -> str:
    for suffix in _CORP_SUFFIXES:
        cuts_brand = len(key) - len(suffix) < brand_length
        if cuts_brand and suffix in _GLUED_BRAND_SUFFIXES:
            continue
        if key.endswith(suffix) and len(key) > len(suffix) + 3:
            return key[: -len(suffix)]
    return key


def employer_text_key(name: str) -> str:
    """Ignore punctuation and suffixes when comparing employer text."""
    key = name.strip().upper()
    if not key:
        return ""
    key = re.sub(r"\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b", "", key)
    # & becomes AND, then AND drops; boundaries before whitespace collapse.
    key = re.sub(r"[&$]", " AND ", key)
    key = re.sub(r"^\s*THE\b", "", key)
    key = re.sub(r"\bAND\b", " ", key)
    key = " ".join(_KEY_TOKEN_EXPANSIONS.get(token, token) for token in key.split())
    # One-word names keep a glued CO/PC/PA/LP: GOLDCO stays.
    words = re.findall(r"[A-Z0-9]+", key)
    while len(words) > 1 and words[-1] in _CORP_SUFFIXES:
        words.pop()
    brand_length = len(words[0]) if len(words) == 1 else 0
    key = re.sub(r"[^A-Z0-9]+", "", key)
    # strip trailing corporate suffixes one at a time until none is left
    while True:
        shorter = _strip_one_suffix(key, brand_length)
        if shorter == key:
            return key
        key = shorter
