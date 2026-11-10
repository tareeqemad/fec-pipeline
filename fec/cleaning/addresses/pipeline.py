"""Clean the addresses of every filing.

Steps, in order (clean_addresses):
    1. standardize_addresses   USPS standard form of street, unit, city, state and ZIP
    2. clear_own_name_street   a street box that holds the filer's own name
    3. clean_zips              five-digit US ZIP codes
    4. apply_address_rules     reviewed corrections (data/rules/address_rules.csv)
    5. ALIGN_STEPS             one spelling per donor and place (donor_addresses.py)
    6. apply_address_rules     the city-only rules again, after the alignment
"""

from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.address_rules import apply_address_rules
from fec.cleaning.addresses.donor_addresses import ALIGN_STEPS
from fec.cleaning.addresses.standard_form import clear_own_name_street, standardize_addresses
from fec.cleaning.addresses.zip_codes import clean_zips


def clean_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """USPS standard form, own-name streets cleared, ZIPs checked, rules and alignment applied."""
    standardize_addresses(df)
    clear_own_name_street(df)
    clean_zips(df)
    apply_address_rules(df, city_only=False, warn_unused=True)
    for align in ALIGN_STEPS:
        align(df)
    apply_address_rules(df, city_only=True, warn_unused=True)
    return df
