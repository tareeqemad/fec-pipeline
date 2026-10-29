"""Environment settings, database connection and project paths."""
import os
from pathlib import Path

from fec.log import get_logger

logger = get_logger(__name__)

# project root = directory containing .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_OWNER = "fec_owner"
DATABASE_READER = "fec_app"


# Load .env without replacing existing environment variables.
def load_env(env_path: Path | None = None) -> None:
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        logger.warning(".env not found at %s - using environment variables only", path)
        return

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:  # don't override existing env vars
                os.environ[key] = value


# Read a setting and report missing required values.
def get_env(key: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(key, default)
    if required and not value:
        raise EnvironmentError(
            f"Required environment variable {key} is not set. "
            f"Add it to .env or export it."
        )
    return value


# Build PostgreSQL connection settings from the environment.
def get_db_config() -> dict:
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
OUTPUT_DIR = DATA_DIR / "output"
REPORTS_DIR = DATA_DIR / "reports"
# raw FEC data; pull.py appends, nothing rewrites it
RAW_CSV = RAW_DIR / "contributions.csv"
# FEC's own entity type and id per sub_id; raw unchanged.
FEC_SOURCE_CSV = RAW_DIR / "fec_source_fields.csv"
CLEANED_CSV = OUTPUT_DIR / "contributions_cleaned.csv"
# Company offices, gathered once; new companies are added by hand.
EMPLOYER_LOCATIONS_CSV = REFERENCE_DIR / "employer_locations.csv"
SCHEMA_SQL = PROJECT_ROOT / "fec" / "database" / "schema.sql"
# The single committee list; add a row per new committee.
COMMITTEES_CSV = REFERENCE_DIR / "committees.csv"
# Curated employer spelling rules
EMPLOYER_NAME_RULES_CSV = RULES_DIR / "employer_name_rules.csv"
# Curated contributor name rules
CONTRIBUTOR_NAME_RULES_CSV = RULES_DIR / "contributor_name_rules.csv"
# Curated contributor address corrections
ADDRESS_RULES_CSV = RULES_DIR / "address_rules.csv"
# Hand-set employer, occupation and previous employer per filing
MANUAL_EMPLOYER_OVERRIDES_CSV = RULES_DIR / "manual_employer_overrides.csv"
GEOCODE_CACHE_JSON = CACHE_DIR / "geocode_cache.json"
