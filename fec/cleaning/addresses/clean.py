"""Address cleaning stage: the standard form, then the reviewed and per-donor fixes."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.fixes.same_street import _recover_address_from_same_street
from fec.cleaning.addresses.fixes.state_zip import (
    _fix_impossible_city_states,
    _fix_state_zip_mismatches,
)
from fec.cleaning.addresses.fixes.unify import (
    _unify_street_spacing,
    _unify_street_spellings,
    _unify_street_types,
    _unify_unit_designators,
)
from fec.cleaning.addresses.fixes.verified import apply_verified_address_fixes
from fec.cleaning.addresses.standardize import clear_own_name_street, standardize_addresses
from fec.cleaning.addresses.zips import clean_zips

# Per-donor alignment passes, in order.
_ALIGN_STEPS = (
    _fix_impossible_city_states,
    _fix_state_zip_mismatches,
    _unify_street_spellings,
    _unify_street_types,
    _unify_street_spacing,
    _recover_address_from_same_street,
    _unify_unit_designators,
)


def clean_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """USPS standard form, own-name streets cleared, ZIPs checked, rules and alignment applied."""
    standardize_addresses(df)
    clear_own_name_street(df)
    clean_zips(df)
    apply_verified_address_fixes(df, city_only=False, warn_unused=True)
    for align in _ALIGN_STEPS:
        align(df)
    apply_verified_address_fixes(df, city_only=True, warn_unused=True)
    return df
