# FEC Pipeline

Pulls itemized FEC receipts, identifies contributions, finds map points and loads PostgreSQL.
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

Each command fetches the full processed Schedule A list for that committee and period.
After the last page arrives, it saves a dated CSV in `data/raw/snapshots/<committee>-<period>/`.
Cleaning uses only the newest complete CSV in each folder. It uses `data/raw/contributions.csv` for periods without a snapshot.
An interrupted pull leaves the previous snapshot available.
For a new committee, first add a row to `data/reference/committees.csv`.

An amendment can replace or remove a receipt. The next complete pull picks up the current amount, name and filing ID.
If FEC amends an earlier period, rerun the command with that period's even year.
Older snapshots remain available for comparison. Separate FEC receipts can share a `transaction_id`.
Corrections scoped to an old `sub_id` need review before applying them to its replacement.

Query `contributions` directly for itemized donations, including earmarked gifts.
The total includes signed adjustments. Donor counts and averages use positive entries.

```sql
SELECT SUM(amount) FROM contributions;
```

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

## Where things are

| Folder | What is in it |
| --- | --- |
| `data/raw/` | Original receipts and source fields, plus dated snapshots with source fields for refreshed periods |
| `data/output/` | The cleaned file the database loads |
| `data/reports/` | Address cases, possible duplicate donors and new occupations to review |
| `data/rules/` | Reviewed decisions, each with its source; a retiree's previous employer is set in `previous_employers.csv` (`[CLEAR]` empties it) |
| `data/config/` | Spelling and word lists; `written_as` is the text as filed |
| `data/reference/` | Committees, company offices, leaders and ZIP centres |
| `data/cache/` | Map points and donor keys from earlier runs; keep it |

An entry in `entity_overrides.csv` can list reviewed filings under `source_exception_sub_ids`. Their original FEC codes stay in the raw data.

Use `filing_previous_employers.csv` when the evidence establishes a former employer for one retired receipt. A rule in `previous_employers.csv` applies to the donor's retired receipts.

An address decision marked `pending` in `address_cases_reviewed.csv` stays in the review report with its source and the detail that still needs verification.

`new_occupations.csv` lists filings whose occupation has neither a general category rule nor a reviewed category for that filing.

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
