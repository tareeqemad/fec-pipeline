"""Constants for the resolve pipeline."""

from fec.config.constants import (
    SKIP_EMPLOYERS, REFUSAL_EMPLOYERS,
    # Re-exported: the existing `from .constants import NOT_REAL_EMPLOYER`
    # sites across resolve keep working. The sets themselves live in config —
    # cleaning and database need the same answer to "is this a company?", and
    # neither should have to import upward from resolve to get it.
    NOT_REAL_EMPLOYER, NOT_REAL_PREFIXES,
)

TIERS = [
    (1, ">500K",     500_000, float("inf")),
    (2, "100K-500K", 100_000, 500_000),
    (3, "50K-100K",   50_000, 100_000),
    (4, "10K-50K",    10_000,  50_000),
    (5, "1K-10K",      1_000,  10_000),
    (6, "<1K",             0,   1_000),
]

RETIRED_VALUES = {"RETIRED"}
SELF_EMPLOYED_VALUES = {"SELF-EMPLOYED", "SELF EMPLOYED", "SELF", "SOLE PROPRIETOR"}

# Cache file names
EMPLOYER_ADDR_CACHE = "resolve_employer_addr.json"
PREV_EMPLOYER_CACHE = "resolve_prev_employer.json"
COMMITTEE_CACHE     = "resolve_committee.json"

# AI settings
AI_BATCH_SIZE = 30
