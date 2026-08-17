# FEC Pipeline

Pipeline for public FEC contribution records:

```text
pull -> clean -> geocode donors -> resolve employers -> geocode employers -> PostgreSQL
```

It tracks the committees configured in
[`data/database/committees.csv`](data/database/committees.csv).

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
python loader.py --reset
python -m fec.database.healthcheck
```

`pull.py` is needed only when new FEC data is available. The other commands
rebuild the cleaned data, resolve work locations, and reload the database.

Cleaning always processes the complete raw file. Donor matching groups filings
under `donor_key`; it never combines or removes contribution rows.

External lookups are cached in `data/*.json`. If AI credits run out, resolve
writes the cached results and leaves unresolved employers blank. Add credits and
run the same command again.

## Pull committee data

The committee must exist in
[`data/database/committees.csv`](data/database/committees.csv). Each command
accepts one committee ID:

```bash
python pull.py C00797670
python pull.py C00797670 --period 2024
python pull.py C00797670 --period 2024 --full
```

- The first command updates the current FEC period.
- `--period` selects an election period.
- `--full` rechecks that entire period.

Normal updates start again from the latest saved date so late filings from that
date are included. Existing rows are skipped by `sub_id`.

## Output files

- `data/contributions.csv`: raw FEC filings.
- `data/contributions_cleaned.csv`: one cleaned row per filing.
- `data/employer_locations.csv`: resolved employer locations.
- `data/database/committees.csv`: committee identities and display names.
- `data/*.json`: persistent lookup caches and quality reports.
- `data/audit_changes.csv`: every semantic change clean made to a filed value,
  one row per cell, with the step and the reason. `data/audit_summary.json`
  counts them per step. `data/audit_format_changes.csv` holds spelling-only
  changes and is regenerated each run, outside Git.

Manual rules, overrides, and caches under `data/` are real project inputs. Keep
them in Git and do not replace them with an older snapshot after a pipeline run.

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

The dashboard connects as `fec_app`, which receives table `SELECT` access after
each load. Give a developer that access through role membership:

```sql
GRANT fec_app TO developer_login;
```

## Data rules

- `NULL` is a real surname; use the CSV helpers in `fec/io.py`.
- Every cleaning step that changes a filed value runs through the audit trail
  (`fec/cleaning/audit_trail.py`) with a named reason. A change is semantic
  when the value now means something else (a different address, employer,
  name or category, a filled blank, a nulled value) and format when only the
  spelling changed; `fec/cleaning/audit_keys.py` draws that line. Semantic
  changes go to `data/audit_changes.csv`; a change undone by a later step
  is not written. A value changed outside a tracked step is written there as
  step `untracked`; the count must stay zero.
- Donor history may override a filed work status: a donor filed as
  `NOT EMPLOYED` or `SELF-EMPLOYED` who is otherwise `RETIRED` becomes
  `RETIRED`, and a filed employer can be replaced by the donor's majority
  employer. Each such row is in `data/audit_changes.csv` under a `donor_*`
  step.
- ZIP codes are zero-padded text, never integers.
- `recipient_committee` is the receiver of the contribution.
- Negative amounts are refunds or redesignations.
- Occupations stay as filed; `occupation_category` groups them.
- Verified contributor address corrections live in
  `data/database/address_rules.csv`; generic address normalization stays in code.
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
- `data/` contains inputs, outputs, caches, and reviewed rules.
- `tests/` protects cleaning, matching, resolving, and loading behavior.
