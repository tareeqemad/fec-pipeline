"""Per-donor name, employer, street, unit, and PO-box canonicalization."""

import re
from collections import defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES

from .matcher import UnionFind

# legal suffixes/connectors carry no identity when comparing employer names
_EMP_DROP_TOKENS = frozenset(
    {
        "LLP",
        "LLC",
        "INC",
        "PC",
        "CO",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "LP",
        "LTD",
        "PLLC",
        "PA",
        "APC",
        "CHARTERED",
        "THE",
        "AND",
        "OF",
    }
)
_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")

_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")


def canonicalize_donor_names(df: pd.DataFrame) -> int:
    """Write one canonical first/last (most common last, fullest first, shared surname tokens dropped) plus a rebuilt LAST, FIRST composite to every row of each donor; returns rows changed."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    changed = 0
    fn_col = "contributor_first_name"
    ln_col = "contributor_last_name"
    cn_col = "contributor_name"

    for idx in df[ind].groupby("donor_key").groups.values():
        rows = df.loc[idx]
        lasts = rows["contributor_last_name"].dropna().map(str).str.strip()
        lasts = lasts[lasts != ""]
        firsts = rows["contributor_first_name"].dropna().map(str).str.strip()
        firsts = firsts[firsts != ""]
        if lasts.empty:
            continue
        canon_last = lasts.mode().iloc[0]
        last_words = set(canon_last.upper().split())
        # candidates must carry a non-surname token: a reversed filing's
        # surname-as-first could otherwise win, then strip to nothing
        fcands = [
            f for f in firsts if any(w.upper() not in last_words for w in f.split())
        ]
        canon_first = max(fcands, key=len) if fcands else None
        if canon_first:
            kept = [w for w in canon_first.split() if w.upper() not in last_words]
            canon_first = " ".join(kept) or None

        # composite rebuilt from the canonical pair, in FEC's "LAST, FIRST" form
        canon_name = f"{canon_last}, {canon_first}" if canon_first else canon_last

        for i in idx:
            cur_f = df.at[i, fn_col]
            cur_l = df.at[i, ln_col]
            cf = cur_f if (isinstance(cur_f, str) and cur_f.strip()) else None
            cl = cur_l if (isinstance(cur_l, str) and cur_l.strip()) else None
            cur_n = df.at[i, cn_col]
            if cf != canon_first or cl != canon_last or cur_n != canon_name:
                df.at[i, fn_col] = canon_first
                df.at[i, ln_col] = canon_last
                df.at[i, cn_col] = canon_name
                changed += 1
    return changed

def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = _EMP_TOKEN_RE.findall(name.upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


def _employer_variant_map(names: list[str]) -> dict[str, str]:
    cores = {name: _emp_core_tokens(name) for name in names}
    union = UnionFind()
    for left_index in range(len(names)):
        for right_index in range(left_index + 1, len(names)):
            left = names[left_index]
            right = names[right_index]
            left_core = cores[left]
            right_core = cores[right]
            if len(left_core & right_core) >= 2 and (
                left_core <= right_core or right_core <= left_core
            ):
                union.union(left, right)

    clusters = defaultdict(list)
    for name in names:
        clusters[union.find(name)].append(name)

    remap = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda name: (len(cores[name]), len(name)))
        remap.update({name: canonical for name in members if name != canonical})
    return remap


def _apply_employer_variants(df: pd.DataFrame, indexes, remap: dict) -> int:
    changed = 0
    for index in indexes:
        current = df.at[index, "contributor_employer"]
        if isinstance(current, str) and current.strip() in remap:
            df.at[index, "contributor_employer"] = remap[current.strip()]
            changed += 1
    return changed


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    """Unify clear employer variants within each donor's history."""
    individuals = df["entity_type"] == "INDIVIDUAL"
    if not individuals.any():
        return 0

    changed = 0
    for indexes in df[individuals].groupby("donor_key").groups.values():
        values = df.loc[indexes, "contributor_employer"].dropna().map(str).str.strip()
        names = [
            name
            for name in values.unique()
            if name and name.upper() not in EMPLOYER_STATUS_VALUES
        ]
        if len(names) >= 2:
            remap = _employer_variant_map(names)
            if remap:
                changed += _apply_employer_variants(df, indexes, remap)
    return changed


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """Rename ORGANIZATION donors to the canonical employer spelling of the same company (reuses canonical_key, adds no new normalization); returns rows aligned."""
    from fec.cleaning.employer_synonyms import canonical_key

    # canonical display name per canonical_key = the donor-side spelling seen most
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    emp = ind["contributor_employer"].dropna().astype(str).str.strip()
    emp = emp[(emp != "") & (~emp.str.upper().isin(EMPLOYER_STATUS_VALUES))]
    if emp.empty:
        return 0
    by_key: dict[str, str] = {}
    for name in emp.value_counts().index:  # value_counts: most common first
        k = canonical_key(name)
        if k and k not in by_key:
            by_key[k] = name

    def _forms(n: str):
        out = [n]
        if "," in n:  # "CAPITAL, WHITE" -> "WHITE CAPITAL"
            a, b = n.split(",", 1)
            out.append(f"{b.strip()} {a.strip()}")
        return out

    org_names = (
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"]
        .dropna()
        .unique()
    )
    remap: dict[str, str] = {}
    for nm in org_names:
        for form in _forms(str(nm)):
            k = canonical_key(form)
            if k and k in by_key and by_key[k].upper() != str(nm).upper():
                remap[nm] = by_key[k]
                break
    if not remap:
        return 0
    mask = (df["entity_type"] == "ORGANIZATION") & df["contributor_name"].isin(remap)
    df.loc[mask, "contributor_name"] = df.loc[mask, "contributor_name"].map(remap)
    return int(mask.sum())


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
