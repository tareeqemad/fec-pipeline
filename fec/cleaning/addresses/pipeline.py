"""Clean the addresses of every filing.

Steps, in order (clean_addresses):
    1. standardize_addresses   USPS standard form of street, unit, city, state and ZIP
    2. clear_own_name_street   a street box that holds the filer's own name
    3. _clean_zips             five-digit US ZIP codes
    4. apply_address_rules     reviewed corrections (data/rules/address_rules.csv)
    5. ALIGN_STEPS             one spelling per donor and place (donor_addresses.py)
    6. apply_address_rules     the city-only rules again, after the alignment
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fec.cleaning.addresses.address_rules import apply_address_rules
from fec.cleaning.addresses.donor_addresses import ALIGN_STEPS
from fec.cleaning.addresses.standard_form import clear_own_name_street, standardize_addresses


def clean_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """USPS standard form, own-name streets cleared, ZIPs checked, rules and alignment applied."""
    standardize_addresses(df)
    clear_own_name_street(df)
    _clean_zips(df)
    apply_address_rules(df, city_only=False, warn_unused=True)
    for align in ALIGN_STEPS:
        align(df)
    apply_address_rules(df, city_only=True, warn_unused=True)
    return df


def _clean_zips(df: pd.DataFrame) -> None:
    digits = df["contributor_zip"].astype(str).str.strip().str.replace(r"[^\d]", "", regex=True)
    five = digits.str.zfill(5).str[:5].where(digits.str.len() <= 5, digits.str.zfill(9).str[:5])
    # ZIPs below 00501 are unassigned typos.
    five[~five.str.match(r"^\d{5}$", na=False) | (five < "00501")] = np.nan
    df["contributor_zip"] = five
