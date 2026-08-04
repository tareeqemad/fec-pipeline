"""Employer name canonicalization: unify suffix, punctuation and spacing variants."""
import re
from collections import defaultdict

import pandas as pd

from fec.config.constants import CANONICAL_EMPLOYER_SKIP_VALUES
from fec.log import get_logger

logger = get_logger(__name__)

# corporate suffixes stripped when grouping; word-boundary anchored (CPA must not match PA)
_DOTTED_SUFFIX_RE = re.compile(
    r',?\s*\b(P\.C\.?|P\.A\.?)\s*$'
)
_CORP_SUFFIX_RE = re.compile(
    r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PC|PA|PLLC|LP)\s*$'
)

# a trailing PA/PC after these tokens is real ("SCOTT FANE CPA PA"), don't strip it
_PA_PC_PROTECT_RE = re.compile(
    r'\b(CPA|DPA|RPA|EPA|SEPA|SHERPA)\s+(PA|PC)\s*$', re.I
)

def _employer_group_key(name: str) -> str:
    """Grouping key for canonicalization: strips suffixes, THE, punctuation and spacing."""
    key = name.strip().upper()

    # mid-word commas (EMA INVEST,MENT)
    key = re.sub(r'([A-Z]),([A-Z])', r'\1\2', key)

    key = re.sub(r'[,.\s]+', ' ', key)

    key = _DOTTED_SUFFIX_RE.sub('', key).strip()

    if not _PA_PC_PROTECT_RE.search(key):
        key = _CORP_SUFFIX_RE.sub('', key).strip()
    else:
        # strip everything except the protected trailing PA/PC
        key = re.sub(
            r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PLLC|LP)\s*$',
            '', key
        ).strip()

    key = re.sub(r'^\s*THE\s+', '', key)

    key = key.replace('&', 'AND')

    key = re.sub(r'\s+', '', key)

    return key


def _canonicalize_employers(df: pd.DataFrame) -> int:
    """Group employer variants by normalized key, rewrite each group to its most frequent spelling; status words are skipped."""
    emp = df['contributor_employer']
    mask = emp.notna() & (~emp.isin(CANONICAL_EMPLOYER_SKIP_VALUES))
    active = emp[mask]

    if active.empty:
        return 0

    # pre-clean: mid-word commas
    mid_comma = active.str.contains(r'[A-Z],[A-Z]', na=False, regex=True)
    if mid_comma.any():
        df.loc[mid_comma[mid_comma].index, 'contributor_employer'] = (
            active[mid_comma].str.replace(r'([A-Z]),([A-Z])', r'\1\2', regex=True)
        )
        active = df.loc[mask, 'contributor_employer']

    # pre-clean: P.C. -> PC, P.A. -> PA
    dotted = active.str.contains(r'\bP\.[CA]\.?\s*$', na=False, regex=True)
    if dotted.any():
        df.loc[dotted[dotted].index, 'contributor_employer'] = (
            active[dotted]
            .str.replace(r',?\s*\bP\.C\.?\s*$', ' PC', regex=True)
            .str.replace(r',?\s*\bP\.A\.?\s*$', ' PA', regex=True)
            .str.strip()
        )
        active = df.loc[mask, 'contributor_employer']

    counts = active.value_counts()

    groups = defaultdict(list)
    for name in counts.index:
        key = _employer_group_key(name)
        if key:
            groups[key].append(name)

    mapping = {}
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        canonical = max(variants, key=lambda variant: counts[variant])
        for variant in variants:
            if variant != canonical:
                mapping[variant] = canonical

    if not mapping:
        return 0

    to_fix = emp.isin(mapping)
    n_changed = int(to_fix.sum())
    if n_changed:
        df.loc[to_fix, 'contributor_employer'] = emp[to_fix].map(mapping)
        logger.info(
            "Canonicalized %d employer variants across %d groups -> %d rows updated",
            len(mapping), len([group for group in groups.values() if len(group) > 1]), n_changed
        )

    return n_changed
