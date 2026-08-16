"""Environment configuration and database connection; all secrets come from .env or environment variables."""
import os
from pathlib import Path

from fec.log import get_logger

logger = get_logger(__name__)

# project root = directory containing .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_OWNER = "fec_owner"
DATABASE_READER = "fec_app"


def load_env(env_path: Path | None = None) -> None:
    """Load .env file into os.environ (simple parser, no dependency)."""
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        logger.warning(".env not found at %s - using environment variables only", path)
        return

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:  # don't override existing env vars
                os.environ[key] = value


def get_env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """Read an environment variable with validation."""
    value = os.environ.get(key, default)
    if required and not value:
        raise EnvironmentError(
            f"Required environment variable {key} is not set. "
            f"Add it to .env or export it."
        )
    return value


def get_db_config() -> dict:
    """Build PostgreSQL connection config from environment."""
    return {
        "host": get_env("PG_HOST", "localhost"),
        "port": int(get_env("PG_PORT", "5432")),
        "dbname": get_env("PG_DBNAME", "fec_db"),
        "user": get_env("PG_USER", required=True),
        "password": get_env("PG_PASSWORD", required=True),
    }


# file paths
RAW_CSV = PROJECT_ROOT / "data" / "contributions.csv"
CLEANED_CSV = PROJECT_ROOT / "data" / "contributions_cleaned.csv"
SCHEMA_SQL = PROJECT_ROOT / "fec" / "database" / "schema.sql"
DATA_DIR = PROJECT_ROOT / "data"
# single source of truth for committee identities; add a row for every new committee
COMMITTEES_CSV = PROJECT_ROOT / "data" / "database" / "committees.csv"
# Known employer offices, built by geocode.py --employer-only
EMPLOYER_LOCATIONS_CSV = PROJECT_ROOT / "data" / "employer_locations.csv"
# Curated employer spelling rules
EMPLOYER_NAME_RULES_CSV = PROJECT_ROOT / "data" / "database" / "employer_name_rules.csv"
# Curated contributor name rules
CONTRIBUTOR_NAME_RULES_CSV = PROJECT_ROOT / "data" / "database" / "contributor_name_rules.csv"
# Curated contributor address corrections
ADDRESS_RULES_CSV = PROJECT_ROOT / "data" / "database" / "address_rules.csv"
