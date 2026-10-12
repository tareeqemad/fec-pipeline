"""Per-donor street, unit and PO Box spellings."""
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.addresses.fixes.unify import unit_core
from fec.donor_match.components import UnionFind

# letter/digit runs for tokenizing street addresses
_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# PO box, number captured: "P.O. BOX #45"
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")


# house number plus sorted remaining tokens
def _addr_fingerprint(street: str) -> str:
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


# rewrite donor spelling variants to top-ranked form
def _collapse_donor_variants(df: pd.DataFrame, column: str, bucket_of, rank) -> int:
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0
    changed = 0
    for idx in df[ind].groupby("donor_key").groups.values():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            value = df.at[i, column]
            if not (isinstance(value, str) and value.strip()):
                continue
            key = bucket_of(df, i, value)
            if key is not None:
                buckets[key].append(i)
        for rows in buckets.values():
            forms = [df.at[i, column].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            # sorted first so ties are deterministic
            canon = max(sorted(set(forms)), key=lambda form: rank(form, forms))
            for i, form in zip(rows, forms):
                if form != canon:
                    df.at[i, column] = canon
                    changed += 1
    return changed


# row ZIP text, or "" if missing
def _zip_text(df: pd.DataFrame, i) -> str:
    z = df.at[i, "contributor_zip"]
    return z if isinstance(z, str) else ""


# bucket by ZIP and street fingerprint
def _street_bucket(df: pd.DataFrame, i, street: str):
    fingerprint = _addr_fingerprint(street)
    return (_zip_text(df, i), fingerprint) if fingerprint else None


# bucket by street_1, ZIP and unit
def _unit_bucket(df: pd.DataFrame, i, unit: str):
    core = unit_core(unit)
    if not core:
        return None
    street = df.at[i, "contributor_street_1"]
    return (street.strip() if isinstance(street, str) else "", _zip_text(df, i), core)


# collapse donor street variants to most common
def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    return _collapse_donor_variants(
        df, "contributor_street_1", _street_bucket, lambda form, forms: forms.count(form),
    )


# collapse donor unit spellings to dominant form
def canonicalize_donor_units(df: pd.DataFrame) -> int:
    return _collapse_donor_variants(
        df, "contributor_street_2", _unit_bucket,
        lambda form, forms: (forms.count(form), len(form)),
    )


# PO box number of street, else empty
def _pobox_num(street: str) -> str:
    m = _POBOX_RE.search(street.upper())
    return m.group(1) if m else ""


# true if one extra character was inserted
def _is_insertion_typo(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) != 1:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    for ch in long:
        if i < len(short) and ch == short[i]:
            i += 1
    return i == len(short)


# group donor PO box rows by ZIP
def _pobox_rows_by_zip(df: pd.DataFrame, indexes) -> dict[str, list]:
    by_zip = defaultdict(list)
    for index in indexes:
        street = df.at[index, "contributor_street_1"]
        box = _pobox_num(street) if isinstance(street, str) else ""
        if not box:
            continue
        zip_code = df.at[index, "contributor_zip"]
        zip_code = zip_code if isinstance(zip_code, str) else ""
        by_zip[zip_code].append((index, street.strip(), box))
    return by_zip


# cluster box numbers linked by insertion typos
def _pobox_clusters(numbers: list[str]) -> list[list[str]]:
    union = UnionFind()
    for left in numbers:
        for right in numbers:
            if left < right and _is_insertion_typo(left, right):
                union.union(left, right)

    clusters = defaultdict(list)
    for number in numbers:
        clusters[union.find(number)].append(number)
    return list(clusters.values())


# rewrite a typo cluster to dominant street
def _apply_pobox_cluster(df: pd.DataFrame, rows: list, members: list[str]) -> int:
    frequencies = defaultdict(int)
    for _, _, box in rows:
        frequencies[box] += 1
    canonical_box = max(
        members,
        key=lambda box: (frequencies[box], -len(box)),
    )
    canonical_forms = [street for _, street, box in rows if box == canonical_box]
    canonical = max(set(canonical_forms), key=canonical_forms.count)

    changed = 0
    for index, street, box in rows:
        if box in members and street != canonical:
            df.at[index, "contributor_street_1"] = canonical
            changed += 1
    return changed


# fix one-digit typos in same-ZIP PO boxes
def canonicalize_donor_pobox_typos(df: pd.DataFrame) -> int:
    changed = 0
    for indexes in df.groupby("donor_key").groups.values():
        for rows in _pobox_rows_by_zip(df, indexes).values():
            numbers = sorted({box for _, _, box in rows})
            if len(numbers) < 2:
                continue
            for members in _pobox_clusters(numbers):
                if len(members) >= 2:
                    changed += _apply_pobox_cluster(df, rows, members)
    return changed
