"""cleaning/pipeline — the main cleaning pipeline.

`clean()` composes every cleaning step into one call; each step logs what it did.
The steps themselves live in focused sibling modules:

    reclassify    — entity-type reclassification (INDIVIDUAL / ORG / COMMITTEE)
    names         — contributor-name parsing & tidy-up
    address_fixes — per-donor state/ZIP/street correction & recovery
    reports       — sanity check + missing-data report

Field-level street/city/ZIP and employer/occupation cleaning live one level up in
`fec.cleaning.addresses` and `fec.cleaning.occupations`.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

from fec.config import US_STATES, STATE_NAMES, MISSING_VALUES, OUTPUT_COLUMNS
from fec.cleaning.addresses import clean_streets, clean_cities, clean_zips
from fec.cleaning.address_review import apply_safe_fixes, build_address_reports
from fec.cleaning.pipeline.fec_recovery import recover_addresses_from_fec
from fec.cleaning.occupations import clean_employer_occupation
from fec.log import get_logger

from .reclassify import _reclassify_entities, _restore_reclassified_committees
from .names import _clean_names
from .address_fixes import (
    _recover_null_streets, _recover_nonstreet_from_donor, _recover_house_number_from_donor,
    _fix_impossible_city_states, _fix_state_zip_mismatches,
    _unify_street_spellings, _unify_street_spacing, _unify_unit_designators,
    _recover_address_from_same_street,
)
from .reports import _sanity_check, _build_missing_report

logger = get_logger(__name__)

__all__ = ["clean", "clean_rows", "unify_donors", "clean_and_match"]


def clean(df: pd.DataFrame, verbose: bool = True, fuzzy_city: bool = True,
          out_dir: str = None, audit: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Main field-level cleaning pipeline. Runs the steps in order (each marked
    with a `# ── N ──` header below) and returns (cleaned_df, missing_report_df)."""
    start = time.time()
    log = logger.info if verbose else lambda msg: None

    # ── 1. Duplicates ──

    dup_count = int(df['sub_id'].duplicated().sum())
    if dup_count:
        log(f"⚠ Duplicates: {dup_count} duplicate sub_ids → dropping")
        df = df.drop_duplicates(subset='sub_id', keep='first').copy()
    else:
        log("Duplicates: none ✓")

    # ── 2. State filter ──

    df['contributor_state'] = df['contributor_state'].astype(str).str.strip().str.upper()
    before = len(df)
    df = df[df['contributor_state'].isin(US_STATES)].copy()
    dropped = before - len(df)
    log(f"State filter: {before:,} → {len(df):,} (dropped {dropped:,})")

    # ── 3. Track missing occupation/employer (before we fill them) ──

    def _mark_missing(field):
        raw = df[field].astype('string').fillna('').str.strip().str.upper()
        return df[field].isna() | (raw == '') | raw.isin(MISSING_VALUES)

    df['_occ_missing'] = _mark_missing('contributor_occupation')
    df['_emp_missing'] = _mark_missing('contributor_employer')

    # ── 4. Dates & flags ──

    df['contribution_receipt_date'] = pd.to_datetime(
        df['contribution_receipt_date'], errors='coerce'
    )

    # Fill missing year from the contribution date
    if 'contributor_year' in df.columns:
        missing_year = df['contributor_year'].isna()
        if missing_year.any():
            df.loc[missing_year, 'contributor_year'] = (
                df.loc[missing_year, 'contribution_receipt_date'].dt.year
            )

    # ── 5. Sanity checks ──

    warnings = _sanity_check(df)
    if warnings:
        log(f"Sanity: {'; '.join(warnings)}")
    else:
        log("Sanity: all amounts & dates OK ✓")

    # ── 6. Normalize is_individual ──

    if 'is_individual' in df.columns:
        raw = df['is_individual'].astype('string').fillna('').str.strip().str.lower()
        df['is_individual'] = raw.isin(('true', 't', '1', 'yes'))

    # ── 6b. Save raw occupation/employer BEFORE step 7 clears them ──
    # Step 7 clears occupation for all records currently marked as committee.
    # Step 8 may reclassify some of those back to INDIVIDUAL.
    # We need the originals to restore them.
    _raw_occ_backup = df['contributor_occupation'].copy()
    _raw_emp_backup = df['contributor_employer'].copy()

    # ── 7. Clean employer & occupation ──

    df, occ_counts = clean_employer_occupation(df)
    log(
        f"Occupations: {occ_counts['normalized']:,} normalized, "
        f"{occ_counts['occ_fixed']:,} typo-fixed, "
        f"{occ_counts['comm_filled']:,} committees filled"
    )

    # ── 8. Reclassify mistyped committees → INDIVIDUAL ──

    n_to_indiv, n_to_comm = _reclassify_entities(df)
    log(f"Reclassified {n_to_indiv:,} committee → INDIVIDUAL, {n_to_comm:,} individual → COMMITTEE")

    # ── 8b/c/d. Restore + clean up committee → individual reclassifications ──
    _restore_reclassified_committees(df, _raw_occ_backup, _raw_emp_backup, log)

    # ── 9. Clean names ──

    _clean_names(df)

    # ── 10. Clean addresses ──

    df, street_counts = clean_streets(df)
    log(
        f"Streets: {street_counts['streets_normalized']:,} normalized, "
        f"{street_counts['units_extracted']:,} units extracted"
    )

    # ── 10-safe. Extra deterministic text fixes (house #, dup word, trailing
    # unit → street_2). Runs here so the cleaned values feed the dedup below. ──
    df, safe_counts = apply_safe_fixes(df)
    if safe_counts['house_number'] or safe_counts['unit_split'] or safe_counts['care_of']:
        log(
            f"Streets: {safe_counts['house_number']:,} house-number/dup fixes, "
            f"{safe_counts['unit_split']:,} trailing units split, "
            f"{safe_counts['care_of']:,} C/O prefixes stripped"
        )

    # ── 10a. Recover NULL streets from other records of same person ──
    # When FEC has an email instead of address, the street gets nulled.
    # If the same person (name+city+state) has a real street in another
    # record, we can fill it in.
    n_recovered = _recover_null_streets(df)
    if n_recovered:
        log(f"Streets: recovered {n_recovered:,} from other records of same donor")

    # Same idea for a street_1 that is present but NOT a usable street — a
    # place-name or fragment ("GOLDEN BEACH", "RING HOUSE … 1801 E JE") the
    # donor wrote instead of their address. Recover the real street from their
    # own other filings (never overwrites a good street).
    n_nonstreet = _recover_nonstreet_from_donor(df)
    if n_nonstreet:
        log(f"Streets: recovered {n_nonstreet:,} non-street fragments from same donor")

    # A street with a type token but no house number ("FAIRWAY DR") is "usable"
    # enough to skip the fragment recovery above, yet only geocodes to a centroid.
    # Backfill the number from the donor's own numbered filing of the same street.
    n_housenum = _recover_house_number_from_donor(df)
    if n_housenum:
        log(f"Streets: backfilled {n_housenum:,} missing house numbers from same donor")

    # Last resort for a street still unknown: the donor gave a full address on
    # contributions to OTHER committees nationwide. Pull it from the FEC API
    # (cached + network-gated), normalised through the same street cleaner.
    n_fec = recover_addresses_from_fec(df, out_dir)
    if n_fec:
        log(f"Streets: recovered {n_fec:,} from FEC.gov (other committees, cached)")

    df, city_counts = clean_cities(df, fuzzy=fuzzy_city, report_dir=out_dir)
    log(
        f"Cities: {city_counts['known_fixes']:,} known fixes, "
        f"{city_counts['fuzzy_fixes']:,} fuzzy fixes, "
        f"{city_counts['punctuation_cleaned']:,} punctuation cleaned"
    )

    df, zip_counts = clean_zips(df)
    log(f"ZIPs: {zip_counts['cleaned']:,} cleaned, {zip_counts['invalid_nulled']:,} invalid → null")

    # ── 10a. Impossible-city-state fix ──
    # Catch double-typos the ZIP/state vote can't: rows where state AND ZIP
    # are both wrong but agree with each other (e.g. "AVENTURA, NJ, 07094" —
    # 07094 is a real NJ ZIP, so it looks self-consistent, but Aventura only
    # exists in FL). Run BEFORE the ZIP/state vote so the corrected state
    # flows into it and the now-conflicting ZIP gets nulled.
    n_city_state = _fix_impossible_city_states(df)
    if n_city_state:
        log(f"City-state: {n_city_state:,} impossible states fixed (city is the witness)")

    # ── 10b. State-ZIP validation ──
    # When ZIP and contributor_state disagree, a 3-way vote with the city
    # decides which field is the typo: fix the state (keep the good ZIP) or
    # null the ZIP (keep the good state).
    zip_state_counts = _fix_state_zip_mismatches(df)
    if zip_state_counts['state_fixed'] or zip_state_counts['zip_nulled']:
        log(
            f"State-ZIP: {zip_state_counts['state_fixed']:,} states fixed (ZIP kept), "
            f"{zip_state_counts['zip_nulled']:,} ZIPs nulled (state kept)"
        )

    # ── 10b-2. Unify street spellings per donor ──
    # Same person + same physical address written two ways (lost space /
    # dropped hyphen / reordered directional): "2425 NW L ST" vs
    # "2425 LST NW", "E 72ND ST" vs "E72ND ST". Collapse to the donor's
    # most-common spelling so the same home isn't two address rows. Runs
    # BEFORE same-street recovery so that step groups the unified streets.
    n_street_spell = _unify_street_spellings(df)
    if n_street_spell:
        log(f"Streets: unified {n_street_spell:,} spelling variants (same donor + same address)")

    # ── 10b-3. Unify spacing/punctuation variants the token fingerprint misses ──
    # "MEADOW RIDGE WAY" vs "MEADOWRIDGE WAY", "PH-2" vs "PH2": identical
    # letters/digits, same order — provably the same address, never a move.
    n_street_space = _unify_street_spacing(df)
    if n_street_space:
        log(f"Streets: unified {n_street_space:,} spacing/punctuation variants (same address)")

    # ── 10c. Same-street address recovery ──
    # The street is the physical anchor: fill blank ZIPs and fix minority
    # city/state/ZIP typos from the dominant value the same donor used at
    # that exact street (e.g. recover a nulled ZIP from their own later
    # filings at the same address). A move = a different street = own group.
    rec = _recover_address_from_same_street(df)
    if rec['zip'] or rec['city'] or rec['state']:
        log(
            f"Same-street: {rec['zip']:,} ZIPs filled, "
            f"{rec['city']:,} cities + {rec['state']:,} states aligned"
        )

    # ── 10c-2. Unify unit designators in street_2 per donor ──
    # Runs AFTER same-street recovery so city/state/ZIP are already aligned —
    # otherwise a donor whose two unit-designator filings had a ZIP/state typo
    # land in different groups and don't merge. Same person + same building +
    # same unit number written differently ("APT 1503" vs "UNIT 1503" vs
    # "# 1503") splits one home into duplicate Address-History cards (the
    # addresses dimension keys on street_1 + street_2). Collapse to the donor's
    # dominant form; scoped per donor + unit id, so distinct units — or other
    # people in the same building — are never merged.
    n_unit = _unify_unit_designators(df)
    if n_unit:
        log(f"Streets: unified {n_unit:,} unit-designator variants (same donor + same unit)")

    # ── 10e. Address review reports (detection only — no value invented) ──
    # Flags rows a human / re-geocode should look at, into two CSVs under
    # out_dir. Also empties a clearly-wrong street_2 (state abbrev / unit
    # keyword with no number) — the only edit this step makes.
    df, review_counts = build_address_reports(df, out_dir)
    if review_counts['manual_review'] or review_counts['regeocode']:
        log(
            f"Address review: {review_counts['manual_review']:,} flagged for manual review, "
            f"{review_counts['regeocode']:,} for re-geocoding "
            f"({review_counts['street2_emptied']:,} bad street_2 emptied)"
        )

    df['state_name'] = df['contributor_state'].map(STATE_NAMES)

    # ── 10d. Recipient committee name ──
    # Replace the cryptic FEC committee_id with the recipient PAC's name
    # (AIPAC / DMFI / UDP), resolved from data/database/committees.csv. Any id
    # not in that file falls back to the raw number so nothing silently drops.
    if 'committee_id' in df.columns:
        from fec.committees import committee_id_to_name
        names = committee_id_to_name()
        df['recipient_committee'] = df['committee_id'].map(names).fillna(df['committee_id'])
        unknown = sorted(set(df.loc[df['committee_id'].notna() &
                                    ~df['committee_id'].isin(names), 'committee_id']))
        if unknown:
            log(f"⚠ committee_id(s) not in committees.csv (kept raw): {unknown}")

    # ── 11. Missing report ──

    missing = _build_missing_report(df)

    # ── 12. Ensure computed columns exist ──
    # These should have been created in steps 7-8, but verify to prevent silent drops.
    for required_col in ('occupation_status', 'committee_type'):
        if required_col not in df.columns:
            log(f"⚠ Missing column '{required_col}' — recreating")
            if required_col == 'occupation_status':
                df['occupation_status'] = 'MISSING'
                has_occ = df['contributor_occupation'].notna() & (df['contributor_occupation'] != '')
                has_emp = df['contributor_employer'].notna() & (df['contributor_employer'] != '')
                df.loc[has_occ & has_emp & (df['entity_type'] == 'INDIVIDUAL'), 'occupation_status'] = 'DISCLOSED'
                df.loc[has_occ & ~has_emp & (df['entity_type'] == 'INDIVIDUAL'), 'occupation_status'] = 'EMPLOYER_MISSING'
                df.loc[df['entity_type'] == 'COMMITTEE/PAC', 'occupation_status'] = 'NOT_APPLICABLE'
            elif required_col == 'committee_type':
                df['committee_type'] = pd.Series(dtype='object', index=df.index)

    # ── 12. Select & order output columns ──

    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]

    # Keep internal audit columns if requested (they will be dropped before saving output).
    if audit:
        internal_cols = [
            '_reclass_reason',
            '_street_email_in_s1', '_street_swapped_from_s2', '_street_nulled_email',
            '_garbled_before',
        ]
        cols += [c for c in internal_cols if c in df.columns]

    df = df[cols]

    elapsed = time.time() - start
    log(f"Done: {len(df):,} rows in {elapsed:.1f}s")
    return df, missing


_SUFFIX_RE = re.compile(r'\b(JR|SR|II|III|IV)\b\.?')


def _report_suffix_merge_suspects(df: pd.DataFrame, out_dir, raw_names=None) -> int:
    """Flag donor_keys that merged a JR/SR-marked name with an unmarked one.

    This is the one over-merge shape that matters here: a father and son share a
    surname, a first name and usually an address, so every similarity signal the
    matcher has says "same person" — only the suffix says otherwise. The house
    rule is that two different people must never share a key, so the pattern is
    surfaced for a human rather than trusted either way.

    Reads the RAW filing names, not df's — clean() rebuilds contributor_name from
    the parsed first/last and drops the suffix on the way ("LEVY JR, EDWARD" ->
    "LEVY, EDWARD"), so checking the cleaned column would find nothing, always,
    and report a clean bill of health it never actually verified.

    Detect-only, and deliberately NOT auto-split: the same shape is far more
    often ONE person whose filers are inconsistent about the suffix. A verified
    example — Edward C. Levy Jr. of Edw. C. Levy Co. files as "LEVY, EDWARD",
    "LEVY, ED" and "LEVY JR, EDWARD"; the company's founder (his father, 1918)
    is long dead, so the cluster is correct. Splitting on the suffix alone would
    have shattered a real 107-filing donor.

    Reports only keys where the names differ BEYOND the suffix — if the suffix is
    the sole difference, sloppy filing is the whole story and there is nothing to
    look at.
    """
    if out_dir is None or 'donor_key' not in df.columns or not raw_names:
        return 0
    ind = df[df['entity_type'] == 'INDIVIDUAL']
    if 'sub_id' not in ind.columns:
        return 0
    per_key = defaultdict(set)
    for sub_id, key in zip(ind['sub_id'].astype(str), ind['donor_key']):
        original = raw_names.get(sub_id)
        if pd.notna(original) and str(original).strip():
            per_key[key].add(str(original).strip().upper())

    rows = []
    for key, names in per_key.items():
        if len(names) < 2:
            continue
        marked = [n for n in names if _SUFFIX_RE.search(n)]
        if not marked or len(marked) == len(names):
            continue
        # collapse the suffix + punctuation: what's left is the person's name
        base = {' '.join(_SUFFIX_RE.sub('', n).replace('.', ' ').replace(',', ' ').split())
                for n in names}
        if len(base) > 1:
            rows.append({'donor_key': key, 'n_filings': int((ind['donor_key'] == key).sum()),
                         'names': ' | '.join(sorted(names))})
    if not rows:
        logger.info("  ✓ Over-merge guard: no JR/SR cluster needs review")
        return 0
    out = Path(out_dir) / 'donor_suffix_merge_review.csv'
    pd.DataFrame(rows).to_csv(out, index=False)
    logger.info(f"  ⚠ Over-merge guard: {len(rows)} JR/SR cluster(s) to review → {out.name}")
    return len(rows)


def clean_rows(df: pd.DataFrame, fuzzy_city: bool = True,
               out_dir: str = None, audit: bool = False):
    """Per-row half of the run: clean() + enhancements + manual employer
    overrides. Safe on any subset of the data.

    Returns: (df_clean, missing_report, enh_audit)
    """
    df_clean, missing = clean(df, fuzzy_city=fuzzy_city, out_dir=out_dir,
                              audit=audit)

    logger.info("\n── Enhancements ──")
    from fec.cleaning.enhancements import run_enhancements
    df_clean, _enh_report, enh_audit = run_enhancements(df_clean)

    # hand-curated per-donor fixes, keyed by sub_id; survive every re-clean
    from fec.cleaning.manual_overrides import apply_manual_employer_overrides
    n_ovr = apply_manual_employer_overrides(df_clean)
    if n_ovr:
        logger.info(f"  ✓ Manual employer overrides applied: {n_ovr:,} rows")

    return df_clean, missing, enh_audit


def unify_donors(df_clean: pd.DataFrame, out_dir: str = None,
                 raw_names: dict = None) -> pd.DataFrame:
    """Cross-row half of the run: donor matching, per-donor canonicalization,
    post-merge fixes, and the final non-individual name clear.

    Must see the COMPLETE dataset — donor_key is the hash of a cluster root
    chosen from the rows given, so a partial batch gets keys that don't line
    up with rows outside it. raw_names maps sub_id → original filing name
    for the JR/SR over-merge guard.
    """
    # per-donor canonicalizers use iat positions; labels must equal positions
    df_clean = df_clean.reset_index(drop=True)
    logger.info("\n── Donor Matching ──")
    from fec.database.donor_match import (  # noqa: F401  (see _report_suffix_merge_suspects)
        match_donors, apply_donor_key, merge_split_name_donors,
        apply_donor_dedup_merges,
        canonicalize_donor_names, canonicalize_donor_employers,
        canonicalize_donor_addresses, canonicalize_donor_pobox_typos,
        canonicalize_donor_units, align_org_donor_company_names,
        build_donor_dedup_review,
    )
    rid_to_key, _ = match_donors(df_clean, verbose=True)
    df_clean = apply_donor_key(df_clean, rid_to_key)
    # Over-merge guard — MUST run here, before canonicalize_donor_names below
    # rewrites every row of a cluster to one spelling. After that pass a key can
    # only ever show ONE name, so any "do two people share a key?" check on the
    # final CSV is vacuous: it cannot fail, and proves nothing.
    _report_suffix_merge_suspects(df_clean, out_dir, raw_names)
    # Each canonicalization is scoped to ONE donor, so unrelated records are
    # never merged. Order matters: names before employers/addresses.
    for fix, label in (
        (merge_split_name_donors,        "Split-name merge: {n:,} rows repointed to canonical donor"),
        (apply_donor_dedup_merges,       "Reviewed dedup merges: {n:,} rows repointed (human-curated pairs)"),
        (canonicalize_donor_names,       "Canonical names: {n:,} rows normalized to per-donor name"),
        (canonicalize_donor_employers,   "Canonical employers: {n:,} rows merged to per-donor firm name"),
        (align_org_donor_company_names,  "Org-donor names aligned to employer firm: {n:,} rows (no duplicate company)"),
        (canonicalize_donor_addresses,   "Canonical addresses: {n:,} rows normalized to per-donor address form"),
        (canonicalize_donor_units,       "Canonical units: {n:,} street_2 unit-designators unified per donor"),
        (canonicalize_donor_pobox_typos, "PO-box typos: {n:,} rows collapsed to canonical box"),
    ):
        n = fix(df_clean)
        if n:
            logger.info("  " + label.format(n=n))

    # Manual name corrections are a human decision, so they have to win over the
    # automatic canonicalization above — canonicalize_donor_names rewrites every
    # row of a donor to their most common spelling, which for a mis-parsed org
    # name is exactly the wrong "LAST, FIRST" form the correction exists to undo.
    # Re-asserting here (idempotent, exact-match on 4 names) keeps the earlier
    # pass in enhancements from being silently reverted.
    from fec.cleaning.entity_classification import apply_name_corrections
    df_clean, n_name_reassert = apply_name_corrections(df_clean)
    if n_name_reassert:
        logger.info(f"  Manual name corrections re-asserted: {n_name_reassert:,} rows")

    n_donors = df_clean[df_clean['entity_type'] == 'INDIVIDUAL']['donor_key'].nunique()
    logger.info(f"  ✓ {n_donors:,} unique donors")

    # ── Post-merge fixes (need donor_key) ──
    from fec.database.post_merge_fixes import apply_post_merge_fixes
    n_post = apply_post_merge_fixes(df_clean)
    if n_post:
        logger.info(f"  Post-merge fixes: {n_post:,} records corrected")

    # ── Donor-dedup review (detection only — writes a report, never merges) ──
    # Surface likely-same-person pairs the matcher left split (same ZIP+surname,
    # related first names) for a human to judge. Only when out_dir is set.
    build_donor_dedup_review(df_clean, out_dir)

    # ── Only individuals have a personal first/last name ──
    # Name parsing splits any "LAST, FIRST" string, so a committee/org name
    # with a comma (e.g. "TEAM GRAHAM, INC., LINDSEY SEN.") wrongly lands a
    # first/last. entity_type is canonical: clear those so non-individuals
    # carry the org name in contributor_name only. Runs last so nothing
    # repopulates them.
    name_cols = [c for c in ('contributor_first_name', 'contributor_last_name')
                 if c in df_clean.columns]
    if 'entity_type' in df_clean.columns and name_cols:
        mask = ((df_clean['entity_type'] != 'INDIVIDUAL')
                & df_clean[name_cols].notna().any(axis=1))
        if mask.any():
            df_clean.loc[mask, name_cols] = np.nan
            logger.info(f"  Cleared first/last on {int(mask.sum()):,} non-individual rows")

    return df_clean


def clean_and_match(df: pd.DataFrame, fuzzy_city: bool = True,
                    out_dir: str = None, audit: bool = False):
    """The COMPLETE cleaning run: clean_rows() then unify_donors().
    What `python clean.py` executes on a full run; the incremental path
    calls the two halves separately.

    Returns: (df_clean, missing_report, enh_audit)
    """
    # snapshot raw names before clean() drops generational suffixes —
    # the JR/SR over-merge guard needs the originals
    raw_names = (dict(zip(df['sub_id'].astype(str), df['contributor_name'].astype(str)))
                 if {'sub_id', 'contributor_name'}.issubset(df.columns) else None)

    df_clean, missing, enh_audit = clean_rows(df, fuzzy_city=fuzzy_city,
                                              out_dir=out_dir, audit=audit)
    df_clean = unify_donors(df_clean, out_dir=out_dir, raw_names=raw_names)
    return df_clean, missing, enh_audit
