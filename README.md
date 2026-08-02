# FEC Pipeline

A data pipeline that tracks political donations to three pro-Israel PACs using
public Federal Election Commission records. It pulls the raw filings, cleans
and de-duplicates 208,283 records into ~18.3K unique donors, resolves and
geocodes their employers, and loads everything into a normalized PostgreSQL
database that powers the [israelsaccomplices.org](https://israelsaccomplices.org)
dashboard.

New here? Read [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow, module
map, and domain rules, and [DATA_DICTIONARY.md](DATA_DICTIONARY.md)
for the column-level data contract.

## Quick start

```bash
# Stage by stage — order matters (employer geocoding needs
# resolve --apply to have written the employer_* columns first)
python pull.py                 # 1. Pull new FEC data (3 tracked committees)
python clean.py                # 2. Clean + de-duplicate + canonicalize
python geocode.py              # 3. Geocode donor addresses
python resolve.py --apply      # 4. Resolve employer HQ addresses (AI-assisted)
python geocode.py --employer-only   # 5. Geocode employer HQs
python build_employers.py      # 6. Normalize employer HQs into employers.csv
python loader.py --reset       # 7. Load into PostgreSQL
```

Every stage caches its work — interrupt and re-run, it resumes where it
stopped. `clean.py --incremental` cleans only new records but still matches
donors across the whole dataset, so donor identities stay consistent between
runs.

## What it does

| Stage | Scale | Details |
|-------|-------|---------|
| Raw FEC data | 208,283 records | Contribution filings, 2020–2026 cycles |
| Cleaning | 12 steps + 16 enhancements | Entity classification, name/address/employer/occupation normalization |
| Safety nets | 40+ fixes (A–AS) | Consistency repairs across clean and post-merge |
| De-duplication | 18,270 unique individual donors | Score-based identity matching, threshold 50 |
| Canonicalization | per donor | One name, employer, and address form across all of a donor's filings |
| Employer resolution | ~12.8K company HQs cached | Cross-record, FEC API, and AI lookup |
| Geocoding | 99.5% of rows | Nominatim with Google fallback, donors and employer HQs |
| Database | 15 tables, 10 views, 1 matview | Normalized PostgreSQL, schema v1.2 |

### Tracked committees

| ID | Name | Type |
|----|------|------|
| C00797670 | AIPAC | PAC |
| C00710848 | DMFI (Democratic Majority for Israel) | PAC |
| C00799031 | UDP (United Democracy Project) | Super PAC |

## Project structure

```
fec-pipeline/
├── pull.py                      # FEC API pull (tracked committees or any id)
├── clean.py                     # Cleaning + donor matching + canonicalization
├── geocode.py                   # Lat/lng geocoding (donors; --employer-only for HQs)
├── resolve.py                   # Employer address resolution (--apply writes the CSV)
├── build_employers.py           # Normalize employer HQs into employers.csv
├── loader.py                    # PostgreSQL loader
│
├── fec/
│   ├── config/                  # Constants, occupation rules, cities, streets
│   ├── cleaning/
│   │   ├── pipeline/            # clean() (12 steps), clean_rows()/unify_donors(),
│   │   │                        #   address fixes, names, reclassify, fec_recovery
│   │   ├── enhancements/        # 16 enhancement steps
│   │   ├── safety_nets/         # 40+ consistency fixes (A–AS)
│   │   ├── employer_synonyms/   # Name mappings + abbreviation expansion
│   │   ├── address_review.py    # Safe address fixes
│   │   ├── address_reports.py   # Manual-review + regeocode-suspect reports
│   │   ├── quality_scan/        # Proactive issue scanner
│   │   └── quality/ + audit.py
│   ├── database/
│   │   ├── schema.sql           # Schema v1.2 (15 tables, 10 views, 1 MV)
│   │   ├── loader/              # Loader + schema-integrity verification
│   │   ├── healthcheck.py       # 80 read-only checks against the live db
│   │   ├── post_merge_fixes/    # Fixes requiring donor_key
│   │   └── donor_match/         # Score-based de-duplication + canonicalization
│   ├── resolve/pipeline/        # Multi-step employer resolution
│   ├── geocoding/               # Nominatim + Google geocoder
│   └── io.py                    # CSV I/O that preserves the literal "NULL" surname
│
├── DATA_DICTIONARY.md           # The column-level data contract
├── tests/                       # Unit + live-DB tests, CI configs
└── data/                        # Input/output CSVs, caches, reports, overrides
```

## Cleaning pipeline

`clean.py` runs everything in one command:

1. Clean — 12 steps: entity classification, name parsing, address, employer,
   and occupation normalization.
2. Enhance — 16 enhancement steps plus 40+ safety nets (A–AS).
3. Match — score-based donor de-duplication (110,812 individual rows →
   18,270 unique donors).
4. Post-merge — fixes that need `donor_key`: fill from the same donor's other
   filings, typo correction, retired-while-active, previous-employer
   propagation.
5. Canonicalize — unify each donor's name, employer, and address across all
   their records.

### Donor matching

Signals are scored per candidate pair; pairs at or above threshold 50 merge:

| Signal | Points |
|--------|-------:|
| Same street | +60 |
| Same employer | +30 |
| Same ZIP | +25 |
| Same city | +15 |
| Rare name (≤3) | +15 |
| Middle-name conflict | −30 |
| Common name (>10) | −15 |

A bad merge is vetoed by adding the pair to
`data/database/donor_no_merge.csv`, which the matcher reads as a hard
do-not-merge blocklist on the next run. Curated same-person pairs live in
`data/database/donor_dedup_merges.csv`.

### Canonicalization

| Pass | What it unifies |
|------|-----------------|
| `canonicalize_donor_names` | One `LAST, FIRST` spelling per identity |
| `canonicalize_donor_employers` | One company name across a donor's filings |
| `canonicalize_donor_addresses` | One street form per physical address |
| `canonicalize_donor_addresses_geo` | Collapses addresses within 50 m (precise geocodes only) |

### Employer normalization

One canonical spelling per company: legal suffixes stripped (`INC`, `LLC`,
`CORP`); `&` kept for real brands (`AT&T`, `K&L GATES`) while ordinary firms
keep `AND`; abbreviations expanded (`MGMT → MANAGEMENT`, `INTL →
INTERNATIONAL`); `ASSOC` disambiguated to ASSOCIATES or ASSOCIATION from
context; synonym chains flattened, with `data/manual_typo_overrides.json` for
stubborn real-world cases.

## Quality and integrity

- Quality scanner (`fec/cleaning/quality_scan/`) sweeps the cleaned output
  for suspicious patterns the rules might have missed — surviving
  abbreviations, near-duplicate company names, name drift, address variants —
  and writes `data/quality_scan.json`.
- Health check (`fec/database/healthcheck.py`) runs 80 read-only checks
  against the live `fec_db`: the shared `query_checks.CHECKS` registry (money
  conservation, 1:1 cardinalities, view-vs-recompute agreement) plus
  live-only extras (geocode coverage, matview freshness, DB-vs-CSV totals).

## Database schema (v1.2)

Normalized third-normal-form PostgreSQL (PG 18): single source of truth per
fact, no denormalized pointers, views composed from small sub-views.

| Table | Purpose |
|-------|---------|
| `donors` | Unique identities (`donor_key` hash), ~19K incl. leadership-only people |
| `contributions` | Fact table — one row per FEC filing (208,283) |
| `committees` | Tracked recipient committees |
| `addresses` | Shared address dimension — donor homes and employer HQs, geocoded once |
| `employers` | Unique companies; resolve/geocode state lives here |
| `donor_addresses` / `donor_employments` | Per-donor history |
| `donor_images` | Dashboard portraits |
| `occupation_categories` | Occupation-to-category lookup |

Reference and dashboard tables: `us_states`, `zcta_state_rel`,
`zip_centroids`, `key_accomplices`, `leaders`, `leader_committees`.

Views: `v_donor_current_*` / `v_donor_newest_*` (DISTINCT ON sub-views),
`v_donor_stats` (aggregate), `v_donor_profile`, `v_contributions_cleaned`,
`v_company`, `v_leaders`, `v_key_accomplices` (composed), and
`mv_donor_profile` (materialized, refreshed concurrently).

Constraints are deliberate: FKs and range CHECKs everywhere they hold;
reference tables are not FK-bound because the lookup data is not exhaustive.
`_verify_schema_integrity()` runs after every create.

## Commands

```bash
# Cleaning
python clean.py                      # Full clean + match + canonicalize
python clean.py --incremental        # Clean only new records (matching still runs on all)
python clean.py --no-audit           # Skip audit trail

# Employer resolution
python resolve.py --stats            # Show progress
python resolve.py --apply            # Resolve and write results to the CSV
python resolve.py --dry-run          # Count without API calls
python resolve.py --test-ai          # Verify the AI provider key with one tiny call

# Geocoding
python geocode.py                    # Donor addresses
python geocode.py --employer-only    # Employer HQs
python geocode.py --stats            # Cache progress

# Database
python loader.py --first-run         # First time on a machine: roles + database + load
python loader.py --reset             # Normal path: drop objects, reload
python -m fec.database.healthcheck   # 80 read-only checks
```

## Testing and CI

```bash
python -m pytest -m "not db"         # Unit tests (no Docker needed)
python -m pytest                     # Everything, incl. throwaway-Postgres schema tests
python -m pytest -m live -v          # Query-correctness checks vs the real fec_db
```

| Marker | Covers |
|--------|--------|
| (unit) | Cleaning, canonicalization, matching, incremental merge, quality scan |
| `db` | Schema, constraints, and view logic against a throwaway `postgres:18` (testcontainers) |
| `live` | Query correctness against the loaded `fec_db`; skips cleanly when no DB is reachable |

CI runs on GitHub Actions (`.github/workflows/tests.yml`) and GitLab CI
(`.gitlab-ci.yml`): a fast unit job plus a DB job backed by a `postgres:18`
service.

## Environment

```env
PG_HOST=localhost
PG_PORT=5432
PG_DBNAME=fec_db
PG_USER=fec_app
PG_PASSWORD=

POSTGRES_PASSWORD=            # For loader.py --first-run
DB_ROLES=fec_owner,fec_app
DB_ROLE_PASSWORD=

FEC_API_KEY=                  # Free: https://api.open.fec.gov/developers/
AI_PROVIDER=openai            # Employer-resolution backend: openai or xai
OPENAI_API_KEY=               # Employer-resolution AI lookup (AI_PROVIDER=openai)
XAI_API_KEY=                  # Same, when AI_PROVIDER=xai
GOOGLE_MAPS_API_KEY=          # Optional geocoding fallback
```

PostgreSQL extensions (installed by `schema.sql`): `pg_trgm`, `cube`,
`earthdistance`.

## Data notes

Rules the pipeline must never break:

- `NULL` is a real surname — donors literally named "NULL, JAMES" exist; CSVs
  are read with NA filtering off so pandas never turns the string into NaN.
- ZIP codes are zero-padded text (`06880`), never parsed as integers.
- Uniqueness is `sub_id` / `transaction_id` — multiple donations per day by
  one donor are normal.
- `recipient_committee` is the PAC that received the money, not the
  contributor's own committee.
- Occupations are not canonicalized (LAWYER vs ATTORNEY stay distinct);
  `occupation_category` does the grouping.

## Data source

All data from the [Federal Election Commission](https://www.fec.gov/) —
public records, 2020–2026 election cycles.
