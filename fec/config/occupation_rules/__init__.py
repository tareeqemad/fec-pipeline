"""Occupation normalization, typo fixes, and category rules."""
from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE  # noqa: F401
from fec.config.occupation_rules.fixes import OCCUPATION_FIXES  # noqa: F401
from fec.config.occupation_rules.categories import (  # noqa: F401
    CATEGORY_PATTERNS, VALID_CATEGORIES,
)
from fec.config.occupation_rules.overrides import CATEGORY_OVERRIDES  # noqa: F401
from fec.config.occupation_rules.canonical import OCCUPATION_CANONICAL  # noqa: F401
