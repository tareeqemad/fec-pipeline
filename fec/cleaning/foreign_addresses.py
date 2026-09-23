"""Foreign addresses are kept exactly as filed.

FEC filings from abroad usually carry a fake US state (REHOVOT / CA) or a
country inside the city (JERUSALEM, ISRAEL / NY). Every address repair in the
pipeline assumes a US address, so on these rows it can only do damage: blank
the city, "align" the street to a US address of the same donor, or geocode
the row to a bogus US coordinate. Policy (2026-09-22): detect these rows on
the raw filing, let the pipeline run, then put the raw street/city/state/zip
back and never geocode them.
"""
import re

import numpy as np
import pandas as pd

from fec.config.constants import AMBIGUOUS_CITIES, FOREIGN_CITIES_NO_US_STATE

ADDRESS_FIELDS = (
    'contributor_street_1', 'contributor_street_2',
    'contributor_city', 'contributor_state', 'contributor_zip',
)
GEO_FIELDS = ('latitude', 'longitude', 'geocode_level')

_COUNTRIES = (
    'ISRAEL', 'ENGLAND', 'UNITED KINGDOM', 'UK', 'SCOTLAND', 'IRELAND', 'CANADA',
    'FRANCE', 'GERMANY', 'SWITZERLAND', 'AUSTRIA', 'ITALY', 'SPAIN', 'PORTUGAL',
    'NETHERLANDS', 'BELGIUM', 'SWEDEN', 'NORWAY', 'DENMARK', 'FINLAND', 'POLAND',
    'SERBIA', 'HUNGARY', 'GREECE', 'TURKEY', 'RUSSIA', 'UKRAINE', 'JAPAN', 'CHINA',
    'HONG KONG', 'SINGAPORE', 'INDIA', 'AUSTRALIA', 'NEW ZEALAND', 'BRAZIL',
    'ARGENTINA', 'MEXICO', 'COLOMBIA', 'CHILE', 'PERU', 'SOUTH AFRICA', 'EGYPT',
    'MOROCCO', 'UAE', 'UNITED ARAB EMIRATES', 'DUBAI', 'TAHITI', 'FRENCH POLYNESIA',
)
# a country name as the LAST word(s) of a city or street ("JERUSALEM, ISRAEL",
# "5 HAGIVAA ST ISRAEL"); a country word inside a street name (GULF OF MEXICO DR,
# 112 INDIA ST, FRANCE AVE) is a US street and never matches
_COUNTRY_TAIL_RE = re.compile(
    r'(?:^|,\s*|\s)(?:' + '|'.join(re.escape(c) for c in _COUNTRIES) + r')\.?$'
)


def _norm(series: pd.Series) -> pd.Series:
    return series.fillna('').astype(str).str.strip().str.upper()


def foreign_address_mask(df: pd.DataFrame) -> pd.Series:
    """True for rows whose filed address is outside the US."""
    city = _norm(df['contributor_city'])
    state = _norm(df['contributor_state'])
    street_1 = _norm(df['contributor_street_1'])
    street_2 = _norm(df['contributor_street_2'])

    mask = city.isin(FOREIGN_CITIES_NO_US_STATE)
    for name, valid_states in AMBIGUOUS_CITIES.items():
        mask |= city.eq(name) & ~state.isin(valid_states)
    mask |= city.str.contains(_COUNTRY_TAIL_RE, na=False)
    mask |= street_1.str.contains(_COUNTRY_TAIL_RE, na=False)
    mask |= street_2.str.contains(_COUNTRY_TAIL_RE, na=False)
    return mask


def snapshot_foreign_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """Raw address fields of every foreign row, indexed by sub_id (taken BEFORE cleaning)."""
    mask = foreign_address_mask(df)
    if not mask.any():
        return pd.DataFrame(columns=list(ADDRESS_FIELDS))
    return (
        df.loc[mask, ['sub_id', *ADDRESS_FIELDS]]
        .drop_duplicates('sub_id')
        .set_index('sub_id')
    )


def restore_foreign_addresses(df: pd.DataFrame, snapshot: pd.DataFrame) -> int:
    """Put the filed address back on every snapshotted row and drop its coordinates; returns cells restored."""
    if snapshot.empty or 'sub_id' not in df.columns:
        return 0
    hit = df['sub_id'].isin(snapshot.index)
    if not hit.any():
        return 0
    rows = df.index[hit]
    keys = df.loc[rows, 'sub_id']
    n_restored = 0
    for field in ADDRESS_FIELDS:
        filed = keys.map(snapshot[field]).to_numpy()
        current = df.loc[rows, field].fillna('').astype(str).to_numpy()
        filed_str = pd.Series(filed).fillna('').astype(str).to_numpy()
        changed = current != filed_str
        if changed.any():
            df.loc[rows[changed], field] = filed[changed]
            n_restored += int(changed.sum())
    for field in GEO_FIELDS:
        if field in df.columns:
            df.loc[rows, field] = np.nan
    return n_restored
