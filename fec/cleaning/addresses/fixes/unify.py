"""Unify each donor's street spellings and units."""
from __future__ import annotations

import re

import pandas as pd

# a house number split by a space
_SPLIT_HOUSE_NUMBER_RE = r'^\d+ \d+\b'


# rewrite minority spellings to the group's dominant
def _collapse_to_dominant(df: pd.DataFrame, col: str, grp: pd.Series,
                          eligible: pd.Series, prefer_longest: bool = False) -> int:
    vals = df[col].fillna('')
    work = pd.DataFrame({'g': grp[eligible], 's': vals[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    # a split house number always loses
    counts['split_number'] = counts['s'].str.match(_SPLIT_HOUSE_NUMBER_RE)
    # most rows wins; prefer_longest favors fuller form
    order = ['g', 'split_number'] + (['len', 'n'] if prefer_longest else ['n', 'len'])
    counts = counts.sort_values(order, ascending=[True, True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canonical = grp.map(winner)

    fix = eligible & canonical.notna() & (vals != canonical)
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = canonical[fix]
    return n_fixed


# collapse a donor's fingerprint-equal street spellings
def _unify_street_variants(df: pd.DataFrame, fingerprint, prefer_longest: bool = False) -> int:
    # per donor and place; other donors untouched
    street = df['contributor_street_1'].fillna('')
    name   = df['contributor_name'].fillna('')
    city   = df['contributor_city'].fillna('')
    state  = df['contributor_state'].fillna('')
    eligible = (name != '') & (street != '')
    if not eligible.any():
        return 0

    fingerprints = street.map(fingerprint)
    SEP = '\x00'
    group_key = name.str.cat([city, state, fingerprints], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_1', group_key, eligible, prefer_longest)


# merge word-order or lost-space street spelling variants
def unify_street_spellings(df: pd.DataFrame) -> int:
    return _unify_street_variants(
        df, lambda street: ' '.join(sorted(re.findall(r'[A-Z0-9]+', street.upper()))))


# merge spacing/punctuation-only street variants
def unify_street_spacing(df: pd.DataFrame) -> int:
    # same characters in order is same address
    return _unify_street_variants(
        df, lambda street: re.sub(r'[^A-Z0-9]', '', street.upper()))


# designators dropped so same unit collapses
_UNIT_DESIGNATOR_RE = re.compile(
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM)\b\.?',
    re.IGNORECASE,
)
# a floor is not a unit
_FLOOR_RE = re.compile(r'\b(?:FL|FLR|FLOOR)\b\.?', re.IGNORECASE)
# a building designator word
_BUILDING_RE = re.compile(r'\b(?:BLDG|BUILDING)\b\.?', re.IGNORECASE)


# bare unit id; floor and building kept
def unit_core(s: str) -> str:
    # each designator starts a new id
    text = _UNIT_DESIGNATOR_RE.sub('|', str(s).upper())
    text = _FLOOR_RE.sub('|FLOOR|', _BUILDING_RE.sub('|BUILDING|', text))
    ids = (re.sub(r'[^A-Z0-9]', '', part) for part in text.split('|'))
    return ' '.join(part for part in ids if part)


# merge a donor's same unit across designators
def unify_unit_designators(df: pd.DataFrame) -> int:
    # per donor address; neighbours never touched
    st2   = df['contributor_street_2'].fillna('')
    name  = df['contributor_name'].fillna('')
    st1   = df['contributor_street_1'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')
    zips  = df['contributor_zip'].fillna('')
    eligible = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (name != '') & (st2 != '') & (st1 != '')
    )
    if not eligible.any():
        return 0

    core = st2.map(unit_core)
    # designator without number: can't prove same unit
    eligible = eligible & (core != '')
    if not eligible.any():
        return 0

    SEP = '\x00'
    group_key = name.str.cat([st1, city, state, zips, core], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_2', group_key, eligible)


_TYPE_TOKENS = {'ST', 'AVE', 'RD', 'DR', 'BLVD', 'LN', 'CT', 'PL', 'CIR', 'TER', 'PKWY', 'HWY', 'SQ', 'WAY', 'TRL', 'PLZ', 'LOOP'}


# add missing street type from fuller spelling
def unify_street_types(df: pd.DataFrame) -> int:
    # conflicting typed spellings each stay as filed
    street = df['contributor_street_1'].fillna('')
    name = df['contributor_name'].fillna('')
    words = street.str.upper().str.findall(r'[A-Z0-9]+')
    bare = words.map(lambda tokens: ' '.join(t for t in tokens if t not in _TYPE_TOKENS))
    types = words.map(lambda tokens: ' '.join(t for t in tokens if t in _TYPE_TOKENS))
    group_key = name.str.cat([df['contributor_city'].fillna(''), df['contributor_state'].fillna(''), bare],
                             sep='\x00')
    typed = types != ''
    kinds = types[typed].groupby(group_key[typed]).nunique()
    conflict = group_key.isin(kinds[kinds > 1].index)
    eligible = (name != '') & (street != '') & ~conflict
    if not eligible.any():
        return 0
    return _collapse_to_dominant(df, 'contributor_street_1', group_key, eligible, prefer_longest=True)
