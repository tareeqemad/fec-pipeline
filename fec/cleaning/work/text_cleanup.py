"""Text normalization and regex categories for employer and occupation."""

import numpy as np
import pandas as pd

from fec.vocabulary.names import MISSING_VALUES
from fec.vocabulary.statuses import EMPLOYER_STATUS_VALUES, SKIP_EMPLOYERS

# junk value -> NaN, one batch replace
_JUNK_TO_NAN = {val: np.nan for val in MISSING_VALUES}

# Cyrillic lookalikes -> ASCII (common in FEC data entry)
_CYRILLIC_TO_ASCII = str.maketrans(
    {
        "\u0410": "A",
        "\u0412": "B",
        "\u0421": "C",
        "\u0415": "E",
        "\u041d": "H",
        "\u041a": "K",
        "\u041c": "M",
        "\u041e": "O",
        "\u0420": "P",
        "\u0422": "T",
        "\u0423": "Y",
        "\u0425": "X",
    }
)


_WORD_DIGITS_RE = r"(\b[A-Z]{3,})\d+\b"

# Employers whose trailing digits are keying noise: SELF EMPLOYED47.
EMPLOYER_STATUS_TEXT = frozenset(EMPLOYER_STATUS_VALUES) | frozenset(SKIP_EMPLOYERS)


def _normalize_text(
    series: pd.Series, normalize_map: dict, digits_only_before: frozenset | set | None = None
) -> pd.Series:
    """Clean text with a spelling map; occupation digits are noise."""
    cleaned = series.astype("string").str.strip().str.upper()

    cleaned = cleaned.str.translate(_CYRILLIC_TO_ASCII)

    # HTML entities
    cleaned = cleaned.str.replace("&RSQUO;", "'", regex=False)
    cleaned = cleaned.str.replace("&LSQUO;", "'", regex=False)
    cleaned = cleaned.str.replace("&AMP;", "&", regex=False)
    cleaned = cleaned.str.replace("&RDQUO;", '"', regex=False)
    cleaned = cleaned.str.replace("&LDQUO;", '"', regex=False)
    cleaned = cleaned.str.replace(r"&[A-Z]+;", "", regex=True)

    # curly braces
    cleaned = cleaned.str.replace(r"[{}]", "", regex=True)

    # trailing digits stuck to words (OWNER3 -> OWNER)
    stripped = cleaned.str.replace(_WORD_DIGITS_RE, r"\1", regex=True)
    if digits_only_before is None:
        cleaned = stripped
    else:
        to_status = stripped.str.strip().isin(digits_only_before).fillna(False)
        cleaned = cleaned.where(~to_status, stripped)

    # trailing punctuation
    cleaned = cleaned.str.replace(r"[\.\,\;\:\/\\]+\s*$", "", regex=True).str.strip()

    # double+ spaces
    cleaned = cleaned.str.replace(r"\s{2,}", " ", regex=True).str.strip()

    cleaned = cleaned.replace(_JUNK_TO_NAN)

    cleaned = cleaned.replace(normalize_map)
    return cleaned.astype(object)
