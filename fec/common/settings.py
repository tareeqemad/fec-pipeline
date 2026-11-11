"""Environment settings, database connection and project paths."""

import os
from pathlib import Path

from fec.common.log import get_logger

logger = get_logger(__name__)

# project root = directory containing .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATABASE_OWNER = "fec_owner"

DATABASE_READER = "fec_app"


def load_env(env_path: Path | None = None) -> None:
    """Load .env without replacing existing environment variables."""
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        logger.warning(".env not found at %s - using environment variables only", path)
        return

    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:  # don't override existing env vars
                os.environ[key] = value


def get_env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """Read a setting and report missing required values."""
    value = os.environ.get(key, default)
    if required and not value:
        raise OSError(f"Required environment variable {key} is not set. Add it to .env or export it.")
    return value


def get_db_config() -> dict:
    """Build PostgreSQL connection settings from the environment."""
    return {
        "host": get_env("PG_HOST", "localhost"),
        "port": int(get_env("PG_PORT", "5432")),
        "dbname": get_env("PG_DBNAME", "fec_db"),
        "user": get_env("PG_USER", required=True),
        "password": get_env("PG_PASSWORD", required=True),
    }


# Project data folders.
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RULES_DIR = DATA_DIR / "rules"
REFERENCE_DIR = DATA_DIR / "reference"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = DATA_DIR / "reports"

# Raw FEC data; pull.py appends, nothing rewrites it. The second file holds FEC's own entity type and id per sub_id.
RAW_CSV = RAW_DIR / "contributions.csv"
FEC_SOURCE_CSV = RAW_DIR / "fec_source_fields.csv"

CLEANED_CSV = DATA_DIR / "output" / "contributions_cleaned.csv"

# Reference files, added to by hand: the committee list and the company offices.
COMMITTEES_CSV = REFERENCE_DIR / "committees.csv"
EMPLOYER_LOCATIONS_CSV = REFERENCE_DIR / "employer_locations.csv"

SCHEMA_SQL = PROJECT_ROOT / "fec" / "database" / "schema.sql"

# Reviewed decisions, every row with its source.
EMPLOYER_NAME_RULES_CSV = RULES_DIR / "employer_name_rules.csv"
CONTRIBUTOR_NAME_RULES_CSV = RULES_DIR / "contributor_name_rules.csv"
ADDRESS_RULES_CSV = RULES_DIR / "address_rules.csv"
FILING_CORRECTIONS_CSV = RULES_DIR / "filing_corrections.csv"
PREVIOUS_EMPLOYERS_CSV = RULES_DIR / "previous_employers.csv"
OCCUPATION_CATEGORIES_CSV = RULES_DIR / "occupation_categories.csv"

# Kept between runs: map points already looked up, and each filing's donor key so keys stay stable.
GEOCODE_CACHE_JSON = CACHE_DIR / "geocode_cache.json"
DONOR_KEYS_CSV = CACHE_DIR / "donor_keys.csv"
