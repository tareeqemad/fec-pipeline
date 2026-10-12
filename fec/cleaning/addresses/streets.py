"""Contributor street_1 and street_2 normalization."""

import numpy as np
import pandas as pd

from fec.cleaning.addresses.house_number_zip import (
    split_house_number_zip,
)
from fec.cleaning.addresses.street_text import (
    TRAILING_UNIT_RE,
    extract_units,
    normalize_unit,
    normalize_street,
)
from fec.config.streets import (
    FLOOR_ONLY_RE,
)


# fix and flag emails in street_1
def _replace_email_street(df: pd.DataFrame) -> None:
    s1 = df['contributor_street_1'].astype('string')
    email_mask = s1.str.contains('@', na=False, regex=False)
    df['_street_email_in_s1'] = email_mask

    s2 = df['contributor_street_2'].astype('string')
    looks_addr = s2.str.contains(r'\d', na=False) | s2.str.contains(r'PO\s*BOX', case=False, na=False)
    swap_mask = email_mask & looks_addr
    null_mask = email_mask & ~swap_mask
    df['_street_swapped_from_s2'] = swap_mask
    df['_street_nulled_email'] = null_mask

    if swap_mask.any():
        df.loc[swap_mask, 'contributor_street_1'] = df.loc[swap_mask, 'contributor_street_2']
        df.loc[swap_mask, 'contributor_street_2'] = np.nan
    if null_mask.any():
        df.loc[null_mask, 'contributor_street_1'] = np.nan


# clear street_1 that repeats donor's name
def _clear_own_name_street(df: pd.DataFrame) -> int:
    street = df['contributor_street_1'].fillna('')
    first_names = df['contributor_first_name'].fillna('').str.strip().str.upper()
    last_names = df['contributor_last_name'].fillna('').str.strip().str.upper()
    is_name = (
        ((street == first_names) & (first_names.str.len() >= 3))
        | ((street == last_names) & (last_names.str.len() >= 3))
        | (street == first_names + ' ' + last_names)
        | (street == last_names + ' ' + first_names)
    )
    if is_name.any():
        df.loc[is_name, 'contributor_street_1'] = np.nan
    return int(is_name.sum())


# run all street cleaning steps, return counts
def clean_streets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    _replace_email_street(df)

    # split while the ZIP is still raw
    n_house_zip = split_house_number_zip(df)

    before = df['contributor_street_1'].copy()
    df['contributor_street_1'] = df['contributor_street_1'].apply(normalize_street)
    n_normalized = int((before.fillna('') != df['contributor_street_1'].fillna('')).sum())

    df['contributor_street_2'] = df['contributor_street_2'].apply(normalize_unit)

    s1, s2, n_extracted, df['_street_unit_leftover'] = extract_units(
        df['contributor_street_1'], df['contributor_street_2']
    )
    df['contributor_street_1'] = s1
    df['contributor_street_2'] = s2.apply(normalize_unit)

    # strip unit words orphaned by the extraction
    df['contributor_street_1'] = df['contributor_street_1'].str.replace(
        TRAILING_UNIT_RE, '', regex=True
    )

    # floor-only street_1 names no street
    n_normalized += _move_floor_only_street(df)

    n_normalized += _clear_own_name_street(df)

    counts = {
        'streets_normalized': n_normalized,
        'units_extracted': n_extracted,
        'house_number_zip': n_house_zip,
    }
    return df, counts


# move floor-only street_1 into blank street_2
def _move_floor_only_street(df: pd.DataFrame) -> int:
    street1 = df['contributor_street_1']
    floor_only = street1.fillna('').astype(str).str.match(FLOOR_ONLY_RE)
    if not floor_only.any():
        return 0
    street2 = df['contributor_street_2']
    s2_blank = street2.isna() | (street2.fillna('').astype(str).str.strip() == '')
    move = floor_only & s2_blank
    if move.any():
        df['contributor_street_2'] = df['contributor_street_2'].astype(object)
        df.loc[move, 'contributor_street_2'] = street1[move].map(normalize_unit)
        df.loc[move, 'contributor_street_1'] = np.nan
    # recovery replaces a floor left in street_1
    return int(move.sum())
