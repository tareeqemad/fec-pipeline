"""Align one person's city, state and ZIP across filings from the same street."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.cities import CITY_TABLE_FIXED
from fec.cleaning.addresses.fixes.safe_text import CARE_OF_BLANKED


# Count supporting city/ZIP filings outside this donor group.
def _support_outside(key: pd.Series, cities: pd.Series, zips: pd.Series, candidate: pd.Series) -> pd.Series:
    filed = (cities != "") & (zips != "")
    pairs = pd.DataFrame({"k": key[filed], "c": cities[filed], "z": zips[filed]})
    everyone = pairs.groupby(["c", "z"]).size().rename("n_all").reset_index()
    own = pairs.groupby(["k", "c", "z"]).size().rename("n_own").reset_index()
    probe = pd.DataFrame({"k": key.to_numpy(), "c": candidate.to_numpy(), "z": zips.to_numpy()})
    probe = probe.merge(everyone, on=["c", "z"], how="left").merge(own, on=["k", "c", "z"], how="left")
    support = probe["n_all"].fillna(0) - probe["n_own"].fillna(0)
    return pd.Series(support.to_numpy(dtype=int), index=key.index)


# find each group's dominant value, its count and the row's
def _dominant(vals: pd.Series, key: pd.Series, eligible: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Per row: the group's dominant non-empty value, its count, and the count of the row's own value."""
    non_empty = eligible & (vals != "")
    counts = (
        pd.DataFrame({"k": key[non_empty], "v": vals[non_empty]})
        .groupby(["k", "v"])
        .size()
        .rename("cnt")
        .reset_index()
    )
    if counts.empty:
        empty = pd.Series(pd.NA, index=vals.index, dtype=object)
        return empty, empty, pd.Series(0, index=vals.index)
    dominant = counts.loc[counts.groupby("k")["cnt"].idxmax()].set_index("k")
    row_count = pd.Series(
        pd.DataFrame({"k": key, "v": vals})
        .merge(counts, on=["k", "v"], how="left")["cnt"]
        .fillna(0)
        .to_numpy(),
        index=vals.index,
    )
    return key.map(dominant["v"]), key.map(dominant["cnt"]), row_count


# fill ZIP blanks and align supported city/state variants at the same street
def _recover_address_from_same_street(df: pd.DataFrame) -> dict:
    """Fill unambiguous ZIP blanks and align supported city/state variants."""
    # Street text alone does not prove two filings name the same location.
    # Conflicting ZIPs remain filed until a verified correction resolves them.
    name = df["contributor_name"].fillna("")
    eligible_base = (df["entity_type"] == "INDIVIDUAL") & (name != "")
    out = {"zip": 0, "city": 0, "state": 0}

    # pass 1: anchor on the exact street (the physical home)
    street = df["contributor_street_1"].fillna("")
    eligible_street = eligible_base & (street != "")
    if eligible_street.any():
        _align_same_street(df, name.str.cat(street, sep="\x00"), eligible_street, out)

    # pass 2: city by (name, ZIP). an appended apartment number splits the
    # street group, so a truncated city on the unit row ("NEW" for "NEW YORK")
    # is recovered from the donor's dominant city at that same ZIP instead.
    zips = df["contributor_zip"].fillna("")
    eligible_zip = eligible_base & (zips != "")
    if CARE_OF_BLANKED in df.columns:
        # a filing that named an office elsewhere keeps its city
        eligible_zip &= ~df[CARE_OF_BLANKED].fillna(False).astype(bool)
    if eligible_zip.any():
        zip_key = name.str.cat(zips, sep="\x00")
        out["city"] += _unify_to_dominant(
            df, "contributor_city", zip_key, eligible_zip, _foreign_city(df, zip_key, eligible_zip)
        )

    return out


# align ZIP, city and state across one person's same-street filings
def _align_same_street(df, street_key, eligible, out) -> None:
    """Align ZIP, city and state across one person's same-street filings."""
    out["zip"] = _fill_missing_place_field(
        df, street_key, eligible, "contributor_zip", ("contributor_city", "contributor_state")
    )
    # A filled ZIP does not authorize replacing the filing's chosen city.
    keep_city = _foreign_city(df, street_key, eligible)
    out["city"] = _unify_to_dominant(df, "contributor_city", street_key, eligible, keep_city)
    out["state"] = _fill_missing_place_field(
        df, street_key, eligible, "contributor_state", ("contributor_city", "contributor_zip")
    )
    out["city"] += _adopt_attested_city(df, street_key, eligible)


def _fill_missing_place_field(df, street_key, eligible, column, context_columns) -> int:
    """Fill only blanks, requiring one value at the same street and full place context."""
    values = _column_text(df, column)
    complete_place = eligible.copy()
    key = street_key
    for context_column in context_columns:
        context = _column_text(df, context_column)
        complete_place &= context.ne("")
        key = key.str.cat(context, sep="\x00")
    known = complete_place & values.ne("")
    if column == "contributor_zip":
        known &= values.str.fullmatch(r"\d{5}") & values.ne("00000")
    choices = pd.DataFrame({"key": key[known], "value": values[known]}).drop_duplicates()
    choices = choices.loc[~choices["key"].duplicated(keep=False)].set_index("key")["value"]
    candidate = key.map(choices)
    fill = complete_place & values.eq("") & candidate.notna()
    df.loc[fill, column] = candidate[fill]
    return int(fill.sum())


# read a column as text with blanks for missing values
def _column_text(df: pd.DataFrame, col: str) -> pd.Series:
    """A column as text, blanks for missing values."""
    return df[col].fillna("").astype(str)


# fill blanks/minority column values to the group's dominant value
def _unify_to_dominant(df, col: str, key: pd.Series, eligible: pd.Series, keep: pd.Series | None = None) -> int:
    """Within each key group, fill blanks / fix minority values in col to the dominant non-empty value; rows in keep are never overwritten."""
    vals = _column_text(df, col)
    dominant_value, dominant_count, row_count = _dominant(vals, key, eligible)
    # only when the dominant strictly outnumbers the row's value (ties left alone)
    fix = (
        eligible
        & dominant_value.notna()
        & (vals != dominant_value)
        & ((vals == "") | (row_count < dominant_count))
    )
    if keep is not None:
        fix &= ~keep
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = dominant_value[fix]
    return n_fixed


# flag rows whose city no outsider files at that ZIP
def _foreign_city(df, key: pd.Series, eligible: pd.Series) -> pd.Series:
    """Rows whose filed city must not become the group's dominant city: nobody outside the group files that city with the row's ZIP."""
    cities, zips = _column_text(df, "contributor_city"), _column_text(df, "contributor_zip")
    dominant_city, _count, _row = _dominant(cities, key, eligible)
    dominant_city = dominant_city.fillna("").astype(str)
    differs = eligible & (cities != "") & (zips != "") & (dominant_city != "") & (cities != dominant_city)
    return differs & (_support_outside(key, cities, zips, dominant_city) == 0)


# Replace truncated or guessed cities only with one supported city at the same street and ZIP.
def _adopt_attested_city(df: pd.DataFrame, key: pd.Series, eligible: pd.Series) -> int:
    cities = df["contributor_city"].fillna("").astype(str)
    zips = df["contributor_zip"].fillna("").astype(str)
    filed = eligible & (cities != "") & (zips != "")
    if not filed.any():
        return 0
    support = _support_outside(key, cities, zips, cities)
    unattested = filed & (support == 0)
    if not unattested.any():
        return 0
    guessed = (
        df[CITY_TABLE_FIXED].fillna(False).astype(bool)
        if CITY_TABLE_FIXED in df.columns
        else pd.Series(False, index=df.index)
    )
    supported = filed & (support > 0)
    attested = pd.DataFrame({
        "k": key[supported], "z": zips[supported], "c": cities[supported],
    }).drop_duplicates()
    if attested.empty:
        return 0
    choices = attested.groupby(["k", "z"])["c"].agg(lambda values: values.iloc[0] if len(values) == 1 else None)
    choices = choices.dropna()
    probe = pd.Series(list(zip(key[unattested], zips[unattested])), index=cities.index[unattested])
    adopted = probe.map(lambda pair: choices.get(pair))
    current = cities[unattested]
    cut_off = pd.Series(
        [isinstance(new, str) and new != old and new.startswith(old) for old, new in zip(current, adopted)],
        index=current.index,
    )
    adopted = adopted[adopted.notna() & (adopted != current) & (cut_off | guessed[unattested])]
    if adopted.empty:
        return 0
    df.loc[adopted.index, "contributor_city"] = adopted
    return int(len(adopted))
