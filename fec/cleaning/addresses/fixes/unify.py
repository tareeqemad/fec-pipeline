"""Per-donor unification of street spellings and street_2 unit designators."""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

# A house number split by a space: '10 17 GREENTREE'.
_SPLIT_HOUSE_NUMBER_RE = r'^\d+ \d+\b'


def _collapse_to_dominant(df: pd.DataFrame, col: str, grp: pd.Series,
                          eligible: pd.Series, prefer_longest: bool = False) -> int:
    """Rewrite each group's minority spellings to the dominant one."""
    vals = df[col].fillna('')
    work = pd.DataFrame({'g': grp[eligible], 's': vals[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    # only groups with >1 distinct spelling need fixing
    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    # Prefer '1017 GREENTREE DR' over '10 17 GREENTREE DR'.
    counts['split_number'] = counts['s'].str.match(_SPLIT_HOUSE_NUMBER_RE)
    # Winner: most rows, then longest; prefer_longest favours the fuller spelling.
    order = ['g', 'split_number'] + (['len', 'n'] if prefer_longest else ['n', 'len'])
    counts = counts.sort_values(order, ascending=[True, True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canonical = grp.map(winner)

    fix = eligible & canonical.notna() & (vals != canonical)
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = canonical[fix]
    return n_fixed


def _unify_street_variants(df: pd.DataFrame, fingerprint, prefer_longest: bool = False) -> int:
    """Collapse equal-fingerprint spellings of one donor's street."""
    # Scope spelling changes by name, city and state.
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


def _unify_street_spellings(df: pd.DataFrame) -> int:
    """Merge word-order / lost-space street variants (fingerprint: sorted alphanumeric tokens)."""
    return _unify_street_variants(
        df, lambda street: ' '.join(sorted(re.findall(r'[A-Z0-9]+', street.upper()))))


def _unify_street_spacing(df: pd.DataFrame) -> int:
    """Merge spacing/punctuation-only variants (fingerprint: non-alphanumerics dropped, order kept)."""
    # Compare letters and digits in their filed order.
    return _unify_street_variants(
        df, lambda street: re.sub(r'[^A-Z0-9]', '', street.upper()))


# Unit designators: APT, UNIT and #.
_UNIT_DESIGNATOR_RE = re.compile(
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM)\b\.?',
    re.IGNORECASE,
)
# A floor or building is not a unit (FL 3).
_FLOOR_RE = re.compile(r'\b(?:FL|FLR|FLOOR)\b\.?', re.IGNORECASE)
# a building designator word: "BLDG 2", "BUILDING C"
_BUILDING_RE = re.compile(r'\b(?:BLDG|BUILDING)\b\.?', re.IGNORECASE)


def unit_core(s: str) -> str:
    """Bare unit id; a floor or building keeps its kind."""
    # Each designator starts an id; inner punctuation is ignored.
    text = _UNIT_DESIGNATOR_RE.sub('|', str(s).upper())
    text = _FLOOR_RE.sub('|FLOOR|', _BUILDING_RE.sub('|BUILDING|', text))
    ids = (re.sub(r'[^A-Z0-9]', '', part) for part in text.split('|'))
    return ' '.join(part for part in ids if part)


def _unify_unit_designators(df: pd.DataFrame) -> int:
    """Collapse one donor's same unit written with different designators."""
    # Unify units only within one donor's street, city, state, ZIP.
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
    # Keep unit designators that have no number.
    eligible = eligible & (core != '')
    if not eligible.any():
        return 0

    SEP = '\x00'
    group_key = name.str.cat([st1, city, state, zips, core], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_2', group_key, eligible)


_TYPE_TOKENS = {'ST', 'AVE', 'RD', 'DR', 'BLVD', 'LN', 'CT', 'PL', 'CIR', 'TER', 'PKWY', 'HWY', 'SQ', 'WAY', 'TRL', 'PLZ', 'LOOP'}


def _unify_street_types(df: pd.DataFrame) -> int:
    """Give "103 STANTON" the type of "103 STANTON AVE"."""
    # Fill missing street types only when the donor's spellings agree.
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


# a run of letters/digits, to tokenize a street address
_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def _addr_fingerprint(street: str) -> str:
    """Anchor the house number and sort the remaining address tokens."""
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def _collapse_donor_variants(df: pd.DataFrame, column: str, bucket_of, rank) -> int:
    """Within each donor, replace variants with the bucket's top form."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0
    changed = 0
    for idx in df[ind].groupby("donor_key").groups.values():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            value = df.at[i, column]
            if not (isinstance(value, str) and value.strip()):
                continue
            key = bucket_of(df, i, value)
            if key is not None:
                buckets[key].append(i)
        for rows in buckets.values():
            forms = [df.at[i, column].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            # Sorted first, so ties pick the same form every run.
            canon = max(sorted(set(forms)), key=lambda form: rank(form, forms))
            for i, form in zip(rows, forms):
                if form != canon:
                    df.at[i, column] = canon
                    changed += 1
    return changed


def _zip_text(df: pd.DataFrame, i) -> str:
    z = df.at[i, "contributor_zip"]
    return z if isinstance(z, str) else ""


def _street_bucket(df: pd.DataFrame, i, street: str):
    fingerprint = _addr_fingerprint(street)
    return (_zip_text(df, i), fingerprint) if fingerprint else None


def _unit_bucket(df: pd.DataFrame, i, unit: str):
    core = unit_core(unit)
    if not core:
        return None
    street = df.at[i, "contributor_street_1"]
    return (street.strip() if isinstance(street, str) else "", _zip_text(df, i), core)


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Unify street spellings with the same ZIP and anchored tokens."""
    return _collapse_donor_variants(
        df, "contributor_street_1", _street_bucket, lambda form, forms: forms.count(form),
    )


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Unify unit spellings at the same street and ZIP."""
    return _collapse_donor_variants(
        df, "contributor_street_2", _unit_bucket,
        lambda form, forms: (forms.count(form), len(form)),
    )
