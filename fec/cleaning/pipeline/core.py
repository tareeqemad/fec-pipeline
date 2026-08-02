"""Cleaning pipeline: clean() is the field-level pass, clean_rows() + unify_donors() = clean_and_match()."""
from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config import US_STATES, STATE_NAMES, MISSING_VALUES, OUTPUT_COLUMNS
from fec.cleaning.addresses import clean_streets, clean_cities, clean_zips
from fec.cleaning.address_review import apply_safe_fixes, build_address_reports
from fec.cleaning.occupations import clean_employer_occupation
from fec.log import get_logger

from .reclassify import _reclassify_entities, _restore_reclassified_committees
from .names import _clean_names
from .address_fixes import (
    _recover_null_streets, _recover_nonstreet_from_donor, _recover_house_number_from_donor,
    _fix_impossible_city_states, _fix_state_zip_mismatches, _unify_street_spellings,
    _unify_street_spacing, _unify_unit_designators, _recover_address_from_same_street,
)
from .fec_recovery import recover_addresses_from_fec
from .reports import _sanity_check, _build_missing_report

logger = get_logger(__name__)

# generational suffixes, for the JR/SR over-merge guard on raw names
_SUFFIX_RE = re.compile(r'\b(JR|SR|II|III|IV)\b\.?')


def clean(df: pd.DataFrame, verbose: bool = True, fuzzy_city: bool = True,
          out_dir: str | None = None, audit: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every field-level cleaning step in order; returns (cleaned_df, missing_report_df)."""
    start = time.time()
    log = logger.info if verbose else lambda msg: None

    dup_count = int(df['sub_id'].duplicated().sum())
    if dup_count:
        log(f"WARNING: Duplicates: {dup_count} duplicate sub_ids -> dropping")
        df = df.drop_duplicates(subset='sub_id', keep='first').copy()
    else:
        log("Duplicates: none")

    df['contributor_state'] = df['contributor_state'].astype(str).str.strip().str.upper()
    before = len(df)
    df = df[df['contributor_state'].isin(US_STATES)].copy()
    dropped = before - len(df)
    log(f"State filter: {before:,} -> {len(df):,} (dropped {dropped:,})")

    # record missing occupation/employer before later steps fill them
    def _mark_missing(field):
        raw = df[field].astype('string').fillna('').str.strip().str.upper()
        return df[field].isna() | (raw == '') | raw.isin(MISSING_VALUES)

    df['_occ_missing'] = _mark_missing('contributor_occupation')
    df['_emp_missing'] = _mark_missing('contributor_employer')

    df['contribution_receipt_date'] = pd.to_datetime(
        df['contribution_receipt_date'], errors='coerce')

    warnings = _sanity_check(df)
    if warnings:
        log(f"Sanity: {'; '.join(warnings)}")
    else:
        log("Sanity: all amounts & dates OK")
    raw = df['is_individual'].astype('string').fillna('').str.strip().str.lower()
    df['is_individual'] = raw.isin(('true', 't', '1', 'yes'))

    # reclassify may flip committee rows back to INDIVIDUAL after the next step
    # clears their occupation; keep the originals for the restore
    _raw_occ_backup = df['contributor_occupation'].copy()
    _raw_emp_backup = df['contributor_employer'].copy()
    df, occ_counts = clean_employer_occupation(df)
    log(f"Occupations: {occ_counts['normalized']:,} normalized, "
        f"{occ_counts['occ_fixed']:,} typo-fixed, {occ_counts['comm_filled']:,} committees filled")

    n_to_indiv, n_to_comm = _reclassify_entities(df)
    log(f"Reclassified {n_to_indiv:,} committee -> INDIVIDUAL, {n_to_comm:,} individual -> COMMITTEE")
    _restore_reclassified_committees(df, _raw_occ_backup, _raw_emp_backup, log)

    _clean_names(df)

    df, street_counts = clean_streets(df)
    log(f"Streets: {street_counts['streets_normalized']:,} normalized, "
        f"{street_counts['units_extracted']:,} units extracted")

    # deterministic text fixes run here so cleaned values feed the recovery/dedup below
    df, safe_counts = apply_safe_fixes(df)
    if safe_counts['house_number'] or safe_counts['unit_split'] or safe_counts['care_of']:
        log(f"Streets: {safe_counts['house_number']:,} house-number/dup fixes, "
            f"{safe_counts['unit_split']:,} trailing units split, "
            f"{safe_counts['care_of']:,} C/O prefixes stripped")

    n_recovered = _recover_null_streets(df)
    if n_recovered:
        log(f"Streets: recovered {n_recovered:,} from other records of same donor")
    n_nonstreet = _recover_nonstreet_from_donor(df)
    if n_nonstreet:
        log(f"Streets: recovered {n_nonstreet:,} non-street fragments from same donor")
    # a typed street with no house number geocodes to a centroid; backfill the number
    n_housenum = _recover_house_number_from_donor(df)
    if n_housenum:
        log(f"Streets: backfilled {n_housenum:,} missing house numbers from same donor")
    # last resort: the donor's FEC-wide filings (network-gated, cached)
    n_fec = recover_addresses_from_fec(df, out_dir)
    if n_fec:
        log(f"Streets: recovered {n_fec:,} from FEC.gov (other committees, cached)")

    df, city_counts = clean_cities(df, fuzzy=fuzzy_city, report_dir=out_dir)
    log(f"Cities: {city_counts['known_fixes']:,} known fixes, "
        f"{city_counts['fuzzy_fixes']:,} fuzzy fixes, "
        f"{city_counts['punctuation_cleaned']:,} punctuation cleaned")

    df, zip_counts = clean_zips(df)
    log(f"ZIPs: {zip_counts['cleaned']:,} cleaned, {zip_counts['invalid_nulled']:,} invalid -> null")
    # must run BEFORE the state-ZIP vote: state and ZIP agree but the city proves both wrong
    n_city_state = _fix_impossible_city_states(df)
    if n_city_state:
        log(f"City-state: {n_city_state:,} impossible states fixed (city is the witness)")

    zip_state_counts = _fix_state_zip_mismatches(df)
    if zip_state_counts['state_fixed'] or zip_state_counts['zip_nulled']:
        log(f"State-ZIP: {zip_state_counts['state_fixed']:,} states fixed (ZIP kept), "
            f"{zip_state_counts['zip_nulled']:,} ZIPs nulled (state kept)")

    # runs BEFORE same-street recovery so that step groups the unified streets
    n_street_spell = _unify_street_spellings(df)
    if n_street_spell:
        log(f"Streets: unified {n_street_spell:,} spelling variants (same donor + same address)")
    n_street_space = _unify_street_spacing(df)
    if n_street_space:
        log(f"Streets: unified {n_street_space:,} spacing/punctuation variants (same address)")

    recovered = _recover_address_from_same_street(df)
    if recovered['zip'] or recovered['city'] or recovered['state']:
        log(f"Same-street: {recovered['zip']:,} ZIPs filled, "
            f"{recovered['city']:,} cities + {recovered['state']:,} states aligned")
    # runs AFTER same-street recovery so a donor's unit variants land in one group
    n_unit = _unify_unit_designators(df)
    if n_unit:
        log(f"Streets: unified {n_unit:,} unit-designator variants (same donor + same unit)")

    # detection only, except that a clearly-wrong street_2 is emptied
    df, review_counts = build_address_reports(df, out_dir)
    if review_counts['manual_review'] or review_counts['regeocode']:
        log(f"Address review: {review_counts['manual_review']:,} flagged for manual review, "
            f"{review_counts['regeocode']:,} for re-geocoding "
            f"({review_counts['street2_emptied']:,} bad street_2 emptied)")
    df['state_name'] = df['contributor_state'].map(STATE_NAMES)
    # committee_id -> recipient PAC name; unknown ids keep the raw id so nothing drops
    from fec.committees import committee_id_to_name
    names = committee_id_to_name()
    df['recipient_committee'] = df['committee_id'].map(names).fillna(df['committee_id'])
    unknown = sorted(set(df.loc[df['committee_id'].notna() &
                                ~df['committee_id'].isin(names), 'committee_id']))
    if unknown:
        log(f"WARNING: committee_id(s) not in committees.csv (kept raw): {unknown}")

    missing = _build_missing_report(df)

    cols = [column for column in OUTPUT_COLUMNS if column in df.columns]
    # internal audit columns ride along and are dropped before saving output
    if audit:
        internal_cols = ['_reclass_reason', '_street_email_in_s1',
                         '_street_swapped_from_s2', '_street_nulled_email', '_garbled_before']
        cols += [column for column in internal_cols if column in df.columns]
    df = df[cols]

    elapsed = time.time() - start
    log(f"Done: {len(df):,} rows in {elapsed:.1f}s")
    return df, missing


def _report_suffix_merge_suspects(df: pd.DataFrame, out_dir, raw_names=None) -> int:
    """Report donor_keys that merged a JR/SR-marked RAW name with an unmarked one differing beyond the suffix; detect-only (the shape is usually one person), reads raw names because clean() strips suffixes."""
    if out_dir is None or 'donor_key' not in df.columns or not raw_names:
        return 0
    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    if 'sub_id' not in individuals.columns:
        return 0
    per_key = defaultdict(set)
    for sub_id, key in zip(individuals['sub_id'].astype(str), individuals['donor_key']):
        original = raw_names.get(sub_id)
        if pd.notna(original) and str(original).strip():
            per_key[key].add(str(original).strip().upper())

    rows = []
    for key, names in per_key.items():
        if len(names) < 2:
            continue
        marked = [name for name in names if _SUFFIX_RE.search(name)]
        if not marked or len(marked) == len(names):
            continue
        # collapse the suffix + punctuation: what's left is the person's name
        base = {' '.join(_SUFFIX_RE.sub('', name).replace('.', ' ').replace(',', ' ').split())
                for name in names}
        if len(base) > 1:
            rows.append({'donor_key': key, 'n_filings': int((individuals['donor_key'] == key).sum()),
                         'names': ' | '.join(sorted(names))})
    if not rows:
        logger.info("  Over-merge guard: no JR/SR cluster needs review")
        return 0
    out = Path(out_dir) / 'donor_suffix_merge_review.csv'
    pd.DataFrame(rows).to_csv(out, index=False)
    logger.info(f"  WARNING: Over-merge guard: {len(rows)} JR/SR cluster(s) to review -> {out.name}")
    return len(rows)


def clean_rows(df: pd.DataFrame, fuzzy_city: bool = True,
               out_dir: str | None = None, audit: bool = False):
    """Per-row half, safe on any subset: clean() + enhancements + manual employer overrides; returns (df_clean, missing_report, enh_audit)."""
    df_clean, missing = clean(df, fuzzy_city=fuzzy_city, out_dir=out_dir,
                              audit=audit)

    logger.info("\n-- Enhancements --")
    from fec.cleaning.enhancements import run_enhancements
    df_clean, _enh_report, enh_audit = run_enhancements(df_clean)

    # hand-curated per-donor fixes keyed by sub_id; survive every re-clean
    from fec.cleaning.manual_overrides import apply_manual_employer_overrides
    n_overrides = apply_manual_employer_overrides(df_clean)
    if n_overrides:
        logger.info(f"  Manual employer overrides applied: {n_overrides:,} rows")

    return df_clean, missing, enh_audit


def unify_donors(df_clean: pd.DataFrame, out_dir: str | None = None,
                 raw_names: dict | None = None) -> pd.DataFrame:
    """Cross-row half: donor matching, per-donor canonicalization, post-merge fixes, non-individual name clear; MUST see the complete dataset or donor_keys won't line up with rows outside the batch."""
    # per-donor canonicalizers use iat positions; labels must equal positions
    df_clean = df_clean.reset_index(drop=True)
    logger.info("\n-- Donor Matching --")
    from fec.donor_match import (
        match_donors, apply_donor_key, merge_split_name_donors,
        apply_donor_dedup_merges,
        canonicalize_donor_names, canonicalize_donor_employers,
        canonicalize_donor_addresses, canonicalize_donor_pobox_typos,
        canonicalize_donor_units, align_org_donor_company_names,
        build_donor_dedup_review,
    )
    rid_to_key, _ = match_donors(df_clean, verbose=True)
    df_clean = apply_donor_key(df_clean, rid_to_key)
    # over-merge guard must run before canonicalize_donor_names rewrites each
    # cluster to one spelling (after that, the check can never fail)
    _report_suffix_merge_suspects(df_clean, out_dir, raw_names)
    # each canonicalization is scoped to ONE donor; order matters: names first
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
        count = fix(df_clean)
        if count:
            logger.info("  " + label.format(n=count))

    # manual name corrections are a human decision and must win over
    # canonicalize_donor_names; re-asserting here is idempotent
    from fec.cleaning.entity_classification import apply_name_corrections
    df_clean, n_name_reassert = apply_name_corrections(df_clean)
    if n_name_reassert:
        logger.info(f"  Manual name corrections re-asserted: {n_name_reassert:,} rows")

    n_donors = df_clean[df_clean['entity_type'] == 'INDIVIDUAL']['donor_key'].nunique()
    logger.info(f"  {n_donors:,} unique donors")

    # post-merge fixes need donor_key
    from fec.database.post_merge_fixes import apply_post_merge_fixes
    n_post = apply_post_merge_fixes(df_clean)
    if n_post:
        logger.info(f"  Post-merge fixes: {n_post:,} records corrected")

    # detection only: writes a review report (out_dir set), never merges
    build_donor_dedup_review(df_clean, out_dir)

    # entity_type is canonical: only individuals keep first/last (a committee
    # name with a comma parses as one); runs last so nothing repopulates them
    name_cols = [column for column in ('contributor_first_name', 'contributor_last_name')
                 if column in df_clean.columns]
    if 'entity_type' in df_clean.columns and name_cols:
        mask = ((df_clean['entity_type'] != 'INDIVIDUAL')
                & df_clean[name_cols].notna().any(axis=1))
        if mask.any():
            df_clean.loc[mask, name_cols] = np.nan
            logger.info(f"  Cleared first/last on {int(mask.sum()):,} non-individual rows")

    return df_clean


def clean_and_match(df: pd.DataFrame, fuzzy_city: bool = True,
                    out_dir: str | None = None, audit: bool = False):
    """The COMPLETE run, clean_rows() then unify_donors(): what clean.py executes on a full run; the incremental path calls the two halves separately. Returns (df_clean, missing_report, enh_audit)."""
    # snapshot raw names before clean() drops generational suffixes;
    # the JR/SR over-merge guard needs the originals
    raw_names = (dict(zip(df['sub_id'].astype(str), df['contributor_name'].astype(str)))
                 if {'sub_id', 'contributor_name'}.issubset(df.columns) else None)

    df_clean, missing, enh_audit = clean_rows(df, fuzzy_city=fuzzy_city,
                                              out_dir=out_dir, audit=audit)
    df_clean = unify_donors(df_clean, out_dir=out_dir, raw_names=raw_names)
    return df_clean, missing, enh_audit
