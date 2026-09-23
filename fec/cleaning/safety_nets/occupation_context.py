"""Specialise a vague occupation word from the filing's own context.

`DEVELOPER` on its own is either a real-estate developer or a software
developer, so no blanket rule can be right. Each filing is decided on its own:

1. the employer NAME, when it carries an unmistakable industry word
   (REALTY / HOMES / ... -> real estate, SOFTWARE / TECHNOLOGIES / ... -> tech);
2. otherwise the employer's OTHER donors: when at least 3 different people at
   that employer file occupations and 60% of them fall in one of the two
   industries, that industry decides;
3. otherwise the word stays `DEVELOPER` exactly as filed.

The occupation TEXT is what gets specialised (`REAL ESTATE DEVELOPER` /
`SOFTWARE DEVELOPER`), so the category still follows from the text alone and
the category-consistency quality gate stays deterministic.
"""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.occupations import _categorize_final
from fec.config.constants import SKIP_EMPLOYERS

VAGUE_WORD = 'DEVELOPER'
SPECIFIC_TEXT = {'REAL ESTATE': 'REAL ESTATE DEVELOPER', 'TECHNOLOGY': 'SOFTWARE DEVELOPER'}

# Only words that name the industry outright. Anything that also names another
# industry (CAPITAL, EQUITIES, LABS, NETWORKS, ...) is deliberately left out.
_REAL_ESTATE_EMPLOYER_RE = re.compile(
    r'\b(REAL ESTATE|REALTY|REALTORS?|PROPERT(?:Y|IES)|HOMES?|HOUSING|HOMEBUILDERS?|'
    r'BUILDERS?|DEVELOPMENT|APARTMENTS?|RESIDENTIAL|COMMUNITIES|ESTATES|LAND|'
    r'CONSTRUCTION)\b'
)
_TECHNOLOGY_EMPLOYER_RE = re.compile(
    r'\b(SOFTWARE|TECH|TECHNOLOG(?:Y|IES)|DIGITAL|CLOUD|CYBER(?:SECURITY)?|ANALYTICS|AI|'
    r'ROBOTICS|COMPUTING|GOOGLE|MICROSOFT|AMAZON|APPLE|META|ORACLE|NVIDIA|SALESFORCE|'
    r'ADOBE|IBM|NETFLIX|UBER|STRIPE|PALANTIR)\b'
    r'|\.(?:COM|IO|AI)\b'
)
_MIN_PEOPLE = 3      # distinct colleagues needed before their industry is trusted
_MIN_SHARE = 0.6     # ...and that industry must cover 60% of ALL colleagues' filings


def _employer_signal(employer: str) -> str | None:
    """Industry named by the employer itself, or None when the name is silent or says both."""
    e = (employer or '').upper()
    if not e or e in SKIP_EMPLOYERS:
        return None
    real_estate = bool(_REAL_ESTATE_EMPLOYER_RE.search(e))
    technology = bool(_TECHNOLOGY_EMPLOYER_RE.search(e))
    if real_estate != technology:
        return 'REAL ESTATE' if real_estate else 'TECHNOLOGY'
    return None


def _peer_signal(df: pd.DataFrame, vague: pd.Series) -> dict[str, str]:
    """employer -> industry that a clear majority of its OTHER donors (distinct people) report."""
    peers = df.loc[
        ~vague
        & (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_employer'].notna()
        & ~df['contributor_employer'].isin(SKIP_EMPLOYERS)
        & df['occupation_category'].fillna('').ne(''),
        ['contributor_employer', 'contributor_name', 'occupation_category'],
    ].drop_duplicates()
    if peers.empty:
        return {}
    people = peers.groupby('contributor_employer')['contributor_name'].nunique()
    by_industry = peers.groupby(['contributor_employer', 'occupation_category'])['contributor_name'].nunique()
    signal: dict[str, str] = {}
    for (employer, category), n in by_industry.items():
        if category in SPECIFIC_TEXT and n >= _MIN_PEOPLE and n / people[employer] >= _MIN_SHARE:
            signal[employer] = category
    return signal


def _disambiguate_vague_occupation(df: pd.DataFrame) -> int:
    """AS-2. Bare DEVELOPER -> REAL ESTATE DEVELOPER or SOFTWARE DEVELOPER, decided per filing by its employer."""
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()
    vague = (df['entity_type'] == 'INDIVIDUAL') & (occ == VAGUE_WORD)
    if not vague.any():
        return 0
    peer = _peer_signal(df, vague)
    n_fixed = 0
    for idx in df.index[vague]:
        employer = df.at[idx, 'contributor_employer']
        industry = _employer_signal(employer) or peer.get(employer)
        if industry is None:
            continue
        new_text = SPECIFIC_TEXT[industry]
        df.at[idx, 'contributor_occupation'] = new_text
        df.at[idx, 'occupation_category'] = _categorize_final(pd.Series([new_text])).iloc[0]
        n_fixed += 1
    return n_fixed
