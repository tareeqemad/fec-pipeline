"""Per-donor street canonicalization: variant forms, unit designators, PO-box typos."""

import re
from collections import defaultdict

import pandas as pd

_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_POBOX_RE = re.compile(r'\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)')


def _addr_fingerprint(street: str) -> str:
    """Order-independent street key: house number anchored, remaining tokens sorted (keeps grid addresses distinct)."""
    toks = _ADDR_TOKEN_RE.findall((street or "").upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Collapse per-donor street_1 variants with the same ZIP and anchored token set to the most common form; returns rows rewritten."""
    if "contributor_street_1" not in df.columns:
        return 0
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    st_col = df.columns.get_loc("contributor_street_1")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        # bucket this donor's rows by (zip, anchored-fingerprint)
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s = df.iat[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            fp = _addr_fingerprint(s)
            if fp:
                buckets[(z, fp)].append(i)

        for (_z, _fp), rows in buckets.items():
            forms = [df.iat[i, st_col].strip() for i in rows]
            distinct = set(forms)
            if len(distinct) < 2:
                continue
            # canonical = most common spelling (tie: deterministic first)
            canon = max(sorted(distinct), key=lambda f: forms.count(f))
            for i in rows:
                if df.iat[i, st_col].strip() != canon:
                    df.iat[i, st_col] = canon
                    changed += 1
    return changed


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Collapse per-donor street_2 spellings of the same unit (APT/UNIT/# 1503), bucketed by (street_1, ZIP, unit id), to the dominant form; returns rows rewritten."""
    if "contributor_street_2" not in df.columns or "donor_key" not in df.columns:
        return 0
    ind = df["entity_type"] == "INDIVIDUAL" if "entity_type" in df.columns \
        else pd.Series(True, index=df.index)
    if not ind.any():
        return 0
    from fec.cleaning.pipeline.address_fixes import _unit_core

    st1_col = df.columns.get_loc("contributor_street_1") if "contributor_street_1" in df.columns else None
    st2_col = df.columns.get_loc("contributor_street_2")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s2 = df.iat[i, st2_col]
            if not (isinstance(s2, str) and s2.strip()):
                continue
            core = _unit_core(s2)
            if not core:
                continue
            s1 = df.iat[i, st1_col] if st1_col is not None else ""
            s1 = s1.strip() if isinstance(s1, str) else ""
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            buckets[(s1, z, core)].append(i)

        for _k, rows in buckets.items():
            forms = [df.iat[i, st2_col].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=lambda f: (forms.count(f), len(f)))
            for i in rows:
                if df.iat[i, st2_col].strip() != canon:
                    df.iat[i, st2_col] = canon
                    changed += 1
    return changed


def _pobox_num(street: str) -> str:
    """Extract the box number from a PO-box street, else ''."""
    m = _POBOX_RE.search(str(street).upper())
    return m.group(1) if m else ''


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


def canonicalize_donor_pobox_typos(df: pd.DataFrame) -> int:
    """Collapse per-donor same-ZIP PO-box numbers that differ by one inserted digit to the most frequent box (all entity types; same-length boxes never merge); returns rows rewritten."""
    if "contributor_street_1" not in df.columns or "donor_key" not in df.columns:
        return 0
    st_col = df.columns.get_loc("contributor_street_1")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df.groupby("donor_key").groups.items():
        by_zip: dict[str, list] = defaultdict(list)
        for i in idx:
            s = df.iat[i, st_col]
            bn = _pobox_num(s) if isinstance(s, str) else ""
            if not bn:
                continue
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            by_zip[z].append((i, s.strip(), bn))

        for _z, rows in by_zip.items():
            nums = sorted({bn for _, _, bn in rows})
            if len(nums) < 2:
                continue
            # union typo-related box numbers into clusters
            parent = {n: n for n in nums}
            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            for a in nums:
                for b in nums:
                    if a < b and _is_insertion_typo(a, b):
                        parent[find(a)] = find(b)
            clusters: dict[str, list] = defaultdict(list)
            for n in nums:
                clusters[find(n)].append(n)

            freq: dict[str, int] = defaultdict(int)
            for _, _, bn in rows:
                freq[bn] += 1
            for members in clusters.values():
                if len(members) < 2:
                    continue
                canon_box = max(members, key=lambda n: (freq[n], -len(n)))
                canon_forms = [f for _, f, bn in rows if bn == canon_box]
                canon_full = max(set(canon_forms), key=canon_forms.count)
                for i, f, bn in rows:
                    if bn in members and f != canon_full:
                        df.iat[i, st_col] = canon_full
                        changed += 1
    return changed
