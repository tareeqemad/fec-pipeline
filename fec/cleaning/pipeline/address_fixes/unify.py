"""Per-donor unification of street spellings and street_2 unit designators."""
from __future__ import annotations

import re

import pandas as pd


def _collapse_to_dominant(df: pd.DataFrame, col: str, grp: pd.Series,
                          eligible: pd.Series) -> int:
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

    # winner per group: most rows, then longest string (keeps the fuller form)
    counts = counts.sort_values(['g', 'n', 'len'], ascending=[True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canonical = grp.map(winner)

    fix = eligible & canonical.notna() & (vals != canonical)
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = canonical[fix]
    return n_fixed


def _unify_street_variants(df: pd.DataFrame, fingerprint) -> int:
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
    return _collapse_to_dominant(df, 'contributor_street_1', group_key, eligible)


def _unify_street_spellings(df: pd.DataFrame) -> int:
    """Merge word-order / lost-space street variants (fingerprint: sorted alphanumeric tokens)."""
    return _unify_street_variants(
        df, lambda street: ' '.join(sorted(re.findall(r'[A-Z0-9]+', street.upper()))))


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
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM|FL|FLOOR|BLDG|BUILDING)\b\.?',
    re.IGNORECASE,
)


def _unit_core(s: str) -> str:
    """Bare unit id of a street_2, designator words and punctuation removed."""
    return re.sub(r'[^A-Z0-9]', '', _UNIT_DESIGNATOR_RE.sub(' ', str(s).upper()))


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
