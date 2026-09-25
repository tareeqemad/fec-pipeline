"""Per-donor unification of street spellings and street_2 unit designators."""
from __future__ import annotations

import re

import pandas as pd

# '10 17 GREENTREE DR': a house number cut in two by a space
_SPLIT_HOUSE_NUMBER_RE = r'^\d+ \d+\b'


# rewrite each group's minority spellings to the dominant one
def _collapse_to_dominant(df: pd.DataFrame, col: str, grp: pd.Series,
                          eligible: pd.Series, prefer_longest: bool = False) -> int:
    """Rewrite each group's minority spellings of col to the dominant one; returns rows rewritten."""
    vals = df[col].fillna('')
    work = pd.DataFrame({'g': grp[eligible], 's': vals[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    # only groups with >1 distinct spelling need fixing
    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    # a house number never holds a space: '10 17 GREENTREE DR' loses to the same
    # donor's '1017 GREENTREE DR' whatever the counts ('100 1ST ST' is not split)
    counts['split_number'] = counts['s'].str.match(_SPLIT_HOUSE_NUMBER_RE)
    # winner per group: most rows, then longest string (keeps the fuller form);
    # prefer_longest flips that so the fuller spelling wins even as a minority
    order = ['g', 'split_number'] + (['len', 'n'] if prefer_longest else ['n', 'len'])
    counts = counts.sort_values(order, ascending=[True, True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canonical = grp.map(winner)

    fix = eligible & canonical.notna() & (vals != canonical)
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = canonical[fix]
    return n_fixed


# collapse one donor's fingerprint-equal street spellings to one form
def _unify_street_variants(df: pd.DataFrame, fingerprint, prefer_longest: bool = False) -> int:
    """Collapse fingerprint-equal spellings of one donor's street to the dominant form; returns rows rewritten."""
    # scoped to ONE donor (name + city + state): only spellings the same person
    # used at the same place merge, so fingerprint collisions across donors
    # (genuine distinct addresses) are left alone
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
def _unify_street_spellings(df: pd.DataFrame) -> int:
    """Merge word-order / lost-space street variants (fingerprint: sorted alphanumeric tokens)."""
    return _unify_street_variants(
        df, lambda street: ' '.join(sorted(re.findall(r'[A-Z0-9]+', street.upper()))))


# merge spacing/punctuation-only street variants
def _unify_street_spacing(df: pd.DataFrame) -> int:
    """Merge spacing/punctuation-only variants (fingerprint: non-alphanumerics dropped, order kept)."""
    # identical letters+digits in the same order is provably the same address,
    # never a move; runs right after _unify_street_spellings to catch what it leaves
    return _unify_street_variants(
        df, lambda street: re.sub(r'[^A-Z0-9]', '', street.upper()))


# interchangeable secondary-unit designators: reducing "APT 1503" / "UNIT 1503" /
# "# 1503" to the bare id lets one donor's same unit collapse to one address
# (the addresses dimension keys on the full street_1 + street_2)
_UNIT_DESIGNATOR_RE = re.compile(
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM)\b\.?',
    re.IGNORECASE,
)
# a floor or a building is other information than a unit: "FL 3" is not "APT 3"
_FLOOR_RE = re.compile(r'\b(?:FL|FLR|FLOOR)\b\.?', re.IGNORECASE)
# a building designator word: "BLDG 2", "BUILDING C"
_BUILDING_RE = re.compile(r'\b(?:BLDG|BUILDING)\b\.?', re.IGNORECASE)


# extract the bare unit id from a street_2 value
def _unit_core(s: str) -> str:
    """Bare unit id of a street_2, unit words and punctuation removed; a floor or building keeps its kind."""
    # every designator starts a new id; inside one id spaces and punctuation
    # go ("705 N" = "705N", "15-03" = "1503"), while two ids stay apart
    # ("BLDG 1 STE 23" is not "BLDG 12 STE 3")
    text = _UNIT_DESIGNATOR_RE.sub('|', str(s).upper())
    text = _FLOOR_RE.sub('|FLOOR|', _BUILDING_RE.sub('|BUILDING|', text))
    ids = (re.sub(r'[^A-Z0-9]', '', part) for part in text.split('|'))
    return ' '.join(part for part in ids if part)


# collapse one donor's same unit written with different designators
def _unify_unit_designators(df: pd.DataFrame) -> int:
    """Collapse one donor's same unit written with different designators; returns rows rewritten."""
    # scoped to ONE donor at ONE (street_1, city, state, zip): only street_2
    # values reducing to the SAME bare unit id merge, to the donor's dominant
    # raw form (most rows, then longest). a different unit keeps a different
    # core, and other people in the same building are never touched.
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

    core = st2.map(_unit_core)
    # a bare designator with no number reduces to '' and is left alone
    # (can't prove it's the same unit)
    eligible = eligible & (core != '')
    if not eligible.any():
        return 0

    SEP = '\x00'
    group_key = name.str.cat([st1, city, state, zips, core], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_2', group_key, eligible)


_TYPE_TOKENS = {'ST', 'AVE', 'RD', 'DR', 'BLVD', 'LN', 'CT', 'PL', 'CIR', 'TER', 'PKWY', 'HWY', 'SQ', 'WAY', 'TRL', 'PLZ', 'LOOP'}


# fill a missing street type from the donor's fuller spelling
def _unify_street_types(df: pd.DataFrame) -> int:
    """Give one donor's "103 STANTON" the type of their "103 STANTON AVE"; returns rows rewritten."""
    # a person does not live at both 103 Stanton and 103 Stanton Ave; the fuller
    # spelling (the one carrying the type) wins even when filed less often.
    # Only a missing type is filled: when the donor's typed spellings disagree
    # ("8044 MONTGOMERY RD" / "8044 MONTGOMERY AVE"), nothing proves which is
    # right, so each filing keeps its own
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
