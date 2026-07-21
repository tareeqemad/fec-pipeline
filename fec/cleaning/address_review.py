"""
cleaning/address_review.py
==========================
Extra address hygiene that runs alongside ``addresses.clean_streets``.

Two halves, per the data owner's rule — *only safe, mechanical text cleanups
are applied automatically; anything that needs a guess or external data is left
untouched and written to a review report instead*:

    apply_safe_fixes(df)              — deterministic text-only fixes (applied)
    build_address_reports(df, dir)    — flag rows for a human / re-geocode,
                                        written to two CSVs (no value invented)

Everything stays UPPERCASE — the canonical case the rest of the pipeline uses,
so CAPS / Title-Case duplicates are already collapsed upstream.
"""
import re
from typing import Tuple

import numpy as np
import pandas as pd
from pathlib import Path

from fec.config.geography import US_STATES, US_STATE_BBOX as _STATE_BBOX

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"
LAT, LON = "latitude", "longitude"

# Street-type tokens — a street_1 with none of these (and no leading house
# number / PO BOX) is most likely an entity name, not a street address.
# NOTE: deliberately BROADER than address_fixes._STREET_TYPE_RE (the
# geocodability predicate): this one runs on pre-normalized text, so it also
# carries the full words (STREET, AVENUE…) and unit keywords (APT, STE…).
_STREET_TYPES = (
    "ST AVE RD BLVD DR LN CT CIR PL PKWY HWY TER TPKE EXPY SQ WAY TRL XING JCT "
    "PLZ PLAZA LOOP PATH RUN BEND PASS WALK ROW ALY PIKE RTE BROADWAY PARK MALL "
    "CTR HTS RIDGE POINTE COMMONS GARDENS CROSSING TRAIL TURNPIKE PARKWAY "
    "STREET AVENUE ROAD BOULEVARD DRIVE LANE COURT CIRCLE PLACE HIGHWAY TERRACE "
    "APT STE UNIT FL"
).split()
_STREET_TYPE_RE = re.compile(r"\b(?:" + "|".join(_STREET_TYPES) + r")\b")

# Bare trailing number → unit (rule 4 gap). EXCLUDE HWY/ROUTE so route numbers
# ("HWY 9", "RTE 1") aren't mistaken for unit numbers.
_SPLIT_TYPES = ("AVE", "BLVD", "ST", "RD", "DR", "LN", "CT", "WAY", "PL",
                "TER", "CIR", "PKWY", "SQ", "TRL", "PLZ", "LOOP", "BROADWAY")
_ORD_FLOOR_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH)\s+(?:FLOOR|FL))$")
_TYPE_NUM_RE = re.compile(r"^(.+\b(?:" + "|".join(_SPLIT_TYPES) + r"))\s+(\d{1,5}[A-Z]?)$")

# street_2 that is a unit keyword with no number → incomplete (drop + flag)
_UNIT_NO_NUM = re.compile(r"^(?:STE|SUITE|UNIT|APT|APARTMENT|FL|FLR|FLOOR|PH|RM|ROOM|BLDG|OFFICE|OFF|DEPT|#)\.?$", re.I)

# "C/O <name>" forwarding prefix — the real street follows it.
_CO_RE = re.compile(r"^C\s*/\s*O\b\.?\s*", re.I)


# ── Rule 3 — house number ─────────────────────────────────────────────
def _fix_house_number(s):
    """Strip a leading non-alphanumeric char, then drop leading zeros from the
    house number (02393 → 2393) unless the number is all zeros."""
    if pd.isna(s):
        return s
    t = re.sub(r"^[^A-Z0-9]+", "", str(s)).strip()
    m = re.match(r"^(\d+)(\b.*)$", t)
    if m and set(m.group(1)) != {"0"}:
        t = (m.group(1).lstrip("0") or "0") + m.group(2)
    return t if t else np.nan


def _collapse_dup_words(s):
    """Collapse an adjacent exactly-equal duplicate word: 'E E' → 'E'."""
    if pd.isna(s):
        return s
    out = []
    for w in str(s).split():
        if not out or out[-1] != w:
            out.append(w)
    return " ".join(out)


def _strip_care_of(s):
    """Recover the street that follows a 'C/O' forwarding prefix:
    'C/O 228 S WASHINGTON' → '228 S WASHINGTON',
    'C/O JFI 410 PARK AVE' → '410 PARK AVE'.
    A name-only 'C/O MOELIS' (no house number after it) is left untouched —
    there is nothing to recover, so the report flags it instead."""
    if pd.isna(s):
        return s
    t = str(s)
    if not _CO_RE.match(t):
        return t
    rest = _CO_RE.sub("", t).strip()
    m = re.search(r"(\d+\s+\S.*)$", rest)   # real address = from the first house number
    return m.group(1).strip() if m else t


def apply_safe_fixes(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """Deterministic text fixes — applied. Runs right after clean_streets so
    the cleaned values feed the per-donor dedup / recovery downstream."""
    counts = {"house_number": 0, "unit_split": 0, "care_of": 0}
    if S1 not in df.columns:
        return df, counts

    b = df[S1].copy()
    co_before = b.fillna("").astype(str).str.match(r"^C\s*/\s*O\b", case=False)
    df[S1] = df[S1].map(_strip_care_of)   # recover street from a "C/O ..." prefix
    df[S1] = df[S1].map(_fix_house_number)
    df[S1] = df[S1].map(_collapse_dup_words)
    co_after = df[S1].fillna("").astype(str).str.match(r"^C\s*/\s*O\b", case=False)
    counts["care_of"] = int((co_before & ~co_after).sum())
    # Trailing comma / whitespace on any field (street_1 is already comma-free,
    # but city/state can carry a stray "TEMPLE," → "TEMPLE").
    for c in (S1, CITY, STATE):
        if c in df.columns:
            m = df[c].notna() & (df[c].astype(str).str.strip() != "")
            df.loc[m, c] = df.loc[m, c].astype(str).str.replace(r"[,\s]+$", "", regex=True).str.strip()
    counts["house_number"] = int((b.fillna("") != df[S1].fillna("")).sum())

    # Rule 4 gap: split a trailing floor / bare unit-number into street_2 (empty).
    if S2 in df.columns:
        df[S2] = df[S2].astype(object)  # an all-NaN column is float64 — reject strings
        s1 = df[S1].fillna("").astype(str)
        s2_blank = df[S2].isna() | (df[S2].astype(str).str.strip() == "")
        f = s1.str.extract(_ORD_FLOOR_RE)   # "... 28TH FLOOR" → unit kept as-is
        n = s1.str.extract(_TYPE_NUM_RE)     # "... DR 601"     → unit prefixed "#"
        is_floor = f[0].notna()
        base = f[0].where(is_floor, n[0])
        unit = f[1].where(is_floor, "# " + n[1].fillna(""))
        do = s2_blank & base.notna()
        if do.any():
            df.loc[do, S1] = base[do].str.strip()
            df.loc[do, S2] = unit[do].str.strip()
            counts["unit_split"] = int(do.sum())

    return df, counts


# ── Detection / reports ───────────────────────────────────────────────

# Partial / truncated city tokens that are normally part of a longer name.
_PARTIAL_CITY = {"SANTA", "SAN", "LAKE", "FORT", "MOUNT", "MT", "NEW", "PORT",
                 "LOS", "LAS", "EL", "WEST", "EAST", "NORTH", "SOUTH"}


def _df_subset(df: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    keep = [c for c in ("sub_id", "donor_key", S1, S2, CITY, STATE, ZIP, LAT, LON) if c in df.columns]
    out = df.loc[mask, keep].copy()
    out.insert(0, "review_reason", reason)
    return out


def build_address_reports(df: pd.DataFrame, out_dir: str | None) -> Tuple[pd.DataFrame, dict]:
    """Detection-only pass (+ rule 5 / state-abbrev which empty a bad street_2).
    Writes data/address_manual_review.csv and data/address_regeocode_suspects.csv.
    Returns (df, counts)."""
    counts = {"manual_review": 0, "regeocode": 0, "street2_emptied": 0}
    if S1 not in df.columns:
        return df, counts

    s1 = df[S1].fillna("").astype(str)
    s2 = df[S2].fillna("").astype(str) if S2 in df.columns else pd.Series("", index=df.index)
    city = df[CITY].fillna("").astype(str) if CITY in df.columns else pd.Series("", index=df.index)
    state = df[STATE].fillna("").astype(str) if STATE in df.columns else pd.Series("", index=df.index)
    zipc = df[ZIP].fillna("").astype(str) if ZIP in df.columns else pd.Series("", index=df.index)

    review, regeo = [], []

    # ── Rule 5 + state-abbrev street_2: empty a bad street_2, flag it ──
    if S2 in df.columns:
        s2u = s2.str.strip().str.upper()
        incomplete = s2u.str.match(_UNIT_NO_NUM)
        # "BERK CA" / "POTO MD" / "# LA" — 2 tokens, last is a state code, no real number
        is_state_abbrev = s2u.apply(
            lambda v: (len(v.split()) == 2 and v.split()[1] in US_STATES
                       and not any(ch.isdigit() for ch in v))
        )
        bad2 = incomplete | is_state_abbrev
        if bad2.any():
            review.append(_df_subset(df, incomplete, "street_2 unit keyword without a number"))
            review.append(_df_subset(df, is_state_abbrev, "street_2 looks like a state/city abbreviation"))
            df.loc[bad2, S2] = np.nan
            counts["street2_emptied"] = int(bad2.sum())

    # ── Rule 6: empty street_1 (incomplete record) ──
    empty1 = (s1.str.strip() == "")
    if empty1.any():
        regeo.append(_df_subset(df, empty1, "missing street_1 (incomplete record)"))

    has1 = ~empty1
    starts_num = s1.str.match(r"^\d")
    is_pobox = s1.str.match(r"^P\.?\s*O\.?\s*BOX|^POST OFFICE BOX", case=False)
    # PMB (private mailbox at a commercial mail agency) with no street in
    # front behaves exactly like a PO box: a valid mailing address that only
    # geocodes to ZIP level — not a human-judgment case.
    is_pmb = s1.str.match(r"^PMB\s*#?\s*\d", case=False)
    has_type = s1.str.contains(_STREET_TYPE_RE)

    # ── Rule 9: PO BOX / PMB — valid, but a geocoder can't pin them to a
    # precise physical point, so they belong with the re-geocode notes, NOT
    # the human-judgment review report (PO boxes are very common). ──
    if is_pobox.any():
        regeo.append(_df_subset(df, has1 & is_pobox, "PO Box (no precise physical point)"))
    if is_pmb.any():
        regeo.append(_df_subset(df, has1 & is_pmb, "PMB private mailbox (no precise physical point)"))

    # ── Rule 8: descriptive / intersection (parens, ' AND ', FORMERLY) ──
    descriptive = has1 & (s1.str.contains(r"\(") | s1.str.contains(r"\bAND\b") | s1.str.contains(r"FORMERLY"))
    if descriptive.any():
        review.append(_df_subset(df, descriptive, "descriptive / intersection address"))

    # ── care-of name with no street to recover ("C/O <name>") ──
    # (C/O lines that *did* have an address were already recovered upstream)
    care_of = has1 & s1.str.match(r"^C\s*/\s*O\b", case=False)
    if care_of.any():
        review.append(_df_subset(df, care_of, "care-of name (no street to recover)"))

    # ── Rule 7: entity name instead of a street address ──
    entity = has1 & ~starts_num & ~is_pobox & ~is_pmb & ~has_type & ~descriptive & ~care_of
    if entity.any():
        review.append(_df_subset(df, entity, "entity / non-address in street_1"))

    # ── Rule 11: out-of-schema (no US state) ──
    bad_state = has1 & (~state.str.upper().isin(US_STATES))
    if bad_state.any():
        review.append(_df_subset(df, bad_state, "missing / non-US state (out of schema)"))

    # ── Rule 10: empty ZIP → re-extract later from a trusted source ──
    # (coords are added by the later geocode step, so this just marks the gap)
    if ZIP in df.columns:
        empty_zip = (zipc.str.strip() == "") & has1
        if empty_zip.any():
            regeo.append(_df_subset(df, empty_zip, "missing ZIP (re-extract later)"))

    # ── partial / truncated city (e.g. lone 'SANTA') ──
    partial = has1 & city.str.upper().str.strip().isin(_PARTIAL_CITY)
    if partial.any():
        regeo.append(_df_subset(df, partial, "partial / truncated city"))

    # ── coordinates fall outside the stated state's bounding box ──
    if LAT in df.columns and LON in df.columns:
        lat = pd.to_numeric(df[LAT], errors="coerce")
        lon = pd.to_numeric(df[LON], errors="coerce")
        st = state.str.upper()
        bbox = st.map(lambda s: _STATE_BBOX.get(s, (-999, 999, -999, 999)))
        outside = lat.notna() & lon.notna() & st.isin(_STATE_BBOX) & ~(
            (lat >= bbox.str[0])
            & (lat <= bbox.str[1])
            & (lon >= bbox.str[2])
            & (lon <= bbox.str[3])
        )
        if outside.any():
            regeo.append(_df_subset(df, outside, "coordinates outside the stated state"))

    # ── Write the two reports ──
    review_df = pd.concat(review, ignore_index=True) if review else pd.DataFrame()
    regeo_df = pd.concat(regeo, ignore_index=True) if regeo else pd.DataFrame()
    counts["manual_review"] = len(review_df)
    counts["regeocode"] = len(regeo_df)

    if out_dir:
        d = Path(out_dir)
        review_df.to_csv(d / "address_manual_review.csv", index=False, na_rep="")
        regeo_df.to_csv(d / "address_regeocode_suspects.csv", index=False, na_rep="")

    return df, counts
