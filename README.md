# FEC Pipeline

Pulls FEC contributions, cleans them, finds map points and loads PostgreSQL.
You need Git, Python 3.12 and PostgreSQL 18.

## 1. Download and create the environment

Linux / macOS:

```bash
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
python3.12 -m venv .venv --prompt fec-pipeline
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
py -3.12 -m venv .venv --prompt fec-pipeline
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Next time, only enter the folder and activate: `cd fec-pipeline` then `source .venv/bin/activate`.

## 2. Fill in `.env`

- `FEC_API_KEY`: your FEC key (only for pulling data).
- Optional, for `geocode.py`: `NOMINATIM_URL` to use your own Nominatim server instead of the public one, `NOMINATIM_USER_AGENT` to name your project to it, `NOMINATIM_DELAY_SECONDS` to wait longer between requests (the public server is never asked more than once in 15 seconds).
- No other key is needed. Occupation categories come from `data/rules/occupation_categories.csv`; addresses are written in USPS form offline, by the `usaddress` parser and the USPS tables of `usaddress-scourgify` and `zipcodes`.
- `PG_PASSWORD`: the `fec_owner` password from step 3.

## 3. Create the database (once)

Open `psql` as the administrator (`sudo -u postgres psql` on Linux) and run:

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
ALTER DEFAULT PRIVILEGES FOR ROLE fec_owner IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE fec_owner IN SCHEMA public GRANT SELECT ON TABLES TO fec_app;
\quit
```

The website reads with `fec_app`; the loader writes with `fec_owner`.

## 4. Pull new data (monthly)

```bash
python pull.py C00797670 --period 2026  # AIPAC
python pull.py C00710848 --period 2026  # DMFI
python pull.py C00799031 --period 2026  # UDP
```

The same command next month adds only new contributions.
For a new committee, first add a row to `data/reference/committees.csv`.

## 5. Clean and load

Run in order; stop if one fails, fix it, and start again from `clean.py`:

```bash
python clean.py
python geocode.py
python resolve.py --apply
python loader.py --reset
```

`loader.py --reset` replaces every table in `fec_db`.
To load the files already in the repository, run only `python loader.py --reset`.

Warnings worth reading after `clean.py`:

- `address rules matched no filing`: a rule in `data/rules/address_rules.csv` is mistyped or no longer needed.
- `New occupations: N`: occupation texts not in `data/rules/occupation_categories.csv`, listed in `data/reports/new_occupations.csv` with the category their last listed word gives (else OTHER); add the right row to the table.
- `donor merge rules found no donor`: a rule in `data/rules/donor_identity_rules.csv` needs an updated key.
- `previous employer rules found no filing` (from `resolve.py`): a `sub_id` in `data/rules/previous_employers.csv` is mistyped.
- `earlier donor keys no longer used`: a donor split or merged since the last run; check it.

## Where things are

| Folder | What is in it |
| --- | --- |
| `data/raw/` | FEC data as pulled; never edited |
| `data/output/` | The cleaned file the database loads |
| `data/reports/` | Possible duplicate donors and new occupations to review |
| `data/rules/` | Reviewed decisions, one file per kind, every row with its `source`. `donor_identity_rules.csv` merges, separates or holds donors: a filing whose owner is not proven is held (`identity_status` `held`) and counts toward nobody rather than risk the wrong person. `filing_corrections.csv` corrects one filing by its `sub_id` (employer, occupation, category or address). `contributor_name_rules.csv` corrects a filed name. `employer_name_rules.csv` gives a company its one spelling. `entity_overrides.csv` says a name is a person, an organization or a committee. `occupation_categories.csv` gives each occupation text its category. `address_rules.csv` corrects one address the parser or the filer got wrong, matched on the street and ZIP as cleaned. A donor whose latest contribution says retired gets a previous employer: the company on their latest earlier contribution, or the answer in `previous_employers.csv` for people whose former work is known (`[CLEAR]` empties it) |
| `data/config/` | Spelling and word lists (`word_lists.csv` holds every list of words the code reads, one row per word). `category_names.csv` lists the categories; `occupation_category_words.csv` gives a new occupation text the category of its last listed word (REGISTERED NURSE, NURSE) |
| `data/reference/` | Committees, company offices, leaders and ZIP centres |
| `data/cache/` | Map points and donor keys from earlier runs; keep it |

## How the code is organised

Each command at the root only starts one stage; the stage is a folder in `fec/` whose `pipeline.py` is its entry point.

| Command | Stage folder | What it does |
| --- | --- | --- |
| `pull.py` | `fec/pull/` | Pulls new contributions from the FEC API into `data/raw/` |
| `clean.py` | `fec/cleaning/` | Cleans names, work fields and addresses, then calls `fec/matching/` to give every person one donor key |
| `geocode.py` | `fec/geocoding/` | Finds a map point for each cleaned address |
| `resolve.py` | `fec/resolve/` | Sets each filing's employer status and the previous employer of retired donors |
| `loader.py` | `fec/database/` | Loads the result into PostgreSQL in one transaction |

Shared code: `fec/common/` (settings and paths, logging, files, the columns each stage needs) and `fec/vocabulary/` (the word lists the code reads, built from `data/config/`).
Inside `fec/cleaning/`, `addresses/` holds the address steps and `work/` the employer and occupation steps.

Writing rules, checked by `ruff check .` (configured in `pyproject.toml`; run `ruff format .` before committing):

- Every function and class has a type-hinted signature and a docstring whose first line starts with a verb.
- A name starting with `_` is used only in its own file; anything another file imports has a public name.
- Each file is named after what it does, and a name is not reused for something else in another file.
- Verbs: `read_` reads a file or a filing's boxes, `apply_` applies reviewed rules, `clean_` is a cleaning step, `load_` writes to the database.
- Word lists live in `data/config/`, reviewed decisions in `data/rules/`, never inside the code.

## Update the code

```bash
git pull --ff-only origin main
pip install -r requirements.txt
```

## Tests

One test file per stage folder: `tests/test_pull.py`, `test_cleaning.py`, `test_addresses.py`, `test_matching.py`, `test_geocoding.py`, `test_database.py`, and `test_common.py` for the shared code.

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest
```
