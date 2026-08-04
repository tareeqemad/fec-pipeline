"""The OTHER-reclassifier may only write recognised category names."""
from fec.config import RECLASSIFY_CATEGORY_RULES, VALID_CATEGORIES


def test_reclassify_categories_are_valid():
    invalid = sorted({
        category
        for _, category in RECLASSIFY_CATEGORY_RULES
        if category not in VALID_CATEGORIES
    })
    assert not invalid, f"categories not in VALID_CATEGORIES: {invalid}"
