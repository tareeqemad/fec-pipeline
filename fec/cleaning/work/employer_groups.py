"""Apply reviewed company names before donor matching."""
import pandas as pd

from fec.cleaning.work.employer_aliases import (
    apply_reviewed_employer_names,
    employer_rule_key,
    reviewed_employer_name,
)


def employer_group_words(name: str) -> tuple[str, ...]:
    """Keep word boundaries, word order and legal forms."""
    return tuple(employer_rule_key(reviewed_employer_name(name)).split())


def _employer_group_key(name: str) -> str:
    return " ".join(employer_group_words(name))


def _canonicalize_employers(df: pd.DataFrame) -> int:
    """No frequency, prefix, or spacing guesses."""
    return apply_reviewed_employer_names(df, ("contributor_employer",))
