"""State/ZIP conflict fixes backed by the ZCTA crosswalk."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

_DATA_DIR = Path(__file__).resolve().parents[4] / 'data' / 'database'


def _read_crosswalk() -> tuple[pd.DataFrame, dict] | None:
    """Read the ZCTA crosswalk; returns (zcta_df, fips_to_code) or None if the CSVs are missing."""
    zcta_path = _DATA_DIR / 'zcta_state_rel.csv'
    states_path = _DATA_DIR / 'us_states.csv'
    if not zcta_path.exists() or not states_path.exists():
        return None
    zcta_df = pd.read_csv(zcta_path, dtype=str)
    states_df = pd.read_csv(states_path, dtype=str)
    fips_to_code = dict(zip(states_df['state_fips'], states_df['code']))
    return zcta_df, fips_to_code


@lru_cache(maxsize=1)
def _load_zcta_to_state() -> dict[str, str]:
    """Load ZCTA5 to state code mapping from data/database/ (cached)."""
    crosswalk = _read_crosswalk()
    if crosswalk is None:
        return {}
    zcta_df, fips_to_code = crosswalk
    state_codes = zcta_df['state_fips'].map(fips_to_code).fillna('')
    return dict(zip(zcta_df['zcta5'], state_codes))


def _load_zip3_to_state() -> dict[str, str]:
    """ZIP3 prefix to state, fallback for non-ZCTA ZIPs (PO-box-only / unique) missing from the crosswalk."""
    crosswalk = _read_crosswalk()
    if crosswalk is None:
        return {}
    zcta_df, fips_to_code = crosswalk

    zcta = zcta_df.assign(
        code=zcta_df['state_fips'].map(fips_to_code),
        z3=zcta_df['zcta5'].str[:3],
    ).dropna(subset=['code'])

    # keep a prefix only when one state owns >= 80% of its ZCTAs; a rare
    # border / multi-state prefix stays unresolved rather than guessed
    out: dict[str, str] = {}
    for z3, codes in zcta.groupby('z3')['code']:
        value_counts = codes.value_counts()
        if value_counts.iloc[0] / value_counts.sum() >= 0.8:
            out[z3] = value_counts.index[0]

    # sandwich rule for prefixes with NO ZCTAs at all (PO-box-only ranges like
    # 192xx Philadelphia). USPS allocates prefixes to states in contiguous
    # blocks, so a gap whose neighbours agree on one state belongs to it;
    # a gap on a state border stays unresolved rather than guessed.
    for prefix_num in range(1, 999):
        prefix = f"{prefix_num:03d}"
        if prefix in out:
            continue
        below, above = out.get(f"{prefix_num-1:03d}"), out.get(f"{prefix_num+1:03d}")
        if below and above and below == above:
            out[prefix] = below
    return out


def _fix_impossible_city_states(df: pd.DataFrame) -> int:
    """Fix a person's state typos from their own history; returns states corrected."""
    # a city sits in one state: the same person filing one city name under two
    # states means the minority spelling is a typo, not a second home.
    # scoping by (person, city) is what keeps this safe: genuinely different
    # same-named cities (Weston MA vs Weston FL) belong to different people.
    first = df['contributor_first_name'].fillna('')
    last  = df['contributor_last_name'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')

    # only individuals with a full (name, city, state) - committees differ
    eligible = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (first != '') & (last != '') & (city != '') & (state != '')
    )
    if not eligible.any():
        return 0

    SEP = '\x00'
    person_city = first.str.cat([last, city], sep=SEP)

    work = pd.DataFrame({
        'pc': person_city[eligible],
        'state': state[eligible],
    })
    counts = work.groupby(['pc', 'state']).size()

    # only (person+city) groups that span more than one state
    states_per_group = counts.groupby(level='pc').size()
    conflict_keys = states_per_group[states_per_group > 1].index
    if len(conflict_keys) == 0:
        return 0

    # the dominant state must strictly outnumber the rest (ties are ambiguous, left alone)
    dominant = {}
    for key in conflict_keys:
        group = counts.loc[key].sort_values(ascending=False)
        if len(group) >= 2 and group.iloc[0] > group.iloc[1]:
            dominant[key] = group.index[0]

    if not dominant:
        return 0

    dominant_state = person_city.map(dominant)
    to_fix = eligible & dominant_state.notna() & (state != dominant_state)

    n_fixed = int(to_fix.sum())
    if n_fixed:
        df.loc[to_fix, 'contributor_state'] = dominant_state[to_fix]
    return n_fixed


def _fix_state_zip_mismatches(df: pd.DataFrame) -> dict:
    """Resolve ZIP/state conflicts by a 3-way vote with the city; returns fix counts."""
    # city agrees with the ZIP -> the state was the typo (fix state, keep ZIP);
    # city backs the state, or gives no signal -> the ZIP was the typo (null it).
    zcta_to_state = _load_zcta_to_state()
    if not zcta_to_state:
        return {'state_fixed': 0, 'zip_nulled': 0}
    zip3_to_state = _load_zip3_to_state()

    zips   = df['contributor_zip'].fillna('')
    states = df['contributor_state'].fillna('')
    city = df['contributor_city'].fillna('')

    zip_state_exact = zips.map(zcta_to_state).fillna('')
    # prefix fallback only fills where the exact ZCTA lookup found nothing
    zip3_state = (zips.str[:3].map(zip3_to_state).fillna('')
                  if zip3_to_state else pd.Series('', index=df.index))
    zip_state = zip_state_exact.where(zip_state_exact != '', zip3_state)

    conflict = (zips != '') & (states != '') & (zip_state != '') & (zip_state != states)
    if not conflict.any():
        return {'state_fixed': 0, 'zip_nulled': 0}

    # learn each city's dominant state from trusted rows, where the EXACT
    # ZIP-implied state already equals the recorded one (exact only, so the
    # prefix fallback never pollutes the city-to-state signal)
    trusted = (zips != '') & (states != '') & (city != '') & (zip_state_exact == states)
    city_to_state = (
        df.loc[trusted]
          .groupby('contributor_city')['contributor_state']
          .agg(lambda values: values.value_counts().idxmax())
          .to_dict()
    )
    city_state = city.map(city_to_state).fillna('')

    fix_state = conflict & (city_state != '') & (city_state == zip_state)
    null_zip = conflict & ~fix_state

    n_state = int(fix_state.sum())
    n_zip   = int(null_zip.sum())

    if n_state:
        df.loc[fix_state, 'contributor_state'] = zip_state[fix_state]
    if n_zip:
        df.loc[null_zip, 'contributor_zip'] = np.nan

    return {'state_fixed': n_state, 'zip_nulled': n_zip}
