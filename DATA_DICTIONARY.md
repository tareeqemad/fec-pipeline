# Data Dictionary

The pipeline emits three CSV files. Together they form a small **normalized**
dataset — each fact lives in exactly one place and the files join by key.

```
contributions_cleaned.csv ──┬── recipient_committee ──▶ committees.csv (committee_short)
   (one row per FEC filing)  │
                             ├── contributor_employer ─▶ employers.csv (employer_name)   [active donors]
                             └── previous_employer ────▶ employers.csv (employer_name)   [retired donors]
```

| File | Grain | Rows |
|------|-------|------|
| `data/contributions_cleaned.csv` | one row per FEC contribution filing | ~198,800 |
| `data/employers.csv` | one row per company (HQ dimension) | ~10,500 |
| `data/database/committees.csv` | one row per tracked recipient committee | 5 |

All files are UTF-8, comma-separated, with a header row. Empty cells mean
"missing / not applicable" (no sentinel strings).

---

## 1. `contributions_cleaned.csv` — 23 columns

The fact table: one row per contribution. **`entity_type` is the master key** —
it tells you what kind of contributor the row is and which other fields apply.

### Identifiers & recipient

| Column | Type | Notes |
|--------|------|-------|
| `sub_id` | string | FEC submission id. **Unique per row** (the primary key). |
| `transaction_id` | string | FEC transaction id. Unique per row. |
| `two_year_transaction_period` | int (year) | FEC election cycle (even year). Values: `2020`, `2022`, `2024`, `2026`. Each cycle covers two calendar years (e.g. 2026 = 2025–2026). |
| `recipient_committee` | enum | The PAC that **received** the money (not the contributor). One of: **`AIPAC`**, **`DMFI`**, **`UDP`**. Joins to `committees.csv`.`committee_short`. |

### Contributor identity

| Column | Type | Notes |
|--------|------|-------|
| `entity_type` | enum | **`INDIVIDUAL`** (a person, ~111K), **`COMMITTEE/PAC`** (a political committee, ~97K), **`ORGANIZATION`** (a company/org, ~250). |
| `contributor_name` | string | For individuals: `LAST, FIRST`. For committees/orgs: the entity's full name. 100% populated. |
| `contributor_first_name` | string | **Individuals only** (100% of them). Null for committees/orgs. |
| `contributor_last_name` | string | **Individuals only** (100% of them). Null for committees/orgs. |
| `donor_key` | string (hash) | Stable identity after de-duplication. All filings by the same person share one `donor_key` (~18.3K unique individual donors). Use this to group a person's rows. |

### Contributor address (their own — home for a person, office for a committee/org)

| Column | Type | Notes |
|--------|------|-------|
| `contributor_street_1` | string | Street / PO box. 100% populated. |
| `contributor_street_2` | string | Unit / suite (~18%). |
| `contributor_city` | string | 100%. |
| `contributor_state` | string | USPS 2-letter code. |
| `contributor_zip` | string | **Zero-padded 5-digit** ZIP (e.g. `06880`). Stored as text — do not parse as a number. |
| `latitude` | float | Geocoded coordinate of the contributor address (~99.5%). City-level for PO boxes. |
| `longitude` | float | See `latitude`. |

### Work / employer (see `employer_status` first)

| Column | Type | Notes |
|--------|------|-------|
| `employer_status` | enum | How to read the work fields: **`active`** (employed at a company), **`self_employed`** (their company = their home address), **`retired`**, **`not_employed`**, **`committee`** (the contributor IS a committee), **`organization`** (the contributor IS an organization — bank, trust, business), **`missing`** (an individual whose employer is unknown). The last three mean the `contributor_employer` join does **not** apply. |
| `contributor_employer` | string | The donor's employer, **unified per company** (legal suffixes stripped, synonyms/typos merged). Joins to `employers.csv`.`employer_name`. **Empty for non-individuals** — `entity_type` / `employer_status` carry the entity class. |
| `previous_employer` | string | The donor's prior employer where known (~18% of individual rows, mostly retired donors). Joins to `employers.csv`.`employer_name`. |
| `contributor_occupation` | string | Raw occupation as filed (100% of individuals after recovery/derivation). Not canonicalized (LAWYER vs ATTORNEY stay distinct). |
| `occupation_category` | enum | Occupation grouped into ~29 buckets (e.g. `LEGAL`, `FINANCE / INVESTMENT`, `REAL ESTATE`, `RETIRED`). Committees → `POLITICAL COMMITTEE`; orgs → `ORGANIZATION`. |

> **Where is the employer's address?** It is **not** on this row — it lives once
> per company in `employers.csv`. See §2 for how to resolve a donor's work
> location.

### Contribution

| Column | Type | Notes |
|--------|------|-------|
| `contribution_receipt_date` | date `YYYY-MM-DD` | Date the contribution was received. |
| `contribution_receipt_amount` | float (USD) | **Can be negative** — 752 rows are refunds/redesignations (mostly committees, but a few individuals/orgs too). `0` = voided. Large values (up to $25M) are committee-to-committee transfers, not individual gifts. |

---

## 2. `employers.csv` — 9 columns

The **employer dimension**: one row per company, so an HQ address is stored and
geocoded once instead of being repeated on every contribution.

| Column | Type | Notes |
|--------|------|-------|
| `employer_name` | string | Join key — matches `contributor_employer` / `previous_employer`. Every referenced company appears here. |
| `employer_address` | string | Corporate HQ street (~5,600 of ~10,500 resolved; blank where unknown). |
| `employer_city` | string | HQ city. |
| `employer_state` | string | HQ state code. |
| `employer_zip` | string | HQ ZIP. |
| `employer_latitude` | float | Geocoded HQ coordinate. |
| `employer_longitude` | float | See above. |
| `address_source` | enum | How the HQ was obtained: **`ai`** (AI-resolved — the majority; treat as best-effort) or **`manual`** (hand-verified). Blank when no address. |
| `address_trust` | enum | How much the address has earned belief. See below. Blank when no address. |

**`address_trust`** replaced `address_confidence` in July 2026. The old column
carried the AI's opinion of its own answer, which measured nothing: of 32
addresses proven wrong by hand that month, all 32 claimed `HIGH`. The new column
grades on evidence the pipeline actually holds:

| Value | Meaning |
|-------|---------|
| `verified` | A person curated it in `data/manual_employer_addresses.csv`. |
| `grounded` | Answered by a web-search-backed lookup (`ai_*_search`), which supersedes the retired closed-book path. |
| `corroborated` | Closed-book answer, but at least one donor of this company files from the address's state. |
| `uncorroborated` | Closed-book answer with no donor in that state. **Treat as wrong until re-resolved:** of 50 checked by hand, 44 were. |

> ⚠️ **Reliability:** filter on `address_trust`. `verified` and `grounded` are
> safe to show as the company's address. `corroborated` is plausible.
> `uncorroborated` should not be presented as fact — it is a queue of known
> suspects, and one `resolve.py` run with API credit clears it, because a
> web-search answer always overwrites a closed-book one.

### Resolving a donor's work location

```
employer_status == 'active'         → join contributor_employer → employers.csv (HQ)
employer_status == 'self_employed'  → use the row's own latitude / longitude
employer_status == 'retired' / 'not_employed'
                                     → join previous_employer → employers.csv (last HQ)
employer_status == 'committee'      → no employer (the org's own address applies)
```

---

## 3. `committees.csv` — 7 columns

Single source of truth for the tracked **recipient** committees. **Add a row
here for every new committee you pull** — the cleaner and loader both read
names from this file.

| Column | Type | Notes |
|--------|------|-------|
| `committee_number` | string | FEC committee id (`C00797670`). Blank for non-FEC orgs (AIEF, ZOA). |
| `committee_name` | string | Full legal name. |
| `committee_short` | string | Display name — the value used in `recipient_committee` (`AIPAC`, `DMFI`, `UDP`). |
| `raised` | number | Total raised (USD), if known. |
| `spent` | number | Total spent (USD), if known. |
| `irs_990_link` | string | URL to the IRS 990, if known. |
| `logo_path` | string | Dashboard logo path. |

---

## Hard rules (gotchas)

- **`NULL` is a real surname** — some donors are literally named "NULL". Read
  CSVs with NA filtering off for the name columns.
- **Multiple gifts per day are normal** — uniqueness is `sub_id` /
  `transaction_id`, never (donor, date, amount).
- **ZIP is text**, zero-padded to 5 digits — never parse as an integer.
- **`recipient_committee` is the receiver**, not the contributor's committee.
- **Employer addresses are not in the contributions file** — join `employers.csv`.
- **Occupations are not canonicalized**; `occupation_category` does the grouping.
