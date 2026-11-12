# FEC Pipeline

Pulls political contributions from the FEC, cleans them, finds a map point for each address and loads everything
into PostgreSQL.

You need **Git**, **Python 3.12 or later** and **PostgreSQL 18**.

## 1. Download the project

Windows (PowerShell):

```powershell
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux / macOS:

```bash
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Every time you come back, enter the folder and activate the environment again
(`.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` on Linux / macOS).

## 2. Create the database (once)

Open `psql` as the PostgreSQL administrator (on Linux: `sudo -u postgres psql`) and run:

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

`fec_owner` loads the data. `fec_app` can only read it; the website uses that one.

## 3. Fill in `.env`

Open the `.env` file you copied in step 1 and set two values:

| Setting | What to put |
| --- | --- |
| `PG_PASSWORD` | the password you gave `fec_owner` in step 2 |
| `FEC_API_KEY` | your FEC key, free from https://api.data.gov/signup/ (only needed for pulling) |

Leave the rest as it is. Never commit `.env`.

## 4. First run: load what is already in the project

The project comes with the data already pulled and cleaned. To put it in your database:

```bash
python loader.py --reset
```

It takes about a minute and ends with `Database load committed.`

## 5. Every month: pull, clean, load

Run these in order. If one fails, fix the cause and start again from `clean.py`.

```bash
python pull.py C00797670 --period 2026
python pull.py C00710848 --period 2026
python pull.py C00799031 --period 2026
python clean.py
python geocode.py
python resolve.py --apply
python loader.py --reset
```

| Command | What it does | How long |
| --- | --- | --- |
| `pull.py` | Asks the FEC for one committee's contributions and adds only the new ones to `data/raw/contributions.csv`. The three lines are AIPAC, DMFI and UDP | minutes |
| `clean.py` | Cleans names, employers, occupations and addresses, gives every person one donor key, and checks the result | about 3 minutes |
| `geocode.py` | Finds the map point of each new address. Addresses seen before come from the cache | seconds, or long when there are many new addresses |
| `resolve.py --apply` | Sets each person's work status and the previous employer of retired donors | seconds |
| `loader.py --reset` | Replaces every table in `fec_db` with the new data, all or nothing | about a minute |

Good to know:

- `--period` is the two-year election period: `2026` covers 2025 and 2026.
- Running `pull.py` twice is safe: the second run adds nothing.
- `resolve.py` refuses a file it already resolved. Run `clean.py` again first.
- To pull a new committee, first add a row for it in `data/reference/committees.csv`.

## 6. What the cleaning does

`clean.py` reads `data/raw/contributions.csv` and writes `data/output/contributions_cleaned.csv`. In order:

1. **Names**: fixes punctuation and spelling slips, splits first and last name.
2. **Who is it**: decides whether each contributor is a person, an organization or a committee.
3. **Work**: reads the employer and the occupation together (RETIRED, SELF-EMPLOYED, a job written in the employer
   box, a company written in the occupation box) and gives each occupation a category. The occupation a donor
   filed is never replaced by another one.
4. **Addresses**: writes every address in the standard USPS form.
5. **Reviewed corrections**: applies the decisions in `data/rules/`.
6. **Donors**: gives every filing of the same person the same donor key. Two people are never joined without
   proof; a filing whose owner is not proven is held and counts toward nobody.
7. **Checks**: if a check fails, nothing is saved and the old file stays.

After `clean.py`, read its warnings:

| Warning | What to do |
| --- | --- |
| `New occupations: N` | Open `data/reports/new_occupations.csv` and add each occupation with its category to `data/rules/occupation_categories.csv` |
| `address rules matched no filing` | A row in `data/rules/address_rules.csv` is mistyped or no longer needed |
| `donor merge rules found no donor` | A row in `data/rules/donor_identity_rules.csv` needs an updated key |
| `earlier donor keys no longer used` | A donor was split or merged since the last run; check it |
| `previous employer rules found no filing` (from `resolve.py`) | A `sub_id` in `data/rules/previous_employers.csv` is mistyped |

## 7. Where things are

| Folder | What is in it |
| --- | --- |
| `data/raw/` | FEC data as pulled. Never edit it |
| `data/output/` | The cleaned file the database loads |
| `data/reports/` | Lists to review after `clean.py`: `new_occupations.csv` |
| `data/rules/` | Reviewed decisions, every row with its `source`: names, companies, addresses, occupation categories, who is the same donor |
| `data/config/` | Spelling and word lists the code reads |
| `data/reference/` | Committees, company offices, leaders and ZIP centres |
| `data/cache/` | Map points and donor keys from earlier runs. Keep it: it saves lookups and keeps donor keys stable |
| `fec/` | The code, one folder per command: `pull`, `cleaning` (with `matching`), `geocoding`, `resolve`, `database` |

Each folder's `pipeline.py` lists its steps in order at the top; start reading there.

## 8. Optional settings

In `.env`, only if you need them:

| Setting | Use |
| --- | --- |
| `FEC_RPM` | FEC requests per minute (15 is safe) |
| `NOMINATIM_URL`, `NOMINATIM_USER_AGENT`, `NOMINATIM_DELAY_SECONDS` | Use your own map server instead of the public one, name your project to it, or wait longer between requests |

## 9. Update the code and run the tests

```bash
git pull --ff-only origin main
pip install -r requirements.txt
```

For people who change the code:

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest
```

Code rules: every function has type hints and a docstring; a name starting with `_` is used only in its own file;
`read_` reads a file, `apply_` applies reviewed rules, `clean_` is a cleaning step, `load_` writes to the
database; word lists live in `data/config/` and reviewed decisions in `data/rules/`, never inside the code.
