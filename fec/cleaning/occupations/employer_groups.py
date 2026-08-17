"""Unify employer spelling variants by a strict group key."""
import re
from collections import defaultdict

import pandas as pd

from fec.config.constants import CANONICAL_EMPLOYER_SKIP_VALUES
from fec.log import get_logger

logger = get_logger(__name__)

_DOTTED_SUFFIX_RE = re.compile(r',?\s*\b(P\.C\.?|P\.A\.?)\s*$')
_CORP_SUFFIX_RE = re.compile(
    r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PC|PA|PLLC|LP)\s*$'
)
_PA_PC_PROTECT_RE = re.compile(
    r'\b(CPA|DPA|RPA|EPA|SEPA|SHERPA)\s+(PA|PC)\s*$',
    re.IGNORECASE,
)


def _employer_group_key(name: str) -> str:
    """Build a strict key for employer spelling variants."""
    key = re.sub(r'([A-Z]),([A-Z])', r'\1\2', name.strip().upper())
    key = re.sub(r'[,\.\s]+', ' ', key)
    key = _DOTTED_SUFFIX_RE.sub('', key).strip()
    if not _PA_PC_PROTECT_RE.search(key):
        key = _CORP_SUFFIX_RE.sub('', key).strip()
    else:
        key = re.sub(
            r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PLLC|LP)\s*$',
            '', key,
        ).strip()
    key = re.sub(r'^\s*THE\s+', '', key).replace('&', 'AND')
    return re.sub(r'\s+', '', key)


def _canonicalize_employers(df: pd.DataFrame) -> int:
    """Unify punctuation, suffix and spacing variants of employer names."""
    employer = df['contributor_employer']
    mask = employer.notna() & ~employer.isin(CANONICAL_EMPLOYER_SKIP_VALUES)
    active = employer[mask]
    if active.empty:
        return 0

    mid_comma = active.str.contains(r'[A-Z],[A-Z]', na=False, regex=True)
    if mid_comma.any():
        indexes = mid_comma[mid_comma].index
        df.loc[indexes, 'contributor_employer'] = active[mid_comma].str.replace(
            r'([A-Z]),([A-Z])', r'\1\2', regex=True,
        )
        active = df.loc[mask, 'contributor_employer']

    dotted = active.str.contains(r'\bP\.[CA]\.?\s*$', na=False, regex=True)
    if dotted.any():
        indexes = dotted[dotted].index
        df.loc[indexes, 'contributor_employer'] = (
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
    for variants in groups.values():
        if len(variants) < 2:
            continue
        canonical = max(variants, key=lambda variant: counts[variant])
        mapping.update(
            {variant: canonical for variant in variants if variant != canonical}
        )

    if not mapping:
        return 0

    to_fix = employer.isin(mapping)
    changed = int(to_fix.sum())
    df.loc[to_fix, 'contributor_employer'] = employer[to_fix].map(mapping)
    logger.info(
        "Canonicalized %d employer variants across %d groups -> %d rows updated",
        len(mapping), sum(len(group) > 1 for group in groups.values()), changed,
    )
    return changed
