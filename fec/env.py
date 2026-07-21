"""
Environment configuration and database connection.

All secrets come from .env or environment variables — nothing hardcoded.
"""
import os
from pathlib import Path
from typing import Optional

from fec.log import get_logger

logger = get_logger(__name__)

# Project root = directory containing .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(env_path: Optional[Path] = None) -> None:
    """Load .env file into os.environ (simple parser, no dependency)."""
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        logger.warning(".env not found at %s — using environment variables only", path)
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


def get_env(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
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


def get_db_roles() -> dict:
    """
    Build role→password mapping from environment.
    
    Format in .env:
        DB_ROLES=fec_owner,fec_app,ytpub001
        DB_ROLE_PASSWORD=your_shared_password
    
    Or individual:
        DB_ROLE_fec_owner=password1
        DB_ROLE_fec_app=password2
    """
    shared_password = get_env("DB_ROLE_PASSWORD")
    role_names = get_env("DB_ROLES", "").split(",")
    role_names = [r.strip() for r in role_names if r.strip()]

    roles = {}
    for role in role_names:
        individual_pw = get_env(f"DB_ROLE_{role}")
        roles[role] = individual_pw or shared_password or ""

    return roles


# File paths
RAW_CSV = PROJECT_ROOT / "data" / "contributions.csv"
CLEANED_CSV = PROJECT_ROOT / "data" / "contributions_cleaned.csv"
SCHEMA_SQL = PROJECT_ROOT / "fec" / "database" / "schema.sql"
DATA_DIR = PROJECT_ROOT / "data"
# Single source of truth for committee identities (FEC number ↔ name/logo/$).
# Add a row here for every new committee you pull.
COMMITTEES_CSV = PROJECT_ROOT / "data" / "database" / "committees.csv"
# Normalized employer dimension — one row per company with its HQ address,
# coordinates and address source/confidence. Built by build_employers.py.
EMPLOYERS_CSV = PROJECT_ROOT / "data" / "employers.csv"
