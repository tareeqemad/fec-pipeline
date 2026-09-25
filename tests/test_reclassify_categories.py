"""Occupation rules may only write recognised, deterministic categories."""
from collections import defaultdict

import pandas as pd
import pytest

from fec.cleaning.occupations import _categorize_final
from fec.cleaning.occupations.normalize import _normalize_text
from fec.config.occupation_rules.categories import (
    RECLASSIFY_CATEGORY_RULES,
    VALID_CATEGORIES,
)
from fec.config.occupation_rules.fixes import OCCUPATION_FIXES
from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE


def test_reclassify_categories_are_valid():
    invalid = sorted({
        category
        for _, category in RECLASSIFY_CATEGORY_RULES
        if category not in VALID_CATEGORIES
    })
    assert not invalid, f"categories not in VALID_CATEGORIES: {invalid}"


def test_occupation_fixes_have_valid_consistent_final_categories():
    final_categories = defaultdict(set)
    invalid = set()

    for final_occupation, category in OCCUPATION_FIXES.values():
        if category:
            final_categories[final_occupation].add(category)
            if category not in VALID_CATEGORIES:
                invalid.add(category)

    conflicts = {
        occupation: sorted(categories)
        for occupation, categories in final_categories.items()
        if len(categories) > 1
    }
    assert not invalid, f"invalid occupation-fix categories: {sorted(invalid)}"
    assert not conflicts, f"conflicting final occupation categories: {conflicts}"


@pytest.mark.parametrize(
    ("raw", "cleaned", "category"),
    [
        ("CONSULYANT", "CONSULTANT", "CONSULTING"),
        ("WNTRERENEUR", "ENTREPRENEUR", "BUSINESS / ENTREPRENEUR"),
        ("PRGRMR", "PROGRAMMER", "TECHNOLOGY"),
        ("CEP", "CEO", "EXECUTIVE / C-SUITE"),
        ("CLAIMS", "CLAIMS", "INSURANCE"),
        ("FILM AND TV", "FILM AND TV", "ARTS / ENTERTAINMENT"),
        ("WEALTH ADVSIOR", "WEALTH ADVISOR", "FINANCE / INVESTMENT"),
        ("COMMERCIAL RE", "COMMERCIAL REAL ESTATE", "REAL ESTATE"),
        ("DIR OF ADVANCEMENT", "DIRECTOR OF ADVANCEMENT", "NONPROFIT / PHILANTHROPY"),
        ("DIR OF REGIONAL AFFAIRS", "DIRECTOR OF REGIONAL AFFAIRS", "EXECUTIVE / C-SUITE"),
        ("PR EXEC", "PR EXECUTIVE", "EXECUTIVE / C-SUITE"),
        ("BUS EXEC", "BUSINESS EXECUTIVE", "EXECUTIVE / C-SUITE"),
        ("BIZ EXEC", "BUSINESS EXECUTIVE", "EXECUTIVE / C-SUITE"),
        ("SR. VP", "SENIOR VICE PRESIDENT", "EXECUTIVE / C-SUITE"),
        ("VP DEVELOPMENT", "VP OF DEVELOPMENT", "NONPROFIT / PHILANTHROPY"),
        ("RETURWS", "RETIRED", "RETIRED"),
        ("MANAGING DIRECT", "MANAGING DIRECTOR", "EXECUTIVE / C-SUITE"),
        ("CANDIDATE FOR DC DELEGATE", "CANDIDATE FOR DC DELEGATE TO CONGRESS", "GOVERNMENT / MILITARY"),
        ("AL ESTATE", "REAL ESTATE", "REAL ESTATE"),
        ("DIR OF OPS", "DIRECTOR OF OPERATIONS", "EXECUTIVE / C-SUITE"),
        ("ENTRPRENUER", "ENTREPRENEUR", "BUSINESS / ENTREPRENEUR"),
    ],
)
def test_verified_occupation_audit_rules(raw, cleaned, category):
    normalized, _ = _normalize_text(pd.Series([raw]), OCCUPATION_NORMALIZE)

    assert normalized.iloc[0] == cleaned
    assert _categorize_final(normalized).iloc[0] == category
