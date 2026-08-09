# CLAUDE.md — FEC Pipeline

Context for Claude Code sessions. See `README.md` / `ARCHITECTURE.md` for the full
design and **`DATA_DICTIONARY.md`** for the column-level data contract.

## What this is
A pipeline tracking donations to three pro-Israel PACs (AIPAC, DMFI, UDP) from
public FEC records: **pull → clean → resolve employers → geocode → build_employers
→ load into PostgreSQL (`fec_db`)**. Powers the `accomplices-demo` dashboard
(separate repo) which reads the DB.

## Output data model (3 normalized CSVs)
- **`data/contributions_cleaned.csv`** (23 cols) — one row per FEC filing.
  - `entity_type` is the master key: `INDIVIDUAL` / `COMMITTEE/PAC` / `ORGANIZATION`.
  - `recipient_committee` (AIPAC/DMFI/UDP) — the PAC that **received** the money.
  - `employer_status` keys the work fields. Employer addresses are **not** here.
  - `donor_key` groups a person's filings (deduped identity).
- **`data/employer_locations.csv`** — known employer locations.
  - join: `contributor_employer` / `previous_employer` → `employer_name`.
  - `is_primary` marks the safe default; a company may have verified extra offices.
  - `address_source` (ai/manual), `address_trust`.
  - `address_trust` stays in the CSV **on purpose** — it is a curation worklist,
    not a published field. The DB schema and the dashboard are untouched by it.
  - `address_trust` grades an address on evidence, not on what the AI claimed
    about itself: `verified` (human-curated) / `grounded` (web-search answer) /
    `corroborated` (closed-book, but a donor lives in that state) /
    `uncorroborated` (closed-book, no donor there — 44 of 50 checked were wrong,
    so treat as wrong until re-resolved). It replaced `address_confidence`, which
    reported the model's self-assessment: all 32 addresses proven wrong by hand
    in July 2026 had claimed HIGH.
- **`data/database/committees.csv`** — committee identities, single source of
  truth. **Add a row for every new committee pulled.**

## Schema history — contributions went 40 → 23 columns
- `committee_id` → `recipient_committee`; `contributor_zip_5` → `contributor_zip`.
- Removed internal/provenance/redundant cols: `is_individual`, `contributor_year`,
  `committee_type`, `state_name`, `occupation_status`, `employer_change_type`,
  `resolve_method`, `resolve_confidence`, `geocode_level`, `employer_geocode_level`,
  `contributor_employer_original`. `state_name` and `employer_change_type` are no
  longer generated at all; the remaining working fields are listed in
  `INTERNAL_OUTPUT_COLUMNS` in `fec/config/data.py`.
- Employer addresses **normalized out** to `employer_locations.csv` (`build_employers.py`).
- Entity classification fixed (campaign committees → COMMITTEE/PAC; banks/trusts →
  ORGANIZATION). Only `INDIVIDUAL` has first/last; committee/org name tails stripped.
- Committees/orgs have **no employer** (only individuals do).
- Employer names unified cross-donor (same HQ + similar name → one canonical).
- PO-box typos collapsed; FEC admin-note junk removed from employers.

## Cleaning architecture (2026-08)
- `fec.cleaning.pipeline.clean_pipeline()` is the full run used by `clean.py`:
  `clean_records()` → `identify_donors()` → `standardize_donors()`.
- `identify_donors()` must see the complete dataset. It matches identities,
  assigns `donor_key`, and applies curated key merges. Rows are never aggregated;
  every FEC filing remains one output row.
- `standardize_donors()` canonicalizes each identity and then runs
  `fec/cleaning/donor_consistency/`, whose fixes use donor history. The old
  `post_merge_fixes` name was removed because no rows are physically merged.
- `clean.py` always rebuilds the full output. Incremental mode and
  `cleaned_ids.csv` were removed so old and new rows cannot use different rules.
- **Address hygiene** (rule: safe mechanical fixes applied automatically;
  anything needing a guess goes to a review report, never edited):
  - `fec/cleaning/address_review.py` — `apply_safe_fixes` (house numbers,
    C/O-prefix street recovery, trailing-unit split). Its sibling
    `fec/cleaning/address_reports.py` holds `build_address_reports`
    → `data/address_manual_review.csv` (human judgment, ~470 rows) and
    `data/address_regeocode_suspects.csv` (PO box / PMB / missing ZIP).
  - `fec/cleaning/pipeline/address_fixes/` — per-donor recovery/unification
    (null + fragment streets from the donor's own filings, spelling/spacing
    variants via `_unify_street_variants`).
  - `fec/cleaning/pipeline/fec_recovery.py` — last resort: pulls a donor's
    address from their OTHER committees' filings via the FEC API. Network-gated
    (needs `out_dir` + `FEC_API_KEY`) so tests/CI never hit the network; cached
    forever in `data/fec_address_cache.json` (git-tracked). Same-city guard.
- Address health: **99.84%** of street_1 geocodable. The residue is
  deliberate: 234 "C/O RED CURVE" committee rows (their official FEC address
  is in a DIFFERENT state than filed — auto-overwriting was considered and
  rejected; an editorial manual-override is the only correct path).

## Gotchas (do not break)
- `NULL` is a real surname — read name columns with NA filtering off.
- ZIP is zero-padded **text** — never parse as int.
- `recipient_committee` is the **receiver**, not the contributor's committee.
- Negative amounts = refunds; very large amounts = committee-to-committee transfers.
- Occupations are **not** canonicalized; `occupation_category` groups them.
- `INTERNAL_OUTPUT_COLUMNS` must stay in `OUTPUT_COLUMNS` (they flow through the
  pipeline — safety nets read them) and are dropped only at save. Removing one from
  `OUTPUT_COLUMNS` breaks the cleaning run.
- Pipeline order: `clean → geocode(donors) → resolve → geocode(employers) →
  build_employers → loader`. Only the **last writer (geocode)** drops provenance cols.
- Refactor verification pattern: regenerate and compare `md5sum` of
  `contributions_cleaned.csv` + `missing_report.csv` before/after — byte-identical
  proves behavior-preserving. For a pure **code refactor**, `git checkout -- data/`
  before committing keeps the commit code-only.
- ⚠️ **NEVER `git checkout -- data/` while the user is running the real pipeline.**
  The tracked `contributions_cleaned.csv` / caches are real artifacts; reverting
  restores a STALE snapshot, and the loader then loads old data (this bit hard:
  fresh address fixes kept getting reverted under the user, then loaded). When the
  pipeline is re-run for real, **commit the refreshed data** — don't revert it.
- **Pipeline-tail idempotence was fixed on 2026-08-03.** Resolve owns employer
  identity; `build_employers` owns the settled display spelling. Resolve preserves
  `previous_employer` when the cached and existing names have the same
  `canonical_key`, but still applies a genuinely different cached company.
  Verified on 33,093 retired rows: the first pass kept 40 real identity updates
  and the second pass changed zero `previous_employer` values.
- The two street-type token lists differ ON PURPOSE: `address_reports._STREET_TYPES`
  (pre-normalization, includes full words + unit keywords) vs
  `address_fixes.recovery._STREET_TYPE_RE` (post-normalization geocodability).
  Don't merge.
- **Preserve real info over blind cleaning** — the recurring rule: a value carrying
  real signal (a company, address, name) is MOVED/recovered to its right field, not
  destroyed. e.g. a company in the occupation field → moved to `contributor_employer`
  when that's empty/SELF-EMPLOYED, nulled only when redundant.

## Employer & occupation fields (cleaning rules)
- **Employer field = a real company or empty** (`fec/cleaning/safety_nets/employer.py`):
  - industry/sector words (HEALTHCARE, REAL ESTATE, FINANCE…, `SECTOR_AS_EMPLOYER`)
    → **null** (not a company, not self-employment); occupation kept.
  - self-employment roles (OWNER, BUSINESS OWNER, INVESTOR…, `ROLE_AS_EMPLOYER`)
    and formal job titles (ATTORNEY/PHYSICIAN…, `OCCUPATION_AS_EMPLOYER`) → SELF-EMPLOYED.
  - status-word typos (RETIEED→RETIRED, SELP EMPLOYED→SELF-EMPLOYED) normalized.
- **Occupation field = a role/title** (`fec/cleaning/safety_nets/occupation.py`
  `_null_junk_occupation`): a bare company name or an email is removed — company
  MOVED to employer if employer is empty/SELF-EMPLOYED, else nulled.
- **Emails are nulled in ALL THREE fields** (occupation, employer, street) and the
  raw-recovery step excludes them — an `@` never survives anywhere.
- Order trap: a safety net that nulls a value can be UNDONE by post-merge
  `_fill_employer_from_raw` re-recovering it from the raw filing. Sector/junk
  values must also be excluded from that recovery (`SECTOR_AS_EMPLOYER` is).

## Resolve (employer/committee HQ addresses)
- `resolve.py` **without `--apply` only updates the caches** (prep); `--apply`
  writes the `employer_*` columns to the CSV. It warns at the end when run
  without `--apply`. Caches are git-tracked (`resolve_employer_addr.json` etc.).
- ALL new AI lookups are **web-search-grounded** (closed-book path removed
  2026-07-29 — it hallucinated HQ addresses): one `/responses` search call per
  company, method `ai_<provider>_search`, cached forever. Search answers are
  **exempt** from the apply-time quality filter — verified harder than the
  heuristics can re-check (don't second-guess them). The filter stays for the
  LEGACY closed-book cache entries (`ai_openai`/`ai_xai`, no `_search` suffix);
  it dies when the last of those is re-resolved. First search-only run also
  retries the old `ai_not_found` backlog once (resolver tag changed) — expected,
  budget accordingly. Triage via donor-state mismatch.
- `dedup_by_resolved_address` **aliases** merged variants (`alias_of=…`) instead
  of deleting them — deleting caused an infinite re-resolve loop (previous_employer
  lookups use the variant spelling). `manual_override` entries are never merged.
- Verified address fixes → `data/manual_employer_addresses.csv` (authoritative at
  Step 0). `is_primary=true` is the default company location; `false` is a
  verified additional office.
- **Employer locations** — one cache entry holds the primary location plus verified
  additional offices. Donor location alone never proves an office exists. `apply`
  and the loader prefer a verified office in the donor's state, using ZIP distance
  only between offices in that state, then fall back to the primary location. This is an
  inference, not proof that the donor works at that office.

## Commands
```bash
python clean.py             # FULL clean: clean + enhance + match + canonicalize
python geocode.py --employer-only  # geocode all known employer locations
python build_employers.py   # build employer_locations.csv (after resolve+geocode)
python loader.py --reset    # load into fec_db
python -m pytest -m "not db"
```
`clean.py` flags: `--no-fuzzy-city`, `--no-audit`
(`--impute-street` was removed — in-pipeline recovery replaced it).

## Local database
PostgreSQL 18 @ `localhost:5432`, db `fec_db`, role `fec_app`. Verified end-to-end:
loader OK, healthcheck 0 FAIL, live tests pass. (Healthcheck = the shared
`query_checks.CHECKS` registry + a few live-only extras; don't hardcode counts.)
