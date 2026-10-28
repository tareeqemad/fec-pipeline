# FEC Pipeline

Pull contributions, clean them, find coordinates and employer addresses, then load PostgreSQL.
Use Git, Python 3.12 and PostgreSQL 18 (for the database step).

## 1. Download

```bash
git clone https://github.com/tareeqemad/fec-pipeline.git
cd fec-pipeline
```

## 2. Create and activate the environment

Linux / macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Create `.venv` and copy `.env` only the first time. In a new terminal, enter the project folder and activate the environment again.

## 3. Set `.env`

Edit these values:

```dotenv
FEC_API_KEY=your_fec_key
FEC_RPM=15

PG_HOST=localhost
PG_PORT=5432
PG_DBNAME=fec_db
PG_USER=fec_owner
PG_PASSWORD=your_database_password
```

- `FEC_API_KEY`: needed to pull data.
- `PG_*`: needed only for loading. Complete the database setup below once; use `fec_owner` for `PG_USER`.

## 4. Set up PostgreSQL once

Open `psql` as the PostgreSQL administrator:

```bash
sudo -u postgres psql  # Linux
```

On Windows, open SQL Shell (psql) and log in as `postgres`. Run:

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

Put the `fec_owner` password in `.env`. The application reads with `fec_app`; the loader grants its read access.

## 5. Pull data

### Existing committee

Run the command for the committee you want:

```bash
python pull.py C00797670 --period 2026  # AIPAC
python pull.py C00710848 --period 2026  # DMFI
python pull.py C00799031 --period 2026  # UDP
```

Use the **same command next month**. It checks the whole period and skips saved `sub_id` values.
Period `2026` covers 2025–2026; use `2024` for 2023–2024. Omit `--period` to use the current election period.

Pull appends new filings. It does not replace or remove older versions of amended transactions.

### New committee

Add a row to [data/reference/committees.csv](data/reference/committees.csv).
Fill `committee_number`, `committee_name`, and a unique `committee_short`. Keep the other four columns; they can be blank:

```csv
C12345678,Official Committee Name,NEW,,,,
```

Replace this example with the real FEC committee ID, official name and short name, then run:

```bash
python pull.py C12345678 --period 2026
```

## 6. Clean and load

After finishing all pulls, run these commands **one at a time, in order**. Stop if any command fails.

```bash
python clean.py
python geocode.py
python resolve.py --apply
python geocode.py --employer-only
python loader.py --reset
```

`--reset` replaces all public tables and views in the configured database; use a database dedicated to this project.

Keep `data/cache/`: accepted results are reused. New coordinates come from OpenStreetMap, then Census; unmatched addresses stay blank.

Donor profiles place a blank address, such as a PO box, at its ZIP from `data/reference/zip_centroids.csv`: Census Gazetteer 2023, plus PO-box ZIPs from [GeoNames](https://www.geonames.org/) (CC BY 4.0).

If resolve stops after writing partial results, fix the cause and restart from `python clean.py`; keep the caches. Do not run resolve twice on its own output.

The finished files are in `data/output/`; cleaning reports are in `data/reports/`.

To load the completed CSVs already included in the repository, skip pulling and processing and run:

```bash
python loader.py --reset
```

## 7. Get later updates

From the project folder, with the environment active:

```bash
git pull --ff-only origin main
python -m pip install -r requirements.txt
```

Keep your existing `.env`. For new FEC data, repeat steps 5–6.

## Optional checks

Run the 18 core tests without network access or a live database:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```
