"""Align one person's city, state and ZIP across filings from the same street."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.cities import CITY_TABLE_FIXED


# count outside rows filing candidate city at the row's ZIP
def _support_outside(key: pd.Series, cities: pd.Series, zips: pd.Series, candidate: pd.Series) -> pd.Series:
    """Per row: how many rows OUTSIDE the row's key group file the candidate city with the row's ZIP.

    The row's own group (same name + street, or same name + ZIP) never vouches
    for itself, so a value only this donor ever writes at the ZIP scores 0.
    """
    filed = (cities != "") & (zips != "")
    pairs = pd.DataFrame({"k": key[filed], "c": cities[filed], "z": zips[filed]})
    everyone = pairs.groupby(["c", "z"]).size().rename("n_all").reset_index()
    own = pairs.groupby(["k", "c", "z"]).size().rename("n_own").reset_index()
    probe = pd.DataFrame({"k": key.to_numpy(), "c": candidate.to_numpy(), "z": zips.to_numpy()})
    probe = probe.merge(everyone, on=["c", "z"], how="left").merge(own, on=["k", "c", "z"], how="left")
    support = probe["n_all"].fillna(0) - probe["n_own"].fillna(0)
    return pd.Series(support.to_numpy(dtype=int), index=key.index)


# strip the house number, leaving just the street name
def _street_name(street: pd.Series) -> pd.Series:
    """Street without its house number: '620 MADISON AVE' -> 'MADISON AVE'."""
    return street.str.replace(r"^\d+[A-Z]?\s+", "", regex=True)


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


# fill blanks and fix minority address fields from same-street filings
def _recover_address_from_same_street(df: pd.DataFrame) -> dict:
    """Fill blanks and fix minority city/state/ZIP typos across a person's filings from the same street."""
    # the street is the physical anchor: same donor + same exact street = same
    # home, so those rows must agree. a genuine MOVE is a DIFFERENT street and
    # its own group, so it is never forced onto the old address.
    name = df["contributor_name"].fillna("")
    eligible_base = (df["entity_type"] == "INDIVIDUAL") & (name != "")
    out = {"zip": 0, "city": 0, "state": 0}

    # pass 1: anchor on the exact street (the physical home)
    street = df["contributor_street_1"].fillna("")
    eligible_street = eligible_base & (street != "")
    if eligible_street.any():
        _align_same_street(df, name.str.cat(street, sep="\x00"), street, eligible_street, out)

    # pass 2: city by (name, ZIP). an appended apartment number splits the
    # street group, so a truncated city on the unit row ("NEW" for "NEW YORK")
    # is recovered from the donor's dominant city at that same ZIP instead.
    zips = df["contributor_zip"].fillna("")
    eligible_zip = eligible_base & (zips != "")
    if eligible_zip.any():
        zip_key = name.str.cat(zips, sep="\x00")
        out["city"] += _unify_to_dominant(
            df, "contributor_city", zip_key, eligible_zip, _foreign_city(df, zip_key, eligible_zip)
        )

    return out


# align ZIP, city and state across one person's same-street filings
def _align_same_street(df, street_key, street, eligible, out) -> None:
    """Align ZIP, city and state across one person's same-street filings."""
    # a row naming another city at a ZIP where the group's city is never
    # filed, on a street other people file at that ZIP, is a second real
    # address sharing the street text (620 MADISON AVE NEW YORK 10022 vs
    # WEST HEMPSTEAD 11552): its ZIP stays. Without that street evidence the
    # row is a mixed-up filing (home street + office ZIP) and is aligned.
    zips = _column_text(df, "contributor_zip")
    streets = _street_name(street.astype(str))
    street_known_at_zip = _support_outside(street_key, streets, zips, streets) > 0
    keep_zip = _foreign_city(df, street_key, eligible) & street_known_at_zip
    zip_before = zips.copy()
    out["zip"] = _unify_to_dominant(df, "contributor_zip", street_key, eligible, keep_zip)
    # a row that just took the group's ZIP joined the group's address, so
    # its city follows; any other row keeps a city the group's dominant city
    # would contradict at its ZIP (MANHATTAN BEACH 90266, never LOS ANGELES)
    zip_moved = _column_text(df, "contributor_zip") != zip_before
    keep_city = _foreign_city(df, street_key, eligible) & ~zip_moved
    out["city"] = _unify_to_dominant(df, "contributor_city", street_key, eligible, keep_city)
    out["state"] = _unify_to_dominant(df, "contributor_state", street_key, eligible, keep_city)
    out["city"] += _adopt_attested_city(df, street_key, eligible)


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


# replace an unattested cut-off/guessed city with the donor's attested one
def _adopt_attested_city(df: pd.DataFrame, key: pd.Series, eligible: pd.Series) -> int:
    """Replace a cut-off or table-guessed city nobody else files with the row's ZIP by the one city the same home files with that ZIP that others do file.

    'SANTA' 87501 next to the donor's own 'SANTA FE' 87501 at the same street
    (a 2-2 tie the majority vote leaves alone), or raw 'LOS ANGELS' 90266, which
    the typo table turned into LOS ANGELES, next to the donor's own 'MANHATTAN
    BEACH' 90266. Only the same ZIP counts, so a different home (a move) is
    never touched, and a real place name the filer chose (ELBERON, LAKE SUCCESS)
    is not a guess and stays as filed.
    """
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
