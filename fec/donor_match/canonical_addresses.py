"""One street, unit and PO Box spelling per donor address."""
import re
from collections import defaultdict

import pandas as pd

from fec.donor_match.components import UnionFind

_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")


_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")


def _addr_fingerprint(street: str) -> str:
    """Order-independent street key: house number anchored, remaining tokens sorted (keeps grid addresses distinct)."""
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Collapse per-donor street_1 variants with the same ZIP and anchored token set to the most common form; returns rows rewritten."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    st_col = "contributor_street_1"
    zip_col = "contributor_zip"
    changed = 0

    for idx in df[ind].groupby("donor_key").groups.values():
        # bucket this donor's rows by (zip, anchored-fingerprint)
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s = df.at[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            z = df.at[i, zip_col]
            z = z if isinstance(z, str) else ""
            fp = _addr_fingerprint(s)
            if fp:
                buckets[(z, fp)].append(i)

        for rows in buckets.values():
            forms = [df.at[i, st_col].strip() for i in rows]
            distinct = set(forms)
            if len(distinct) < 2:
                continue
            # canonical = most common spelling (tie: deterministic first)
            canon = max(sorted(distinct), key=lambda f: forms.count(f))
            for i, f in zip(rows, forms):
                if f != canon:
                    df.at[i, st_col] = canon
                    changed += 1
    return changed


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Collapse per-donor street_2 spellings of the same unit (APT/UNIT/# 1503), bucketed by (street_1, ZIP, unit id), to the dominant form; returns rows rewritten."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0
    from fec.cleaning.pipeline.address_fixes import _unit_core

    st1_col = "contributor_street_1"
    st2_col = "contributor_street_2"
    zip_col = "contributor_zip"
    changed = 0

    for idx in df[ind].groupby("donor_key").groups.values():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s2 = df.at[i, st2_col]
            if not (isinstance(s2, str) and s2.strip()):
                continue
            core = _unit_core(s2)
            if not core:
                continue
            s1 = df.at[i, st1_col]
            s1 = s1.strip() if isinstance(s1, str) else ""
            z = df.at[i, zip_col]
            z = z if isinstance(z, str) else ""
            buckets[(s1, z, core)].append(i)

        for rows in buckets.values():
            forms = [df.at[i, st2_col].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=lambda f: (forms.count(f), len(f)))
            for i, f in zip(rows, forms):
                if f != canon:
                    df.at[i, st2_col] = canon
                    changed += 1
    return changed


def _pobox_num(street: str) -> str:
    """Extract the box number from a PO-box street, else ''."""
    m = _POBOX_RE.search(street.upper())
    return m.group(1) if m else ""


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
