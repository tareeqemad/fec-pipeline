"""Address hygiene beside clean_streets: safe mechanical text fixes are applied; anything needing a guess goes to the review reports."""

import csv
import re
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config.geography import US_STATES
from fec.env import ADDRESS_RULES_CSV

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

# Bare trailing number -> unit. Excludes HWY/RTE so route numbers
# ("HWY 9", "RTE 1") aren't mistaken for unit numbers.
_SPLIT_TYPES = (
    "AVE",
    "BLVD",
    "ST",
    "RD",
    "DR",
    "LN",
    "CT",
    "WAY",
    "PL",
    "TER",
    "CIR",
    "PKWY",
    "SQ",
    "TRL",
    "PLZ",
    "LOOP",
    "BROADWAY",
)
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
_TYPE_NUM_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$"
)
_TYPE_NUM_DIR_RE = re.compile(
    r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+"
    r"(\d{1,5}\s+(?:NE|NW|SE|SW|N|S|E|W))$"
)
_REPEATED_ADDRESS_START_RE = re.compile(
    r"^(?P<start>\d+[A-Z]?(?:\s+(?:NE|NW|SE|SW|N|S|E|W))?)\s+"
    r"(?P<body>.+\b(?:" + "|".join(_SPLIT_TYPES) + r")\b)\s+(?P=start)$"
)

# "C/O <name>" forwarding prefix; the real street follows it
_CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.IGNORECASE)

# PO BOX / PMB: valid mail addresses with no precise physical point
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


def _fix_house_number(s):
    """Strip a leading non-alphanumeric char, then drop leading zeros from the house number (02393 -> 2393) unless all zeros."""
    if pd.isna(s):
        return s
    text = re.sub(r"^[^A-Z0-9]+", "", str(s)).strip()
    match = re.match(r"^(\d+)(\b.*)$", text)
    if match and set(match.group(1)) != {"0"}:
        text = (match.group(1).lstrip("0") or "0") + match.group(2)
    return text if text else np.nan


def _collapse_dup_words(s):
    """Collapse an adjacent exactly-equal duplicate word: 'E E' -> 'E'."""
    if pd.isna(s):
        return s
    out = []
    for word in str(s).split():
        if not out or out[-1] != word:
            out.append(word)
    return " ".join(out)


def _drop_repeated_address_start(s):
    """Drop a repeated house-number prefix from the end."""
    if pd.isna(s):
        return s
    match = _REPEATED_ADDRESS_START_RE.match(str(s))
    if not match:
        return s
    return f'{match.group("start")} {match.group("body")}'


def _strip_care_of(s):
    """Recover the street after a 'C/O' prefix; a name-only C/O with no house number is left for the report."""
    if pd.isna(s):
        return s
    text = str(s)
    if not _CO_RE.match(text):
        return text
    rest = _CO_RE.sub("", text).strip()
    match = re.search(
        r"(\d+\s+\S.*)$", rest
    )  # real address = from the first house number
    return match.group(1).strip() if match else text


def apply_safe_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Deterministic text fixes, applied; runs right after clean_streets so the cleaned values feed the per-donor dedup/recovery downstream."""
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}

    before = df[S1].copy()
    was_care_of = before.fillna("").astype(str).str.match(_CO_RE)
    df[S1] = df[S1].map(_strip_care_of)
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    df[S1] = df[S1].map(_drop_repeated_address_start)
    still_care_of = df[S1].fillna("").astype(str).str.match(_CO_RE)
    counts["care_of"] = int((was_care_of & ~still_care_of).sum())

    # trailing comma / whitespace on any field (city/state can carry a stray "TEMPLE,")
    for column in (S1, CITY, STATE):
        mask = df[column].notna() & (df[column].astype(str).str.strip() != "")
        df.loc[mask, column] = (
            df.loc[mask, column]
            .astype(str)
            .str.replace(r"[,\s]+$", "", regex=True)
            .str.strip()
        )
    counts["house_number"] = int((before.fillna("") != df[S1].fillna("")).sum())

    # split a trailing floor / bare unit-number into an empty street_2
    df[S2] = df[S2].astype(
        object
    )  # an all-NaN column is float64, which rejects strings
    s1 = df[S1].fillna("").astype(str)
    s2_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
    floor = s1.str.extract(_ORD_FLOOR_RE)  # "... 28TH FLOOR" -> unit kept as-is
    num = s1.str.extract(_TYPE_NUM_RE)  # "... DR 601"     -> unit prefixed "#"
    num_dir = s1.str.extract(_TYPE_NUM_DIR_RE)  # "... DR 601 N" -> "# 601 N"
    is_floor = floor[0].notna()
    is_num = num[0].notna()
    base = floor[0].where(is_floor, num[0].where(is_num, num_dir[0]))
    unit = floor[1].where(
        is_floor,
        ("# " + num[1].fillna("")).where(
            is_num,
            "# " + num_dir[1].fillna(""),
        ),
    )
    apply_mask = s2_blank & base.notna()
    if apply_mask.any():
        df.loc[apply_mask, S1] = base[apply_mask].str.strip()
        df.loc[apply_mask, S2] = unit[apply_mask].str.strip()
        counts["unit_split"] = int(apply_mask.sum())

    return df, counts


def apply_verified_address_fixes(df: pd.DataFrame) -> int:
    """Apply exact, externally verified address corrections."""
    changed = 0
    df[S2] = df[S2].astype(object)
    for (street, state, zipcode), fixed in _read_address_rules().items():
        fixed_street, fixed_unit, fixed_city, fixed_state, fixed_zip = fixed
        mask = (
            df[S1].fillna("").eq(street)
            & df[STATE].fillna("").eq(state)
            & df[ZIP].fillna("").eq(zipcode)
        )
        if not mask.any():
            continue
        row_changed = mask & df[S1].fillna("").ne(fixed_street)
        df.loc[mask, S1] = fixed_street
        if fixed_unit is not None:
            row_changed |= mask & df[S2].fillna("").ne(fixed_unit)
            df.loc[mask, S2] = fixed_unit
        if fixed_city is not None:
            row_changed |= mask & df[CITY].fillna("").ne(fixed_city)
            df.loc[mask, CITY] = fixed_city
        if fixed_state is not None:
            row_changed |= mask & df[STATE].fillna("").ne(fixed_state)
            df.loc[mask, STATE] = fixed_state
        if fixed_zip is not None:
            row_changed |= mask & df[ZIP].fillna("").ne(fixed_zip)
            df.loc[mask, ZIP] = fixed_zip
        changed += int(row_changed.sum())
    return changed


def _read_address_rules() -> dict[tuple[str, str, str], tuple]:
    """Read exact address corrections."""
    if not ADDRESS_RULES_CSV.exists():
        raise FileNotFoundError(f"Missing address rules: {ADDRESS_RULES_CSV}")

    rules = {}
    fixed_fields = ("street", "unit", "city", "state", "zip")
    with ADDRESS_RULES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            key = tuple(
                (row.get(field) or "").strip().upper()
                for field in ("match_street", "match_state", "match_zip")
            )
            fixed = tuple((row.get(field) or "").strip() or None for field in fixed_fields)
            source = (row.get("source") or "").strip()
            if not all(key) or not fixed[0] or not source:
                raise ValueError(f"Invalid address rule on row {number}")
            if key in rules:
                raise ValueError(f"Duplicate address rule on row {number}: {key}")
            rules[key] = fixed
    return rules


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
