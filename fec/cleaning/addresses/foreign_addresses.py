"""Foreign addresses are kept exactly as filed."""

import re

import pandas as pd

from fec.common.settings import RULES_DIR
from fec.common.text import upper_text
from fec.vocabulary.geography import AMBIGUOUS_CITIES, FOREIGN_CITIES_NO_US_STATE
from fec.vocabulary.tables import ordered_words, read_rows, words

ADDRESS_FIELDS = tuple(f"contributor_{field}" for field in ("street_1", "street_2", "city", "state", "zip"))
# A country as the last word(s), not inside street names.
_COUNTRY_TAIL_RE = re.compile(
    r"(?:^|,\s*|\s)(?:" + "|".join(re.escape(country) for country in ordered_words("foreign_country_names")) + r")\.?$"
)
# Foreign localities without US namesakes, missing from the shared list.
_MORE_FOREIGN_CITIES = words("more_foreign_cities")
# Single filings abroad that look like US places, each reviewed by hand.
_REVIEWED_FOREIGN_SUB_IDS = {row["sub_id"] for row in read_rows(RULES_DIR / "foreign_filings.csv")}


def foreign_address_mask(df: pd.DataFrame) -> pd.Series:
    """Mark the rows whose filed address is outside the US: these are kept as filed."""
    city = upper_text(df["contributor_city"])
    state = upper_text(df["contributor_state"])
    mask = city.isin(FOREIGN_CITIES_NO_US_STATE) | city.isin(_MORE_FOREIGN_CITIES)
    for name, valid_states in AMBIGUOUS_CITIES.items():
        mask |= city.eq(name) & ~state.isin(valid_states)
    mask |= upper_text(df["sub_id"]).isin(_REVIEWED_FOREIGN_SUB_IDS)
    for column in ("contributor_city", "contributor_street_1", "contributor_street_2"):
        mask |= upper_text(df[column]).str.contains(_COUNTRY_TAIL_RE, na=False)
    return mask


def snapshot_foreign_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """Raw address fields of the foreign rows, by sub_id."""
    foreign = df.loc[foreign_address_mask(df), ["sub_id", *ADDRESS_FIELDS]]
    return foreign.drop_duplicates("sub_id").set_index("sub_id")


def restore_foreign_addresses(df: pd.DataFrame, snapshot: pd.DataFrame) -> None:
    """Put the filed address back on the snapshotted rows."""
    rows = df["sub_id"].isin(snapshot.index)
    for field in ADDRESS_FIELDS:
        filed = df.loc[rows, "sub_id"].map(snapshot[field])
        changed = df.loc[rows, field].fillna("").astype(str).ne(filed.fillna("").astype(str))
        df.loc[changed.index[changed], field] = filed[changed].to_numpy()
