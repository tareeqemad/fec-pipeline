# FEC Pipeline

Pipeline for public FEC contribution records:

```text
pull -> clean -> geocode donors -> resolve employers -> geocode employers -> PostgreSQL
```

It tracks the committees configured in
[`data/reference/committees.csv`](data/reference/committees.csv).

## Setup

Clone the repository and enter its directory:

```bash
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
```

Use Python 3.12 and install the dependencies:

The direct runtime dependencies in `requirements.txt` and pytest in
`requirements-dev.txt` are pinned to explicit versions. Create the virtual
environment on the machine where it will run; do not copy a Linux `.venv`
directory to Windows or reuse it there.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For `pull.py`, set `FEC_API_KEY` in `.env`. That is the only credential it needs;
PostgreSQL and OpenAI settings are used by later stages. Keep secrets in `.env`,
which Git ignores. `.env.example` lists the supported settings. Copy it only for
the first setup; keep an existing `.env` and its values.

## Load the included data

After installing dependencies, completing [database setup](#first-database-setup),
and setting the PostgreSQL values in `.env`, run:

```bash
python loader.py --reset
```

The loader reads `data/output/contributions_cleaned.csv` and
`data/output/employer_locations.csv` included in Git. Keep both files from the
same version of the project. It validates their contents without requiring
API keys or another cleaning run.

The included files contain the coordinates already cached. Geocoding is still
partial; run the pipeline below to look up missing addresses.

## Run the pipeline

To fetch new records and rebuild the outputs, run the stages in this order:

```bash
python pull.py C00797670
python clean.py
python geocode.py
python resolve.py --apply
python geocode.py --employer-only
python loader.py --reset
```

`loader.py --reset` replaces the contents of the dedicated database configured
in `.env`. When rebuilding the outputs, finish the earlier stages before loading.

`pull.py` is needed only when new FEC data is available. The other commands
rebuild the cleaned data, resolve work locations, and reload the database.
`resolve.py --apply` runs only on a fresh `clean.py` output: it reads
`previous_employer` as what the donor filed, so it refuses a file it already resolved.

`data/reference/leaders.csv` and `data/reference/key_accomplices.csv` are curated inputs.
The pipeline reads them without updating their addresses or jobs from new filings.
Edit these files manually when needed. Current donor profiles still follow the
latest contribution records.
In both rosters committees are written by their short name (`{AIPAC,DMFI}`, `ZOA`),
never by number; the loader stops on an unknown name.

Each stage rewrites `data/output/contributions_cleaned.csv` and checks it against
`fec/contract.py`: the columns it needs, adds and drops. The loader takes only
the finished column set.

Cleaning always processes the complete raw file. Donor matching groups filings
under `donor_key`; it never combines or removes contribution rows.

Geocoding reuses accepted results from `data/cache/geocode_cache.json`. For a new
address, it tries OpenStreetMap Nominatim, then the Census Geocoder for US
addresses. If neither returns an accepted address result, coordinates stay blank;
the pipeline does not substitute a city or ZIP centre. Temporary provider errors
are retried on the next run. Address text is sent as supplied and is not rewritten.

The public Nominatim service uses a minimum 15-second interval between requests.
Optional `.env` settings are `NOMINATIM_URL` (search endpoint),
`NOMINATIM_DELAY_SECONDS` (request interval), and `NOMINATIM_USER_AGENT`
(application identification). The public endpoint keeps the 15-second minimum
even when a shorter interval is configured.

External lookups are cached in `data/cache/*.json`. Resolve reuses accepted employer
addresses, including equivalent company names, even when `OPENAI_API_KEY` is set.
Changing the model or search version does not fetch those addresses again.
Leave the key blank to disable paid lookups for missing addresses.
The default model is `gpt-6-luna`; `AI_MODEL` can override it. Employer searches
require web search and a source for every address. Historical addresses for the
same company are accepted and marked in the cached source description.

If AI credits run out, resolve
writes the cached results, leaves unresolved employers blank, and exits with code
2. Stop the command sequence there. After adding credits, rebuild resolve's input:

```bash
python clean.py
python geocode.py
python resolve.py --apply
```

The caches are kept, so successful lookups can be reused. Do not rerun resolve
directly on its own output: its column guard deliberately rejects that file.
After resolve succeeds, continue with employer geocoding and loading below.
Run one pipeline command at a time, and stop if
any command reports an error.

### Continue on a Linux server

Pull the same `main` version, create the Python environment there, and set up
`.env`. Keep `data/cache/`; it works on both Windows and Linux.
For the existing raw data, run from the project directory:

```bash
set -e
python clean.py
python geocode.py
OPENAI_API_KEY='' python resolve.py --apply
python geocode.py --employer-only
python tools/audits/final_verify.py
python loader.py --reset
```

The empty OpenAI key disables paid lookups for this run. A free `FEC_API_KEY`
can still supply previous employers. Do not add another resolve after employer
geocoding. Run this sequence inside `tmux` or `screen` if you will disconnect SSH.

## Pull committee data

After setup, run from the project directory with the Python environment active.
Each command pulls one committee from
[`data/reference/committees.csv`](data/reference/committees.csv). To update all three
configured FEC committees for the current election period:

```bash
python pull.py C00797670
python pull.py C00710848
python pull.py C00799031
```

The IDs are AIPAC, DMFI and UDP respectively. The default period follows the
calendar year: 2025 and 2026 use period 2026. After the pulls finish, continue
from `clean.py` in [Run the pipeline](#run-the-pipeline).

For a specific election period:

```bash
python pull.py C00797670 --period 2026
```

Use the same command for the first pull and every update. It checks every page
in that period and saves only new `sub_id` values, including filings published
later with older contribution dates. If interrupted, rerun the same command.
Each update reads the whole period, so large committees take longer and use more
FEC requests. Run pulls one at a time.

To add a committee, add its FEC ID, name and unique short name to
`data/reference/committees.csv` (`committee_number`, `committee_name`,
`committee_short`). The other four fields can be blank. Then use its ID with the
same pull command. Period 2026 covers 2025 and 2026; use `--period 2024` for 2023
and 2024.

To fill missing FEC source fields for previously saved rows:

```bash
python pull.py C00797670 --period 2024 --backfill-source
```

`--backfill-source` fills `data/raw/fec_source_fields.csv`:
  FEC's own `fec_entity_type`, `fec_contributor_id` and `fec_image_number` (the
  filing page) per `sub_id`. A row is matched by `sub_id`, or, when FEC now returns
  the transaction under a newer `sub_id`, by one exact match on committee,
  transaction id, date and amount; never by name. It reads
  `data/raw/contributions.csv` and never writes it, and reports the rows it could not
  match. Run it once per committee and period.

`data/raw/contributions.csv` is only ever appended to by a pull; nothing rewrites
it. A pull writes each new row's source fields to `data/raw/fec_source_fields.csv`.
Existing rows are skipped by `sub_id`, so a normal pull does not fill source
fields for them. An amended filing with a different `sub_id` is a new raw row;
the pull does not reconcile amended versions of a transaction.

## Data folders

- `data/raw/`: what FEC sent. `contributions.csv` (only appended by `pull.py`) and
  `fec_source_fields.csv`.
- `data/rules/`: corrections for names, donor identities, entity types, addresses
  and employment.
- `data/reference/`: committees, leaders, key accomplices, states and ZIP tables.
- `data/cache/`: external lookups (geocoder, FEC API, AI resolve). Costly to
  rebuild; keep them.
- `data/output/`: what the pipeline builds: `contributions_cleaned.csv` and
  `employer_locations.csv`.
- `data/reports/`: audits, quality gates and review queues. `audit_changes.csv`
  holds every net cleaning change with its step, reason and source; it and the
  address review queues are regenerated each run outside Git.
  `data/reports/review/` holds the audit tools' output (not in Git).

Keep rules, reference data and caches in Git.

In `employer_name_rules.csv`, `action=rename` applies the sourced name correction.
`action=separate` records a rejected mapping: keep the two names distinct, including
during automatic spelling and substring matching. It can mean different entities
or insufficient evidence to combine them. The `source` column records the evidence;
formatting-only changes do not establish a company's legal identity.

## Database views

`fec/database/schema.sql` is the source of truth for the public database schema.
The dashboard reads the normalized tables and the `mv_donor_profile` materialized
view directly. The remaining views (`v_donor_stats`, `v_donor_current_address`,
`v_donor_current_employment`, `v_donor_profile`) exist to build `mv_donor_profile`.

### Reading the database code

Start at the root `loader.py`: `main()` controls the transaction and
`load_all()` shows the table loading order. Then follow the part you need:

- `loader/validate.py`: check the input files before loading.
- `loader/donors.py`, `employers.py`, `addresses.py`, `contributions.py`: build and load each type of row.
- `loader/reference.py`: load reference CSVs; required and optional columns are listed together.
- `loader/leadership.py`: load the curated rosters.
- `schema.sql`: table definitions, constraints, indexes and views.

The whole reset and load uses one transaction in `loader/_base.py`.
An error during loading rolls back the load.

## First database setup

The loader does not create roles, databases, or extensions. Run this once as the
PostgreSQL administrator:

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE fec_owner LOGIN;
\password fec_owner

CREATE ROLE fec_app LOGIN;
\password fec_app

CREATE DATABASE fec_db OWNER fec_owner;
\connect fec_db

CREATE EXTENSION pg_trgm;
CREATE EXTENSION cube;
CREATE EXTENSION earthdistance;

REVOKE ALL PRIVILEGES ON DATABASE fec_db FROM PUBLIC;
GRANT CONNECT ON DATABASE fec_db TO fec_owner, fec_app;

REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC;
GRANT ALL PRIVILEGES ON SCHEMA public TO fec_owner;
GRANT USAGE ON SCHEMA public TO fec_app;

ALTER DEFAULT PRIVILEGES FOR ROLE fec_owner IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE fec_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO fec_app;

\quit
```

Set `.env` to connect as `fec_owner`, then run:

```bash
python loader.py --reset
```

This setup uses two logins: `fec_owner` owns the tables and views, and `fec_app`
reads them. Each load removes table and sequence privileges from `PUBLIC` and
`fec_app`, then grants `fec_app` only `SELECT` on the tables and views. The loader
rejects a reader with admin privileges or membership in another role.

## Data rules

- `NULL` is a real surname; use the CSV helpers in `fec/io.py`.
- Every cleaning step that changes a filed value runs through the audit trail
  (`fec/cleaning/reports/trail.py`). The audit records the old value, new value,
  step, reason, and source. Changes undone later are omitted. A value changed
  outside a tracked step is recorded as `untracked`; its count must stay zero.
- Employment provenance stays in `data/reports/audit_summary.json`
  (`inferred_employment_sub_ids`), outside the cleaned CSV and database.
- Work status is historical per filing. A later job never replaces an earlier
  `RETIRED`, `NOT EMPLOYED`, `SELF-EMPLOYED`, `STUDENT`, or `HOMEMAKER` value.
  A retiree's `previous_employer` may come only from an earlier filing for the
  same `donor_key`; otherwise resolve leaves it for verified lookup.
- External FEC work history is searched in the filing's election cycle,
  from other committees than those in the donor's local records for that cycle. A 2026 filing
  searches 2025–2026 only; later records do not fill an earlier filing.
  Matching also requires a compatible name and the same filed street, unit,
  state and ZIP. The cache keeps the source filing ID, date, cycle and committee.
  Known nicknames and earlier addresses from reviewed donor merges are allowed.
  Misses retry when matching rules or filed evidence change.
  Older answers without this evidence remain in the cache for rechecking;
  they do not fill `previous_employer`.
- Officially reviewed historical roles can be saved as `manual_override` with
  sources in the previous-employer cache. They do not imply current or paid work.
- ZIP codes are zero-padded text, never integers.
  Same-street alignment fills missing ZIP/state values only with unambiguous
  place evidence; conflicting filed values need a verified correction.
- `recipient_committee` is the receiver of the contribution.
- Negative amounts are refunds or redesignations.
- Occupations stay as filed; `occupation_category` groups them.
- Verified contributor address corrections live in
  `data/rules/address_rules.csv`; generic address normalization stays in code.
  Optional `match_unit` restricts the entire correction to that normalized unit.
  Optional `match_sub_id` restricts it to one reviewed FEC filing as well.
  A rule using `[CLEAR]` to remove a unit must specify `match_unit`.
  Institution/place descriptions stay as filed. Street recovery needs matching
  address evidence and must preserve the filing's unit without mixing addresses.
- Per-filing corrections in `data/rules/manual_employer_overrides.csv` can
  set `occupation_category` when the reviewed context is more specific than
  the generic occupation mapping. `preserve_fields` lists explicit fields
  (separated by `;`) to keep through the final donor-consistency pass, such as
  an approved occupation or the filing's original unit designator.
- Geocode preserves cleaned addresses and leaves coordinates blank when no
  accepted address result is available.
- Clean owns employer names. Resolve only finds employer locations.
  Combined affiliations keep every employer; a standalone name is not expanded
  into a combined filing just because it matches one component.
- Employer cleaning and loading share
  `fec/cleaning/work/status.py:is_real_employer`. Status markers, disclosure
  placeholders, and generic work descriptions are not employer names. The final
  quality gate reports rejected company fields with their filing IDs and blocks
  loading. Review new cases and update the shared rules before
  rerunning the pipeline. This filter does not externally verify that an unfamiliar
  name identifies a real organization. `SELF-EMPLOYED` may describe previous work
  but is never an employer-table entry.
- Only verified or web-grounded employer addresses are loaded. Uncertain rows
  stay in `employer_locations.csv` for review and are not published.
- A verified office in the same city and state is preferred, then the nearest
  same-state office by ZIP, then the primary company location. This is not proof
  of the donor's exact workplace.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

For an optional raw-versus-cleaned audit, run:

```bash
python tools/audits/final_verify.py
```

## Project map

- Root scripts are the command-line entry points.
- `fec/` contains the pipeline logic.
- `data/` holds `raw/`, `rules/`, `reference/`, `cache/`, `output/` and `reports/` (see Data folders).
- `tests/` protects cleaning, matching, resolving, and loading behavior.
- `tools/audits/` holds optional raw-vs-cleaned audits and `final_verify.py`.

### Reading the cleaning code

Start with `clean.py:main`: read the raw file, clean it, check it, then save it.
Next, read `fec/cleaning/pipeline.py:clean_pipeline` and `clean_records` at the
top of that file. They show the execution order; the helpers are below them.

```text
fec/cleaning/
  pipeline.py   execution order
  names.py      name rules and name cleaning
  entities.py   individuals, organizations and committees
  addresses/   streets, cities, ZIPs and address recovery
  work/        occupations and current/previous employers
  donors/      donor matching and consistency across filings
  rules/       record corrections and reviewed exceptions
  reports/     audit, quality checks and reports
```

`_helpers.py` contains shared small functions. `__init__.py` marks the package.
`__pycache__` is generated by Python and is not source code.

### Reading the donor matching code

Start with `fec/donor_match/matcher.py:match_donors`. It builds profiles,
compares them, groups matching profiles, then returns donor keys.

- `matcher.py` runs matching, builds profiles and groups matches.
- `phases.py` chooses and compares candidate pairs.
- `normalize.py` prepares names and fields for comparison.
- `scoring.py` scores the evidence shared by each pair.
- `joint.py` separates joint filings from solo filings.
- `keys.py` assigns donor keys and applies reviewed decisions.
- `rules.py` reads and evaluates the reviewed identity rules.

Keep these matching modules and the spelling/review modules listed below at
300 lines or fewer.

This runs inside `clean.py`, before the cleaned CSV is written. A clean name
does not identify a person by itself: different people can share a name, and
one person can file under several addresses. Matching keeps every contribution
row and its amount.

After matching, `fec/cleaning/donors/names.py` unifies names, using spelling
choices from `name_choice.py`, and
`fec/cleaning/donors/employer_variants.py` unifies employer spellings.
Address unification is in `fec/cleaning/addresses/fixes/unify.py`;
duplicate-donor reports are in `fec/cleaning/reports/donors.py`.

### Reading the resolve code

Run `python resolve.py --apply`. Start reading at `fec/resolve/pipeline.py:main`;
`_run_steps` shows the order:

1. `manual.py` reads reviewed corrections.
2. `previous.py` finds previous employers in the donor's own records and prepares
   unresolved donors for FEC search.
3. `fec.py` searches older FEC filings and checks who they belong to.
4. `employers.py` finds employer addresses, using `lookups.py` for missing employers
   and `search.py` for web requests.
5. `cache.py` builds and stores results, and combines matching employer aliases.
6. `apply.py` writes the resolved fields, using `rows.py` for each filing and
   `locations.py` to choose an office.

Afterward, `geocode.py --employer-only` uses `addresses.py` and
`employer_locations.py` to build the employer-location CSV.
Resolve has 14 Python files, each under 300 lines. Cache filenames and commands are unchanged.
