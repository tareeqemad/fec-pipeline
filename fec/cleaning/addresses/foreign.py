"""Foreign addresses are kept exactly as filed."""
import re

import pandas as pd

from fec.cleaning._helpers import upper_text
from fec.cleaning.audit_trail import ADDRESS_FIELDS
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
# country as the last word only
_COUNTRY_TAIL_RE = re.compile(
    r'(?:^|,\s*|\s)(?:' + '|'.join(re.escape(c) for c in _COUNTRIES) + r')\.?$'
)


# no US town shares these names
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
# foreign only outside these US states
_MORE_AMBIGUOUS_CITIES = {
    'MADRID': frozenset({'AL', 'CO', 'IA', 'ME', 'NE', 'NM', 'NY'}),
}

# hand-reviewed filings abroad at US-named places
REVIEWED_FOREIGN_SUB_IDS: dict[str, str] = {
    '4062420241962026749': 'Toronto, Ontario (employer MEDCAN, Toronto)',
}


# hand-reviewed self-contradicting US filings, kept as filed
REVIEWED_AS_FILED_SUB_IDS: dict[str, str] = {
    '4060420241953437845': 'street_and_city_zip_name_two_states_kept_as_filed',
}


# true for rows kept as filed
def kept_as_filed_mask(df: pd.DataFrame) -> pd.Series:
    mask = foreign_address_mask(df)
    if 'sub_id' in df.columns:
        mask |= upper_text(df['sub_id']).isin(REVIEWED_AS_FILED_SUB_IDS)
    return mask


# audit reason for restoring a filed address
def kept_as_filed_reason(df: pd.DataFrame) -> pd.Series:
    reasons = df['sub_id'].astype(str).str.strip().map(REVIEWED_AS_FILED_SUB_IDS)
    return reasons.fillna('foreign_address_kept_as_filed')


# true where filed address is outside US
def foreign_address_mask(df: pd.DataFrame) -> pd.Series:
    city = upper_text(df['contributor_city'])
    state = upper_text(df['contributor_state'])
    street_1 = upper_text(df['contributor_street_1'])
    street_2 = upper_text(df['contributor_street_2'])

    mask = city.isin(FOREIGN_CITIES_NO_US_STATE) | city.isin(_MORE_FOREIGN_CITIES)
    ambiguous = {**AMBIGUOUS_CITIES, **_MORE_AMBIGUOUS_CITIES}
    for name, valid_states in ambiguous.items():
        mask |= city.eq(name) & ~state.isin(valid_states)
    if 'sub_id' in df.columns:
        mask |= upper_text(df['sub_id']).isin(REVIEWED_FOREIGN_SUB_IDS)
    mask |= city.str.contains(_COUNTRY_TAIL_RE, na=False)
    mask |= street_1.str.contains(_COUNTRY_TAIL_RE, na=False)
    mask |= street_2.str.contains(_COUNTRY_TAIL_RE, na=False)
    return mask


# snapshot kept rows' raw address before cleaning
def snapshot_foreign_addresses(df: pd.DataFrame) -> pd.DataFrame:
    mask = kept_as_filed_mask(df)
    if not mask.any():
        return pd.DataFrame(columns=list(ADDRESS_FIELDS))
    return (
        df.loc[mask, ['sub_id', *ADDRESS_FIELDS]]
        .drop_duplicates('sub_id')
        .set_index('sub_id')
    )


# restore filed address on snapshotted rows
def restore_foreign_addresses(df: pd.DataFrame, snapshot: pd.DataFrame) -> int:
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
