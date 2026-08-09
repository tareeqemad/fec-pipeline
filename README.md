# FEC Pipeline

A data pipeline that tracks political donations to three pro-Israel PACs using
public Federal Election Commission records. It pulls the raw filings, cleans
and de-duplicates 214,428 records into 19,397 unique donors, resolves and
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
python resolve.py --apply      # 4. Resolve employer locations (AI-assisted)
python geocode.py --employer-only   # 5. Geocode all known employer locations
python build_employers.py      # 6. Build employer_locations.csv
python loader.py --reset       # 7. Load into PostgreSQL
```

Employer resolution and geocoding cache their external lookups. Cleaning always
rebuilds the complete output so every row uses the same rules.

## What it does

| Stage | Scale | Details |
|-------|-------|---------|
| Raw FEC data | 214,428 records | Contribution filings, 2020–2026 cycles |
| Cleaning | 12 steps + 16 enhancements | Entity classification, name/address/employer/occupation normalization |
| Safety nets | 40+ fixes (A–AS) | Consistency repairs before and after donor identification |
| De-duplication | 18,723 unique individual donors | Score-based identity matching, threshold 50 |
| Canonicalization | per donor | One name, employer, and address form across all of a donor's filings |
| Employer resolution | 10,775 referenced employers | Cross-record, FEC API, and grounded AI lookup |
| Geocoding | Persistent cache | Nominatim for donors and employer locations |
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
├── geocode.py                   # Lat/lng geocoding
├── resolve.py                   # Employer address resolution (--apply writes the CSV)
├── build_employers.py           # Build employer_locations.csv
├── loader.py                    # PostgreSQL loader
│
├── fec/
│   ├── config/                  # Constants, occupation rules, cities, streets
│   ├── cleaning/
│   │   ├── pipeline/            # records → donor identity → consistency
│   │   │                        #   address fixes, names, reclassify, fec_recovery
│   │   ├── donor_consistency/   # Cross-filing fixes requiring donor_key
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
│   │   └── healthcheck.py       # Read-only checks against the live db
│   ├── donor_match/             # Score-based de-duplication + canonicalization (runs inside clean)
│   ├── resolve/pipeline/        # Multi-step employer resolution
│   ├── geocoding/               # Nominatim geocoder + persistent cache
│   └── io.py                    # CSV I/O that preserves the literal "NULL" surname
│
├── DATA_DICTIONARY.md           # The column-level data contract
├── tests/                       # Unit + live-DB tests, CI configs
└── data/                        # Input/output CSVs, caches, reports, overrides
```

## Cleaning pipeline

`clean.py` runs the complete dataset through three stages:

1. `clean_records()` classifies entities and normalizes names, addresses,
   employers, and occupations.
2. `identify_donors()` matches identities, assigns `donor_key`, and applies
   curated merge and no-merge rules.
3. `standardize_donors()` repairs fields from each donor's history and chooses
   canonical names, employers, occupations, and addresses.

No contribution rows are aggregated or removed by donor matching. Each FEC
filing remains one output row.

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

### Employer locations and geocoding

Resolve never changes a cleaned employer name. It finds the primary company
location plus verified additional offices. A verified office in the donor's
state is preferred; otherwise the primary location is used. This is the best
supported company location, not proof of the person's exact desk or branch.

Automatic geocoding uses Nominatim only. Results live in
`data/geocode_cache.json`, so later runs reuse them. Human-verified coordinates
may be stored there with a `manual_*` source and are not replaced. Temporary
network failures stay retryable on the next run. `manual_census` means the full
address matched through the [US Census Geocoder](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/census-geocoder.html).

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
| `contributions` | Fact table — one row per FEC filing (214,428) |
| `committees` | Tracked recipient committees |
| `addresses` | Shared address dimension — donor and employer locations |
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
python clean.py --no-audit           # Skip audit trail

# Employer resolution
python resolve.py --stats            # Show progress
python resolve.py --apply            # Resolve and write results to the CSV
python resolve.py --dry-run          # Count without API calls
python resolve.py --test-ai          # Verify the provider with one grounded search

# Geocoding
python geocode.py                    # Donor addresses
python geocode.py --employer-only    # All known employer locations
python geocode.py --stats            # Cache progress

# Database
python loader.py --first-run         # First time on a machine: roles + database + load
python loader.py --reset             # Normal path: drop objects, reload
python -m fec.database.healthcheck   # 80 read-only checks
```

`--first-run` owns database/schema/default privilege policy. Normal `--reset`
only refreshes project objects, restores `fec_app` table reads, and intentionally
removes sequence access because read-only queries do not need it. Server admins
grant developer access through role membership, without adding people to this
repository:

```sql
GRANT fec_app TO developer_login;
```

Direct grants on tables disappear when `--reset` recreates those tables; role
membership survives and inherits the refreshed `fec_app` privileges.

Employer searches use cleaned names plus limited donor location/occupation context.
Every returned location needs its own complete US address and source URL. The
same-state location is preferred and ZIP only breaks ties within that state;
this is not proof of the person's exact workplace.

## Testing and CI

```bash
python -m pytest -m "not db"         # Unit tests (no Docker needed)
python -m pytest                     # Everything, incl. throwaway-Postgres schema tests
python -m pytest -m live -v          # Query-correctness checks vs the real fec_db
```

| Marker | Covers |
|--------|--------|
| (unit) | Cleaning, canonicalization, donor identity, quality scan |
| `db` | Schema, constraints, and view logic against a throwaway `postgres:18` (testcontainers) |
| `live` | Query correctness against the loaded `fec_db`; skips cleanly when no DB is reachable |

GitLab CI (`.gitlab-ci.yml`) runs a fast unit job plus a DB job backed by a
`postgres:18` service.

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
FEC_RPM=15                    # FEC requests per minute
AI_PROVIDER=openai            # Employer-resolution backend: openai or xai
OPENAI_API_KEY=               # Employer-resolution AI lookup (AI_PROVIDER=openai)
XAI_API_KEY=                  # Same, when AI_PROVIDER=xai
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
