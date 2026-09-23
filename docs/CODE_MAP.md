# Code map

How the pipeline is laid out and in which order every rule runs. Read this top to bottom to follow a record from the FEC API to the database.

## Entry points (run in this order)

| Script | Package it drives | Reads | Writes |
|---|---|---|---|
| `pull.py` | `fec/pull.py` | FEC Schedule A API | `data/contributions.csv` |
| `clean.py` | `fec/cleaning/` | `data/contributions.csv`, `data/database/*.csv` rules | `data/contributions_cleaned.csv`, `data/audit_*.{csv,json}`, `data/quality_*.json`, `data/missing_report.csv`, `data/donor_dedup_review.csv` |
| `resolve.py --apply` | `fec/resolve/` | cleaned CSV, `data/manual_employer_addresses.csv`, `data/manual_employer_overrides.csv` | employer address/previous-employer columns in the cleaned CSV, `data/resolve_*.json` caches |
| `geocode.py` | `fec/geocoding/` | cleaned CSV, `data/geocode_cache.json`, `data/database/zip_centroids.csv` | contributor `latitude`/`longitude` |
| `geocode.py --employer-only` | `fec/geocoding/` + `build_employers.py` | employer address cache | `data/employer_locations.csv` |
| `sync_rosters.py` | `fec/database/roster_sync.py` | cleaned CSV | `data/database/leaders.csv`, `data/database/key_accomplices.csv` (FEC-linked rows only) |
| `loader.py --reset` | `fec/database/loader/` | everything above | PostgreSQL `fec_db` |
| `python -m fec.database.healthcheck` | `fec/database/query_checks.py` | the database | report only |

## Packages

- `fec/config/` — data tables only, no logic: word lists (`constants.py`), occupation tables (`occupation_rules/rules.py`), category regexes (`occupation_rules/categories.py`), employer abbreviations, city/street/geography tables.
- `fec/cleaning/` — everything `clean.py` does. Orchestration in `pipeline/core.py`; field cleaners in `pipeline/names.py`, `pipeline/address_stage.py`, `occupations/`; row rules in `record_rules.py`; guard rules in `safety_nets/`; per-donor rules in `donor_consistency/` and `pipeline/donor_stage.py`; employer-name unification in `employer_synonyms/`; quality gates in `quality.py`.
- `fec/donor_match/` — assigns one `donor_key` per person (`matcher.py`, `phases.py`, `scoring.py`) and applies the verified merge/separate rules from `data/database/donor_identity_rules.csv` (`rules.py`).
- `fec/resolve/` — fills `previous_employer` (cross-record, then FEC API) and employer addresses (manual file, then AI web lookup), see `pipeline/cli.py` for the step order.
- `fec/geocoding/` — Census, then Nominatim, then city-level fallback; foreign filings are never geocoded.
- `fec/database/` — schema (`schema.sql`), loader steps (`loader/`), roster linking (`leadership_matcher.py`, `loader/leadership.py`), roster sync (`roster_sync.py`), read-only checks (`query_checks.py`, `healthcheck.py`).

## Execution order inside `clean.py`

Every step below is an `AuditTrail` step; its change count appears under the same name in `data/audit_summary.json`. A step missing from the JSON changed nothing on the current data.

1. **People** (`pipeline/core.py::_clean_people`)
   - `names_preclean_punctuation` (`pipeline/names.py`)
   - `occ_normalize_text` … `occ_cross_fill`, `occ_mark_missing` (`occupations/clean.py`, deep-clean passes in `occupations/employer_deep_clean.py`, grouping in `occupations/employer_groups.py`)
   - `reclassify`, `reclassify_restore_work_fields`, `reclassify_clear_residue` (`pipeline/reclassify*.py`)
   - `names_*` steps (`NAME_STEPS` in `pipeline/names.py`)
2. **Addresses** (`pipeline/address_stage.py`): `streets_normalize` → safe fixes (`address_fixes/safe_text.py`: house number, C/O prefix (an unusable remainder such as "100 M" is blanked for the donor-history refill), the row's own city/state/ZIP typed into street_1, trailing unit split) → donor-history recoveries (`address_fixes/recovery.py`: null / non-street / house number / `streets_trim_truncated` for 34-char FEC streets with a city or building tail) → FEC API recovery → `cities_normalize` → `zips_normalize` → city/state and ZIP/state conflicts (`address_fixes/state_zip.py`) → `address_verified_rules` (`data/database/address_rules.csv`) → per-donor street unification (`address_fixes/unify.py`: word order, spacing, with/without street type where the typed form wins, unit designators; all scoped to one donor at one city/state so a genuine move is never merged) → `address_review` (also nulls street_2 values that are state/ZIP/city fragments).
3. **Record rules** (`record_rules.py`)
   - `_AUDITED_RULES` (16 `enh_*` steps: entity classification, name fixes, occupation/employer swaps and junk, first employer-synonym pass)
   - `SAFETY_RULES` (`safety_nets/__init__.py`, 45 `safety_*` guards in declared order; files: `committee.py`, `occupation.py`, `occupation_context.py`, `employer.py`, `employer_swaps.py`, `addresses.py`)
   - `enh_normalize_occupation_style`, `enh_occupation_typo_fixes` (`occupations/style.py`; must run after the swap guards, see the memory note on ordering)
   - employer finalisation: `employer_recanonicalize`, `employer_restore_suffixes`, `enh_employer_synonyms_final`, abbreviation/ASSOC expansion, `self_employed_variants`, `late_placeholder_employers`
   - `manual_overrides` (`data/manual_employer_overrides.csv`)
4. **Donor identity** (`fec/donor_match/`): profiles → 5 matching phases → curated merges → `donor_key`.
5. **Donor standardisation** (`pipeline/donor_stage.py`): network identity recovery → per-donor canonical name/employer/address/unit/PO box → `curated_name_corrections` → `CONSISTENCY_FIXES` (`donor_consistency/__init__.py`, 20 `donor_*` steps, including `donor_converge_occupation_within_employer`: one spelling per job when a donor filed the same-category occupation two ways over overlapping dates) → final employer synonym pass → `non_individual_names`.
6. `foreign_address_restore` (`foreign_addresses.py`): foreign filings go back exactly as filed.
7. Quality gates (`quality.py::_QUALITY_GATES`, 18 gates) and the detect-only scan (`quality_scan.py`).

## Reference CSVs (`data/database/`)

| File | Consumer | Purpose |
|---|---|---|
| `contributor_name_rules.csv` | `cleaning/name_rules.py` | verified name corrections |
| `employer_name_rules.csv` | `cleaning/employer_synonyms/synonyms.py` | variant → canonical employer name (targets are suffix-free; divisions map to the parent) |
| `donor_identity_rules.csv` | `donor_match/rules.py` | verified `merge_keys` / `separate` rules with evidence and sources |
| `entity_overrides.csv` | `cleaning/donor_consistency/entity.py` | forced entity type by name |
| `address_rules.csv` | `cleaning/pipeline/address_fixes/verified.py` | verified address corrections |
| `zip_centroids.csv`, `zcta_state_rel.csv`, `us_states.csv` | geocoding, resolve, address fixes, loader | ZIP geography |
| `committees.csv` | `fec/committees.py`, loader | tracked committees |
| `leaders.csv`, `key_accomplices.csv` | `database/loader/leadership.py`, `database/roster_sync.py` | editorial rosters keyed by `donor_key`; FEC-linked rows are synced by `sync_rosters.py` |

Columns kept for humans only (no code reads them): `source` in the name/employer rules, `zip_a/zip_b/evidence/combined_amount/note` in the identity rules, `note` in entity overrides, `source/fetched_at` in ZIP centroids, `accomplice_role`, `accomplice_country`.

## Known duplication (candidates for a later consolidation)

- Four employer-name key functions encode "same company": `occupations/employer_groups.py::_employer_group_key`, `employer_synonyms/canonical.py::canonical_key`, `employer_synonyms/synonyms.py::_canonicalize_for_match`, `employer_synonyms/normalize.py::normalize_employer_canonical`.
- `apply_employer_synonyms` runs three times (record rules, employer finalisation, donor stage); the donor-stage abbreviation/ASSOC companions change nothing on current data.
- `safety_nets/occupation.py::_reclassify_other_category` is a subset of `donor_consistency/occupation.py::_rederive_occupation_category`, which runs later on every row.
- Several safety-net guards change nothing on the current data; they stay as insurance because the audit only records surviving changes.
