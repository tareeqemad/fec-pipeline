"""The occupation categories in use, listed in data/config/occupation_categories.csv."""
from fec.config.tables import CONFIG_DIR, read_set

VALID_CATEGORIES = read_set(CONFIG_DIR / "occupation_categories.csv")
