"""Foreign addresses are kept exactly as filed."""
import re

import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.reports.trail import ADDRESS_FIELDS
from fec.config.constants import AMBIGUOUS_CITIES, FOREIGN_CITIES_NO_US_STATE

_COUNTRIES = (
    'ISRAEL', 'ENGLAND', 'UNITED KINGDOM', 'UK', 'SCOTLAND', 'IRELAND', 'CANADA',
    'FRANCE', 'GERMANY', 'SWITZERLAND', 'AUSTRIA', 'ITALY', 'SPAIN', 'PORTUGAL',
    'NETHERLANDS', 'BELGIUM', 'SWEDEN', 'NORWAY', 'DENMARK', 'FINLAND', 'POLAND',
    'SERBIA', 'HUNGARY', 'GREECE', 'TURKEY', 'RUSSIA', 'UKRAINE', 'JAPAN', 'CHINA',
    'HONG KONG', 'SINGAPORE', 'INDIA', 'AUSTRALIA', 'NEW ZEALAND', 'BRAZIL',
    'ARGENTINA', 'MEXICO', 'COLOMBIA', 'CHILE', 'PERU', 'SOUTH AFRICA', 'EGYPT',
    'MOROCCO', 'UAE', 'UNITED ARAB EMIRATES', 'DUBAI', 'TAHITI', 'FRENCH POLYNESIA',
)
# A country as the last word(s), not inside street names.
_COUNTRY_TAIL_RE = re.compile(
    r'(?:^|,\s*|\s)(?:' + '|'.join(re.escape(c) for c in _COUNTRIES) + r')\.?$'
)


# Foreign localities without US namesakes, missing from the shared list.
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
# Foreign cities that also name US places, flagged elsewhere only.
_MORE_AMBIGUOUS_CITIES = {
    'MADRID': frozenset({'AL', 'CO', 'IA', 'ME', 'NE', 'NM', 'NY'}),
}

# Hand-reviewed single filings abroad that look like US places.
REVIEWED_FOREIGN_SUB_IDS: dict[str, str] = {
    # TORONTO / OH / 19273: a Toronto, Ontario employer.
    '4062420241962026749': 'Toronto, Ontario (employer MEDCAN, Toronto)',
}


# Self-contradicting US filings, kept as filed and not geocoded.
REVIEWED_AS_FILED_SUB_IDS: dict[str, str] = {
    # A Scottsdale street filed with Highland Park, IL and ZIP.
    '4060420241953437845': 'street_and_city_zip_name_two_states_kept_as_filed',
}


# True for rows kept as filed.
def kept_as_filed_mask(df: pd.DataFrame) -> pd.Series:
    """foreign_address_mask plus REVIEWED_AS_FILED_SUB_IDS."""
    mask = foreign_address_mask(df)
    if 'sub_id' in df.columns:
        mask |= _norm(df['sub_id']).isin(REVIEWED_AS_FILED_SUB_IDS)
    return mask


# the audit reason for putting a filed address back
def kept_as_filed_reason(df: pd.DataFrame) -> pd.Series:
    reasons = df['sub_id'].astype(str).str.strip().map(REVIEWED_AS_FILED_SUB_IDS)
    return reasons.fillna('foreign_address_kept_as_filed')


# true for rows whose filed address is outside the US
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


# snapshot foreign rows' raw address fields before cleaning runs
def snapshot_foreign_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """Raw address fields of rows kept as filed, by sub_id."""
    mask = kept_as_filed_mask(df)
    if not mask.any():
        return pd.DataFrame(columns=list(ADDRESS_FIELDS))
    return (
        df.loc[mask, ['sub_id', *ADDRESS_FIELDS]]
        .drop_duplicates('sub_id')
        .set_index('sub_id')
    )


# put the filed address back on every snapshotted row
def restore_foreign_addresses(df: pd.DataFrame, snapshot: pd.DataFrame) -> int:
    """Restore filed addresses on snapshotted rows; returns cells restored."""
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
    return n_restored
