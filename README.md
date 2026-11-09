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
- `OPENAI_API_KEY`: an OpenAI key; `clean.py` asks the model for the category of every occupation text it has not seen before. Without it the new texts are categorized by patterns and listed in `data/reports/new_occupations.csv`.
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
- `donor merge rules found no donor`: a rule in `data/rules/donor_identity_rules.csv` needs an updated key.
- `previous employer rules found no filing` (from `resolve.py`): a `sub_id` in `data/rules/previous_employers.csv` is mistyped.
- `earlier donor keys no longer used`: a donor split or merged since the last run; check it.
- `OPENAI_API_KEY not set`: new occupation texts were categorized by patterns only; set the key and run `clean.py` again.

## Where things are

| Folder | What is in it |
| --- | --- |
| `data/raw/` | FEC data as pulled; never edited |
| `data/output/` | The cleaned file the database loads |
| `data/reports/` | Address cases, possible duplicate donors and new occupations to review |
| `data/rules/` | Reviewed decisions, each with its source. `occupation_category_overrides.csv` fixes one occupation's category when the model gets it wrong. An address case from the report is closed by a row in `address_rules.csv`: a correction, or a confirmation whose `street` equals its `match_street`. A retiree's previous employer is set in `previous_employers.csv` (`[CLEAR]` empties it) |
| `data/config/` | Spelling and word lists; `written_as` is the text as filed. `occupation_category_rules.md` is the instruction the model reads to categorize occupations: edit it to change how a kind of text is categorized |
| `data/reference/` | Committees, company offices, leaders and ZIP centres |
| `data/cache/` | Map points, donor keys and the model's occupation categories from earlier runs; keep it |

## Update the code

```bash
git pull --ff-only origin main
pip install -r requirements.txt
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```
