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
| `sync_rosters.py` | `fec/database/roster_sync.py` | cleaned CSV | `data/database/leaders.csv`, `data/database/key_accomplices.csv`: FEC-linked rows take the newest filing; editorial-only US streets are put through the cleaning's own street normaliser |
| `loader.py --reset` | `fec/database/loader/` | everything above | PostgreSQL `fec_db` |
| `python -m fec.database.healthcheck` | `fec/database/query_checks.py` | the database | report only |

## Packages

- `fec/config/` — data tables only, no logic: word lists (`constants.py`), occupation tables (`occupation_rules/rules.py`), category regexes (`occupation_rules/categories.py`), employer abbreviations, city/street/geography tables.
- `fec/cleaning/` — everything `clean.py` does. Orchestration in `pipeline/core.py`; field cleaners in `pipeline/names.py`, `pipeline/address_stage.py`, `occupations/`; row rules in `record_rules.py`; guard rules in `safety_nets/`; per-donor rules in `donor_consistency/` and `pipeline/donor_stage.py`; employer-name unification in `employer_synonyms/`; quality gates in `quality.py`.
- `fec/donor_match/` — assigns one `donor_key` per person (`matcher.py`, `phases.py`, `scoring.py`) and applies the verified merge/separate rules from `data/database/donor_identity_rules.csv` (`rules.py`).
- `fec/resolve/` — fills `previous_employer` (cross-record, then FEC API) and employer addresses (manual file, then AI web lookup), see `pipeline/cli.py` for the step order.
- `fec/geocoding/` — Census, then Nominatim, then a ZIP-centroid fallback (a city pin only when no ZIP centroid fits); foreign filings are never geocoded. A street match outside the filed ZIP is accepted only if the same street is found inside the ZIP, or Census and Nominatim agree within 1 km inside the filed city; otherwise the ZIP centroid is used. Hand-checked ZIP typos live in `reviewed_points.py`. Foreign employer offices go only to the international engine and are kept only if the result is outside the US. `build_employers.py` writes US employer addresses in the same uppercase USPS style as donor addresses, so one building is one address row.
- `fec/database/` — schema (`schema.sql`), loader steps (`loader/`), roster linking (`leadership_matcher.py`, `loader/leadership.py`), roster sync (`roster_sync.py`), read-only checks (`query_checks.py`, `healthcheck.py`). One employment row per (donor, employer, occupation, employer_status), so each filing keeps the status it reported; a retired donor previously self-employed is stored as `previous_self_employed` and shown as `SELF-EMPLOYED` by `v_donor_profile.previous_employer`. Roster employments take their employer's workplace address like FEC ones.

## Execution order inside `clean.py`

Every step below is an `AuditTrail` step; its change count appears under the same name in `data/audit_summary.json`. A step missing from the JSON changed nothing on the current data.

1. **People** (`pipeline/core.py::_clean_people`)
   - `names_preclean_punctuation` (`pipeline/names.py`)
   - `occ_normalize_text` … `occ_cross_fill`, `occ_mark_missing` (`occupations/clean.py`, deep-clean passes in `occupations/employer_deep_clean.py`, grouping in `occupations/employer_groups.py`)
   - `reclassify`, `reclassify_restore_work_fields`, `reclassify_clear_residue` (`pipeline/reclassify*.py`)
   - `names_*` steps (`NAME_STEPS` in `pipeline/names.py`)
2. **Addresses** (`pipeline/address_stage.py`): `streets_normalize` → safe fixes (`address_fixes/safe_text.py`: house number, C/O prefix (an unusable remainder such as "100 M" is blanked for the donor-history refill), the row's own city/state/ZIP typed into street_1, trailing unit split) → donor-history recoveries (`address_fixes/recovery.py`: null / non-street / house number / `streets_trim_truncated` for 34-char FEC streets with a city or building tail) → FEC API recovery → `cities_normalize` → `zips_normalize` → city/state and ZIP/state conflicts (`address_fixes/state_zip.py`) → `address_verified_rules` (`data/database/address_rules.csv`) → per-donor street unification (`address_fixes/unify.py`: word order, spacing, with/without street type where the typed form wins, unit designators; all scoped to one donor at one city/state so a genuine move is never merged) → `address_review` (only nulls street_2 values that are state/ZIP/city fragments). The review queues (`address_manual_review.csv` with status `open`/`auto_fixed`, `address_regeocode_suspects.csv` with individual-only PO Box/PMB rows) are written by `core.clean_pipeline` after the donor stage, carry `donor_key` and skip foreign filings; nothing reads them back.
3. **Record rules** (`record_rules.py`)
   - `_AUDITED_RULES` (16 `enh_*` steps: entity classification, name fixes, occupation/employer swaps and junk, first employer-synonym pass)
   - `SAFETY_RULES` (`safety_nets/__init__.py`, 45 `safety_*` guards in declared order; files: `committee.py`, `occupation.py`, `occupation_context.py`, `employer.py`, `employer_swaps.py`, `addresses.py`)
   - `enh_normalize_occupation_style`, `enh_occupation_typo_fixes` (`occupations/style.py`; must run after the swap guards, see the memory note on ordering)
   - employer finalisation: `employer_recanonicalize`, `employer_restore_suffixes`, `enh_employer_synonyms_final`, abbreviation/ASSOC expansion, `self_employed_variants`, `late_placeholder_employers`
   - `manual_overrides` (`data/manual_employer_overrides.csv`)
4. **Donor identity** (`fec/donor_match/`): profiles → 5 matching phases → curated merges → `donor_key`.
5. **Donor standardisation** (`pipeline/donor_stage.py`): network identity recovery → per-donor canonical name/employer/address/unit/PO box → `curated_name_corrections` → `CONSISTENCY_FIXES` (`donor_consistency/__init__.py`, 23 `donor_*` steps, including `donor_own_firm_absorbs_self_employed` (a donor's `SELF-EMPLOYED` rows take their own-surname firm) and `donor_fill_self_employed_occupation_from_donor` (`SELF-EMPLOYED` as an occupation is replaced by the donor's real one), `donor_employer_substring_variants`: a donor's `SYNERGY` folds into their `SYNERGY HEALTH PARTNERS`, unless the short name is a parent brand many people file on its own, then the division folds into the parent; `donor_employer_acronym_variants`: a donor's `WPCM` next to their `WHITE PINE CAPITAL MANAGEMENT` takes the full name, never across donors; `donor_converge_occupation_within_employer`: one spelling per job when a donor filed the same-category occupation two ways over overlapping dates) → final employer synonym pass → `non_individual_names`.
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
| `leaders.csv`, `key_accomplices.csv` | `database/loader/leadership.py`, `database/roster_sync.py` | editorial rosters keyed by `donor_key`; FEC-linked rows are synced by `sync_rosters.py`. Committees are referenced by `committee_short` (`{AIPAC,DMFI}`, `ZOA`), never by id; the loader resolves them through the committees table and stops on an unknown name |

Columns kept for humans only (no code reads them): `source` in the name/employer rules, `zip_a/zip_b/evidence/combined_amount/note` in the identity rules, `note` in entity overrides, `source/fetched_at` in ZIP centroids, `accomplice_role`, `accomplice_country`.

## Known duplication (candidates for a later consolidation)

- Four employer-name key functions encode "same company": `occupations/employer_groups.py::_employer_group_key`, `employer_synonyms/canonical.py::canonical_key`, `employer_synonyms/synonyms.py::_canonicalize_for_match`, `employer_synonyms/normalize.py::normalize_employer_canonical`.
- `apply_employer_synonyms` runs three times (record rules, employer finalisation, donor stage); the donor-stage abbreviation/ASSOC companions change nothing on current data.
- `safety_nets/occupation.py::_reclassify_other_category` is a subset of `donor_consistency/occupation.py::_rederive_occupation_category`, which runs later on every row.
- Several safety-net guards change nothing on the current data; they stay as insurance because the audit only records surviving changes.

## Rules added after the 2026-09-23 database audit

- **Employer text:** digits that belong to a company name are kept (GENESIS10, ROC360, CENTURY21 KING REALTY; only a status word with stray digits such as SELF EMPLOYED47 loses them, `occupations/normalize.py`). Loose or doubled slashes between two employers become " / " (`employer_synonyms/apply.py::tidy_slash_spacing`). A whitespace-only spelling difference keeps the word breaks the same filers use in the company's full name (TWIN CITY FAN, ATRIUM HEALTH; `occupations/employer_groups.py`).
- **Street text:** OFFICE followed by a place word is part of the street (5 GREENWICH OFFICE PARK, 1 POST OFFICE SQ; `config/streets.py::UNIT_EXTRACT`); a street_1 that is only a floor (3RD FLOOR) is a unit; placeholder street_2 values ('.', NONE, HOME) are NULL (`addresses.py`).
- **Foreign filings:** extra foreign and ambiguous cities and single reviewed sub_ids live in `foreign_addresses.py` (`_MORE_FOREIGN_CITIES`, `_MORE_AMBIGUOUS_CITIES`, `REVIEWED_FOREIGN_SUB_IDS`).
- **Previous employer:** a RETIRED marker around a company is stripped (GOLDMAN SACHS-RETIRED -> GOLDMAN SACHS); bare job titles are not employers (`config/constants.py::OCCUPATION_TITLE_EMPLOYERS`); canonical names are uppercase.
- **Categories:** COUNSEL is LEGAL only as a word (school counselors -> EDUCATION, mental-health counselors -> MEDICAL / HEALTHCARE); INVESTIGATOR is not FINANCE; C-suite titles match only as whole words (COORDINATOR is not COO); elected and diplomatic titles go to GOVERNMENT / MILITARY.
- **Display names** (`donor_match/canonicalize.py`, after donor keys are assigned, so keys never change): the majority clean spelling wins over decorated variants ("JOHN.", "KENNETH (ELLEN)"); a leading middle initial stuck to the surname moves to the first name; ties are not broken alphabetically.

## Rules added after the 2026-09-23 cleaning check

- **City typos** (`addresses.py::_auto_detect_city_typos`): a fuzzy rare->common city fix needs ZIP evidence (another row files the target city with the same state + ZIP5; the donor's own rows count) and is applied per (state, city, ZIP5), never globally; a pair that only swaps a place word (EAST/WEST, NORTH/SOUTH, UPPER/LOWER, NEW/OLD...) is a different town and is rejected (EAST HARTFORD stays EAST HARTFORD). `NY` becomes NEW YORK / BROOKLYN / BRONX / STATEN ISLAND by ZIP3 (`config/cities.py::CITY_ZIP3_NORMALIZE`); SCOTTDALE -> SCOTTSDALE only in AZ.
- **Same-street alignment** (`address_fixes/recovery.py`): a row's city is not overwritten by the group's majority when nobody outside the group files that city with the row's ZIP, and a row whose street is also filed at its own ZIP by other people is a second real address (kept as filed). A cut-off city (SANTA -> SANTA FE) or a city guessed by a typo table (LOS ANGELS at 90266 -> MANHATTAN BEACH) takes the city the same name + street files with the same ZIP.
- **Street names** (`config/streets.py`): a direction word that is the whole street name stays spelled out (650 WEST AVE, 5555 SOUTH ST), per USPS Pub. 28; `123 NORTH MAIN ST` still becomes `123 N MAIN ST`. Route numbers stay in street_1 (COUNTY ROAD 102, STATE ROAD 130; `address_fixes/safe_text.py`). SREET -> STREET.
- **First-name typo rules** (`contributor_name_rules.csv`, `name_rules.py`, `pipeline/names.py`): each `first_name` rule is written `SURNAME, GARBLED` with the filer's `zip5` and fires only for that person, so real names such as BRIA, CAROLL, AURI never rename someone else.
- **Joint filings** (`donor_match/joint.py`, `matcher.py::split_joint_filings`, `canonicalize.py`): a filing naming two people (glued `GAYLEDAVID`, spaced `MARC MELISSA`) goes to its own donor key when the second name is a household member who files alone; each person's solo filings keep their own name and a co-filer's name is never chosen as the display name. A second word that is the filer's own middle name (verified: MICHAN CARLOS DAVID, GOTTESMAN) is exempted by a curated rule. Display names: equally full spellings go to the donor's most-filed spelling, a period no longer counts as fullness (ISAAC S. vs ISAAC A).
- **Occupations** (`config/occupation_rules/`, `safety_nets/`): donor-verified typo entries and category mappings for unambiguous job titles (EQUESTRIAN, INTERPRETER, BUYER...); a role word in the employer box next to a company in the occupation box is swapped back (CEO @ TAP) and a sector word never becomes an employer; an occupation is filled only from the donor's own filings, never from co-workers.
- **Checks**: `resolve.py --apply` rewrites `data/quality_gates.json` for the final data and `tools/audits/final_verify.py` fails on any failed or not-run gate; `tools/audits/address_audit.py` lists every city that no other filing pairs with the row's ZIP5, whatever step made it.
