"""Company identity follows reviewed rules; loose keys compare text."""
import re

from fec.cleaning.work.employer_aliases import (
    employer_rule_key,
    preserved_employer_name,
    reviewed_employer_name,
)

_CORP_SUFFIXES = (
    "INCORPORATED",
    "CORPORATION",
    "COMPANY",
    "ENTERPRISES",
    "PLLC",
    "CORP",
    "GROUP",
    "LLC",
    "LLP",
    "INC",
    "LTD",
    "CO",
    "PC",
    "PA",
    "LP",
)
# Short suffixes glued onto one word belong to the brand.
_GLUED_BRAND_SUFFIXES = frozenset({"CO", "PC", "PA", "LP"})

# Abbreviations folded for grouping only; ASSOC is deliberately absent.
_KEY_TOKEN_EXPANSIONS = {"UNIV": "UNIVERSITY", "MT": "MOUNT", "ASSOCS": "ASSOCIATES"}



def canonical_key(name: str) -> str:
    """Only explicit aliases can join differently named companies."""
    return employer_rule_key(reviewed_employer_name(name))


def employer_text_key(name: str) -> str:
    """Ignore punctuation and suffixes when comparing employer text."""
    key = name.strip().upper()
    if not key:
        return ""
    key = re.sub(r"\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b", "", key)
    # & becomes AND, then AND drops; boundaries before whitespace collapse.
    key = re.sub(r"[&$]", " AND ", key)
    key = re.sub(r"^\s*THE\b", "", key)
    key = re.sub(r"\bAND\b", " ", key)
    key = " ".join(_KEY_TOKEN_EXPANSIONS.get(token, token) for token in key.split())
    # One-word names keep a glued CO/PC/PA/LP: GOLDCO stays.
    words = re.findall(r"[A-Z0-9]+", key)
    while len(words) > 1 and words[-1] in _CORP_SUFFIXES:
        words.pop()
    brand_length = len(words[0]) if len(words) == 1 else 0
    key = re.sub(r"[^A-Z0-9]+", "", key)
    # iteratively strip trailing corporate suffixes until stable
    while True:
        for suffix in _CORP_SUFFIXES:
            cuts_brand = len(key) - len(suffix) < brand_length
            if cuts_brand and suffix in _GLUED_BRAND_SUFFIXES:
                continue
            if key.endswith(suffix) and len(key) > len(suffix) + 3:
                key = key[: -len(suffix)]
                break
        else:
            break
    return key



def tidy_employer_commas(name):
    """Style only: keep every name word and legal form."""
    if not isinstance(name, str) or "," not in name or preserved_employer_name(name):
        return name
    return re.sub(r"\s*,(?=[A-Z])", ", ", re.sub(r"\s+,", ",", name))
