"""The category of an occupation: the reviewed table, else the category of its last listed word."""

import re

import pandas as pd

from fec.vocabulary.occupations import OCCUPATION_CATEGORIES, OCCUPATION_CATEGORY_WORDS


def category_of(occupation: str) -> str:
    """Return the table's category; an unlisted text takes the one of its last listed word (NURSE), else OTHER."""
    if occupation in OCCUPATION_CATEGORIES:
        return OCCUPATION_CATEGORIES[occupation]
    for word in reversed(re.findall(r"[A-Z]+", occupation)):
        if word in OCCUPATION_CATEGORY_WORDS:
            return OCCUPATION_CATEGORY_WORDS[word]
    return "OTHER"


def lookup_categories(occupation_series: pd.Series) -> pd.Series:
    """Return the category of each occupation; an empty occupation has none."""
    texts = occupation_series.dropna().astype(str).unique()
    categories = {text: category_of(text) for text in texts}
    return occupation_series.map(categories).astype(object).where(occupation_series.notna(), pd.NA)


def categorize_occupations(occupation_series: pd.Series) -> pd.Series:
    """Return the category of final cleaned occupations."""
    return lookup_categories(occupation_series.fillna("").astype(str).str.strip().str.upper())
