"""cleaning/pipeline/address_fixes.py — per-donor address correction & recovery.

Higher-level address fixes that run AFTER the field-level street/city/ZIP
cleaning in `fec.cleaning.addresses`. These use a donor's own history to resolve
state/ZIP conflicts, unify street spellings, and recover blanked values — always
scoped to one person so genuine distinct addresses are never merged.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd


@lru_cache(maxsize=1)
def _load_zcta_to_state() -> Dict[str, str]:
    """Load ZCTA5 → state code mapping from data/database/ (cached)."""
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'database'
    zcta_path = data_dir / 'zcta_state_rel.csv'
    states_path = data_dir / 'us_states.csv'

    if not zcta_path.exists() or not states_path.exists():
        return {}

    zcta_df = pd.read_csv(zcta_path, dtype=str)
    states_df = pd.read_csv(states_path, dtype=str)

    fips_to_code = dict(zip(states_df['state_fips'], states_df['code']))
    # Vectorized: map state_fips → code, then zip into dict (no iterrows)
    state_codes = zcta_df['state_fips'].map(fips_to_code).fillna('')
    return dict(zip(zcta_df['zcta5'], state_codes))


def _load_zip3_to_state() -> Dict[str, str]:
    """ZIP3 prefix → state, a fallback for ZIPs absent from the ZCTA crosswalk.

    Non-ZCTA ZIPs (PO-box-only / unique ZIPs, e.g. 19273 = Philadelphia PA)
    have no ZCTA row, so the exact ZCTA5 lookup can't place them — and a wrong
    state on such a row slips past the state-ZIP vote. A 3-digit ZIP prefix
    (USPS SCF region) is almost always a single state, so it's a safe fallback.
    A prefix is kept only when one state owns ≥80% of its ZCTAs; the rare
    border / multi-state prefix is left unresolved rather than guessed.
    """
    data_dir = Path(__file__).resolve().parent.parent.parent.parent / 'data' / 'database'
    zcta_path = data_dir / 'zcta_state_rel.csv'
    states_path = data_dir / 'us_states.csv'
    if not zcta_path.exists() or not states_path.exists():
        return {}

    zcta_df = pd.read_csv(zcta_path, dtype=str)
    states_df = pd.read_csv(states_path, dtype=str)
    fips_to_code = dict(zip(states_df['state_fips'], states_df['code']))

    z = zcta_df.assign(
        code=zcta_df['state_fips'].map(fips_to_code),
        z3=zcta_df['zcta5'].str[:3],
    ).dropna(subset=['code'])

    out: Dict[str, str] = {}
    for z3, codes in z.groupby('z3')['code']:
        vc = codes.value_counts()
        if vc.iloc[0] / vc.sum() >= 0.8:
            out[z3] = vc.index[0]

    # Sandwich rule for prefixes with NO ZCTAs at all. The map above is derived
    # from ZCTAs, but the very ZIPs that need the fallback are PO-box-only
    # ranges whose whole prefix may lack ZCTAs (192xx Philadelphia — the exact
    # example in _fix_state_zip_mismatches' docstring — was silently MISSING,
    # so "OH 19273" sailed through). USPS allocates prefixes to states in
    # contiguous blocks, so a gap whose immediate neighbours agree on one state
    # belongs to that state too. Only same-state sandwiches are filled; a gap on
    # a state border stays unresolved rather than guessed.
    for n in range(1, 999):
        p = f"{n:03d}"
        if p in out:
            continue
        lo, hi = out.get(f"{n-1:03d}"), out.get(f"{n+1:03d}")
        if lo and hi and lo == hi:
            out[p] = lo
    return out


def _fix_impossible_city_states(df: pd.DataFrame) -> int:
    """
    Fix a single person's state typos using their OWN address history.

    A city sits in exactly one state, so when ONE person files from the same
    city name under two different states, the minority spelling is a typo —
    not a second home (two homes would be two different cities, not the same
    city name in two states).

    Example: Nancy Epstein has 24 filings as "AVENTURA, FL" and 1 as
    "AVENTURA, NJ, 07094". 07094 is a real NJ ZIP so it looks self-consistent
    and the ZIP/state vote can't catch it — but her own dominant state for
    Aventura is FL, so the lone NJ row is corrected to FL.

    Scoping by (person, city) is what keeps this safe: genuinely different
    same-named cities (Weston MA vs Weston FL, Springfield NJ vs MA) belong
    to DIFFERENT people, so they never share a group and are never flipped.
    Only flips when one state strictly outnumbers another (ties are left).

    Returns: number of states corrected.
    """
    first = df['contributor_first_name'].fillna('')
    last  = df['contributor_last_name'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')

    # Only individuals with a full (name, city, state) — committees differ.
    eligible = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (first != '') & (last != '') & (city != '') & (state != '')
    )
    if not eligible.any():
        return 0

    SEP = '\x00'
    person_city = first.str.cat([last, city], sep=SEP)

    # Count rows per (person+city, state), restricted to eligible rows.
    work = pd.DataFrame({
        'pc': person_city[eligible],
        'state': state[eligible],
    })
    counts = work.groupby(['pc', 'state']).size()

    # Keep only (person+city) groups that span more than one state.
    states_per_pc = counts.groupby(level='pc').size()
    conflict_pcs = states_per_pc[states_per_pc > 1].index
    if len(conflict_pcs) == 0:
        return 0

    # For each conflicting group, the dominant state must strictly outnumber
    # the rest (skip ties — those are genuinely ambiguous, leave them alone).
    dominant = {}
    for pc in conflict_pcs:
        grp = counts.loc[pc].sort_values(ascending=False)
        if len(grp) >= 2 and grp.iloc[0] > grp.iloc[1]:
            dominant[pc] = grp.index[0]

    if not dominant:
        return 0

    dom = person_city.map(dominant)
    to_fix = eligible & dom.notna() & (dom != '') & (state != dom)

    n = int(to_fix.sum())
    if n:
        df.loc[to_fix, 'contributor_state'] = dom[to_fix]
    return n


def _fix_state_zip_mismatches(df: pd.DataFrame) -> dict:
    """
    Resolve ZIP/state conflicts with a 3-way vote between city, state, ZIP.

    When a row's ZIP implies a different state than contributor_state, we
    don't blindly assume the ZIP is wrong. We ask the city which side it
    agrees with (the city's usual state, learned from rows where ZIP and
    state already agree):

      • city agrees with the ZIP  → the STATE field was the typo
        (e.g. Calabasas + 91302 recorded as AZ → fix state to CA, keep ZIP)
      • city agrees with the state → the ZIP was the typo
        (e.g. Boca Raton FL + a Virginia ZIP → null the ZIP)
      • city gives no clear signal → fall back to nulling the ZIP
        (city+state historically more reliable than a lone ZIP)

    Non-ZCTA ZIPs (PO-box / unique, e.g. 19273) aren't in the exact crosswalk,
    so their implied state comes from the 3-digit prefix fallback — that's what
    lets a garbled "OH 19273" finally be caught (city decides: fix the state to
    PA, or null the ZIP when no city corroborates).

    Returns: {'state_fixed': int, 'zip_nulled': int}
    """
    zcta_to_state = _load_zcta_to_state()
    if not zcta_to_state:
        return {'state_fixed': 0, 'zip_nulled': 0}
    zip3_to_state = _load_zip3_to_state()

    z5   = df['contributor_zip'].fillna('')
    st   = df['contributor_state'].fillna('')
    city = df['contributor_city'].fillna('')

    # Exact ZCTA5 → state (reliable); used as-is to learn each city's state.
    zip_state_exact = z5.map(zcta_to_state).fillna('')
    # Fallback for non-ZCTA ZIPs (PO-box / unique): 3-digit prefix → state.
    # Only fills where the exact lookup found nothing, so valid ZIPs are untouched.
    zip3_state = (z5.str[:3].map(zip3_to_state).fillna('')
                  if zip3_to_state else pd.Series('', index=df.index))
    zip_state = zip_state_exact.where(zip_state_exact != '', zip3_state)

    # Rows where the ZIP maps to a real state that differs from the recorded one.
    conflict = (z5 != '') & (st != '') & (zip_state != '') & (zip_state != st)
    if not conflict.any():
        return {'state_fixed': 0, 'zip_nulled': 0}

    # Learn each city's dominant state from TRUSTED rows — those where the EXACT
    # ZIP-implied state already equals the recorded state (exact only, so the
    # prefix fallback never pollutes the city→state signal).
    trusted = (z5 != '') & (st != '') & (city != '') & (zip_state_exact == st)
    city_to_state = (
        df.loc[trusted]
          .groupby('contributor_city')['contributor_state']
          .agg(lambda s: s.value_counts().idxmax())
          .to_dict()
    )
    city_state = city.map(city_to_state).fillna('')

    # The state was the typo when the city's usual state matches the ZIP.
    fix_state = conflict & (city_state != '') & (city_state == zip_state)
    # Otherwise (city backs the state, or no city signal) the ZIP is the typo.
    null_zip = conflict & ~fix_state

    n_state = int(fix_state.sum())
    n_zip   = int(null_zip.sum())

    if n_state:
        df.loc[fix_state, 'contributor_state'] = zip_state[fix_state]
    if n_zip:
        df.loc[null_zip, 'contributor_zip'] = np.nan

    return {'state_fixed': n_state, 'zip_nulled': n_zip}


def _unify_street_variants(df: pd.DataFrame, fingerprint) -> int:
    """
    Collapse different spellings of ONE person's ONE street to a single form.

    Two streets that share a `fingerprint(street) -> str` key are treated as
    the same physical address written two ways. Scoped to ONE donor
    (name + city + state): we only merge spellings the SAME person used at
    the SAME city — never across donors — so genuine distinct addresses that
    happen to fingerprint-collide are left alone. Within each group the
    dominant (most-common, longest as tiebreaker) spelling wins.

    Returns: number of rows rewritten.
    """
    street = df['contributor_street_1'].fillna('')
    name   = df['contributor_name'].fillna('')
    city   = df['contributor_city'].fillna('')
    state  = df['contributor_state'].fillna('')
    eligible = (name != '') & (street != '')
    if not eligible.any():
        return 0

    fp = street.map(fingerprint)
    SEP = '\x00'
    grp = name.str.cat([city, state, fp], sep=SEP)   # donor + place + addr-shape

    work = pd.DataFrame({'g': grp[eligible], 's': street[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    # Only groups with >1 distinct spelling need fixing.
    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    # Winner per group: most rows, then longest string (keeps the fuller form).
    counts = counts.sort_values(['g', 'n', 'len'], ascending=[True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canon = grp.map(winner)

    fix = eligible & canon.notna() & (street != canon)
    n = int(fix.sum())
    if n:
        df.loc[fix, 'contributor_street_1'] = canon[fix]
    return n


def _unify_street_spellings(df: pd.DataFrame) -> int:
    """Merge word-order / lost-space variants of the same street.

    Fingerprint: alphanumeric tokens, SORTED — so "2425 NW L ST" and
    "2425 LST NW" (same tokens, different order) collapse to one key.
    """
    return _unify_street_variants(
        df, lambda s: ' '.join(sorted(re.findall(r'[A-Z0-9]+', s.upper()))))


def _unify_street_spacing(df: pd.DataFrame) -> int:
    """Merge spacing/punctuation-only variants the token fingerprint misses.

    Fingerprint: every non-alphanumeric char dropped, order KEPT — so
    "MEADOW RIDGE"/"MEADOWRIDGE", "PH-2"/"PH2", "BEAR'S"/"BEARS" collapse,
    while a real letter difference ("SPENCEHILL" vs "SPENCEHIL") stays
    apart. Identical letters+digits in the same order is provably the same
    address — never a move (a move changes the house number or name).
    Runs right after `_unify_street_spellings` to catch what it leaves.
    """
    return _unify_street_variants(
        df, lambda s: re.sub(r'[^A-Z0-9]', '', s.upper()))


# Secondary-unit designator words in street_2 that are interchangeable ways to
# write the SAME unit: "APT 1503" / "UNIT 1503" / "# 1503" / "STE 1503". Reducing
# them to the bare unit id ("1503") lets one person's filings of the same unit
# collapse to one address instead of splitting their Address History into
# duplicate cards (the addresses dimension keys on the full street_1 + street_2).
_UNIT_DESIGNATOR_RE = re.compile(
    r'#|\b(?:APARTMENT|APT|UNIT|STE|SUITE|NUMBER|NO|RM|ROOM|FL|FLOOR|BLDG|BUILDING)\b\.?',
    re.I,
)


def _unit_core(s: str) -> str:
    """Bare unit id of a street_2 — designator words and punctuation removed.
    'APT 1503' / 'UNIT 1503' / '# 1503' all -> '1503'."""
    return re.sub(r'[^A-Z0-9]', '', _UNIT_DESIGNATOR_RE.sub(' ', str(s).upper()))


def _unify_unit_designators(df: pd.DataFrame) -> int:
    """Collapse ONE person's SAME unit written with different designators.

    "730 N OCEAN BLVD, APT 1503" and "...UNIT 1503" are the same home, but the
    addresses dimension keys on the full (street_1 + street_2 + city/state/zip),
    so a differing designator splits a donor's Address History into duplicate
    cards (both showing only "City, ST ZIP"). Scoped to ONE donor at ONE
    (street_1, city, state, zip): only street_2 values that reduce to the SAME
    bare unit id are merged, to the donor's dominant raw form (most rows, then
    longest). A genuinely different unit ("APT 1503" vs "APT 1502") keeps a
    different core → never merged. Per-donor, so two people in the same building
    are never touched.

    Returns: number of rows rewritten.
    """
    if 'entity_type' not in df.columns:
        return 0
    st2   = df['contributor_street_2'].fillna('')
    name  = df['contributor_name'].fillna('')
    st1   = df['contributor_street_1'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')
    zip5  = df['contributor_zip'].fillna('')
    eligible = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (name != '') & (st2 != '') & (st1 != '')
    )
    if not eligible.any():
        return 0

    core = st2.map(_unit_core)
    # Need a real unit id to anchor the merge — a bare designator ("APT" with no
    # number) reduces to '' and is left alone (can't prove it's the same unit).
    eligible = eligible & (core != '')
    if not eligible.any():
        return 0

    SEP = '\x00'
    grp = name.str.cat([st1, city, state, zip5, core], sep=SEP)  # donor+building+unit
    work = pd.DataFrame({'g': grp[eligible], 's': st2[eligible]})
    counts = work.groupby(['g', 's']).size().rename('n').reset_index()
    counts['len'] = counts['s'].str.len()

    # Only groups with >1 distinct designator spelling of the same unit.
    multi = counts.groupby('g')['s'].transform('nunique') > 1
    counts = counts[multi]
    if counts.empty:
        return 0

    counts = counts.sort_values(['g', 'n', 'len'], ascending=[True, False, False])
    winner = counts.drop_duplicates('g').set_index('g')['s']
    canon = grp.map(winner)

    fix = eligible & canon.notna() & (st2 != canon)
    n = int(fix.sum())
    if n:
        df.loc[fix, 'contributor_street_2'] = canon[fix]
    return n


def _recover_address_from_same_street(df: pd.DataFrame) -> dict:
    """
    Make a person's city / state / ZIP consistent across the SAME street.

    The street address is the physical anchor: every filing a donor makes
    from the exact same street is the same home, so city, state and ZIP must
    agree across them. This step fills blanks and corrects minority typos
    from the dominant value the donor used at that street.

    Crucially, a genuine MOVE is a DIFFERENT street → its own group → it is
    never forced onto the old address. But if the donor moved and mistyped
    the NEW city or ZIP on some filings, the majority of same-street records
    carry the correct value and the typo'd minority is fixed to match.

    Example: Rochelle Levy filed 19x from "6233 VARIEL AVE, WOODLAND HILLS,
    CA" with a blank ZIP (her "01367" typo was nulled upstream) and 7x from
    the same street with ZIP 91367. The blanks are filled with 91367 — her
    own confirmed ZIP for that exact address, not a guess.

    A second pass also unifies the CITY by (name, ZIP): when an apartment
    number is appended to the street ("175 E 74TH ST 14A" vs "175 E 74TH
    ST"), the street group splits, so a truncated city on the apartment row
    ("NEW" for "NEW YORK") is recovered from the donor's dominant city at
    that same ZIP instead.

    Only fixes a value when the dominant strictly outnumbers it (ties are
    left alone). Returns counts per field: {'zip': n, 'city': n, 'state': n}.
    """
    name = df['contributor_name'].fillna('')
    eligible_base = (df['entity_type'] == 'INDIVIDUAL') & (name != '')

    def _unify(col: str, key: pd.Series, eligible: pd.Series) -> int:
        """Within each `key` group, fill blanks / fix minority typos in `col`
        to the dominant non-empty value. Returns rows changed."""
        vals = df[col].fillna('')
        ne = eligible & (vals != '')
        counts = (
            pd.DataFrame({'k': key[ne], 'v': vals[ne]})
            .groupby(['k', 'v']).size().rename('cnt').reset_index()
        )
        if counts.empty:
            return 0
        dom = counts.loc[counts.groupby('k')['cnt'].idxmax()].set_index('k')
        dom_val = key.map(dom['v'])
        dom_cnt = key.map(dom['cnt'])
        row_cnt = pd.Series(
            pd.DataFrame({'k': key, 'v': vals})
              .merge(counts, on=['k', 'v'], how='left')['cnt']
              .fillna(0).to_numpy(),
            index=df.index,
        )
        fix = (
            eligible
            & dom_val.notna()
            & (vals != dom_val)
            & ((vals == '') | (row_cnt < dom_cnt))
        )
        n = int(fix.sum())
        if n:
            df.loc[fix, col] = dom_val[fix]
        return n

    out = {'zip': 0, 'city': 0, 'state': 0}

    # Pass 1 — anchor on the exact street (the physical home).
    street = df['contributor_street_1'].fillna('')
    eligible_street = eligible_base & (street != '')
    if eligible_street.any():
        street_key = name.str.cat(street, sep='\x00')
        out['zip']   = _unify('contributor_zip', street_key, eligible_street)
        out['city']  = _unify('contributor_city', street_key, eligible_street)
        out['state'] = _unify('contributor_state', street_key, eligible_street)

    # Pass 2 — city by (name, ZIP). Catches apartment-split streets where a
    # truncated city ("NEW") sits on the unit row but the donor's dominant
    # city at that same ZIP is the full name ("NEW YORK").
    zip5 = df['contributor_zip'].fillna('')
    eligible_zip = eligible_base & (zip5 != '')
    if eligible_zip.any():
        zip_key = name.str.cat(zip5, sep='\x00')
        out['city'] += _unify('contributor_city', zip_key, eligible_zip)

    return out


def _recover_null_streets(df: pd.DataFrame) -> int:
    """
    Fill NULL streets from other records of the same person.

    When FEC has an email in the street field, we null it during cleaning.
    But the same person (name+city+state) often has a real street in
    other contributions. This step recovers those addresses.

    Returns: number of streets recovered.
    """
    null_street = df['contributor_street_1'].isna()
    if not null_street.any():
        return 0

    # Build a lookup: (name, city, state) → most common street
    has_street = df['contributor_street_1'].notna()
    street_lookup = (
        df.loc[has_street]
        .groupby(['contributor_name', 'contributor_city', 'contributor_state'])
        ['contributor_street_1']
        .agg(lambda x: x.value_counts().index[0])  # most common street
        .to_dict()
    )

    n_recovered = 0
    for idx in df[null_street].index:
        key = (
            df.at[idx, 'contributor_name'],
            df.at[idx, 'contributor_city'],
            df.at[idx, 'contributor_state'],
        )
        street = street_lookup.get(key)
        if street:
            df.at[idx, 'contributor_street_1'] = street
            n_recovered += 1

    return n_recovered


# A street_1 is "usable" if a geocoder can place it: a house number, a PO box,
# or a recognisable street-type token. Anything else ("GOLDEN BEACH", "RING
# HOUSE", "CALLE ACE") is a fragment / place-name the donor wrote instead of
# their street.
# NOTE: deliberately NARROWER than address_review._STREET_TYPES: this runs on
# POST-normalized streets (types already abbreviated), so only the abbreviated
# tokens belong here — and unit keywords (APT/STE) don't make a street usable.
_STREET_TYPE_RE = (
    r'\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|'
    r'ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|'
    r'RUN|PT|PASS|WALK|BEND|MALL)\b'
)


def _is_usable_street(s: pd.Series) -> pd.Series:
    """Vectorised: True where street_1 looks geocodable."""
    u = s.fillna('').astype(str).str.upper()
    return (
        u.str.startswith('PO BOX')
        | u.str.match(r'^\d')
        | u.str.contains(_STREET_TYPE_RE, regex=True)
    )


def _recover_nonstreet_from_donor(df: pd.DataFrame) -> int:
    """
    Replace a non-usable street_1 with the SAME person's real street.

    Some filings carry a place-name or fragment where the street belongs —
    "GOLDEN BEACH", "RING HOUSE APT # 431 1801 E JE", "CALLE ACE" — because the
    donor hand-keyed it incompletely. But the same person (name + city + state)
    almost always wrote a full street on their OTHER contributions. This fills
    those in from the donor's own history, the deterministic sibling of
    `_recover_null_streets` for the non-null case.

    Only INDIVIDUAL rows are touched; only a CLEAN donor street (house number /
    PO box / street type) is ever used as the replacement; and only a
    non-usable value is ever overwritten — a good street is never changed.

    Returns: number of streets recovered.
    """
    if 'entity_type' not in df.columns:
        return 0
    usable = _is_usable_street(df['contributor_street_1'])
    target = (
        (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_street_1'].notna()
        & (df['contributor_street_1'].astype(str).str.strip() != '')
        & ~usable
    )
    if not target.any():
        return 0

    # Lookup uses ONLY usable streets — never recover one fragment with another.
    clean = df.loc[usable & df['contributor_street_1'].notna()]
    if clean.empty:
        return 0
    # Primary key: name + city + state → the street matches the row's place.
    by_place = (
        clean.groupby(['contributor_name', 'contributor_city', 'contributor_state'])
        ['contributor_street_1']
        .agg(lambda x: x.value_counts().index[0])
        .to_dict()
    )
    # Fallback: name + state, but ONLY when the donor has exactly ONE clean
    # street in the whole state — no city ambiguity, so a garbled/mismatched
    # city on the fragment row (the common reason the primary key misses) is
    # safe to look past. Two distinct streets in a state → skip (can't choose).
    state_streets: Dict[tuple, set] = {}
    for (name, _city, state), street in (
        clean.groupby(['contributor_name', 'contributor_city', 'contributor_state'])
        ['contributor_street_1'].agg(lambda x: x.value_counts().index[0]).items()
    ):
        state_streets.setdefault((name, state), set()).add(street)
    by_state_single = {k: next(iter(v)) for k, v in state_streets.items() if len(v) == 1}

    n_recovered = 0
    for idx in df[target].index:
        name = df.at[idx, 'contributor_name']
        state = df.at[idx, 'contributor_state']
        street = (
            by_place.get((name, df.at[idx, 'contributor_city'], state))
            or by_state_single.get((name, state))
        )
        if street:
            df.at[idx, 'contributor_street_1'] = street
            n_recovered += 1

    return n_recovered


def _recover_house_number_from_donor(df: pd.DataFrame) -> int:
    """Backfill a missing house number from the SAME donor's numbered filing.

    A street_1 with a street-type token but NO leading house number
    ('FAIRWAY DR', 'WILSHIRE BLVD') passes `_is_usable_street`, so
    `_recover_nonstreet_from_donor` leaves it alone — yet it geocodes only to the
    street/ZIP centroid, not the donor's actual point. When the same donor wrote
    the SAME street WITH a number elsewhere ('108 FAIRWAY DR'), copy that full
    value in.

    Conservative: the number-stripped street must match EXACTLY, the key is
    name+state, and only the donor's dominant numbered form is used — so a
    different street is never substituted.
    """
    if 'entity_type' not in df.columns:
        return 0
    s = df['contributor_street_1'].fillna('').astype(str).str.upper().str.strip()
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    has_type = s.str.contains(_STREET_TYPE_RE, regex=True)
    starts_num = s.str.match(r'^\d')
    no_number = is_indiv & has_type & ~starts_num & ~s.str.startswith('PO BOX') & (s != '')
    if not no_number.any():
        return 0

    numbered = df[is_indiv & starts_num]
    if numbered.empty:
        return 0

    def _strip_num(x: str) -> str:
        return re.sub(r'^\d+\s+', '', str(x).upper().strip())

    tmp = numbered[['contributor_name', 'contributor_state', 'contributor_street_1']].copy()
    tmp['_stripped'] = tmp['contributor_street_1'].map(_strip_num)
    key_full = (
        tmp.groupby(['contributor_name', 'contributor_state', '_stripped'])
        ['contributor_street_1'].agg(lambda x: x.value_counts().index[0]).to_dict()
    )

    n = 0
    for idx in df[no_number].index:
        full = key_full.get(
            (df.at[idx, 'contributor_name'], df.at[idx, 'contributor_state'], s.at[idx])
        )
        if full and str(full).upper().strip() != s.at[idx]:
            df.at[idx, 'contributor_street_1'] = full
            n += 1
    return n
