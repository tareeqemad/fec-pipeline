"""Specialise vague DEVELOPER from filing context."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.occupations.normalize import categorize_final
from fec.config.constants import SKIP_EMPLOYERS

VAGUE_WORD = 'DEVELOPER'
SPECIFIC_TEXT = {'REAL ESTATE': 'REAL ESTATE DEVELOPER', 'TECHNOLOGY': 'SOFTWARE DEVELOPER'}

# only words naming the industry outright
_REAL_ESTATE_EMPLOYER_RE = re.compile(
    r'\b(REAL ESTATE|REALTY|REALTORS?|PROPERT(?:Y|IES)|HOMES?|HOUSING|HOMEBUILDERS?|'
    r'BUILDERS?|DEVELOPMENT|APARTMENTS?|RESIDENTIAL|COMMUNITIES|ESTATES|LAND|'
    r'CONSTRUCTION)\b'
)
# tech words or companies like "GOOGLE"
_TECHNOLOGY_EMPLOYER_RE = re.compile(
    r'\b(SOFTWARE|TECH|TECHNOLOG(?:Y|IES)|DIGITAL|CLOUD|CYBER(?:SECURITY)?|ANALYTICS|AI|'
    r'ROBOTICS|COMPUTING|GOOGLE|MICROSOFT|AMAZON|APPLE|META|ORACLE|NVIDIA|SALESFORCE|'
    r'ADOBE|IBM|NETFLIX|UBER|STRIPE|PALANTIR)\b'
    r'|\.(?:COM|IO|AI)\b'
)
_MIN_PEOPLE = 3      # distinct colleagues needed to trust industry
_MIN_SHARE = 0.6     # share of colleagues in that industry


# industry the employer name says, else None
def _employer_signal(employer: str) -> str | None:
    e = (employer or '').upper()
    if not e or e in SKIP_EMPLOYERS:
        return None
    real_estate = bool(_REAL_ESTATE_EMPLOYER_RE.search(e))
    technology = bool(_TECHNOLOGY_EMPLOYER_RE.search(e))
    if real_estate != technology:
        return 'REAL ESTATE' if real_estate else 'TECHNOLOGY'
    return None


# employer to its colleagues' majority industry
def _peer_signal(df: pd.DataFrame, vague: pd.Series) -> dict[str, str]:
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


# specialize bare DEVELOPER from employer context
def disambiguate_vague_occupation(df: pd.DataFrame) -> int:
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
        df.at[idx, 'occupation_category'] = categorize_final(pd.Series([new_text])).iloc[0]
        n_fixed += 1
    return n_fixed
