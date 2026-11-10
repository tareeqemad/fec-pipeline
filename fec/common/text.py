"""Shared cleaning helpers."""

import re
from typing import Any

import pandas as pd


def name_words_without_initials(*parts: Any) -> frozenset[str]:
    """Word set of a personal name, ignoring order, punctuation and initials."""
    text = " ".join(str(part) for part in parts if isinstance(part, str))
    return frozenset(word for word in re.sub(r"[^A-Z]", " ", text.upper()).split() if len(word) > 1)


def upper_text(s: pd.Series) -> pd.Series:
    """Fill blanks, trim spaces and uppercase text."""
    return s.fillna("").astype(str).str.strip().str.upper()
