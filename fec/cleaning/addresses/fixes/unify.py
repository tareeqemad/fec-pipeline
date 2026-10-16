"""Per-donor unification of street spellings and street_2 unit designators."""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from fec.donor_match.matcher import UnionFind

# '10 17 GREENTREE DR': a house number cut in two by a space
_SPLIT_HOUSE_NUMBER_RE = r'^\d+ \d+\b'


def _collapse_to_dominant(df: pd.DataFrame, col: str, grp: pd.Series,
                          eligible: pd.Series, prefer_longest: bool = False) -> int:
    """Rewrite each group's minority spellings of col to the dominant one; returns rows rewritten."""
    vals = df[col].fillna('')
    work = pd.DataFrame({'g': grp[eligible], 's': vals[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    # only groups with >1 distinct spelling need fixing
    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    # Prefer '1017 GREENTREE DR' over '10 17 GREENTREE DR'.
    counts['split_number'] = counts['s'].str.match(_SPLIT_HOUSE_NUMBER_RE)
    # winner per group: most rows, then longest string (keeps the fuller form);
    # prefer_longest flips that so the fuller spelling wins even as a minority
    order = ['g', 'split_number'] + (['len', 'n'] if prefer_longest else ['n', 'len'])
    counts = counts.sort_values(order, ascending=[True, True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canonical = grp.map(winner)

    fix = eligible & canonical.notna() & (vals != canonical)
    n_fixed = int(fix.sum())
    if n_fixed:
        df.loc[fix, col] = canonical[fix]
    return n_fixed


def _unify_street_variants(df: pd.DataFrame, fingerprint, prefer_longest: bool = False) -> int:
    """Collapse fingerprint-equal spellings of one donor's street to the dominant form; returns rows rewritten."""
    # Scope spelling changes by name, city and state.
    street = df['contributor_street_1'].fillna('')
    name   = df['contributor_name'].fillna('')
    city   = df['contributor_city'].fillna('')
    state  = df['contributor_state'].fillna('')
    eligible = (name != '') & (street != '')
    if not eligible.any():
        return 0

    fingerprints = street.map(fingerprint)
    SEP = '\x00'
    group_key = name.str.cat([city, state, fingerprints], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_1', group_key, eligible, prefer_longest)


def _unify_street_spellings(df: pd.DataFrame) -> int:
    """Merge word-order / lost-space street variants (fingerprint: sorted alphanumeric tokens)."""
    return _unify_street_variants(
        df, lambda street: ' '.join(sorted(re.findall(r'[A-Z0-9]+', street.upper()))))


def _unify_street_spacing(df: pd.DataFrame) -> int:
    """Merge spacing/punctuation-only variants (fingerprint: non-alphanumerics dropped, order kept)."""
    # Compare letters and digits in their filed order.
    return _unify_street_variants(
        df, lambda street: re.sub(r'[^A-Z0-9]', '', street.upper()))


# Unit designators: APT, UNIT and #.
_UNIT_DESIGNATOR_RE = re.compile(
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM)\b\.?',
    re.IGNORECASE,
)
# a floor or a building is other information than a unit: "FL 3" is not "APT 3"
_FLOOR_RE = re.compile(r'\b(?:FL|FLR|FLOOR)\b\.?', re.IGNORECASE)
# a building designator word: "BLDG 2", "BUILDING C"
_BUILDING_RE = re.compile(r'\b(?:BLDG|BUILDING)\b\.?', re.IGNORECASE)


def unit_core(s: str) -> str:
    """Bare unit id of a street_2, unit words and punctuation removed; a floor or building keeps its kind."""
    # every designator starts a new id; inside one id spaces and punctuation
    # go ("705 N" = "705N", "15-03" = "1503"), while two ids stay apart
    # ("BLDG 1 STE 23" is not "BLDG 12 STE 3")
    text = _UNIT_DESIGNATOR_RE.sub('|', str(s).upper())
    text = _FLOOR_RE.sub('|FLOOR|', _BUILDING_RE.sub('|BUILDING|', text))
    ids = (re.sub(r'[^A-Z0-9]', '', part) for part in text.split('|'))
    return ' '.join(part for part in ids if part)


def _unify_unit_designators(df: pd.DataFrame) -> int:
    """Collapse one donor's same unit written with different designators; returns rows rewritten."""
    # Unify the same unit only within one donor's street, city, state and ZIP.
    st2   = df['contributor_street_2'].fillna('')
    name  = df['contributor_name'].fillna('')
    st1   = df['contributor_street_1'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')
    zips  = df['contributor_zip'].fillna('')
    eligible = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (name != '') & (st2 != '') & (st1 != '')
    )
    if not eligible.any():
        return 0

    core = st2.map(unit_core)
    # Keep unit designators that have no number.
    eligible = eligible & (core != '')
    if not eligible.any():
        return 0

    SEP = '\x00'
    group_key = name.str.cat([st1, city, state, zips, core], sep=SEP)
    return _collapse_to_dominant(df, 'contributor_street_2', group_key, eligible)


_TYPE_TOKENS = {'ST', 'AVE', 'RD', 'DR', 'BLVD', 'LN', 'CT', 'PL', 'CIR', 'TER', 'PKWY', 'HWY', 'SQ', 'WAY', 'TRL', 'PLZ', 'LOOP'}


def _unify_street_types(df: pd.DataFrame) -> int:
    """Give one donor's "103 STANTON" the type of their "103 STANTON AVE"; returns rows rewritten."""
    # Fill missing street types only when the donor's filed spellings agree.
    street = df['contributor_street_1'].fillna('')
    name = df['contributor_name'].fillna('')
    words = street.str.upper().str.findall(r'[A-Z0-9]+')
    bare = words.map(lambda tokens: ' '.join(t for t in tokens if t not in _TYPE_TOKENS))
    types = words.map(lambda tokens: ' '.join(t for t in tokens if t in _TYPE_TOKENS))
    group_key = name.str.cat([df['contributor_city'].fillna(''), df['contributor_state'].fillna(''), bare],
                             sep='\x00')
    typed = types != ''
    kinds = types[typed].groupby(group_key[typed]).nunique()
    conflict = group_key.isin(kinds[kinds > 1].index)
    eligible = (name != '') & (street != '') & ~conflict
    if not eligible.any():
        return 0
    return _collapse_to_dominant(df, 'contributor_street_1', group_key, eligible, prefer_longest=True)


# a run of letters/digits, to tokenize a street address
_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# a PO box with its number captured: "PO BOX 123", "P.O. BOX #45"
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")


def _addr_fingerprint(street: str) -> str:
    """Anchor the house number and sort the remaining address tokens."""
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def _collapse_donor_variants(df: pd.DataFrame, column: str, bucket_of, rank) -> int:
    """Within each donor, replace variants in a bucket with its highest-ranked form."""
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


def _zip_text(df: pd.DataFrame, i) -> str:
    z = df.at[i, "contributor_zip"]
    return z if isinstance(z, str) else ""


def _street_bucket(df: pd.DataFrame, i, street: str):
    fingerprint = _addr_fingerprint(street)
    return (_zip_text(df, i), fingerprint) if fingerprint else None


def _unit_bucket(df: pd.DataFrame, i, unit: str):
    core = unit_core(unit)
    if not core:
        return None
    street = df.at[i, "contributor_street_1"]
    return (street.strip() if isinstance(street, str) else "", _zip_text(df, i), core)


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Unify street spellings with the same ZIP and anchored tokens; return rows changed."""
    return _collapse_donor_variants(
        df, "contributor_street_1", _street_bucket, lambda form, forms: forms.count(form),
    )


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Unify unit spellings at the same street and ZIP; return rows changed."""
    return _collapse_donor_variants(
        df, "contributor_street_2", _unit_bucket,
        lambda form, forms: (forms.count(form), len(form)),
    )


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
