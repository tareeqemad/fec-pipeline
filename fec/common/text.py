"""Shared cleaning helpers."""

import re
from typing import Any

import pandas as pd

# The letter-and-digit words of uppercase text.
WORD_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def upper_single_spaced(value: Any) -> str:
    """Uppercase text with single spaces; '' for a missing value."""
    return " ".join(str(value or "").upper().split())


def upper_dots_as_spaces(value: object) -> str:
    """Uppercase text with dots read as spaces and single spaces; '' for anything but text."""
    return " ".join(value.upper().replace(".", " ").split()) if isinstance(value, str) else ""


def name_words_without_initials(*parts: Any) -> frozenset[str]:
    """Word set of a personal name, ignoring order, punctuation and initials."""
    text = " ".join(str(part) for part in parts if isinstance(part, str))
    return frozenset(word for word in re.sub(r"[^A-Z]", " ", text.upper()).split() if len(word) > 1)


def upper_text(values: pd.Series) -> pd.Series:
    """Fill blanks, trim spaces and uppercase text."""
    return values.fillna("").astype(str).str.strip().str.upper()
