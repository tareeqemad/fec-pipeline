"""Address hygiene beside clean_streets: safe mechanical text fixes are applied; anything needing a guess goes to the review reports."""

import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from fec.cleaning.pipeline.address_fixes.safe_text import _CO_RE
from fec.config.geography import US_STATES

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

# Bare trailing number -> unit. Excludes HWY/RTE so route numbers
# ("HWY 9", "RTE 1") aren't mistaken for unit numbers.

_PO_BOX_RE = re.compile(
    r"^P\.?\s*O\.?\s*BOX|^POST OFFICE BOX",
    re.IGNORECASE,
)
_PMB_RE = re.compile(r"^PMB\s*#?\s*\d", re.IGNORECASE)

# Street-type tokens: a street_1 with none of these (and no leading house number
# or PO BOX) is likely an entity name, not a street. Deliberately BROADER than
# address_fixes._STREET_TYPE_RE: this runs on pre-normalized text, so it also
# carries the full words (STREET, AVENUE) and unit keywords (APT, STE). Do not merge.
_STREET_TYPES = (
    "ST AVE RD BLVD DR LN CT CIR PL PKWY HWY TER TPKE EXPY SQ WAY TRL XING JCT "
    "PLZ PLAZA LOOP PATH RUN BEND PASS WALK ROW ALY PIKE RTE BROADWAY PARK MALL "
    "CTR HTS RIDGE POINTE COMMONS GARDENS CROSSING TRAIL TURNPIKE PARKWAY "
    "STREET AVENUE ROAD BOULEVARD DRIVE LANE COURT CIRCLE PLACE HIGHWAY TERRACE "
    "APT STE UNIT FL"
).split()
_STREET_TYPE_RE = re.compile(r"\b(?:" + "|".join(_STREET_TYPES) + r")\b")

# street_2 that is a unit keyword with no number: incomplete (drop + flag)
_UNIT_NO_NUM = re.compile(
    r"^(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLR|FLOOR|PH|RM|ROOM|BLDG|OFFICE|OFF|DEPT|#)\.?$",
    re.IGNORECASE,
)

# partial / truncated city tokens that are normally part of a longer name
_PARTIAL_CITY = {
    "SANTA",
    "SAN",
    "LAKE",
    "FORT",
    "MOUNT",
    "MT",
    "NEW",
    "PORT",
    "LOS",
    "LAS",
    "EL",
    "WEST",
    "EAST",
    "NORTH",
    "SOUTH",
}


def _df_subset(df: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    keep = [
        column
        for column in (
            "sub_id",
            "donor_key",
            "contributor_name",
            S1,
            S2,
            CITY,
            STATE,
            ZIP,
        )
        if column in df.columns
    ]
    out = df.loc[mask, keep].copy()
    out.insert(0, "review_reason", reason)
    return out


def _near_street_variant_review(df: pd.DataFrame) -> pd.DataFrame:
    """Return one representative row per near street spelling; never edits data."""
    needed = {"contributor_name", S1, CITY, STATE, ZIP}
    if not needed.issubset(df.columns):
        return pd.DataFrame()

    work = df.copy()
    if "entity_type" in work.columns:
        work = work[work["entity_type"] == "INDIVIDUAL"]

    work = work[
        work["contributor_name"].fillna("").ne("")
        & work[S1].fillna("").str.match(r"^\d+")
    ].copy()
    if work.empty:
        return pd.DataFrame()

    work["_house"] = work[S1].str.extract(r"^(\d+[A-Z]?)\b", expand=False)
    groups = ["contributor_name", CITY, STATE, ZIP, "_house"]
    candidates = []

    for _, rows in work.groupby(groups, dropna=False, sort=False):
        streets = sorted(rows[S1].dropna().unique())
        if len(streets) < 2:
            continue

        near = set()
        for left, right in combinations(streets, 2):
            score = SequenceMatcher(None, left, right).ratio()
            if score >= 0.88:
                near.update((left, right))

        if near:
            candidates.append(rows[rows[S1].isin(near)].drop_duplicates(S1))

    if not candidates:
        return pd.DataFrame()

    variants = pd.concat(candidates, ignore_index=False)
    return _df_subset(
        variants,
        pd.Series(True, index=variants.index),
        "near-duplicate street spelling for same donor/location",
    )


def _append_report(reports, df, mask, reason) -> None:
    if mask.any():
        reports.append(_df_subset(df, mask, reason))


def _review_street2(df: pd.DataFrame, s2: pd.Series) -> tuple[list, int]:
    reports = []
    s2_upper = s2.str.strip().str.upper()
    incomplete = s2_upper.str.match(_UNIT_NO_NUM)
    tokens = s2_upper.str.split()
    is_state_abbrev = (
        tokens.str.len().eq(2)
        & tokens.str[1].isin(US_STATES)
        & ~s2_upper.str.contains(r"\d")
    )
    bad2 = incomplete | is_state_abbrev
    _append_report(reports, df, incomplete, "street_2 unit keyword without a number")
    _append_report(
        reports,
        df,
        is_state_abbrev,
        "street_2 looks like a state/city abbreviation",
    )
    if bad2.any():
        df.loc[bad2, S2] = np.nan
    return reports, int(bad2.sum())


def _review_street1(
    df: pd.DataFrame,
    s1: pd.Series,
    city: pd.Series,
    state: pd.Series,
    zips: pd.Series,
) -> tuple[list, list]:
    review, regeocode = [], []
    empty1 = s1.str.strip() == ""
    has1 = ~empty1
    starts_num = s1.str.match(r"^\d")
    is_pobox = s1.str.match(_PO_BOX_RE)
    is_pmb = s1.str.match(_PMB_RE)
    has_type = s1.str.contains(_STREET_TYPE_RE)

    descriptive = (
        s1.str.contains(r"\(")
        | s1.str.contains(r"\bAND\b")
        | s1.str.contains("FORMERLY", regex=False)
    )
    care_of = s1.str.match(_CO_RE)
    entity = (
        has1 & ~starts_num & ~is_pobox & ~is_pmb & ~has_type & ~descriptive & ~care_of
    )
    bad_state = has1 & (~state.str.upper().isin(US_STATES))
    empty_zip = (zips.str.strip() == "") & has1
    partial = has1 & city.str.upper().str.strip().isin(_PARTIAL_CITY)

    _append_report(regeocode, df, empty1, "missing street_1 (incomplete record)")
    _append_report(regeocode, df, is_pobox, "PO Box (no precise physical point)")
    _append_report(
        regeocode, df, is_pmb, "PMB private mailbox (no precise physical point)"
    )
    _append_report(regeocode, df, empty_zip, "missing ZIP (re-extract later)")
    _append_report(regeocode, df, partial, "partial / truncated city")

    _append_report(review, df, descriptive, "descriptive / intersection address")
    _append_report(review, df, care_of, "care-of name (no street to recover)")
    _append_report(review, df, entity, "entity / non-address in street_1")
    _append_report(review, df, bad_state, "missing / non-US state (out of schema)")
    return review, regeocode


def _combine_reports(reports: list) -> pd.DataFrame:
    return pd.concat(reports, ignore_index=True) if reports else pd.DataFrame()


def build_address_reports(
    df: pd.DataFrame, out_dir: str | None
) -> tuple[pd.DataFrame, dict]:
    """Flag questionable addresses and empty clearly bad street_2 values."""
    s1 = df[S1].fillna("").astype(str)
    s2 = df[S2].fillna("").astype(str)
    city = df[CITY].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)
    zips = df[ZIP].fillna("").astype(str)

    review = []
    spelling_variants = _near_street_variant_review(df)
    if not spelling_variants.empty:
        review.append(spelling_variants)

    street2_review, street2_emptied = _review_street2(df, s2)
    street1_review, regeocode = _review_street1(df, s1, city, state, zips)
    review.extend(street2_review)
    review.extend(street1_review)

    review_df = _combine_reports(review)
    regeocode_df = _combine_reports(regeocode)
    counts = {
        "manual_review": len(review_df),
        "regeocode": len(regeocode_df),
        "street2_emptied": street2_emptied,
    }

    if out_dir:
        out_path = Path(out_dir)
        review_df.to_csv(out_path / "address_manual_review.csv", index=False, na_rep="")
        regeocode_df.to_csv(
            out_path / "address_regeocode_suspects.csv", index=False, na_rep=""
        )

    return df, counts
