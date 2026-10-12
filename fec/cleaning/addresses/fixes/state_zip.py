"""State/ZIP fixes backed by the ZCTA crosswalk."""
from __future__ import annotations

from functools import lru_cache
import numpy as np
import pandas as pd

from fec.env import RULES_DIR

_DATA_DIR = RULES_DIR


# load ZCTA and state CSVs, or None
def _read_crosswalk() -> tuple[pd.DataFrame, dict] | None:
    zcta_path = _DATA_DIR / 'zcta_state_rel.csv'
    states_path = _DATA_DIR / 'us_states.csv'
    if not zcta_path.exists() or not states_path.exists():
        return None
    zcta_df = pd.read_csv(zcta_path, dtype=str)
    states_df = pd.read_csv(states_path, dtype=str)
    fips_to_code = dict(zip(states_df['state_fips'], states_df['code']))
    return zcta_df, fips_to_code


# build the ZCTA5-to-state lookup, caching the result
@lru_cache(maxsize=1)
def _load_zcta_to_state() -> dict[str, str]:
    crosswalk = _read_crosswalk()
    if crosswalk is None:
        return {}
    zcta_df, fips_to_code = crosswalk
    state_codes = zcta_df['state_fips'].map(fips_to_code).fillna('')
    return dict(zip(zcta_df['zcta5'], state_codes))


# build ZIP3-to-state fallback for non-ZCTA ZIPs
@lru_cache(maxsize=1)
def _load_zip3_to_state() -> dict[str, str]:
    crosswalk = _read_crosswalk()
    if crosswalk is None:
        return {}
    zcta_df, fips_to_code = crosswalk

    zcta = zcta_df.assign(
        code=zcta_df['state_fips'].map(fips_to_code),
        z3=zcta_df['zcta5'].str[:3],
    ).dropna(subset=['code'])

    # border prefixes stay unresolved, not guessed
    out: dict[str, str] = {}
    for z3, codes in zcta.groupby('z3')['code']:
        value_counts = codes.value_counts()
        if value_counts.iloc[0] / value_counts.sum() >= 0.8:
            out[z3] = value_counts.index[0]

    # USPS prefixes are contiguous; neighbours fill gaps
    for prefix_num in range(1, 999):
        prefix = f"{prefix_num:03d}"
        if prefix in out:
            continue
        below, above = out.get(f"{prefix_num-1:03d}"), out.get(f"{prefix_num+1:03d}")
        if below and above and below == above:
            out[prefix] = below
    return out


# ZIP5 state from crosswalk, else ZIP3 prefix
def zip_state(zip5: str) -> str:
    zip5 = str(zip5 or '')
    if len(zip5) != 5 or not zip5.isdigit():
        return ''
    return _load_zcta_to_state().get(zip5) or _load_zip3_to_state().get(zip5[:3], '')


# true for area ZIPs, not PO-box-only
def is_zcta(zip5: str) -> bool:
    return str(zip5 or '') in _load_zcta_to_state()


# fix state typos from person's own filings
def fix_impossible_city_states(df: pd.DataFrame) -> int:
    # person plus city scope keeps this safe
    first = df['contributor_first_name'].fillna('')
    last  = df['contributor_last_name'].fillna('')
    city  = df['contributor_city'].fillna('')
    state = df['contributor_state'].fillna('')

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

    states_per_group = counts.groupby(level='pc').size()
    conflict_keys = states_per_group[states_per_group > 1].index
    if len(conflict_keys) == 0:
        return 0

    # ties are ambiguous, left alone
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


# resolve state/ZIP conflicts by city vote
def fix_state_zip_mismatches(df: pd.DataFrame) -> dict:
    # ZIP wins only when city backs it
    zcta_to_state = _load_zcta_to_state()
    if not zcta_to_state:
        return {'state_fixed': 0, 'zip_nulled': 0}
    zip3_to_state = _load_zip3_to_state()

    zips   = df['contributor_zip'].fillna('')
    states = df['contributor_state'].fillna('')
    city = df['contributor_city'].fillna('')

    zip_state_exact = zips.map(zcta_to_state).fillna('')
    zip3_state = (zips.str[:3].map(zip3_to_state).fillna('')
                  if zip3_to_state else pd.Series('', index=df.index))
    zip_state = zip_state_exact.where(zip_state_exact != '', zip3_state)

    conflict = (zips != '') & (states != '') & (zip_state != '') & (zip_state != states)
    if not conflict.any():
        return {'state_fixed': 0, 'zip_nulled': 0}

    # exact matches only, prefix fallback never pollutes
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
