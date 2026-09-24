"""Address hygiene beside clean_streets: safe mechanical text fixes are applied; anything needing a guess goes to the review reports.

Two review files, both regenerated every clean run (nothing reads them back):

* address_manual_review.csv - one row per item, with a ``status`` column:
    ``open``        needs a human (near-duplicate street spellings, care-of or
                    entity names in street_1, descriptive addresses, non-US state);
    ``auto_fixed``  a street_2 the address_review step already emptied (a unit
                    word with no number, a state/city abbreviation, a state/ZIP/city
                    fragment). Listed after the open items so the removal stays
                    visible; the row shows the address as the step saw it, with
                    street_2 as it was before it was emptied.
* address_regeocode_suspects.csv - rows whose point can only be approximate:
  missing street_1 or ZIP, a truncated city, and PO Box / PMB mail addresses of
  INDIVIDUAL donors. Committee and organization mailboxes are not listed: they
  are normal for a filer's office address and the geocoder already places every
  PO box at its ZIP/city point.

In the clean pipeline the street_2 fix runs inside the address stage, and the files
are written after the donor stage (``build_review_queues`` + ``write_review_queues``)
so they reflect the final rows and carry donor_key; foreign filings, kept exactly
as filed, are left out.
"""

import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from fec.cleaning.pipeline.address_fixes.safe_text import _CO_RE, is_state_zip_fragment
from fec.config.geography import US_STATES

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

MANUAL_REVIEW_CSV = "address_manual_review.csv"
REGEOCODE_CSV = "address_regeocode_suspects.csv"
OPEN, AUTO_FIXED = "open", "auto_fixed"

PO_BOX_REASON = "PO Box (no precise physical point)"
PMB_REASON = "PMB private mailbox (no precise physical point)"

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


def _df_subset(df: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    keep = [column for column in _REPORT_COLUMNS if column in df.columns]
    out = df.loc[mask, keep].copy()
    out.insert(0, "review_reason", reason)
    return out


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


def _append_report(reports, df, mask, reason) -> None:
    if mask.any():
        reports.append(_df_subset(df, mask, reason))


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


def _combine_reports(reports: list) -> pd.DataFrame:
    return pd.concat(reports, ignore_index=True) if reports else pd.DataFrame()


def _with_status(report: pd.DataFrame, status: str) -> pd.DataFrame:
    if report.empty:
        return report
    report = report.copy()
    report.insert(1, "status", status)
    return report


def apply_street2_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Empty clearly bad street_2 values (the only edit of the address_review step).

    Returns the frame, the emptied rows as they were before the edit (reason,
    sub_id, address with the original street_2) and the number of rows emptied.
    """
    s2 = df[S2].fillna("").astype(str)
    reports, emptied = _review_street2(df, s2)
    return df, _combine_reports(reports), emptied


def _still_emptied(auto_fixed: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Drop auto-fixed rows whose street_2 a later step put back (a foreign filing restored as filed)."""
    if auto_fixed.empty or "sub_id" not in auto_fixed.columns or "sub_id" not in df.columns:
        return auto_fixed
    final_s2 = df.drop_duplicates("sub_id").set_index("sub_id")[S2]
    final_s2 = final_s2.fillna("").astype(str).str.strip()
    now = auto_fixed["sub_id"].map(final_s2)
    before = auto_fixed[S2].fillna("").astype(str).str.strip()
    return auto_fixed[now.notna() & now.ne(before)]


def _add_donor_key(report: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Attach the final donor_key (and entity_type) to rows captured before donors were identified."""
    if report.empty or "sub_id" not in report.columns or "sub_id" not in df.columns:
        return report
    final = df.drop_duplicates("sub_id").set_index("sub_id")
    report = report.copy()
    for column in ("donor_key", "entity_type"):
        if column in final.columns and column not in report.columns:
            report[column] = report["sub_id"].map(final[column])
    order = ["review_reason"] + [c for c in _REPORT_COLUMNS if c in report.columns]
    return report[order + [c for c in report.columns if c not in order]]


def build_review_queues(
    df: pd.DataFrame,
    auto_fixed: pd.DataFrame | None = None,
    exclude_sub_ids=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the manual-review and re-geocode queues from ``df``; never edits it.

    ``auto_fixed`` holds the street_2 values the address_review step emptied
    (from ``apply_street2_fixes``); they are appended with status auto_fixed.
    ``exclude_sub_ids`` (foreign filings kept as filed) are left out.
    """
    if exclude_sub_ids is not None and len(exclude_sub_ids) and "sub_id" in df.columns:
        df = df[~df["sub_id"].isin(set(exclude_sub_ids))]
        if auto_fixed is not None and not auto_fixed.empty and "sub_id" in auto_fixed.columns:
            auto_fixed = auto_fixed[~auto_fixed["sub_id"].isin(set(exclude_sub_ids))]

    s1 = df[S1].fillna("").astype(str)
    city = df[CITY].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)
    zips = df[ZIP].fillna("").astype(str)

    review = []
    spelling_variants = _near_street_variant_review(df)
    if not spelling_variants.empty:
        review.append(spelling_variants)
    street1_review, regeocode = _review_street1(df, s1, city, state, zips)
    review.extend(street1_review)

    queue = [_with_status(_combine_reports(review), OPEN)]
    if auto_fixed is not None and not auto_fixed.empty:
        fixed = _add_donor_key(_still_emptied(auto_fixed, df), df)
        queue.append(_with_status(fixed, AUTO_FIXED))
    review_df = _combine_reports([part for part in queue if not part.empty])
    return review_df, _combine_reports(regeocode)


def write_review_queues(out_dir, review_df: pd.DataFrame, regeocode_df: pd.DataFrame) -> None:
    out_path = Path(out_dir)
    review_df.to_csv(out_path / MANUAL_REVIEW_CSV, index=False, na_rep="")
    regeocode_df.to_csv(out_path / REGEOCODE_CSV, index=False, na_rep="")


def queue_counts(review_df: pd.DataFrame, regeocode_df: pd.DataFrame) -> dict:
    status = review_df["status"] if "status" in review_df.columns else pd.Series(dtype=object)
    return {
        "manual_review": int(status.eq(OPEN).sum()),
        "auto_fixed": int(status.eq(AUTO_FIXED).sum()),
        "regeocode": len(regeocode_df),
    }


def build_address_reports(
    df: pd.DataFrame, out_dir: str | None
) -> tuple[pd.DataFrame, dict]:
    """Empty clearly bad street_2 values, then flag questionable addresses (one-shot form)."""
    df, auto_fixed, street2_emptied = apply_street2_fixes(df)
    review_df, regeocode_df = build_review_queues(df, auto_fixed)
    counts = {**queue_counts(review_df, regeocode_df), "street2_emptied": street2_emptied}

    if out_dir:
        write_review_queues(out_dir, review_df, regeocode_df)

    return df, counts
