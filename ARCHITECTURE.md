# Architecture & Onboarding

A map of the FEC pipeline for anyone picking up the project. Read this once and
you should know **where things live, how data flows, and the rules you must not
break.** (User-facing usage lives in [README.md](README.md); this is the
developer's view.)

---

## 1. What it is, in one breath

Raw FEC contribution filings → **clean & de-duplicate** into real people →
**geocode & resolve** their employers → **load into a normalized PostgreSQL** →
served to the [israelsaccomplices.org](https://israelsaccomplices.org) site.
Everything is driven from CLI scripts.

---

## 2. Data flow (the pipeline)

```
            ┌─────────────┐
  FEC API → │   pull.py   │ → data/contributions.csv        (raw filings, append-only)
            └─────────────┘
                   │
                   ▼
            ┌─────────────┐   clean → match donors → post-merge fixes → canonicalize
  clean.py →│ fec/cleaning│ → data/contributions_cleaned.csv   (+ donor_key)
            │ +donor_match│
            └─────────────┘
                   │
        ┌──────────┴───────────┐
        ▼                      ▼
 ┌────────────┐         ┌────────────┐   (both cache to data/*.json; safe to re-run)
 │ geocode.py │         │ resolve.py │   geocode = lat/lng; resolve = employer HQ
 │fec/geocoding│        │ fec/resolve│
 └────────────┘         └────────────┘
        └──────────┬───────────┘
                   ▼
            ┌─────────────┐   schema.sql → tables → views → matview
  loader.py→│fec/database │ → PostgreSQL  fec_db
            └─────────────┘
                   │
                   ▼
          healthcheck.py / query_checks  (read-only correctness verification)
```

The full pipeline is **clean → geocode → resolve --apply → geocode
--employer-only → build_employers** (see the Quick start in README.md). Each
stage **caches** its work and resumes if interrupted, so re-running is cheap
and idempotent.

---

## 3. Entry points (root scripts)

| Script | Role | Notes |
|--------|------|-------|
| `pull.py` | Pull from the FEC API — the 3 tracked committees, or any id(s) | `python pull.py [Cxxxxxxxx ...] [--full]` |
| `clean.py` | **Clean + match + post-merge + canonicalize** (self-contained) | writes `contributions_cleaned.csv` with `donor_key` |
| `geocode.py` | Lat/lng for donor + employer addresses | cached in `data/geocode_cache.json` |
| `resolve.py` | Employer HQ address resolution (cross-record + FEC API + AI) | cached in `data/resolve_*.json` |
| `build_employers.py` | Normalize employer HQs → `employers.csv` | last file-writing stage |
| `loader.py` | Load the cleaned CSV into PostgreSQL | thin wrapper for `fec.database.loader` |

> Root scripts marked **wrapper/thin** just call into the `fec/` package — the
> real logic lives there.

---

## 4. The `fec/` package

```
fec/
├── env.py          # PROJECT_ROOT, DATA_DIR, .env loading
├── log.py          # get_logger() — use this, not bare logging
├── io.py           # safe CSV read/write (preserves literal "NULL" surname, leading-zero ZIPs)
│
├── config/         # DATA, not logic — lookup tables & rules
│   ├── constants.py        # skip-sets, status words
│   ├── occupation_rules/   # occupation normalization + 15 category rules + typo fixes
│   ├── cities.py           # city corrections
│   ├── geography.py        # state / ZIP validation
│   └── streets.py          # street standardization
│
├── cleaning/       # the cleaning pipeline (CPU-only, no network)
│   ├── pipeline/               # 12-step orchestrator package (clean() + names/reclassify/address_fixes/reports)
│   ├── entity_classification.py# INDIVIDUAL vs COMMITTEE vs ORG vs …
│   ├── occupations/            # employer + occupation cleaning/categorization (logic)
│   ├── addresses/              # address normalization
│   ├── employer_synonyms/      # company-name mappings, abbreviation/ASSOC expansion
│   ├── enhancements/           # enhancement steps
│   ├── safety_nets/            # consistency-fix package (by field: committee/occupation/employer/names/addresses) — see §6
│   ├── audit.py                # change tracking
│   ├── quality.py              # quality gates + outlier detection
│   └── quality_scan.py         # proactive issue scanner → data/quality_scan.json
│
├── database/
│   ├── schema.sql              # the whole DB (15 tables, 10 views, 1 matview) — schema v1.2
│   ├── loader/                 # CSV → PostgreSQL loader package (_base + schema_create/schema_reset + per-table modules)
│   ├── healthcheck.py          # read-only checks vs the LIVE fec_db (registry + live-only extras)
│   ├── query_checks.py         # structured query-correctness checks (shared by tests + healthcheck)
│   └── leadership_matcher.py   # match leadership/accomplices to donors
│
├── post_merge_fixes/   # fixes needing donor_key (Y–AP, runs inside clean, no DB): fill-from-same-donor, etc.
│
├── donor_match/    # donor identity resolution (runs inside clean, no DB) — 6 files
│   ├── matcher.py              # orchestrator: profiles → phases → chains → force merges (+ UnionFind)
│   ├── phases.py               # the five pair-generation phases
│   ├── scoring.py              # string normalization + compute_score
│   ├── constants.py            # match weights (documented table), nicknames, trap pairs
│   ├── keys.py                 # donor_key hashing, key-level merges, dedup review report
│   └── canonicalize.py         # per-donor official name/employer/street (+ post-geocode pass)
│
├── geocoding/
│   ├── pipeline.py             # geocode orchestrator
│   ├── engines.py              # Nominatim + Google backends
│   └── cache.py                # on-disk cache
│
└── resolve/pipeline/
    ├── apply.py                # resolve orchestrator
    └── steps/                  # cross_record → fec_api → committee_address → ai_employer
```

**Rule of thumb:** `config/` = *data you tweak*; `cleaning/`, `geocoding/`,
`resolve/`, `database/` = *logic*. CLI scripts at the root are *just entry points*.

---

## 5. The database

Normalized 3NF PostgreSQL (PG 18), **schema v1.2**, all defined in one file:
`fec/database/schema.sql`. Layers:

- **Tables** — `donors`, `contributions` (the fact table), `committees`,
  `addresses` (shared dimension: donor homes *and* employer HQs dedup here),
  `employers`, `donor_addresses`, `donor_employments`, `occupation_categories`,
  reference tables (`us_states`, `zcta_state_rel`, `zip_centroids`), and the
  dashboard tables (`key_accomplices`, `leaders`, `leader_committees`).
- **Views** (no LATERAL, composed from small sub-views): `v_donor_stats`,
  `v_donor_current_*` / `v_donor_newest_*`, `v_donor_profile`,
  `v_contributions_cleaned`, `v_company`, `v_key_accomplices`, `v_leaders`.
- **Matview** `mv_donor_profile` — cached `v_donor_profile`, `REFRESH CONCURRENTLY`.

The loader recomposes the flat CSV layout from the normalized tables via
`v_contributions_cleaned` (there is **no** `contributions_cleaned` table). After
any load, `_verify_schema_integrity()` runs and `query_checks` should be green.

---

## 6. Domain rules — DO NOT break these

Each of these cost a real bug. They are enforced in code and tests.

| Rule | Why |
|------|-----|
| **`NULL` is a real surname** | Donors named "NULL, JAMES" exist. Always read CSVs with `na_filter=False` (see `fec/io.py`); never let pandas coerce the string to NaN. |
| **Multiple donations/day are normal** | Uniqueness is `sub_id` / `transaction_id` only — never (donor, date, amount). |
| **Keep `&` in real brands** | AT&T, S&P, H&M, K&L Gates keep `&`; ordinary firms like "M AND R MANAGEMENT" keep AND. (`AMPERSAND_BRANDS` list.) |
| **Don't canonicalize occupations** | LAWYER vs ATTORNEY stay distinct; `occupation_category` does the grouping. |
| **Employer ≠ status word** | RETIRED / SELF-EMPLOYED / HOMEMAKER are statuses, not employers (and watch the employer↔occupation swap bug). |
| **Canonicalize per donor, don't wipe** | When unifying a donor's name, ignore first-name candidates that are entirely the surname (reversed mis-parses) — else a real first name gets erased. |

The cleaning **safety nets** (`fec/cleaning/safety_nets/`) are independent fix
functions grouped by the field they touch (committee / occupation / employer /
names / addresses); `apply_safety_nets()` runs them in order. `post_merge_fixes/`
continues the Y–AP fixes that need `donor_key`. Read each function's docstring.

---

## 7. Glossary

| Term | Meaning |
|------|---------|
| `sub_id` | FEC's globally-unique id for a contribution row (the PK) |
| `transaction_id` | FEC's per-filer transaction id (also unique) |
| `two_year_transaction_period` / `election_cycle` | the even-year FEC cycle (2026 = 2025–2026) |
| `donor_key` | our hash that groups filings belonging to the same real person |
| **matching** | score-based de-dup that assigns `donor_key` (threshold 50) |
| **canonicalization** | pick the best name/employer/address a donor used and back-fill it to all their rows |
| **resolve** | find an employer's HQ address (cross-record → FEC API → AI) |
| **safety net** | a targeted consistency fix applied during cleaning |

---

## 8. How to… (recipes)

- **Add a tracked committee:** add its id to `TRACKED` in `pull.py` and a row to
  `data/database/committees.csv`.
- **Add a cleaning safety net:** add a function to the right module in
  `fec/cleaning/safety_nets/`, call it from `apply_safety_nets()`, add a test.
- **Add/alter a view:** edit `fec/database/schema.sql`, add it to `loader.VIEWS`,
  add a correctness check to `fec/database/query_checks.py`.
- **Verify the DB is correct:** `pytest -m live` — the shared `query_checks`
  registry cross-validates every view against the base tables.

---

## 9. Testing & verification

| Layer | Where | Runs against |
|-------|-------|--------------|
| Unit | `tests/test_*` (no marker) | cleaning / canonicalize / employer logic |
| Schema | `tests/test_db_schema.py` (`db`) | throwaway `postgres:18` (testcontainers) |
| **Live** | `tests/test_db_live.py` (`live`) | the **real `fec_db`** (skips if absent) |

`pytest -m "not db"` runs everything that needs no Docker (incl. live checks when
the DB is reachable). CI: GitHub Actions + GitLab CI.
