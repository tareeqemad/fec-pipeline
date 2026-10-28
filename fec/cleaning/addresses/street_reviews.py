"""Find street_1/street_2 values that need a person's review."""
import re
from difflib import SequenceMatcher
from itertools import combinations

import numpy as np
import pandas as pd

from fec.cleaning.addresses.fixes.safe_text import _CO_RE, is_state_zip_fragment
from fec.config.geography import US_STATES

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

CARE_OF_WITH_STREET_REASON = "care-of name in street_1, street in street_2 (keep both as filed)"

# Leading PO box marker: "PO BOX 123", "P.O. BOX 45".
_PO_BOX_RE = re.compile(
    r"^P\.?\s*O\.?\s*BOX|^POST OFFICE BOX",
    re.IGNORECASE,
)


# leading private mailbox marker: "PMB 123", "PMB#45"
_PMB_RE = re.compile(r"^PMB\s*#?\s*\d", re.IGNORECASE)


# Street-type tokens; a street without any is likely an entity.
_STREET_TYPES = (
    "ST AVE RD BLVD DR LN CT CIR PL PKWY HWY TER TPKE EXPY SQ WAY TRL XING JCT "
    "PLZ PLAZA LOOP PATH RUN BEND PASS WALK ROW ALY PIKE RTE BROADWAY PARK MALL "
    "CTR HTS RIDGE POINTE COMMONS GARDENS CROSSING TRAIL TURNPIKE PARKWAY "
    "STREET AVENUE ROAD BOULEVARD DRIVE LANE COURT CIRCLE PLACE HIGHWAY TERRACE "
    "APT STE UNIT FL"
).split()


# Any street-type or unit word as a token.
_STREET_TYPE_RE = re.compile(r"\b(?:" + "|".join(_STREET_TYPES) + r")\b")


# A street_2 unit keyword without a number: dropped and flagged.
_UNIT_NO_NUM = re.compile(
    r"^(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLR|FLOOR|PH|RM|ROOM|BLDG|OFFICE|OFF|DEPT|#)\.?$",
    re.IGNORECASE,
)


_REPORT_COLUMNS = (
    "sub_id",
    "donor_key",
    "entity_type",
    "contributor_name",
    S1,
    S2,
    CITY,
    STATE,
    ZIP,
)


# slice df to report columns, tag rows with a reason
def _df_subset(df: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    keep = [column for column in _REPORT_COLUMNS if column in df.columns]
    out = df.loc[mask, keep].copy()
    out.insert(0, "review_reason", reason)
    return out


# One row per similar street spelling, by donor or name.
def _near_street_variant_review(df: pd.DataFrame) -> pd.DataFrame:
    person = "donor_key" if "donor_key" in df.columns else "contributor_name"
    needed = {person, S1, CITY, STATE, ZIP}
    if not needed.issubset(df.columns):
        return pd.DataFrame()

    work = df.copy()
    if "entity_type" in work.columns:
        work = work[work["entity_type"] == "INDIVIDUAL"]

    work = work[
        work[person].fillna("").astype(str).ne("")
        & work[S1].fillna("").str.match(r"^\d+")
    ].copy()
    if work.empty:
        return pd.DataFrame()

    work["_house"] = work[S1].str.extract(r"^(\d+[A-Z]?)\b", expand=False)
    groups = [person, CITY, STATE, ZIP, "_house"]
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


# append a subset report if any rows match
def _append_report(reports, df, mask, reason) -> None:
    if mask.any():
        reports.append(_df_subset(df, mask, reason))


# detect street_2 values that are a truncated city prefix
def _is_city_prefix(s2_upper: pd.Series, city: pd.Series) -> pd.Series:
    """'ATLA' for ATLANTA: a city cut off by FEC's limit."""
    core = s2_upper.str.lstrip("#").str.strip()
    city_upper = city.fillna("").astype(str).str.strip().str.upper()
    return (
        core.str.len().between(3, 6)
        & core.str.isalpha()
        & ~core.str.match(_UNIT_NO_NUM)
        & (city_upper != "")
        & (core != city_upper)
        & pd.Series([c != "" and u.startswith(c) for c, u in zip(core, city_upper)], index=s2_upper.index)
    )


# find and empty clearly bad street_2 values, reporting each one
def _review_street2(df: pd.DataFrame, s2: pd.Series) -> tuple[list, int]:
    reports = []
    s2_upper = s2.str.strip().str.upper()
    incomplete = s2_upper.str.match(_UNIT_NO_NUM)
    tokens = s2_upper.str.split()
    is_state_abbrev = (
        tokens.str.len().eq(2)
        & tokens.str[1].isin(US_STATES)
        & ~s2_upper.str.contains(r"\d")
        & ~s2_upper.str.match(_CO_RE)
    )
    is_fragment = s2_upper.map(is_state_zip_fragment) | _is_city_prefix(s2_upper, df["contributor_city"])
    bad2 = incomplete | is_state_abbrev | is_fragment
    _append_report(reports, df, incomplete, "street_2 unit keyword without a number")
    _append_report(
        reports,
        df,
        is_state_abbrev,
        "street_2 looks like a state/city abbreviation",
    )
    _append_report(reports, df, is_fragment & ~is_state_abbrev, "street_2 is a state/ZIP/city fragment of the truncated street")
    if bad2.any():
        df.loc[bad2, S2] = np.nan
    return reports, int(bad2.sum())


# street_1 problems needing a person's review
def _review_street1(
    df: pd.DataFrame,
    s1: pd.Series,
    state: pd.Series,
) -> list:
    review = []
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
    # C/O in street_1 with the street in street_2.
    street_in_s2 = df[S2].fillna("").astype(str).str.match(r"^\s*\d") if S2 in df.columns else False
    entity = (
        has1 & ~starts_num & ~is_pobox & ~is_pmb & ~has_type & ~descriptive & ~care_of
    )
    bad_state = has1 & (~state.str.upper().isin(US_STATES))

    _append_report(review, df, descriptive, "descriptive / intersection address")
    _append_report(review, df, care_of & ~street_in_s2, "care-of name (no street to recover)")
    _append_report(review, df, care_of & street_in_s2, CARE_OF_WITH_STREET_REASON)
    _append_report(review, df, entity, "entity / non-address in street_1")
    _append_report(review, df, bad_state, "missing / non-US state (out of schema)")
    return review
