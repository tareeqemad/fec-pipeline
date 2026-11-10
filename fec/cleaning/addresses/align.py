"""One donor, one place, one spelling.

After the standard form, a donor's filings can still write the same place
in several ways. Each step here takes the filings that agree on a key
(the donor and where they are) and gives the fewer ones the spelling the
donor files most. Different real addresses are never merged: the key
always holds what makes the place the same.
"""

from __future__ import annotations

import re
from functools import cache

import numpy as np
import pandas as pd
import zipcodes
from scourgify.address_constants import LONGHAND_STREET_TYPES

from fec.config.tables import words

_TYPE_TOKENS = words("type_tokens")
# Unit designators: APT, UNIT and #. A floor or building is not a unit (FL 3).
_UNIT_DESIGNATOR_RE = re.compile(r"#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM)\b\.?")
_FLOOR_RE = re.compile(r"\b(?:FL|FLR|FLOOR)\b\.?")
_BUILDING_RE = re.compile(r"\b(?:BLDG|BUILDING)\b\.?")


def _text(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column].fillna("").astype(str)


def _key(df: pd.DataFrame, *parts) -> pd.Series:
    """Return the filings that belong together: columns by name, or values already worked out."""
    series = [_text(df, part) if isinstance(part, str) else part for part in parts]
    return series[0].str.cat(series[1:], sep="\x00")


def _most_filed(values: pd.Series, key: pd.Series, *, longest: bool = False) -> pd.DataFrame:
    """Per key: the value filed most (or the longest), how often, and how many values there are."""
    counts = pd.DataFrame({"key": key, "value": values}).groupby(["key", "value"]).size().rename("n").reset_index()
    counts["length"] = counts["value"].str.len()
    # 1017 GREENTREE DR over 10 17 GREENTREE DR
    counts["split_number"] = counts["value"].str.match(r"^\d+ \d+\b")
    order = ["key", "split_number", *(("length", "n") if longest else ("n", "length"))]
    counts = counts.sort_values(order, ascending=[True, True, False, False])
    best = counts.drop_duplicates("key").set_index("key")
    best["spellings"] = counts.groupby("key").size()
    best["runner_up"] = counts.groupby("key")["n"].apply(lambda n: sorted(n)[-2] if len(n) > 1 else 0)
    return best


def unify(df: pd.DataFrame, column: str, key: pd.Series, eligible: pd.Series, *, longest: bool = False) -> None:
    """Filings sharing a key take the spelling the donor files most."""
    values = _text(df, column)
    best = _most_filed(values[eligible], key[eligible], longest=longest)
    winner = key.map(best["value"].where(best["spellings"] > 1))
    change = eligible & winner.notna() & values.ne(winner)
    df.loc[change, column] = winner[change]


@cache
def _zip_place(zipcode: str) -> tuple[str, frozenset]:
    """Return the state of a ZIP and the cities USPS names for it."""
    found = zipcodes.matching(zipcode) if len(zipcode) == 5 and zipcode.isdigit() else []
    cities = {name.upper() for record in found for name in (record["city"], *record.get("acceptable_cities", []))}
    return (found[0]["state"] if found else ""), frozenset(cities)


def unify_state(df: pd.DataFrame) -> None:
    """One person's city under two states: the state filed less is a slip."""
    eligible = (
        df["entity_type"].eq("INDIVIDUAL") & _text(df, "contributor_name").ne("") & _text(df, "contributor_city").ne("")
    )
    unify(
        df,
        "contributor_state",
        _key(df, "contributor_name", "contributor_city"),
        eligible & _text(df, "contributor_state").ne(""),
    )


def settle_state_and_zip(df: pd.DataFrame) -> None:
    """Settle a ZIP of another state: the state is wrong when the city belongs to the ZIP, else the ZIP is."""
    zips, states, cities = _text(df, "contributor_zip"), _text(df, "contributor_state"), _text(df, "contributor_city")
    place = zips.map(_zip_place)
    zip_state = place.str[0]
    conflict = zips.ne("") & states.ne("") & zip_state.ne("") & zip_state.ne(states)
    city_fits = pd.Series([city in names for city, (_, names) in zip(cities, place)], index=df.index)
    df.loc[conflict & city_fits, "contributor_state"] = zip_state[conflict & city_fits]
    df.loc[conflict & ~city_fits, "contributor_zip"] = np.nan


def unify_street_types(df: pd.DataFrame) -> None:
    """103 STANTON takes the type of the same donor's 103 STANTON AVE."""
    street, name = _text(df, "contributor_street_1"), _text(df, "contributor_name")
    # 5 YANKEE HL and 5 YANKEE HILL RD are one street: a rare type is read in full
    tokens = street.str.findall(r"[A-Z0-9]+").map(
        lambda found: [t if t in _TYPE_TOKENS else LONGHAND_STREET_TYPES.get(t, t) for t in found]
    )
    bare = tokens.map(lambda found: " ".join(t for t in found if t not in _TYPE_TOKENS))
    types = tokens.map(lambda found: " ".join(t for t in found if t in _TYPE_TOKENS))
    key = _key(df, name, "contributor_city", "contributor_state", bare)
    # two different types at one bare street are two streets
    kinds = types[types.ne("")].groupby(key[types.ne("")]).nunique()
    unify(
        df, "contributor_street_1", key, name.ne("") & street.ne("") & ~key.isin(kinds[kinds > 1].index), longest=True
    )


def unify_street_spacing(df: pd.DataFrame) -> None:
    """Spacing and punctuation only: 1 MAINST and 1 MAIN ST."""
    street, name = _text(df, "contributor_street_1"), _text(df, "contributor_name")
    key = _key(df, name, "contributor_city", "contributor_state", street.str.replace(r"[^A-Z0-9]", "", regex=True))
    unify(df, "contributor_street_1", key, name.ne("") & street.ne(""))


def unit_core(unit: str) -> str:
    """Return the bare id of a unit; a floor or a building keeps its kind."""
    text = _UNIT_DESIGNATOR_RE.sub("|", str(unit).upper())
    text = _FLOOR_RE.sub("|FLOOR|", _BUILDING_RE.sub("|BUILDING|", text))
    return " ".join(part for part in (re.sub(r"[^A-Z0-9]", "", piece) for piece in text.split("|")) if part)


def unify_units(df: pd.DataFrame, donor: str = "contributor_name") -> None:
    """APT 4, UNIT 4 and # 4 at one street are one unit."""
    unit, core = _text(df, "contributor_street_2"), _text(df, "contributor_street_2").map(unit_core)
    eligible = (
        df["entity_type"].eq("INDIVIDUAL")
        & _text(df, donor).ne("")
        & _text(df, "contributor_street_1").ne("")
        & core.ne("")
    )
    place = (
        ("contributor_street_1", "contributor_zip")
        if donor == "donor_key"
        else ("contributor_street_1", "contributor_city", "contributor_state", "contributor_zip")
    )
    unify(df, "contributor_street_2", _key(df, donor, *place, core), eligible & unit.ne(""))


def align_place(df: pd.DataFrame) -> None:
    """At one street of one donor a blank ZIP or state is filled; the city is the one the donor files most."""
    name, street = _text(df, "contributor_name"), _text(df, "contributor_street_1")
    people = df["entity_type"].eq("INDIVIDUAL") & name.ne("")
    home = _key(df, name, street)
    _fill_blank(df, "contributor_zip", _key(df, home, "contributor_city", "contributor_state"), people & street.ne(""))
    _city_most_filed(df, home, people & street.ne(""))
    _fill_blank(df, "contributor_state", _key(df, home, "contributor_city", "contributor_zip"), people & street.ne(""))
    # the same donor in the same ZIP: a unit row with the city cut short takes the full one
    _city_most_filed(df, _key(df, name, "contributor_zip"), people & _text(df, "contributor_zip").ne(""))


def _city_most_filed(df: pd.DataFrame, key: pd.Series, eligible: pd.Series) -> None:
    """Blank and less filed cities take the most filed one, when it strictly outnumbers them."""
    cities, zips = _text(df, "contributor_city"), _text(df, "contributor_zip")
    named = eligible & cities.ne("")
    counts = pd.DataFrame({"key": key[named], "city": cities[named]}).value_counts()
    best = _most_filed(cities[named], key[named])
    city, count = key.map(best["value"]), key.map(best["n"])
    filed = pd.Series([counts.get(pair, 0) for pair in zip(key, cities)], index=df.index)
    fewer = eligible & city.notna() & cities.ne(city) & (cities.eq("") | (filed < count))
    # a city nobody else files with that ZIP is no better than the one filed: it stays
    pairs = pd.DataFrame({"key": key, "city": cities, "zip": zips})[cities.ne("") & zips.ne("")]
    everyone, own = pairs.groupby(["city", "zip"]).size(), pairs.groupby(["key", "city", "zip"]).size()
    others = pd.Series(
        [everyone.get((c, z), 0) - own.get((k, c, z), 0) for k, c, z in zip(key, city.fillna(""), zips)], index=df.index
    )
    keep = cities.ne("") & zips.ne("") & others.eq(0)
    df.loc[fewer & ~keep, "contributor_city"] = city[fewer & ~keep]


def _fill_blank(df: pd.DataFrame, column: str, key: pd.Series, eligible: pd.Series) -> None:
    """Fill a blank with the one value the same donor files at the same place."""
    values = _text(df, column)
    known = eligible & values.ne("") & ~key.str.contains("\x00\x00|\x00$", regex=True)
    if column == "contributor_zip":
        known &= values.str.fullmatch(r"\d{5}") & values.ne("00000")
    choices = pd.DataFrame({"key": key[known], "value": values[known]}).drop_duplicates()
    choices = choices[~choices["key"].duplicated(keep=False)].set_index("key")["value"]
    fill = eligible & values.eq("") & key.map(choices).notna() & ~key.str.contains("\x00\x00|\x00$", regex=True)
    df.loc[fill, column] = key.map(choices)[fill]


# In order.
ALIGN_STEPS = (unify_state, settle_state_and_zip, unify_street_types, unify_street_spacing, align_place, unify_units)
