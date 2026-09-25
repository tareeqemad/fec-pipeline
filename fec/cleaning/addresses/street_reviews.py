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

PO_BOX_REASON = "PO Box (no precise physical point)"
PMB_REASON = "PMB private mailbox (no precise physical point)"

# leading PO box marker: "PO BOX 123", "P.O. BOX 45", "Post Office Box 9"
_PO_BOX_RE = re.compile(
    r"^P\.?\s*O\.?\s*BOX|^POST OFFICE BOX",
    re.IGNORECASE,
)


# leading private mailbox marker: "PMB 123", "PMB#45"
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


# any street-type/unit word as a token: "MAIN ST", "5TH AVE", "APT 4"
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


# flag near-duplicate street spellings for the same donor/location
def _near_street_variant_review(df: pd.DataFrame) -> pd.DataFrame:
    """Return one representative row per near street spelling; never edits data.

    Grouped by donor_key once donors are identified (one person under several
    name spellings is one group), by contributor_name before that.
    """
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
    """'ATLA' under ATLANTA, 'ENGL' under ENGLEWOOD: the start of the city name, cut off by the FEC field limit."""
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


# split street_1 problems into review and regeocode queues
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
    # a mailbox is only worth a note for a person: committee/organization
    # mail addresses are their normal filing address (see module docstring)
    if "entity_type" in df.columns:
        individual = df["entity_type"].eq("INDIVIDUAL")
    else:
        individual = pd.Series(True, index=df.index)

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
    _append_report(regeocode, df, is_pobox & individual, PO_BOX_REASON)
    _append_report(regeocode, df, is_pmb & individual, PMB_REASON)
    _append_report(regeocode, df, empty_zip, "missing ZIP (re-extract later)")
    _append_report(regeocode, df, partial, "partial / truncated city")

    _append_report(review, df, descriptive, "descriptive / intersection address")
    _append_report(review, df, care_of, "care-of name (no street to recover)")
    _append_report(review, df, entity, "entity / non-address in street_1")
    _append_report(review, df, bad_state, "missing / non-US state (out of schema)")
    return review, regeocode
