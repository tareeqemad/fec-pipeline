"""Align donor city, state, ZIP per street."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.cities import CITY_TABLE_FIXED
from fec.cleaning.addresses.fixes.safe_text import CARE_OF_BLANKED


# count outsiders filing candidate at row's ZIP
def _support_outside(key: pd.Series, cities: pd.Series, zips: pd.Series, candidate: pd.Series) -> pd.Series:
    filed = (cities != "") & (zips != "")
    pairs = pd.DataFrame({"k": key[filed], "c": cities[filed], "z": zips[filed]})
    everyone = pairs.groupby(["c", "z"]).size().rename("n_all").reset_index()
    own = pairs.groupby(["k", "c", "z"]).size().rename("n_own").reset_index()
    probe = pd.DataFrame({"k": key.to_numpy(), "c": candidate.to_numpy(), "z": zips.to_numpy()})
    probe = probe.merge(everyone, on=["c", "z"], how="left").merge(own, on=["k", "c", "z"], how="left")
    support = probe["n_all"].fillna(0) - probe["n_own"].fillna(0)
    return pd.Series(support.to_numpy(dtype=int), index=key.index)


# strip the house number from a street
def _street_name(street: pd.Series) -> pd.Series:
    return street.str.replace(r"^\d+[A-Z]?\s+", "", regex=True)


# per row: dominant value, count, own count
def _dominant(vals: pd.Series, key: pd.Series, eligible: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
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


# fill or fix city/state/ZIP from same-street filings
def recover_address_from_same_street(df: pd.DataFrame) -> dict:
    # a move is a different street group
    name = df["contributor_name"].fillna("")
    eligible_base = (df["entity_type"] == "INDIVIDUAL") & (name != "")
    out = {"zip": 0, "city": 0, "state": 0}

    # pass 1: exact street is the home
    street = df["contributor_street_1"].fillna("")
    eligible_street = eligible_base & (street != "")
    if eligible_street.any():
        _align_same_street(df, name.str.cat(street, sep="\x00"), street, eligible_street, out)

    # pass 2: city by name and ZIP
    zips = df["contributor_zip"].fillna("")
    eligible_zip = eligible_base & (zips != "")
    if CARE_OF_BLANKED in df.columns:
        # care-of office filings keep their city
        eligible_zip &= ~df[CARE_OF_BLANKED].fillna(False).astype(bool)
    if eligible_zip.any():
        zip_key = name.str.cat(zips, sep="\x00")
        out["city"] += _unify_to_dominant(
            df, "contributor_city", zip_key, eligible_zip, _foreign_city(df, zip_key, eligible_zip)
        )

    return out


# align ZIP, city, state per street group
def _align_same_street(df, street_key, street, eligible, out) -> None:
    # street known at ZIP means second address
    zips = _column_text(df, "contributor_zip")
    streets = _street_name(street.astype(str))
    street_known_at_zip = _support_outside(street_key, streets, zips, streets) > 0
    keep_zip = _foreign_city(df, street_key, eligible) & street_known_at_zip
    zip_before = zips.copy()
    out["zip"] = _unify_to_dominant(df, "contributor_zip", street_key, eligible, keep_zip)
    # rows that took the ZIP follow city
    zip_moved = _column_text(df, "contributor_zip") != zip_before
    keep_city = _foreign_city(df, street_key, eligible) & ~zip_moved
    out["city"] = _unify_to_dominant(df, "contributor_city", street_key, eligible, keep_city)
    out["state"] = _unify_to_dominant(df, "contributor_state", street_key, eligible, keep_city)
    out["city"] += _adopt_attested_city(df, street_key, eligible)


# column as text, blanks for missing
def _column_text(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].fillna("").astype(str)


# set blank/minority values to dominant, skip keep
def _unify_to_dominant(df, col: str, key: pd.Series, eligible: pd.Series, keep: pd.Series | None = None) -> int:
    vals = _column_text(df, col)
    dominant_value, dominant_count, row_count = _dominant(vals, key, eligible)
    # dominant must strictly outnumber; ties stay
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


# flag off-dominant cities no outsider files there
def _foreign_city(df, key: pd.Series, eligible: pd.Series) -> pd.Series:
    cities, zips = _column_text(df, "contributor_city"), _column_text(df, "contributor_zip")
    dominant_city, _count, _row = _dominant(cities, key, eligible)
    dominant_city = dominant_city.fillna("").astype(str)
    differs = eligible & (cities != "") & (zips != "") & (dominant_city != "") & (cities != dominant_city)
    return differs & (_support_outside(key, cities, zips, dominant_city) == 0)


# replace cut-off/guessed city with attested one
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
    attested = pd.DataFrame({"k": key[filed & (support > 0)], "z": zips[filed & (support > 0)],
                             "c": cities[filed & (support > 0)]}).drop_duplicates()
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
