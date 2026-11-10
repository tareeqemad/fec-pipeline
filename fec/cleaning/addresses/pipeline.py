"""Address cleaning stage: the standard form, then the reviewed and per-donor fixes."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.address_rules import apply_verified_address_fixes
from fec.cleaning.addresses.donor_addresses import ALIGN_STEPS
from fec.cleaning.addresses.standard_form import clear_own_name_street, standardize_addresses
from fec.cleaning.addresses.zip_codes import clean_zips


def clean_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """USPS standard form, own-name streets cleared, ZIPs checked, rules and alignment applied."""
    standardize_addresses(df)
    clear_own_name_street(df)
    clean_zips(df)
    apply_verified_address_fixes(df, city_only=False, warn_unused=True)
    for align in ALIGN_STEPS:
        align(df)
    apply_verified_address_fixes(df, city_only=True, warn_unused=True)
    return df
