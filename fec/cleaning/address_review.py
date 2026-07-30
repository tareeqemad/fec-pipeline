"""Address hygiene beside clean_streets: safe mechanical text fixes are applied; anything needing a guess goes to the review reports."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config.geography import US_STATES

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"

# Bare trailing number -> unit. Excludes HWY/RTE so route numbers
# ("HWY 9", "RTE 1") aren't mistaken for unit numbers.
_SPLIT_TYPES = ("AVE", "BLVD", "ST", "RD", "DR", "LN", "CT", "WAY", "PL",
                "TER", "CIR", "PKWY", "SQ", "TRL", "PLZ", "LOOP", "BROADWAY")
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
_TYPE_NUM_RE = re.compile(r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$")

# "C/O <name>" forwarding prefix; the real street follows it
_CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.I)

# PO BOX / PMB: valid mail addresses with no precise physical point
_PO_BOX_RE = re.compile(r"^P\.?\s*O\.?\s*BOX|^POST OFFICE BOX", re.I)
_PMB_RE = re.compile(r"^PMB\s*#?\s*\d", re.I)

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
_UNIT_NO_NUM = re.compile(r"^(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLR|FLOOR|PH|RM|ROOM|BLDG|OFFICE|OFF|DEPT|#)\.?$", re.I)

# partial / truncated city tokens that are normally part of a longer name
_PARTIAL_CITY = {"SANTA", "SAN", "LAKE", "FORT", "MOUNT", "MT", "NEW", "PORT",
                 "LOS", "LAS", "EL", "WEST", "EAST", "NORTH", "SOUTH"}


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


def _strip_care_of(s):
    """Recover the street after a 'C/O' prefix; a name-only C/O with no house number is left for the report."""
    if pd.isna(s):
        return s
    text = str(s)
    if not _CO_RE.match(text):
        return text
    rest = _CO_RE.sub("", text).strip()
    match = re.search(r"(\d+\s+\S.*)$", rest)   # real address = from the first house number
    return match.group(1).strip() if match else text


def apply_safe_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Deterministic text fixes, applied; runs right after clean_streets so the cleaned values feed the per-donor dedup/recovery downstream."""
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}

    before = df[S1].copy()
    was_care_of = before.fillna("").astype(str).str.match(_CO_RE)
    df[S1] = df[S1].map(_strip_care_of)
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    still_care_of = df[S1].fillna("").astype(str).str.match(_CO_RE)
    counts["care_of"] = int((was_care_of & ~still_care_of).sum())

    # trailing comma / whitespace on any field (city/state can carry a stray "TEMPLE,")
    for column in (S1, CITY, STATE):
        mask = df[column].notna() & (df[column].astype(str).str.strip() != "")
        df.loc[mask, column] = df.loc[mask, column].astype(str).str.replace(r"[,\s]+$", "", regex=True).str.strip()
    counts["house_number"] = int((before.fillna("") != df[S1].fillna("")).sum())

    # split a trailing floor / bare unit-number into an empty street_2
    df[S2] = df[S2].astype(object)  # an all-NaN column is float64, which rejects strings
    s1 = df[S1].fillna("").astype(str)
    s2_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
    floor = s1.str.extract(_ORD_FLOOR_RE)   # "... 28TH FLOOR" -> unit kept as-is
    num = s1.str.extract(_TYPE_NUM_RE)      # "... DR 601"     -> unit prefixed "#"
    is_floor = floor[0].notna()
    base = floor[0].where(is_floor, num[0])
    unit = floor[1].where(is_floor, "# " + num[1].fillna(""))
    apply_mask = s2_blank & base.notna()
    if apply_mask.any():
        df.loc[apply_mask, S1] = base[apply_mask].str.strip()
        df.loc[apply_mask, S2] = unit[apply_mask].str.strip()
        counts["unit_split"] = int(apply_mask.sum())

    return df, counts


def _df_subset(df: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    keep = [column for column in ("sub_id", "donor_key", S1, S2, CITY, STATE, ZIP) if column in df.columns]
    out = df.loc[mask, keep].copy()
    out.insert(0, "review_reason", reason)
    return out


def build_address_reports(df: pd.DataFrame, out_dir: str | None) -> tuple[pd.DataFrame, dict]:
    """Detection-only pass (except that a clearly-bad street_2 is emptied); writes address_manual_review.csv and address_regeocode_suspects.csv, returns (df, counts)."""
    counts = {"manual_review": 0, "regeocode": 0, "street2_emptied": 0}

    s1 = df[S1].fillna("").astype(str)
    s2 = df[S2].fillna("").astype(str)
    city = df[CITY].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)
    zips = df[ZIP].fillna("").astype(str)

    review, regeocode = [], []

    # a street_2 that is a bare unit keyword ("STE") or a state/city
    # abbreviation ("POTO MD") is certainly wrong: empty it and flag the row
    s2_upper = s2.str.strip().str.upper()
    incomplete = s2_upper.str.match(_UNIT_NO_NUM)
    tokens = s2_upper.str.split()
    is_state_abbrev = (tokens.str.len().eq(2)
                       & tokens.str[1].isin(US_STATES)
                       & ~s2_upper.str.contains(r"\d"))
    bad2 = incomplete | is_state_abbrev
    if bad2.any():
        review.append(_df_subset(df, incomplete, "street_2 unit keyword without a number"))
        review.append(_df_subset(df, is_state_abbrev, "street_2 looks like a state/city abbreviation"))
        df.loc[bad2, S2] = np.nan
        counts["street2_emptied"] = int(bad2.sum())

    # empty street_1: incomplete record
    empty1 = (s1.str.strip() == "")
    if empty1.any():
        regeocode.append(_df_subset(df, empty1, "missing street_1 (incomplete record)"))

    has1 = ~empty1
    starts_num = s1.str.match(r"^\d")
    is_pobox = s1.str.match(_PO_BOX_RE)
    # PMB with no street in front behaves like a PO box: valid mail address, ZIP-level geocode only
    is_pmb = s1.str.match(_PMB_RE)
    has_type = s1.str.contains(_STREET_TYPE_RE)

    # PO BOX / PMB are valid but have no precise point: regeocode, not manual review
    if is_pobox.any():
        regeocode.append(_df_subset(df, is_pobox, "PO Box (no precise physical point)"))
    if is_pmb.any():
        regeocode.append(_df_subset(df, is_pmb, "PMB private mailbox (no precise physical point)"))

    # descriptive / intersection addresses (parens, ' AND ', FORMERLY)
    descriptive = s1.str.contains(r"\(") | s1.str.contains(r"\bAND\b") | s1.str.contains(r"FORMERLY")
    if descriptive.any():
        review.append(_df_subset(df, descriptive, "descriptive / intersection address"))

    # care-of name with no street to recover (C/O lines that had an address were recovered upstream)
    care_of = s1.str.match(_CO_RE)
    if care_of.any():
        review.append(_df_subset(df, care_of, "care-of name (no street to recover)"))

    # entity name instead of a street address
    entity = has1 & ~starts_num & ~is_pobox & ~is_pmb & ~has_type & ~descriptive & ~care_of
    if entity.any():
        review.append(_df_subset(df, entity, "entity / non-address in street_1"))

    # out-of-schema: no US state
    bad_state = has1 & (~state.str.upper().isin(US_STATES))
    if bad_state.any():
        review.append(_df_subset(df, bad_state, "missing / non-US state (out of schema)"))

    # empty ZIP, re-extract later (coords come from the later geocode step)
    empty_zip = (zips.str.strip() == "") & has1
    if empty_zip.any():
        regeocode.append(_df_subset(df, empty_zip, "missing ZIP (re-extract later)"))

    # partial / truncated city (e.g. lone 'SANTA')
    partial = has1 & city.str.upper().str.strip().isin(_PARTIAL_CITY)
    if partial.any():
        regeocode.append(_df_subset(df, partial, "partial / truncated city"))

    review_df = pd.concat(review, ignore_index=True) if review else pd.DataFrame()
    regeocode_df = pd.concat(regeocode, ignore_index=True) if regeocode else pd.DataFrame()
    counts["manual_review"] = len(review_df)
    counts["regeocode"] = len(regeocode_df)

    if out_dir:
        out_path = Path(out_dir)
        review_df.to_csv(out_path / "address_manual_review.csv", index=False, na_rep="")
        regeocode_df.to_csv(out_path / "address_regeocode_suspects.csv", index=False, na_rep="")

    return df, counts
