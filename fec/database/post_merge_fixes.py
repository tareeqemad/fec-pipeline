"""
post_merge_fixes.py — Fixes that need donor_key (run after donor_match).

These can't run in clean.py's safety nets because donor_key
doesn't exist yet at that point. Called by donor_match.py --apply.
"""
import numpy as np
import pandas as pd

from fec.config.constants import (
    SKIP_EMPLOYERS, SKIP_OCCUPATIONS, RAW_JUNK_EMPLOYERS, RAW_STATUS_MAP,
    JUNK_EMPLOYER_RE, REFUSAL_EMPLOYERS, SECTOR_AS_EMPLOYER, ADMIN_NOTE_EMPLOYER_RE,
    ROLE_AS_EMPLOYER, OCCUPATION_AS_EMPLOYER,
)
from fec.log import get_logger
from fec.cleaning.occupations import _categorize
from fec.cleaning.previous_employer import normalize_previous_employer_column

logger = get_logger(__name__)


def apply_post_merge_fixes(df: pd.DataFrame) -> int:
    """Run all fixes that require donor_key. Returns total records fixed."""
    total = 0
    # Labels are stable slugs derived from the function names; the trailing
    # [letter] is the historical step tag (comments below still reference it).
    steps = [
        ("null-surname preserved [Y]",               _null_surname),
        ("truncated-house-numbers [Z]",              _truncated_house_numbers),
        ("retired-while-active → real employer [AA]", _retired_while_active),
        ("employer-typos (pass 1) [AB]",             _employer_typos),
        ("employer-typos (pass 2) [AB]",             _employer_typos),
        ("occupation-consolidation (pass 1) [AE]",   _occupation_consolidation),
        ("occupation-consolidation (pass 2) [AE]",   _occupation_consolidation),
        ("selfemployed-while-retired [AF]",          _selfemployed_while_retired),
        ("employer-substring-variants [AG]",         _employer_substring_variants),
        ("swapped-emp-occ-retired [AH]",             _swapped_emp_occ_retired),
        ("disclosed-no-employer [W]",                _disclosed_no_employer),
        ("fill-employer-from-donor [AI]",            _fill_employer_from_donor),
        ("fill-occupation-from-donor [AJ]",          _fill_occupation_from_donor),
        ("fill-employer-from-occupation [AK]",       _fill_employer_from_occupation),
        ("not-applicable-individual-sweep [AM]",     _not_applicable_individual_sweep),
        ("fill-prev-employer-from-donor [AN]",       _fill_prev_employer_from_donor),
        ("once-retired-always-retired [AO]",         _once_retired_always_retired),
        ("propagate-prev-employer-within-donor [AP]", _propagate_previous_employer_within_donor),
        ("retired-active-sync [AQ]",                 _retired_active_sync),
        # Null refusal/placeholder employers (N/A, PRIVATE, PHYSICAN typo …)
        # that AK/AL may have re-filled from the raw FEC value. Runs after all
        # employer-FILLING steps (AI/AJ/AK/AL above) so nothing reintroduces
        # them — and BEFORE the AO convergence pass below, because blanking an
        # employer can itself turn a donor into a once-retired case (their only
        # "real" employer was the placeholder). If AS ran after AO, those
        # donors would never be re-evaluated and the gate below would fail.
        # RETIRED / SELF-EMPLOYED / NOT EMPLOYED stay — valid status values.
        ("null-refusal-employers [AS]",              _null_refusal_employers),
        # Final convergence pass: AK/AL fill empty employers from raw FEC
        # and occupation_category, and AS blanks placeholders — both can
        # expose new retired-donor status-word pairs that AO (pass 1) couldn't
        # see. A second AO pass after all other sweeps guarantees the
        # retired-consistency gate passes on a fresh clean.py run.
        ("once-retired-always-retired (pass 2) [AO]", _once_retired_always_retired),
        # Entity types must SETTLE before the derive steps below — AU/AV can
        # re-type rows (committee→ORGANIZATION, →INDIVIDUAL), and a row re-typed
        # AFTER the derives would keep stale committee-era fields. (This was a
        # real bug: 17 org rows kept 'CAMPAIGN/COMMITTEE' employers and 8 kept
        # professional categories because AU/AV used to run last.)
        # Re-enforce same-name → same entity_type now that canonicalize_donor_names
        # has unified each donor's name (the clean()-time pass saw the raw names).
        ("reenforce-entity-consistency [AU]",        _reenforce_entity_consistency),
        # Hand-curated entity_type fixes the heuristics can't get (token-less
        # business names, a bank misparsed as a person). Wins over AU.
        ("apply-entity-overrides [AV]",              _apply_entity_overrides),
        # Re-derive occupation_status — every preceding step can change
        # occupation/employer, which leaves the earlier-computed status stale.
        ("rederive-occupation-status [AR]",          _rederive_occupation_status),
        # Re-derive occupation_category from the FINAL occupation text — the
        # occupation-fill steps above can replace a generic status occupation
        # with the donor's real profession, leaving the category stale.
        ("rederive-occupation-category [AT]",        _rederive_occupation_category),
        # Non-individual employer placeholders, re-enforced LAST: the contract
        # every outside reader sees is "a non-individual's employer names its
        # entity class". Rows AU/AV just re-typed carry their old employer, so
        # this must run after them (mirrors safety-net steps A/A2, idempotent).
        ("enforce-nonindividual-placeholders [AW]",  _enforce_nonindividual_placeholders),
        # previous_employer, settled LAST. Three steps above write this column
        # from three different sources (AN from a donor's other filings, AP
        # propagated within a donor, AQ copied off contributor_employer) and
        # none of them owns the question "is this string a company?". Guarding
        # each writer separately is how a status word survives: one writer's
        # guard covers it, another's doesn't. One pass at the end, through the
        # SAME contract the resolve stage applies, so the column no longer
        # depends on which of the two stages happened to run last.
        ("normalize-previous-employer [AX]",         _normalize_previous_employer),
    ]
    for label, fn in steps:
        n = fn(df)
        if n:
            logger.info(f"    {label}: {n:,} fixed")
        total += n
    return total


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SKIP = SKIP_EMPLOYERS


def _null_surname(df: pd.DataFrame) -> int:
    """Y. 'NULL' is a real surname — pandas reads it as NaN or empty string."""
    ln = df['contributor_last_name'].fillna('')
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (ln == '')
        & df['contributor_name'].fillna('').str.startswith('NULL,')
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_last_name'] = 'NULL'
    return n


def _truncated_house_numbers(df: pd.DataFrame) -> int:
    """Z. '97 SHIRLEY RD'(1) → '970 SHIRLEY RD'(103) for same donor."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        sts = grp['contributor_street_1'].dropna().value_counts()
        if len(sts) < 2:
            continue
        for a in sts.index:
            for b in sts.index:
                if a >= b:
                    continue
                pa, pb = str(a).split(' ', 1), str(b).split(' ', 1)
                if len(pa) < 2 or len(pb) < 2:
                    continue
                if pa[1] != pb[1]:
                    continue
                if not pa[0].isdigit() or not pb[0].isdigit():
                    continue
                if not (pa[0].startswith(pb[0]) or pb[0].startswith(pa[0])):
                    continue
                cnt_a, cnt_b = sts[a], sts[b]
                if cnt_a <= 2 and cnt_b >= 5:
                    mask = (df['donor_key'] == dk) & (df['contributor_street_1'] == a)
                    df.loc[mask, 'contributor_street_1'] = b
                    n_fixed += int(mask.sum())
                elif cnt_b <= 2 and cnt_a >= 5:
                    mask = (df['donor_key'] == dk) & (df['contributor_street_1'] == b)
                    df.loc[mask, 'contributor_street_1'] = a
                    n_fixed += int(mask.sum())
    return n_fixed


def _retired_while_active(df: pd.DataFrame) -> int:
    """AA. RETIRED entries while donor is still active → fix."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emp_vals = set(grp['contributor_employer'].dropna().unique())
        if 'RETIRED' not in emp_vals:
            continue
        real_emps = emp_vals - _SKIP
        if not real_emps:
            continue

        ret_rows = grp[grp['contributor_employer'] == 'RETIRED']
        active_rows = grp[grp['contributor_employer'].isin(real_emps)]
        if ret_rows.empty or active_rows.empty:
            continue

        if ret_rows['contribution_receipt_date'].max() <= active_rows['contribution_receipt_date'].max():
            main_emp = active_rows['contributor_employer'].value_counts().index[0]
            sub = active_rows[active_rows['contributor_employer'] == main_emp]
            occ_vc = sub['contributor_occupation'].value_counts()
            if occ_vc.empty:
                continue          # no occupation to propagate — skip this donor
            main_occ = occ_vc.index[0]
            main_cat = sub['occupation_category'].mode()
            main_cat = main_cat.iloc[0] if len(main_cat) > 0 else 'OTHER'

            mask = (df['donor_key'] == dk) & (df['contributor_employer'] == 'RETIRED')
            df.loc[mask, 'contributor_employer'] = main_emp
            df.loc[mask, 'contributor_occupation'] = main_occ
            df.loc[mask, 'occupation_category'] = main_cat
            df.loc[mask, 'occupation_status'] = 'DISCLOSED'
            n_fixed += int(mask.sum())
    return n_fixed


def _rederive_occupation_status(df: pd.DataFrame) -> int:
    """AR. Re-derive occupation_status from the FINAL occupation/employer
    state for individuals.

    occupation_status is first computed mid-pipeline, but later passes
    (AI/AJ donor-fill, the junk cleaners, manual overrides) change
    occupation/employer afterwards — leaving the status stale, e.g. a
    filled occupation+employer row still flagged MISSING. Rules:
        INDIVIDUAL + occupation + employer  -> DISCLOSED
        INDIVIDUAL + occupation, no employer -> EMPLOYER_MISSING
    Rows with no occupation are left untouched so the MISSING vs
    NOT_DISCLOSED distinction is preserved."""
    if 'occupation_status' not in df.columns:
        return 0
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    has_occ = df['contributor_occupation'].fillna('').astype(str).str.strip() != ''
    has_emp = df['contributor_employer'].fillna('').astype(str).str.strip() != ''
    st = df['occupation_status']

    # DERIVED is preserved everywhere — it is a provenance marker set by the
    # donor-history fills (AI/AJ/AL); never overwrite it with DISCLOSED.
    disclosed = is_indiv & has_occ & has_emp & ~st.isin(['DISCLOSED', 'DERIVED'])
    emp_missing = (is_indiv & has_occ & ~has_emp
                   & ~st.isin(['EMPLOYER_MISSING', 'NOT_DISCLOSED', 'DERIVED']))
    df.loc[disclosed, 'occupation_status'] = 'DISCLOSED'
    df.loc[emp_missing, 'occupation_status'] = 'EMPLOYER_MISSING'
    return int(disclosed.sum()) + int(emp_missing.sum())


def _rederive_occupation_category(df: pd.DataFrame) -> int:
    """AT. Re-derive occupation_category from the FINAL occupation text.

    occupation_category is computed early (from the occupation as first seen),
    but later passes ENRICH the occupation — e.g. the donor-history fills replace
    a generic 'SELF EMPLOYED' / 'SELF' occupation with the donor's real
    profession ('ATTORNEY') — without re-categorizing, leaving a stale life-STATUS
    category sitting on a real profession (occ='ATTORNEY' but category
    'SELF-EMPLOYED'). occupation_category groups the OCCUPATION; employment status
    lives in the employer field / employer_status — so when the final occupation
    text yields a real (non-status, non-OTHER) category, adopt it.

    Conservative: only touches rows whose category is currently a life-STATUS
    bucket AND whose occupation text is NOT itself a status word. Genuine
    RETIRED/SELF-EMPLOYED occupations, OTHER, and already-correct categories are
    left untouched (so the OTHER-reclassification work is preserved).
    """
    _STATUS_CATS = {'SELF-EMPLOYED', 'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'STUDENT'}
    _STATUS_OCC = {'SELF-EMPLOYED', 'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER',
                   'HOUSEWIFE', 'STUDENT', 'UNEMPLOYED'}
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    cat = df['occupation_category'].fillna('')

    stale = (
        is_indiv & cat.isin(_STATUS_CATS)
        & (occ != '') & ~occ.str.upper().isin(_STATUS_OCC)
    )
    idx = df.index[stale]
    n_fixed = 0
    if len(idx):
        new_cats = _categorize(df.loc[idx, 'contributor_occupation'])
        keep = new_cats.notna() & ~new_cats.isin(_STATUS_CATS) & (new_cats != 'OTHER')
        fix_idx = idx[keep.values]
        if len(fix_idx):
            df.loc[fix_idx, 'occupation_category'] = new_cats[keep].values
        n_fixed = int(len(fix_idx))

    # Inverse direction — the column must keep its own promise to an outside
    # reader: occupation_category GROUPS the occupation text. A status-word
    # occupation (NOT EMPLOYED) wearing a professional category (ARTS /
    # EXECUTIVE — inherited from the donor's other filings) reads as a
    # contradiction inside a single row. The donor's profession is still
    # visible through their employer / other rows; the category follows the
    # text of THIS row's occupation.
    occ_u = occ.str.upper()
    torn = is_indiv & occ_u.isin(_STATUS_OCC) & (cat != '') & ~cat.isin(_STATUS_CATS)
    if torn.any():
        status_cat = occ_u[torn].replace({'HOUSEWIFE': 'HOMEMAKER', 'UNEMPLOYED': 'NOT EMPLOYED'})
        df.loc[torn, 'occupation_category'] = status_cat.values
        n_fixed += int(torn.sum())

    # NOT DISCLOSED is deliberately uncategorized (see OCCUPATION_FIXES: junk →
    # NOT DISCLOSED with NULL category). A couple of rows drifted into OTHER /
    # SELF-EMPLOYED via donor-level harmonization — align them with the design
    # so all NOT DISCLOSED rows read identically.
    nd = is_indiv & (occ_u == 'NOT DISCLOSED') & (cat != '')
    if nd.any():
        df.loc[nd, 'occupation_category'] = pd.NA
        n_fixed += int(nd.sum())

    return n_fixed


def _enforce_nonindividual_placeholders(df: pd.DataFrame) -> int:
    """AW. Employer placeholder on every non-individual — the file's contract.

    COMMITTEE/PAC → 'CAMPAIGN/COMMITTEE', ORGANIZATION → 'ORGANIZATION'.
    The clean()-time safety nets already do this, but AU/AV above re-type rows
    AFTER those nets ran; without this final pass a bank re-typed by an
    override keeps the committee placeholder (or an empty employer) and the
    published CSV contradicts its own data dictionary."""
    n = 0
    for etype, placeholder in (('COMMITTEE/PAC', 'CAMPAIGN/COMMITTEE'),
                               ('ORGANIZATION', 'ORGANIZATION')):
        mask = ((df['entity_type'] == etype)
                & (df['contributor_employer'].fillna('').str.strip().str.upper() != placeholder))
        if mask.any():
            df.loc[mask, 'contributor_employer'] = placeholder
            n += int(mask.sum())
    return n


def _apply_entity_overrides(df: pd.DataFrame) -> int:
    """AV. Hand-curated entity_type corrections, keyed by contributor_name.

    For editorial cases the name heuristics cannot get right: businesses whose
    names carry NO LLC/INC/GROUP token (a law firm, a manufacturer) and so
    default to COMMITTEE/PAC, plus a bank ('… , N.A.') misparsed as INDIVIDUAL.
    Lives in data/database/entity_overrides.csv (contributor_name, entity_type,
    note); supports ORGANIZATION / COMMITTEE/PAC / INDIVIDUAL targets. Conduit
    PACs (ACTBLUE, DEMOCRACY ENGINE) are deliberately NOT here — they really are
    committees the heuristics already get right.
    """
    import csv
    from fec.env import PROJECT_ROOT
    path = PROJECT_ROOT / "data" / "database" / "entity_overrides.csv"
    if not path.exists():
        return 0

    overrides: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            nm = (row.get("contributor_name") or "").strip().upper()
            et = (row.get("entity_type") or "").strip().upper()
            if nm and et:
                overrides[nm] = et
    if not overrides:
        return 0

    name_u = df["contributor_name"].fillna("").str.upper().str.strip()
    n = 0
    for nm_u, et in overrides.items():
        mask = name_u == nm_u
        if not mask.any():
            continue
        df.loc[mask, "entity_type"] = et
        if et == "ORGANIZATION":
            if "is_individual" in df.columns:
                df.loc[mask, "is_individual"] = False
            if "occupation_category" in df.columns:
                df.loc[mask, "occupation_category"] = "ORGANIZATION"
            # an org has no personal employer/occupation; first/last are cleared
            # by the non-individual name-clear step that runs after post-merge.
            for col in ("contributor_occupation", "contributor_employer"):
                if col in df.columns:
                    df.loc[mask, col] = np.nan
        elif et == "COMMITTEE/PAC":
            # a committee mistyped as ORGANIZATION — a token-less PAC name (e.g.
            # LEVELFIELD = Carey PAC C00784140, not the financial firm). Mirror a
            # real committee row: the CAMPAIGN/COMMITTEE employer sentinel + the
            # POLITICAL COMMITTEE category, no personal occupation; first/last are
            # cleared by the non-individual name-clear step after post-merge.
            if "is_individual" in df.columns:
                df.loc[mask, "is_individual"] = False
            if "contributor_employer" in df.columns:
                df.loc[mask, "contributor_employer"] = "CAMPAIGN/COMMITTEE"
            if "contributor_occupation" in df.columns:
                df.loc[mask, "contributor_occupation"] = np.nan
            if "occupation_category" in df.columns:
                df.loc[mask, "occupation_category"] = "POLITICAL COMMITTEE"
        elif et == "INDIVIDUAL":
            # a real person mistyped as a committee — parse 'LAST, FIRST' back out.
            if "is_individual" in df.columns:
                df.loc[mask, "is_individual"] = True
            parts = df.loc[mask, "contributor_name"].fillna("").str.split(",", n=1)
            df.loc[mask, "contributor_last_name"] = parts.str[0].str.strip()
            df.loc[mask, "contributor_first_name"] = (
                parts.str[1].fillna("").str.strip().str.lstrip(". ")
            )
            if "occupation_category" in df.columns:
                df.loc[mask, "occupation_category"] = "OTHER"
            # drop the committee-only employer sentinel left from its PAC days —
            # an individual never has employer=CAMPAIGN/COMMITTEE.
            if "contributor_employer" in df.columns:
                comm_emp = mask & (df["contributor_employer"].fillna("") == "CAMPAIGN/COMMITTEE")
                df.loc[comm_emp, "contributor_employer"] = np.nan
        n += int(mask.sum())
    return n


def _reenforce_entity_consistency(df: pd.DataFrame) -> int:
    """AU. Re-enforce same-name → same entity_type AFTER name canonicalization.

    _reclassify_entities enforces "a name that is ORGANIZATION in any row makes
    all its non-individual rows ORGANIZATION" — but it runs in clean() on the RAW
    contributor_name. canonicalize_donor_names later rewrites a donor's filings to
    one canonical name, which can leave a now-byte-identical name ORGANIZATION in
    one row but still COMMITTEE/PAC (the default) in another — e.g. a law/media
    firm like MILLER BARONDESS or TRILOGY INTERACTIVE split across both types
    under one donor_key. Re-run the consistency pass so ORGANIZATION wins.
    """
    from fec.cleaning.pipeline.reclassify import _enforce_entity_name_consistency
    before = df['entity_type'].copy()
    n = _enforce_entity_name_consistency(df)
    if n and 'occupation_category' in df.columns:
        flipped = (before == 'COMMITTEE/PAC') & (df['entity_type'] == 'ORGANIZATION')
        df.loc[flipped, 'occupation_category'] = 'ORGANIZATION'
    return n


def _employer_typos(df: pd.DataFrame) -> int:
    """AB. Unify near-identical employer spellings within the same donor.

    Same donor = same person = same employer, so two near-identical
    spellings are a typo/abbreviation, not two companies. Two paths:
      - Levenshtein <= 2 with the correct form >= 3x dominant (tight typos)
      - fuzzy ratio >= 90 (abbreviations / longer typos Levenshtein misses,
        e.g. CAMBRIDGE INFO GROUP <-> CAMBRIDGE INFORMATION GROUP)
    The same-donor gate keeps it safe — different companies are used by
    different people, not by one person; and `other` must be the rarer
    spelling, so the canonical is always the donor's dominant form."""
    from fec.cleaning._helpers import levenshtein
    from difflib import SequenceMatcher

    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in _SKIP]
        if len(real) < 2:
            continue

        counts = grp['contributor_employer'].value_counts()
        canonical = max(real, key=lambda e: counts.get(e, 0))
        cc = counts.get(canonical, 0)

        for other in real:
            if other == canonical:
                continue
            oc = counts.get(other, 0)
            if oc >= cc:
                continue
            lev_ok = (levenshtein(other.upper(), canonical.upper()) <= 2
                      and cc / max(oc, 1) >= 3)
            fuzzy_ok = SequenceMatcher(
                None, other.upper(), canonical.upper()).ratio() * 100 >= 90
            if lev_ok or fuzzy_ok:
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == other)
                df.loc[mask, 'contributor_employer'] = canonical
                n_fixed += int(mask.sum())
    return n_fixed


def _occupation_consolidation(df: pd.DataFrame) -> int:
    """AE. Same donor + same employer → pick most common occupation.
    e.g. 'REAL ESTATE'(1) vs 'REAL ESTATE DEVELOPER'(64) → all become REAL ESTATE DEVELOPER."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for (dk, emp), grp in indiv.groupby(['donor_key', 'contributor_employer'], dropna=False):
        if pd.isna(emp) or emp in _SKIP:
            continue
        occs = grp['contributor_occupation'].dropna().unique()
        if len(occs) < 2:
            continue

        counts = grp['contributor_occupation'].value_counts()
        canonical = counts.index[0]
        canonical_count = counts.iloc[0]

        for other_occ in occs:
            if other_occ == canonical:
                continue
            other_count = counts.get(other_occ, 0)
            # Fix when: dominant is ≥3x more common, AND one is substring of the other
            if canonical_count >= 3 * other_count and (other_occ in canonical or canonical in other_occ):
                mask = (
                    (df['donor_key'] == dk)
                    & (df['contributor_employer'] == emp)
                    & (df['contributor_occupation'] == other_occ)
                )
                df.loc[mask, 'contributor_occupation'] = canonical
                n_fixed += int(mask.sum())

    return n_fixed


def _selfemployed_while_retired(df: pd.DataFrame) -> int:
    """AF. Stray SELF-EMPLOYED records when donor is clearly RETIRED.
    
    e.g. Robert Goldberg: 5 RETIRED + 2 SELF-EMPLOYED, latest is RETIRED $25K.
    The SELF-EMPLOYED records are data entry inconsistencies.
    Fix: convert SELF-EMPLOYED → RETIRED when ≥60% of records are RETIRED,
    latest record is RETIRED, and SELF-EMPLOYED count ≤ 3.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].value_counts()
        if 'RETIRED' not in emps or 'SELF-EMPLOYED' not in emps:
            continue

        ret_count = emps.get('RETIRED', 0)
        se_count = emps.get('SELF-EMPLOYED', 0)
        total = len(grp)

        # Latest record must be RETIRED. pd.isna() first: a blanked employer is
        # NA, and `NA != 'RETIRED'` evaluates to NA — which raises inside `if`
        # rather than being falsy. A missing employer simply isn't RETIRED, so
        # the donor is skipped either way.
        latest_emp = grp.sort_values('contribution_receipt_date', ascending=False).iloc[0]['contributor_employer']
        if pd.isna(latest_emp) or latest_emp != 'RETIRED':
            continue

        # ≥60% RETIRED and ≤3 stray SELF-EMPLOYED
        if ret_count / total >= 0.6 and se_count <= 3:
            mask = (df['donor_key'] == dk) & (df['contributor_employer'] == 'SELF-EMPLOYED')
            df.loc[mask, 'contributor_employer'] = 'RETIRED'
            df.loc[mask, 'contributor_occupation'] = 'RETIRED'
            df.loc[mask, 'occupation_category'] = 'RETIRED'
            n_fixed += int(mask.sum())

    return n_fixed


def _employer_substring_variants(df: pd.DataFrame) -> int:
    """AG. Employer substring variants within same donor.

    Same donor writes employer two ways — one is a substring of the other.
    e.g. LINDEN(17) vs LINDEN CAPITAL PARTNERS(58) → all become LINDEN CAPITAL PARTNERS.
    Fix: the more frequent variant wins when ≥3x more common.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in _SKIP]
        if len(real) < 2:
            continue

        counts = grp['contributor_employer'].value_counts()
        fixed_in_group = set()

        # Compare all pairs
        for i, a in enumerate(real):
            for b in real[i + 1:]:
                # Check substring relationship
                au, bu = a.upper(), b.upper()
                if au not in bu and bu not in au:
                    continue

                ca, cb = counts.get(a, 0), counts.get(b, 0)

                # The longer (more specific) name wins if ≥3x, else the more frequent
                if ca >= 3 * cb and cb > 0:
                    winner, loser = a, b
                elif cb >= 3 * ca and ca > 0:
                    winner, loser = b, a
                else:
                    continue

                if loser in fixed_in_group:
                    continue

                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == loser)
                n = int(mask.sum())
                if n:
                    df.loc[mask, 'contributor_employer'] = winner
                    fixed_in_group.add(loser)
                    n_fixed += n

    return n_fixed


def _swapped_emp_occ_retired(df: pd.DataFrame) -> int:
    """AH. Swapped employer/occupation for RETIRED donors.

    Donors who are ≥70% RETIRED (vs real employers) have 1-3 records where
    a real employer appears with occ=RETIRED — classic FEC form fill error.
    e.g. Susan Talles: emp=AIPAC occ=RETIRED — she's retired, not an AIPAC employee.
    Fix: set emp=RETIRED, occ=RETIRED for those stray records.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].value_counts()
        ret_count = emps.get('RETIRED', 0)
        if ret_count == 0:
            continue

        # Find records with real employer + occ=RETIRED (the swapped ones)
        real_emps = set(emps.index) - _SKIP
        if not real_emps:
            continue

        real_count = sum(emps.get(e, 0) for e in real_emps)

        # Ratio: RETIRED vs (RETIRED + real employers) — ignores NONE/NOT EMPLOYED/etc.
        if ret_count / (ret_count + real_count) < 0.70:
            continue

        for emp in real_emps:
            emp_count = emps.get(emp, 0)
            if emp_count > 3:
                continue  # too many to be a stray

            # Check that these records have occ=RETIRED (the swap signature)
            swap_mask = (
                (df['donor_key'] == dk)
                & (df['contributor_employer'] == emp)
                & (df['contributor_occupation'].fillna('').str.upper() == 'RETIRED')
            )
            n = int(swap_mask.sum())
            if n > 0 and n == emp_count:
                # All records with this employer have occ=RETIRED → confirmed swap
                df.loc[swap_mask, 'contributor_employer'] = 'RETIRED'
                df.loc[swap_mask, 'contributor_occupation'] = 'RETIRED'
                df.loc[swap_mask, 'occupation_category'] = 'RETIRED'
                n_fixed += n

    return n_fixed


def _disclosed_no_employer(df: pd.DataFrame) -> int:
    """W. DISCLOSED status but no employer → EMPLOYER_MISSING."""
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['occupation_status'] == 'DISCLOSED')
        & df['contributor_employer'].isna()
        & df['contributor_occupation'].notna()
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n


def _fill_employer_from_donor(df: pd.DataFrame) -> int:
    """AI. Fill NaN employer from same donor's other records (needs donor_key)."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    null_emp = indiv[indiv['contributor_employer'].isna()]
    if null_emp.empty:
        return 0

    n_fixed = 0
    for dk, grp in null_emp.groupby('donor_key'):
        all_recs = df[(df['donor_key'] == dk) & (df['entity_type'] == 'INDIVIDUAL')]
        real_recs = all_recs[all_recs['contributor_employer'].notna()
                             & ~all_recs['contributor_employer'].isin(_SKIP)]
        if real_recs.empty:
            continue

        # most-recent: employer from the donor's latest-dated qualifying
        # filing — closest in time = best estimate (handles job changes).
        main_emp = real_recs.sort_values(
            'contribution_receipt_date', na_position='first'
        )['contributor_employer'].iloc[-1]
        emp_recs = all_recs[all_recs['contributor_employer'] == main_emp]
        main_occ = emp_recs['contributor_occupation'].dropna().value_counts()
        main_occ = main_occ.index[0] if len(main_occ) > 0 else None
        main_cat = emp_recs['occupation_category'].dropna().value_counts()
        main_cat = main_cat.index[0] if len(main_cat) > 0 else None

        mask = (df['donor_key'] == dk) & df['contributor_employer'].isna()
        df.loc[mask, 'contributor_employer'] = main_emp
        df.loc[mask, 'occupation_status'] = 'DERIVED'   # employer filled from donor history
        if main_occ and df.loc[mask, 'contributor_occupation'].isna().all():
            df.loc[mask, 'contributor_occupation'] = main_occ
        if main_cat:
            df.loc[mask, 'occupation_category'] = main_cat
        n_fixed += int(mask.sum())

    return n_fixed


def _fill_occupation_from_donor(df: pd.DataFrame) -> int:
    """AJ. Fill NaN occupation from same donor's other records (needs donor_key)."""
    _OCC_SKIP = SKIP_OCCUPATIONS
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    null_occ = indiv[indiv['contributor_occupation'].isna()]
    if null_occ.empty:
        return 0

    n_fixed = 0
    for dk, grp in null_occ.groupby('donor_key'):
        all_recs = df[(df['donor_key'] == dk) & (df['entity_type'] == 'INDIVIDUAL')]
        real_recs = all_recs[all_recs['contributor_occupation'].notna()
                             & ~all_recs['contributor_occupation'].isin(_OCC_SKIP)
                             & (all_recs['contributor_occupation'] != '')]
        if real_recs.empty:
            continue

        # most-recent: occupation from the donor's latest-dated qualifying
        # filing — closest in time = best estimate.
        main_occ = real_recs.sort_values(
            'contribution_receipt_date', na_position='first'
        )['contributor_occupation'].iloc[-1]
        main_cat = all_recs[all_recs['contributor_occupation'] == main_occ]['occupation_category'].dropna()
        main_cat = main_cat.value_counts().index[0] if len(main_cat) > 0 else None

        mask = (df['donor_key'] == dk) & df['contributor_occupation'].isna()
        df.loc[mask, 'contributor_occupation'] = main_occ
        df.loc[mask, 'occupation_status'] = 'DERIVED'   # filled from donor history
        if main_cat:
            df.loc[mask, 'occupation_category'] = main_cat
        n_fixed += int(mask.sum())

    return n_fixed


def _fill_employer_from_occupation(df: pd.DataFrame) -> int:
    """AK. Empty employer but occupation is a status word -> set employer = occupation.
    e.g. emp=NaN occ=RETIRED -> emp=RETIRED
         emp=NaN occ=HOMEMAKER -> emp=HOMEMAKER"""
    _STATUS_MAP = {
        'RETIRED': 'RETIRED',
        'HOMEMAKER': 'HOMEMAKER',
        'HOUSEWIFE': 'HOMEMAKER',
        'NOT EMPLOYED': 'NOT EMPLOYED',
        'STUDENT': 'STUDENT',
        'UNEMPLOYED': 'NOT EMPLOYED',
        'SELF-EMPLOYED': 'SELF-EMPLOYED',
    }
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    empty_emp = df['contributor_employer'].isna() | (df['contributor_employer'] == '')
    occ = df['contributor_occupation'].fillna('')

    n = 0
    for occ_val, emp_val in _STATUS_MAP.items():
        mask = is_indiv & empty_emp & (occ == occ_val)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Set from occupation_category
    still_empty = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    _CAT_MAP = {
        'RETIRED': 'RETIRED',
        'NOT EMPLOYED': 'NOT EMPLOYED',
        'HOMEMAKER': 'HOMEMAKER',
        'STUDENT': 'STUDENT',
        'SELF-EMPLOYED': 'SELF-EMPLOYED',
    }
    for cat, emp_val in _CAT_MAP.items():
        mask = still_empty & (df['occupation_category'] == cat)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Try to recover from raw FEC data
    still_empty2 = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    if still_empty2.any():
        n += _fill_employer_from_raw(df, still_empty2)

    return n


def _fill_employer_from_raw(df: pd.DataFrame, empty_mask: pd.Series) -> int:
    """AL. Recover employer from raw FEC filings for same person.
    Some donors gave employer in one filing but not another."""
    import os
    raw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'contributions.csv')
    if not os.path.exists(raw_path):
        return 0

    raw = pd.read_csv(raw_path, dtype=str, usecols=['contributor_name', 'contributor_state', 'contributor_employer'], low_memory=False)

    # Build lookup keys for empty donors
    empty_rows = df[empty_mask]
    empty_keys = set(
        (str(r['contributor_name']).strip().upper() + '|' + str(r['contributor_state']).strip().upper())
        for _, r in empty_rows.iterrows()
    )

    # Search raw for same name+state
    raw['_key'] = raw['contributor_name'].fillna('').str.strip().str.upper() + '|' + raw['contributor_state'].fillna('').str.strip().str.upper()
    raw_matches = raw[raw['_key'].isin(empty_keys)]

    _JUNK = RAW_JUNK_EMPLOYERS
    _STATUS_RAW = RAW_STATUS_MAP

    # Build key -> best employer from raw
    key_to_emp = {}
    for key in empty_keys:
        person = raw_matches[raw_matches['_key'] == key]
        emps = person['contributor_employer'].fillna('').str.strip()

        # Try real employer first. Exclude industry/sector words too — they are
        # nulled upstream as non-companies, so don't re-recover them from raw
        # (e.g. a donor who only ever wrote "HEALTHCARE").
        # Roles/titles and refusals are blanked or converted to SELF-EMPLOYED
        # upstream for the same reason sectors are — re-recovering them here
        # silently undid that (a donor whose only raw employer was 'DRIVING'
        # got it back after the safety nets had already resolved the row).
        real = emps[~emps.str.upper().isin(_JUNK)
                    & ~emps.str.upper().isin(_STATUS_RAW.keys())
                    & ~emps.str.upper().isin(SECTOR_AS_EMPLOYER)
                    & ~emps.str.upper().isin(ROLE_AS_EMPLOYER)
                    & ~emps.str.upper().isin(OCCUPATION_AS_EMPLOYER)
                    & ~emps.str.upper().isin(REFUSAL_EMPLOYERS)
                    & (emps.str.len() > 2)]
        # Filter out emails, structural junk (dates, masked digits like
        # XXX-XX-XXXX, all-numeric strings) and admin-note/refusal phrasings —
        # same patterns the cleaner uses, so junk blanked upstream is not
        # re-recovered here.
        real = real[~real.str.contains('@', na=False)
                    & ~real.str.upper().str.match(JUNK_EMPLOYER_RE, na=False)
                    & ~real.str.upper().str.match(ADMIN_NOTE_EMPLOYER_RE, na=False)]
        if len(real) > 0:
            key_to_emp[key] = real.value_counts().index[0]
            continue

        # Try status word
        status = emps[emps.str.upper().isin(_STATUS_RAW.keys())]
        if len(status) > 0:
            raw_val = status.value_counts().index[0].upper()
            key_to_emp[key] = _STATUS_RAW.get(raw_val, raw_val)

    # Apply
    n = 0
    for idx in df[empty_mask].index:
        name = str(df.at[idx, 'contributor_name']).strip().upper()
        state = str(df.at[idx, 'contributor_state']).strip().upper()
        key = f'{name}|{state}'
        if key in key_to_emp:
            df.at[idx, 'contributor_employer'] = key_to_emp[key]
            df.at[idx, 'occupation_status'] = 'DERIVED'   # recovered from donor's raw filings
            n += 1

    if n:
        logger.info(f"    AL. Recovered {n} employers from raw FEC filings")
    return n


def _not_applicable_individual_sweep(df: pd.DataFrame) -> int:
    """AM. Fix individuals left with occupation_status='NOT_APPLICABLE'.

    NOT_APPLICABLE is reserved for committees (schema CHECK constraint).
    When committee records get reclassified to INDIVIDUAL mid-pipeline,
    their occupation_status should be re-derived — but some slip past
    the earlier sweep in pipeline.py (committee_to_individual restore).
    This is the final safety net, run after donor_match / enhancements
    have finished touching the rows.

    Rule (matches the rest of the pipeline):
      - occupation empty / NaN     -> MISSING
      - occupation == 'NOT DISCLOSED' -> NOT_DISCLOSED
      - otherwise                   -> DISCLOSED
    """
    bad = (df['entity_type'] == 'INDIVIDUAL') & (df['occupation_status'] == 'NOT_APPLICABLE')
    n = int(bad.sum())
    if not n:
        return 0

    occ = df.loc[bad, 'contributor_occupation'].astype('string').str.strip().str.upper()
    is_empty = occ.isna() | occ.eq('')
    is_notdisc = occ.eq('NOT DISCLOSED')

    df.loc[bad & is_empty.reindex(df.index, fill_value=False),   'occupation_status'] = 'MISSING'
    df.loc[bad & is_notdisc.reindex(df.index, fill_value=False), 'occupation_status'] = 'NOT_DISCLOSED'
    remaining = bad & ~(is_empty | is_notdisc).reindex(df.index, fill_value=False)
    df.loc[remaining, 'occupation_status'] = 'DISCLOSED'
    return n


# Refusal / placeholder words that must be NULLED out of the employer field.
# Unlike RETIRED / SELF-EMPLOYED / NOT EMPLOYED (which the schema keeps in the
# employer column as a status), these carry no information. Built from the
# single-source REFUSAL set plus N/A-style placeholders and known typos, with
# internal whitespace collapsed so "N A" and "N/A" both match.
_NULL_EMPLOYER_WORDS = (
    REFUSAL_EMPLOYERS
    | {'N/A', 'NA', 'N A', 'NONE', 'NOT APPLICABLE', 'NOT APPLICAABLE',
       'NOT DISCLOSED', 'INFORMATION REQUESTED',
       'INFORMATION REQUESTED PER BEST EFFORTS', 'PHYSICAN', 'SELP EMPLOYED'}
)


def _null_refusal_employers(df: pd.DataFrame) -> int:
    """AS. Final sweep — null employers that are really refusals/placeholders.

    AK (employer-from-occupation) and AL (employer-from-raw) can re-fill a
    blank employer with the raw FEC value, which may be a refusal word or a
    typo like 'PHYSICAN'/'SELP EMPLOYED'. This runs after every fill step so
    those never survive into the output. Valid status words (RETIRED,
    SELF-EMPLOYED, NOT EMPLOYED, CAMPAIGN/COMMITTEE) are intentionally kept.
    """
    emp = df['contributor_employer'].fillna('').astype(str)
    collapsed = emp.str.strip().str.upper().str.replace(r'\s+', ' ', regex=True)
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (emp != '')
        & collapsed.isin(_NULL_EMPLOYER_WORDS)
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


# Status-word employers that can't serve as a previous_employer value.
_PREV_EMP_STATUS = {
    'RETIRED', 'NOT EMPLOYED', 'SELF-EMPLOYED', 'SELF EMPLOYED',
    'UNEMPLOYED', 'HOMEMAKER', 'STUDENT', 'NOT DISCLOSED',
    'CAMPAIGN/COMMITTEE', 'NONE', 'N/A', 'NA', 'NAN', '',
}


def _fill_prev_employer_from_donor(df: pd.DataFrame) -> int:
    """AN. Fill retired donors' previous_employer from their other records.

    When a donor has both retired-period filings (employer='RETIRED')
    and pre-retirement filings (employer='Acme Corp'), the retired
    rows should show 'Acme Corp' as the previous employer. The FEC
    filer typically doesn't repeat the previous employer on every
    retired filing, so resolve.py's cache is the primary source — but
    the cache misses donors whose prior employer simply appears as a
    regular record elsewhere in this dataset.

    Scope: conservative — same donor_key only, not fuzzy-name
    matching. That caps false positives: a different person with the
    same name in the same city won't be conflated.

    Returns: number of rows updated.
    """
    if 'previous_employer' not in df.columns or 'donor_key' not in df.columns:
        return 0

    need_fill = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_employer'] == 'RETIRED')
        & (df['previous_employer'].fillna('').astype(str).str.strip() == '')
    )
    if not need_fill.any():
        return 0

    target_dks = set(df.loc[need_fill, 'donor_key'].unique())

    # Candidate pool: other individual rows with a real (non-status) employer.
    candidates = df[
        (df['donor_key'].isin(target_dks))
        & ~need_fill
        & (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_employer'].notna()
    ].copy()
    candidates = candidates[
        ~candidates['contributor_employer'].astype(str).str.strip().str.upper().isin(_PREV_EMP_STATUS)
    ]
    if candidates.empty:
        return 0

    # For each donor, pick the most common real employer (ties broken by recency).
    if 'contribution_receipt_date' in candidates.columns:
        candidates = candidates.sort_values('contribution_receipt_date', ascending=False)

    best: dict[str, str] = {}
    for dk, grp in candidates.groupby('donor_key'):
        vc = grp['contributor_employer'].value_counts()
        best[dk] = vc.index[0]

    if not best:
        return 0

    fillable = need_fill & df['donor_key'].isin(best)
    n = int(fillable.sum())
    if n:
        df.loc[fillable, 'previous_employer'] = df.loc[fillable, 'donor_key'].map(best)
    return n


_NOT_EMP_VARIANTS  = {'NOT EMPLOYED', 'UNEMPLOYED'}
_SELF_EMP_VARIANTS = {'SELF-EMPLOYED', 'SELF EMPLOYED'}
_STATUS_NON_RETIRED = _NOT_EMP_VARIANTS | _SELF_EMP_VARIANTS | {'NOT DISCLOSED', ''}


def _once_retired_always_retired(df: pd.DataFrame) -> int:
    """AO. Normalize non-retired status words to RETIRED for retired donors.

    If a donor has (a) at least one filing with `employer=RETIRED`,
    (b) at least one filing with `NOT EMPLOYED` / `SELF-EMPLOYED`, and
    (c) never listed a real employer in any filing, their non-RETIRED
    filings are just a different way of describing the same state —
    retirement. Collapse them so the dashboard shows a single coherent
    status per donor and filters don't miss half the records.

    Scope rules (conservative):
      - Only touches donors who NEVER listed a real employer (any real
        employer in any filing disqualifies the donor — they may have
        un-retired, or the real employer belongs to a pre-retirement
        period we shouldn't rewrite).
      - Doesn't touch HOMEMAKER, STUDENT, or other real-category
        statuses — those describe different life situations.
      - Updates contributor_occupation / occupation_category only
        when they themselves were status words (never overwrites a
        real occupation like PHYSICIAN).

    Returns: number of rows updated.
    """
    if 'donor_key' not in df.columns:
        return 0
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    if not is_indiv.any():
        return 0

    emp_upper = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()

    # Classify each row's employer for per-donor aggregation
    is_retired  = emp_upper.eq('RETIRED')
    is_notemp   = emp_upper.isin(_NOT_EMP_VARIANTS)
    is_selfemp  = emp_upper.isin(_SELF_EMP_VARIANTS)
    # "real" = not NaN, not a status word
    is_real = ~emp_upper.isin(_STATUS_NON_RETIRED | {'RETIRED', 'HOMEMAKER', 'STUDENT',
                                                      'CAMPAIGN/COMMITTEE', 'N/A', 'NA',
                                                      'NAN', 'NONE'}) & (emp_upper != '')

    # Per-donor aggregation
    donor_has = pd.DataFrame({
        'donor_key': df.loc[is_indiv, 'donor_key'],
        'retired':  is_retired[is_indiv],
        'notemp':   is_notemp[is_indiv],
        'selfemp':  is_selfemp[is_indiv],
        'real':     is_real[is_indiv],
    }).groupby('donor_key').agg('any')

    target_donors = donor_has.index[
        donor_has['retired']
        & ~donor_has['real']
        & (donor_has['notemp'] | donor_has['selfemp'])
    ]
    if len(target_donors) == 0:
        return 0

    to_change = is_indiv & df['donor_key'].isin(target_donors) & (is_notemp | is_selfemp)
    n = int(to_change.sum())
    if n == 0:
        return 0

    df.loc[to_change, 'contributor_employer'] = 'RETIRED'

    # Only update occupation / category if they were status words themselves
    occ_upper = df.loc[to_change, 'contributor_occupation'].fillna('').astype(str).str.strip().str.upper()
    occ_is_status = occ_upper.isin(_STATUS_NON_RETIRED)
    occ_idx = df.loc[to_change].index[occ_is_status.values]
    df.loc[occ_idx, 'contributor_occupation'] = 'RETIRED'
    df.loc[occ_idx, 'occupation_category']    = 'RETIRED'

    if 'employer_status' in df.columns:
        df.loc[to_change, 'employer_status'] = 'retired'

    return n


def _propagate_previous_employer_within_donor(df: pd.DataFrame) -> int:
    """AP. Fill empty previous_employer from the donor's other retired records.

    After AO collapses NOT EMPLOYED / SELF-EMPLOYED into RETIRED, a
    donor can end up with some retired rows showing `previous_employer
    = TIGER MGMT` (from resolve.py's AI cache) and others empty (the
    cache only ran against the original RETIRED filings). Copy the
    known value to the empty rows so every retired row of a given
    donor agrees on the previous employer.

    Also catches pre-existing inconsistencies independent of AO —
    e.g., donors who always filed as RETIRED but resolve.py only
    filled prev_employer on some of their filings.

    Scope: RETIRED rows only. The value source must be another RETIRED
    row of the *same donor_key*; if multiple distinct non-empty values
    exist we pick the most frequent (ties broken by recency).

    Returns: number of rows updated.
    """
    if 'previous_employer' not in df.columns or 'donor_key' not in df.columns:
        return 0
    is_retired = (df['entity_type'] == 'INDIVIDUAL') & (df['contributor_employer'] == 'RETIRED')
    if not is_retired.any():
        return 0

    prev = df.loc[is_retired, 'previous_employer'].fillna('').astype(str).str.strip()
    empty = prev.eq('')
    if not empty.any():
        return 0

    # Simpler: get dks where there's at least one empty + at least one non-empty retired row
    ret_df = df.loc[is_retired, ['donor_key']].copy()
    ret_df['prev_empty'] = empty.values
    ret_df['prev_val']   = prev.values
    agg = ret_df.groupby('donor_key').agg(
        has_empty=('prev_empty', 'any'),
        has_value=('prev_empty', lambda x: (~x).any()),
    )
    candidate_dks = agg.index[agg['has_empty'] & agg['has_value']]
    if len(candidate_dks) == 0:
        return 0

    # For each candidate donor, most common non-empty value (tie → recency via sort beforehand)
    if 'contribution_receipt_date' in df.columns:
        ret_sorted = df.loc[is_retired].sort_values('contribution_receipt_date', ascending=False)
    else:
        ret_sorted = df.loc[is_retired]
    ret_sorted = ret_sorted[ret_sorted['donor_key'].isin(candidate_dks)]
    ret_sorted = ret_sorted[ret_sorted['previous_employer'].fillna('').astype(str).str.strip() != '']

    best: dict[str, str] = {}
    for dk, grp in ret_sorted.groupby('donor_key'):
        vc = grp['previous_employer'].value_counts()
        best[dk] = vc.index[0]

    # Apply: fill empty retired rows of these donors
    fill_mask = (
        is_retired
        & df['donor_key'].isin(best)
        & empty.reindex(df.index, fill_value=False)
    )
    n = int(fill_mask.sum())
    if n:
        df.loc[fill_mask, 'previous_employer'] = df.loc[fill_mask, 'donor_key'].map(best)
    return n


# Placeholders that look like company names but aren't — seen as
# contributor_employer for retired filers. Never copy to previous_employer.
_RETIRED_SYNC_NOT_A_COMPANY = frozenset({
    'NOT SPECIFIED', 'MR AND MRS', 'NONE', 'N/A', 'NA', 'NAN', '',
})


def _retired_active_sync(df: pd.DataFrame) -> int:
    """AQ. Sync employer_status=retired when occupation says RETIRED.

    When an FEC filer lists occupation=RETIRED but writes their former
    employer's name in contributor_employer (instead of the string
    "RETIRED"), the pipeline classifies the donor as retired in
    occupation_category but leaves employer_status=active — a logical
    contradiction that breaks the `retired/active` quality gate.

    Fix per matching row:
      1. Move contributor_employer -> previous_employer (only if
         previous_employer is empty and the employer string is a real
         company, not a placeholder like "NOT SPECIFIED" / "MR AND MRS").
      2. Unify contributor_employer -> 'RETIRED' (the convention used
         by the ~29k other retired donors).
      3. Set employer_status -> 'retired'.
      4. Clear employer address fields (a retired donor has no current
         employer, so employer_address/city/state/zip/lat/lng are
         meaningless).
      5. Mark resolve_method='retired_consistency_fix' for audit.

    Idempotent: after the first pass, zero rows match the trigger
    (occupation_category=RETIRED AND employer_status=active).

    Returns: number of rows updated.
    """
    required = {'entity_type', 'occupation_category', 'employer_status',
                'contributor_employer'}
    if not required.issubset(df.columns):
        return 0

    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['occupation_category'] == 'RETIRED')
        & (df['employer_status'] == 'active')
    )
    n = int(mask.sum())
    if not n:
        return 0

    # Step 1: copy employer -> previous_employer where safe
    if 'previous_employer' in df.columns:
        prev_empty = df['previous_employer'].fillna('').astype(str).str.strip().eq('')
        emp_up = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
        is_real_company = ~emp_up.isin(_RETIRED_SYNC_NOT_A_COMPANY)
        copy_mask = mask & prev_empty & is_real_company
        if copy_mask.any():
            df.loc[copy_mask, 'previous_employer'] = df.loc[copy_mask, 'contributor_employer']

    # Step 2-3: unify to retired-convention
    df.loc[mask, 'contributor_employer'] = 'RETIRED'
    df.loc[mask, 'employer_status'] = 'retired'

    # Step 4: clear employer address fields (no current employer for retirees)
    for col in ('employer_address', 'employer_city', 'employer_state',
                'employer_zip', 'employer_latitude', 'employer_longitude'):
        if col in df.columns:
            df.loc[mask, col] = pd.NA
    if 'employer_geocode_level' in df.columns:
        df.loc[mask, 'employer_geocode_level'] = 'not_applicable'

    # Step 5: audit trail
    if 'resolve_method' in df.columns:
        df.loc[mask, 'resolve_method'] = 'retired_consistency_fix'
    if 'resolve_confidence' in df.columns:
        df.loc[mask, 'resolve_confidence'] = 'NONE'

    return n


def _normalize_previous_employer(df: pd.DataFrame) -> int:
    """AX. previous_employer holds a real company, 'SELF-EMPLOYED', or nothing.

    Thin delegation — the contract lives in fec/cleaning/previous_employer.py,
    shared with the resolve stage, so there is exactly one definition of what
    the column may contain. Idempotent, so a second clean.py run reports 0.
    """
    return normalize_previous_employer_column(df)
