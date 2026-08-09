"""Keep each donor consistent across filings."""
import pandas as pd

from fec.cleaning.safety_nets.occupation import _fix_emp_occ_category_consistency
from fec.log import get_logger

from .employer import (
    _employer_substring_variants,
    _employer_typos,
    _fill_employer_from_donor,
    _fill_employer_from_occupation,
    _null_refusal_employers,
)
from .entity import (
    _apply_entity_overrides,
    _clear_nonindividual_employer_field,
    _reenforce_entity_consistency,
)
from .names_addresses import _recover_missing_streets, _truncated_house_numbers
from .occupation import (
    _fill_occupation_from_donor,
    _not_applicable_individual_sweep,
    _rederive_occupation_category,
    _rederive_occupation_status,
)
from .retired import (
    _fill_prev_employer_from_donor,
    _normalize_previous_employer,
    _once_retired_always_retired,
    _propagate_previous_employer_within_donor,
    _retired_active_sync,
    _retired_while_active,
    _selfemployed_while_retired,
    _settle_retired_employer,
    _swapped_emp_occ_retired,
)

logger = get_logger(__name__)


def apply_donor_consistency(df: pd.DataFrame) -> int:
    """Repair values using donor history."""
    fixes = (
        ("recover missing streets", _recover_missing_streets),
        ("repair house numbers", _truncated_house_numbers),
        ("recover active employers", _retired_while_active),
        ("employer typos", _employer_typos),
        ("employer typo convergence", _employer_typos),
        ("repair retired self-employment", _selfemployed_while_retired),
        ("employer substring variants", _employer_substring_variants),
        ("repair swapped retired fields", _swapped_emp_occ_retired),
        ("fill employer from donor", _fill_employer_from_donor),
        ("fill occupation from donor", _fill_occupation_from_donor),
        ("fill employer from occupation", _fill_employer_from_occupation),
        ("clear not-applicable people", _not_applicable_individual_sweep),
        ("recover previous employer", _fill_prev_employer_from_donor),
        ("settle retirement after fills", _once_retired_always_retired),
        ("propagate previous employer", _propagate_previous_employer_within_donor),
        ("sync retired status", _retired_active_sync),
        ("clear refusal employers", _null_refusal_employers),
        ("settle retirement after clearing", _once_retired_always_retired),
        ("enforce entity consistency", _reenforce_entity_consistency),
        ("apply entity overrides", _apply_entity_overrides),
        ("sync employer and occupation", _fix_emp_occ_category_consistency),
        ("rebuild occupation status", _rederive_occupation_status),
        ("rebuild occupation category", _rederive_occupation_category),
        ("settle retired employer", _settle_retired_employer),
        ("settle retirement after status", _once_retired_always_retired),
        ("repropagate previous employer", _propagate_previous_employer_within_donor),
        ("clear non-individual employers", _clear_nonindividual_employer_field),
        ("normalize previous employers", _normalize_previous_employer),
    )

    total = 0
    for label, fix in fixes:
        count = fix(df)
        total += count
        if count:
            logger.info("  %-38s %s", label, f"{count:,}")
    return total
