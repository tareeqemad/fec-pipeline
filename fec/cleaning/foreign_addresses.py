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


# Localities abroad with no US place of the same name that the shared list in
# fec/config/constants.py does not carry yet. Israel's code IL reads as Illinois,
# so an Israeli filing (EFRAT / IL / 90435) looks like a US one to every other
# check. Only names no US town shares belong here.
_MORE_FOREIGN_CITIES = frozenset({
    'EFRAT', 'BEIT SHEMESH', 'BET SHEMESH', 'RAMAT BEIT SHEMESH',
    'MAALE ADUMIM', "MA'ALE ADUMIM", 'MEVASERET ZION', 'ALON SHVUT',
    'NEVE DANIEL', 'GUSH ETZION', 'KARNEI SHOMRON', 'GINOT SHOMRON',
    'ASHDOD', 'BNEI BRAK', 'HOLON', 'BAT YAM', 'HOD HASHARON',
    'RAMAT HASHARON', 'GIVAT SHMUEL', 'ZICHRON YAAKOV', 'ZIKHRON YAAKOV',
    'CAESAREA', 'KOCHAV YAIR', 'TZUR YIGAL', 'SHOHAM', 'NES ZIONA',
    'YAVNE', 'KIRYAT ONO', 'ROSH HAAYIN', 'PETACH TIKVA', 'BEERSHEBA',
    'BEER SHEBA', "BE'ER SHEVA", 'TZFAT', 'SAFED', 'KARMIEL', 'NAHARIYA',
    'EILAT', 'TIBERIAS', 'HERZLIYA PITUACH', 'HERTZLIYA', "RA'ANANA",
    "MODI'IN", 'KFAR SAVA', 'TEL AVIV-YAFO', 'TEL AVIV YAFO', 'TEL-AVIV',
})
# Foreign cities that also name a US place, flagged only outside those states
# (MADRID / IL / 28012 is Calle Doctor Fourquet in Madrid, Spain).
_MORE_AMBIGUOUS_CITIES = {
    'MADRID': frozenset({'AL', 'CO', 'IA', 'ME', 'NE', 'NM', 'NY'}),
}

# Single filings reviewed by hand: abroad, although city and state name a real US
# place, so no rule can tell. A ZIP from another state alone proves only a ZIP
# typo (VANCOUVER / WA with a Portland ZIP), so each needs evidence of its own.
REVIEWED_FOREIGN_SUB_IDS: dict[str, str] = {
    # TORONTO / OH / 19273: 19273 is a Pennsylvania prefix, not Toronto OH's 43964,
    # and the filer's employer MEDCAN HEALTH MANAGEMENT is a Toronto, Ontario company
    '4062420241962026749': 'Toronto, Ontario (employer MEDCAN, Toronto)',
}


def _norm(series: pd.Series) -> pd.Series:
    return series.fillna('').astype(str).str.strip().str.upper()


def foreign_address_mask(df: pd.DataFrame) -> pd.Series:
    """True for rows whose filed address is outside the US."""
    city = _norm(df['contributor_city'])
    state = _norm(df['contributor_state'])
    street_1 = _norm(df['contributor_street_1'])
    street_2 = _norm(df['contributor_street_2'])

    mask = city.isin(FOREIGN_CITIES_NO_US_STATE) | city.isin(_MORE_FOREIGN_CITIES)
    ambiguous = {**AMBIGUOUS_CITIES, **_MORE_AMBIGUOUS_CITIES}
    for name, valid_states in ambiguous.items():
        mask |= city.eq(name) & ~state.isin(valid_states)
    if 'sub_id' in df.columns:
        mask |= _norm(df['sub_id']).isin(REVIEWED_FOREIGN_SUB_IDS)
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
