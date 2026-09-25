"""One street, unit and PO Box spelling per donor address."""
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.pipeline.address_fixes.unify import _unit_core
from fec.donor_match.components import UnionFind

# a run of letters/digits, to tokenize a street address
_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# a PO box with its number captured: "PO BOX 123", "P.O. BOX #45"
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")


# build an order-independent key from a street's number and tokens
def _addr_fingerprint(street: str) -> str:
    """Order-independent street key: house number anchored, remaining tokens sorted (keeps grid addresses distinct)."""
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


# rewrite each donor's variant spellings to the winning form
def _collapse_donor_variants(df: pd.DataFrame, column: str, bucket_of, rank) -> int:
    """Within each donor, bucket rows by bucket_of(df, i, value) and rewrite every
    spelling in a bucket to the one with the highest rank(form, forms); returns rows rewritten.
    """
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
            # sorted first, so a tie picks the same form on every run
            canon = max(sorted(set(forms)), key=lambda form: rank(form, forms))
            for i, form in zip(rows, forms):
                if form != canon:
                    df.at[i, column] = canon
                    changed += 1
    return changed


# the row's ZIP text, or "" when missing
def _zip_text(df: pd.DataFrame, i) -> str:
    z = df.at[i, "contributor_zip"]
    return z if isinstance(z, str) else ""


# same ZIP and same anchored street fingerprint
def _street_bucket(df: pd.DataFrame, i, street: str):
    fingerprint = _addr_fingerprint(street)
    return (_zip_text(df, i), fingerprint) if fingerprint else None


# same street_1, ZIP and unit number
def _unit_bucket(df: pd.DataFrame, i, unit: str):
    core = _unit_core(unit)
    if not core:
        return None
    street = df.at[i, "contributor_street_1"]
    return (street.strip() if isinstance(street, str) else "", _zip_text(df, i), core)


# collapse a donor's street variants to the most common form
def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Collapse per-donor street_1 variants with the same ZIP and anchored token set to the most common form; returns rows rewritten."""
    return _collapse_donor_variants(
        df, "contributor_street_1", _street_bucket, lambda form, forms: forms.count(form),
    )


# collapse a donor's unit spelling variants to the dominant form
def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Collapse per-donor street_2 spellings of the same unit (APT/UNIT/# 1503), bucketed by (street_1, ZIP, unit id), to the dominant form; returns rows rewritten."""
    return _collapse_donor_variants(
        df, "contributor_street_2", _unit_bucket,
        lambda form, forms: (forms.count(form), len(form)),
    )


# extract a PO box number from a street, else empty
def _pobox_num(street: str) -> str:
    """Extract the box number from a PO-box street, else ''."""
    m = _POBOX_RE.search(street.upper())
    return m.group(1) if m else ""


# check if one string equals the other plus one digit
def _is_insertion_typo(a: str, b: str) -> bool:
    """True iff one string is the other with exactly one extra digit inserted; same-length pairs never match."""
    if abs(len(a) - len(b)) != 1:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    for ch in long:
        if i < len(short) and ch == short[i]:
            i += 1
    return i == len(short)


# group a donor's PO box rows by ZIP code
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


# cluster PO box numbers connected by single-digit-insertion typos
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


# rewrite a PO box typo cluster to dominant street form
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


# unify one-digit-insertion typos in a donor's same-ZIP PO boxes
def canonicalize_donor_pobox_typos(df: pd.DataFrame) -> int:
    """Unify one-digit insertion typos in a donor's same-ZIP PO boxes."""
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
