"""Recover a donor's missing or unusable address fields from their own other filings."""
from __future__ import annotations

import re

import pandas as pd

# a street_1 is usable if a geocoder can place it: house number, PO box, or a
# street-type token. deliberately NARROWER than address_review._STREET_TYPES:
# this runs on POST-normalized streets (types already abbreviated) and unit
# keywords (APT/STE) don't make a street usable. never merge the two lists.
_STREET_TYPE_RE = re.compile(
    r'\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|'
    r'ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|'
    r'RUN|PT|PASS|WALK|BEND|MALL)\b'
)


def _recover_null_streets(df: pd.DataFrame) -> int:
    """Fill NULL streets (e.g. nulled emails) from other records of the same (name, city, state)."""
    null_street = df['contributor_street_1'].isna()
    if not null_street.any():
        return 0

    has_street = df['contributor_street_1'].notna()
    street_lookup = (
        df.loc[has_street]
        .groupby(['contributor_name', 'contributor_city', 'contributor_state'])
        ['contributor_street_1']
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )

    n_recovered = 0
    for idx in df[null_street].index:
        key = (
            df.at[idx, 'contributor_name'],
            df.at[idx, 'contributor_city'],
            df.at[idx, 'contributor_state'],
        )
        street = street_lookup.get(key)
        if street:
            df.at[idx, 'contributor_street_1'] = street
            n_recovered += 1

    return n_recovered


def _is_usable_street(s: pd.Series) -> pd.Series:
    """Vectorised: True where street_1 looks geocodable."""
    upper = s.fillna('').astype(str).str.upper()
    return (
        upper.str.startswith('PO BOX')
        | upper.str.match(r'^\d')
        | upper.str.contains(_STREET_TYPE_RE)
    )


def _recover_nonstreet_from_donor(df: pd.DataFrame) -> int:
    """Replace a non-usable street_1 (place-name / fragment) with the same donor's real street."""
    # individuals only; only a non-usable value is overwritten, a good street never
    usable = _is_usable_street(df['contributor_street_1'])
    target = (
        (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_street_1'].notna()
        & (df['contributor_street_1'].astype(str).str.strip() != '')
        & ~usable
    )
    if not target.any():
        return 0

    # lookup uses ONLY usable streets - never recover one fragment with another
    clean = df.loc[usable & df['contributor_street_1'].notna()]
    if clean.empty:
        return 0
    # primary key: name + city + state, so the street matches the row's place
    by_place = (
        clean.groupby(['contributor_name', 'contributor_city', 'contributor_state'])
        ['contributor_street_1']
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )
    # fallback name + state only when the donor has exactly ONE clean street in
    # the whole state (no city ambiguity, so a garbled city on the fragment row
    # is safe to look past); two distinct streets -> skip, can't choose
    state_streets: dict[tuple, set] = {}
    for (name, _city, state), street in by_place.items():
        state_streets.setdefault((name, state), set()).add(street)
    by_state_single = {key: next(iter(streets)) for key, streets in state_streets.items() if len(streets) == 1}

    n_recovered = 0
    for idx in df[target].index:
        name = df.at[idx, 'contributor_name']
        state = df.at[idx, 'contributor_state']
        street = (
            by_place.get((name, df.at[idx, 'contributor_city'], state))
            or by_state_single.get((name, state))
        )
        if street:
            df.at[idx, 'contributor_street_1'] = street
            n_recovered += 1

    return n_recovered


def _recover_house_number_from_donor(df: pd.DataFrame) -> int:
    """Backfill a missing house number from the same donor's numbered filing of the same street."""
    # a typed-but-unnumbered street ("FAIRWAY DR") passes _is_usable_street yet
    # only geocodes to a centroid. conservative: the number-stripped street must
    # match EXACTLY, key is name+state, only the dominant numbered form is used.
    streets = df['contributor_street_1'].fillna('').astype(str).str.upper().str.strip()
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    has_type = streets.str.contains(_STREET_TYPE_RE)
    starts_num = streets.str.match(r'^\d')
    no_number = is_indiv & has_type & ~starts_num & ~streets.str.startswith('PO BOX') & (streets != '')
    if not no_number.any():
        return 0

    numbered = df[is_indiv & starts_num]
    if numbered.empty:
        return 0

    def _strip_num(x: str) -> str:
        return re.sub(r'^\d+\s+', '', str(x).upper().strip())

    numbered_streets = numbered[['contributor_name', 'contributor_state', 'contributor_street_1']].copy()
    numbered_streets['_stripped'] = numbered_streets['contributor_street_1'].map(_strip_num)
    key_full = (
        numbered_streets.groupby(['contributor_name', 'contributor_state', '_stripped'])
        ['contributor_street_1'].agg(lambda values: values.value_counts().index[0]).to_dict()
    )

    n_filled = 0
    for idx in df[no_number].index:
        full = key_full.get(
            (df.at[idx, 'contributor_name'], df.at[idx, 'contributor_state'], streets.at[idx])
        )
        if full and str(full).upper().strip() != streets.at[idx]:
            df.at[idx, 'contributor_street_1'] = full
            n_filled += 1
    return n_filled


def _recover_address_from_same_street(df: pd.DataFrame) -> dict:
    """Fill blanks and fix minority city/state/ZIP typos across a person's filings from the same street."""
    # the street is the physical anchor: same donor + same exact street = same
    # home, so those rows must agree. a genuine MOVE is a DIFFERENT street and
    # its own group, so it is never forced onto the old address.
    name = df['contributor_name'].fillna('')
    eligible_base = (df['entity_type'] == 'INDIVIDUAL') & (name != '')

    def _unify(col: str, key: pd.Series, eligible: pd.Series) -> int:
        """Within each key group, fill blanks / fix minority values in col to the dominant non-empty value."""
        vals = df[col].fillna('')
        non_empty = eligible & (vals != '')
        counts = (
            pd.DataFrame({'k': key[non_empty], 'v': vals[non_empty]})
            .groupby(['k', 'v']).size().rename('cnt').reset_index()
        )
        if counts.empty:
            return 0
        dominant = counts.loc[counts.groupby('k')['cnt'].idxmax()].set_index('k')
        dominant_value = key.map(dominant['v'])
        dominant_count = key.map(dominant['cnt'])
        row_count = pd.Series(
            pd.DataFrame({'k': key, 'v': vals})
              .merge(counts, on=['k', 'v'], how='left')['cnt']
              .fillna(0).to_numpy(),
            index=df.index,
        )
        # only when the dominant strictly outnumbers the row's value (ties left alone)
        fix = (
            eligible
            & dominant_value.notna()
            & (vals != dominant_value)
            & ((vals == '') | (row_count < dominant_count))
        )
        n_fixed = int(fix.sum())
        if n_fixed:
            df.loc[fix, col] = dominant_value[fix]
        return n_fixed

    out = {'zip': 0, 'city': 0, 'state': 0}

    # pass 1: anchor on the exact street (the physical home)
    street = df['contributor_street_1'].fillna('')
    eligible_street = eligible_base & (street != '')
    if eligible_street.any():
        street_key = name.str.cat(street, sep='\x00')
        out['zip']   = _unify('contributor_zip', street_key, eligible_street)
        out['city']  = _unify('contributor_city', street_key, eligible_street)
        out['state'] = _unify('contributor_state', street_key, eligible_street)

    # pass 2: city by (name, ZIP). an appended apartment number splits the
    # street group, so a truncated city on the unit row ("NEW" for "NEW YORK")
    # is recovered from the donor's dominant city at that same ZIP instead.
    zips = df['contributor_zip'].fillna('')
    eligible_zip = eligible_base & (zips != '')
    if eligible_zip.any():
        zip_key = name.str.cat(zips, sep='\x00')
        out['city'] += _unify('contributor_city', zip_key, eligible_zip)

    return out
