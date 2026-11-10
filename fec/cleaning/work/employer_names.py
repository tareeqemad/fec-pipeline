"""Normalize employer names and fix misplaced occupations."""
import re

import pandas as pd

from fec.cleaning.work.employer_aliases import (
    reviewed_employer_name,
)

def expand_abbreviation_text(values: pd.Series) -> pd.Series:
    """Only reviewed full company names expand an abbreviation."""
    return values.map(reviewed_employer_name)


_LOOSE_SLASH_RE = re.compile(r'\s/|/\s|//')

