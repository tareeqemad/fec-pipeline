# FEC Pipeline

Pipeline for public FEC contribution records:

```text
pull -> clean -> geocode donors -> resolve employers -> geocode employers -> PostgreSQL
```

It tracks the committees configured in
[`data/rules/committees.csv`](data/rules/committees.csv).

## Setup

Use Python 3.12 and install the dependencies:

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

Edit `.env` with the real database and API credentials. Keep secrets only in
`.env`; Git ignores that file. `.env.example` is the list of supported settings.

## Run the pipeline

Run the stages in this order:

```bash
python pull.py C00797670
python clean.py
python geocode.py
python resolve.py --apply
python geocode.py --employer-only
python sync_rosters.py
python loader.py --reset
python -m fec.database.healthcheck
```

`pull.py` is needed only when new FEC data is available. The other commands
rebuild the cleaned data, resolve work locations, and reload the database.
`resolve.py --apply` runs only on a fresh `clean.py` output: it reads
`previous_employer` as what the donor filed, so it refuses a file it already resolved.

`sync_rosters.py` rewrites the FEC-linked rows of `data/rules/leaders.csv` and
`data/rules/key_accomplices.csv` from each donor's newest cleaned filing
(address, employer, occupation); `--check` only reports drift. Editorial-only rows
are never touched.
In both rosters committees are written by their short name (`{AIPAC,DMFI}`, `ZOA`),
never by number; the loader stops on an unknown name.

Each stage rewrites `data/output/contributions_cleaned.csv` and checks it against
`fec/contract.py`: the columns it needs, adds and drops. The loader takes only
the finished column set. `clean.py` starts a run in `data/output/pipeline_run.json`;
every later stage records the files it writes, and a stage or the loader stops
on a file that is not what this run last wrote (edited by hand, restored from
an older run, or left from before the last `clean.py`). Rerun from the stage
the message names.

Cleaning always processes the complete raw file. Donor matching groups filings
under `donor_key`; it never combines or removes contribution rows.

External lookups are cached in `data/cache/*.json`. If AI credits run out, resolve
writes the cached results and leaves unresolved employers blank. Add credits and
run the same command again.

## Pull committee data

The committee must exist in
[`data/rules/committees.csv`](data/rules/committees.csv). Each command
accepts one committee ID:

```bash
python pull.py C00797670
python pull.py C00797670 --period 2024
python pull.py C00797670 --period 2024 --full
python pull.py C00797670 --period 2024 --backfill-source
```

- The first command updates the current FEC period.
- `--period` selects an election period.
- `--full` rechecks that entire period.
- `--backfill-source` fills `data/raw/fec_source_fields.csv` for rows already pulled:
  FEC's own `fec_entity_type`, `fec_contributor_id` and `fec_image_number` (the
  filing page) per `sub_id`. A row is matched by `sub_id`, or, when FEC now returns
  the transaction under a newer `sub_id`, by one exact match on committee,
  transaction id, date and amount; never by name. It reads
  `data/raw/contributions.csv` and never writes it, and reports the rows it could not
  match. Run it once per committee and period.

`data/raw/contributions.csv` is only ever appended to by a pull; nothing rewrites
it. A pull writes each new row's source fields to `data/raw/fec_source_fields.csv`.
Normal updates start again from the latest saved date so late filings from that
date are included. Existing rows are skipped by `sub_id`, so `--full` alone does
not fill source fields for them.

## Data folders

- `data/raw/`: what FEC sent. `contributions.csv` (only appended by `pull.py`) and
  `fec_source_fields.csv`.
- `data/rules/`: what people curate: committees, identity/name/address/employer
  rules, entity overrides, manual employer overrides and addresses, the two
  rosters, and the ZIP/state reference tables.
- `data/cache/`: external lookups (geocoder, FEC API, AI resolve). Costly to
  rebuild; keep them.
- `data/output/`: what the pipeline builds: `contributions_cleaned.csv`,
  `employer_locations.csv`, and `pipeline_run.json` (the run id; not in Git).
- `data/reports/`: audits, quality gates and review queues. `audit_changes.csv`
  holds every net cleaning change with its step, reason and source; it and the
  address review queues are regenerated each run outside Git.
  `data/reports/review/` holds the audit tools' output (not in Git).

Rules and caches are real project inputs. Keep them in Git and do not replace
them with an older snapshot after a pipeline run.

After pulling the commit that split `data/`, run `python tools/move_data_layout.py`
once: Git moves the files it tracks, the tool moves the rest and never overwrites.

## Database views

`fec/database/schema.sql` is the source of truth for the public database schema.
The dashboard reads the normalized tables and the `mv_donor_profile` materialized
view directly. The remaining views (`v_donor_stats`, `v_donor_current_address`,
`v_donor_current_employment`, `v_donor_profile`) exist to build `mv_donor_profile`.

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

The database has two logins only: `fec_owner` owns every table, view and
materialized view (full read/write), and `fec_app` receives `SELECT` on all of
them after each load and nothing else. The healthcheck fails if another schema
or login role exists, or if `fec_app` can write.

## Data rules

- `NULL` is a real surname; use the CSV helpers in `fec/io.py`.
- Every cleaning step that changes a filed value runs through the audit trail
  (`fec/cleaning/audit_trail.py`). The audit records the old value, new value,
  step, reason, and source. Changes undone later are omitted. A value changed
  outside a tracked step is recorded as `untracked`; its count must stay zero.
- Work status is historical per filing. A later job never replaces an earlier
  `RETIRED`, `NOT EMPLOYED`, `SELF-EMPLOYED`, `STUDENT`, or `HOMEMAKER` value.
  A retiree's `previous_employer` may come only from an earlier filing for the
  same `donor_key`; otherwise resolve leaves it for verified lookup.
- ZIP codes are zero-padded text, never integers.
- `recipient_committee` is the receiver of the contribution.
- Negative amounts are refunds or redesignations.
- Occupations stay as filed; `occupation_category` groups them.
- Verified contributor address corrections live in
  `data/rules/address_rules.csv`; generic address normalization stays in code.
- Geocode only adds coordinates; it never edits a cleaned address. US streets
  use the Census Geocoder first, then Nominatim. A street-level failure cannot
  fall back to a city that conflicts with the ZIP.
- Clean owns employer names. Resolve only finds employer locations.
- Only verified or web-grounded employer addresses are loaded. Uncertain rows
  stay in `employer_locations.csv` for review and are not published.
- A same-state office is preferred when verified; otherwise the primary company
  location is used. This is not proof of the donor's exact workplace.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

After loading PostgreSQL, also run:

```bash
python -m fec.database.healthcheck
```

## Project map

- Root scripts are the command-line entry points.
- `fec/` contains the pipeline logic.
- `data/` holds `raw/`, `rules/`, `cache/`, `output/` and `reports/` (see Data folders).
- `tests/` protects cleaning, matching, resolving, and loading behavior.
- `tools/audits/` holds the raw-vs-cleaned audits (addresses, names, self-employed) and the final verification; run them after every pipeline run.
