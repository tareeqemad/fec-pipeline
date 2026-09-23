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


def employer_group_words(name: str) -> tuple[str, ...]:
    """The strict group key with its word breaks kept: 'TWIN CITY FAN, LTD.' -> ('TWIN', 'CITY', 'FAN')."""
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
    key = re.sub(r'^\s*THE\s+', '', key).replace('&', ' AND ')
    return tuple(key.split())


def _employer_group_key(name: str) -> str:
    """Build a strict key for employer spelling variants (spacing ignored)."""
    return ''.join(employer_group_words(name))


def employer_filers(employers: pd.Series, filers: pd.Series) -> dict[str, frozenset]:
    """Employer name -> the set of filer names who wrote it (the evidence base of prefer_attested_spacing)."""
    frame = pd.DataFrame({'employer': employers, 'filer': filers}).dropna()
    frame = frame[(frame['employer'].astype(str).str.strip() != '')
                  & (frame['filer'].astype(str).str.strip() != '')]
    if frame.empty:
        return {}
    frame['filer'] = frame['filer'].astype(str).str.strip().str.upper()
    return {name: frozenset(group) for name, group in frame.groupby('employer')['filer']}


def spacing_index(names) -> dict[str, list]:
    """First word -> [(words, name)] over every employer name, for prefix lookups."""
    index = defaultdict(list)
    for name in names:
        words = employer_group_words(name)
        if words:
            index[words[0]].append((words, name))
    return index


def prefer_attested_spacing(winner: str, variants, counts, filers: dict, index: dict) -> str:
    """Among spacing variants of one name (TWINCITY FAN / TWIN CITY FAN) keep the spacing the group's own filers use in a longer name of the same company.

    Evidence is a longer employer name that starts with exactly those words and was
    filed by one of the people who filed the group (BARRY files TWINCITY FAN, TWIN CITY FAN
    and TWIN CITY FAN COMPANIES LTD). The frequency winner stays unless exactly one
    spacing has such evidence, so a spacing that only an unrelated company shares
    (BLACK ROCK COFFEE next to BLACKROCK) can never flip a group.
    """
    words = {variant: employer_group_words(variant) for variant in variants}
    spacings = set(words.values())
    if len(spacings) < 2 or not filers:
        return winner
    # only plain letter/digit word breaks; a break at '/' or '-' ('BANK OF
    # AMERICA/ MERRILL') is punctuation style, which other rules own
    if not all(word.isalnum() for spacing in spacings for word in spacing):
        return winner
    group_filers = frozenset().union(*(filers.get(variant, frozenset()) for variant in variants))
    if not group_filers:
        return winner
    members = set(variants)
    attested = set()
    for spacing in spacings:
        size = len(spacing)
        for other_words, other in index.get(spacing[0], ()):
            if (other not in members and len(other_words) > size
                    and other_words[:size] == spacing
                    and filers.get(other, frozenset()) & group_filers):
                attested.add(spacing)
                break
    if len(attested) != 1:
        return winner
    spacing = next(iter(attested))
    if words.get(winner) == spacing:
        return winner
    return max(
        (variant for variant in variants if words[variant] == spacing),
        key=lambda variant: (counts.get(variant, 0), variant),
    )


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

    # spacing evidence (who filed which name) is only built when some group
    # actually mixes word breaks; frames without filer names skip it
    spaced = any(
        len({employer_group_words(variant) for variant in variants}) > 1
        for variants in groups.values() if len(variants) > 1
    )
    filers, index = {}, {}
    if spaced and 'contributor_name' in df.columns:
        filers = employer_filers(active, df.loc[active.index, 'contributor_name'])
        index = spacing_index(counts.index)

    mapping = {}
    for variants in groups.values():
        if len(variants) < 2:
            continue
        canonical = max(variants, key=lambda variant: counts[variant])
        canonical = prefer_attested_spacing(canonical, variants, counts, filers, index)
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
