"""tests/test_reclassify_categories.py — the OTHER-reclassifier may only write
category names the rest of the pipeline recognises.

Three names in its keyword map had drifted from CATEGORY_RULES
('TECHNOLOGY / ENGINEERING', 'ARCHITECTURE / DESIGN',
'NON-PROFIT / PHILANTHROPY'). Nothing caught it because no row had reached
those branches — until an employer/occupation swap put 'INTERIOR ARCHITECT'
in an occupation field and the quality gate started reporting an invalid
category on every run."""
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
