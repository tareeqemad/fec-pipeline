"""The OTHER-reclassifier may only write category names the rest of the pipeline recognises."""
import re

from fec.config import VALID_CATEGORIES


def test_reclassify_categories_are_valid():
    src = open('fec/cleaning/safety_nets/occupation.py', encoding='utf-8').read()
    start = src.index('_KEYWORD_CATS = [')
    block = src[start:src.index(']', start) + 1]
    cats = re.findall(r"',\s*'([A-Z][^']*)'\)", block)

    assert cats, "could not parse the keyword→category map"
    invalid = sorted({c for c in cats if c not in VALID_CATEGORIES})
    assert not invalid, f"categories not in VALID_CATEGORIES: {invalid}"
