"""Text normalization for the employer and occupation boxes."""

import numpy as np
import pandas as pd

from fec.vocabulary.names import MISSING_VALUES
from fec.vocabulary.statuses import EMPLOYER_STATUS_VALUES, SKIP_EMPLOYERS
from fec.vocabulary.tables import CONFIG_DIR, read_map

_JUNK_TO_NAN = dict.fromkeys(MISSING_VALUES, np.nan)
# Cyrillic lookalikes typed for Latin letters.
_CYRILLIC_TO_ASCII = str.maketrans(read_map(CONFIG_DIR / "cyrillic_lookalikes.csv"))
# Replaced in file order, before any other entity is dropped.
_HTML_ENTITIES = read_map(CONFIG_DIR / "html_entities.csv")
_WORD_DIGITS_RE = r"(\b[A-Z]{3,})\d+\b"
# Employers whose trailing digits are keying noise: SELF EMPLOYED47.
EMPLOYER_STATUS_TEXT = frozenset(EMPLOYER_STATUS_VALUES) | frozenset(SKIP_EMPLOYERS)


def clean_box_text(series: pd.Series, normalize_map: dict, digits_only_before: frozenset | None = None) -> pd.Series:
    """Clean text with a spelling map; trailing digits go, or only where they leave a text in digits_only_before."""
    cleaned = series.astype("string").str.strip().str.upper().str.translate(_CYRILLIC_TO_ASCII)
    for entity, text in _HTML_ENTITIES.items():
        cleaned = cleaned.str.replace(entity, text, regex=False)
    cleaned = cleaned.str.replace(r"&[A-Z]+;", "", regex=True).str.replace(r"[{}]", "", regex=True)
    # trailing digits stuck to words (OWNER3 -> OWNER)
    stripped = cleaned.str.replace(_WORD_DIGITS_RE, r"\1", regex=True)
    if digits_only_before is None:
        cleaned = stripped
    else:
        to_status = stripped.str.strip().isin(digits_only_before).fillna(False)
        cleaned = cleaned.where(~to_status, stripped)
    # trailing punctuation, then double spaces
    cleaned = cleaned.str.replace(r"[\.\,\;\:\/\\]+\s*$", "", regex=True).str.strip()
    cleaned = cleaned.str.replace(r"\s{2,}", " ", regex=True).str.strip()
    return cleaned.replace(_JUNK_TO_NAN).replace(normalize_map).astype(object)
