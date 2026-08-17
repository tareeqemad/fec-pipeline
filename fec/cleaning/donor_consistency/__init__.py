"""Keep each donor consistent across filings."""
import pandas as pd

from fec.cleaning.audit_trail import (
    EMPLOYMENT_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    STREET_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.safety_nets.occupation import _fix_emp_occ_category_consistency
from fec.log import get_logger, log_count

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
from fec.cleaning.pipeline.address_fixes.recovery import (
    _recover_missing_streets,
    _truncated_house_numbers,
)
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

PREVIOUS = ("previous_employer",)
ENTITY_AND_WORK = ENTITY_FIELDS + NAME_FIELDS + WORK_FIELDS

# (label, fix, fields, reason, evidence)
CONSISTENCY_FIXES = (
    ("recover missing streets", _recover_missing_streets, STREET_FIELDS,
     "street_filled_from_donor_only_street_same_place", None),
    ("repair house numbers", _truncated_house_numbers, STREET_FIELDS,
     "truncated_house_number_repaired_from_donor_common_form", None),
    ("recover active employers", _retired_while_active, WORK_FIELDS,
     "retired_overwritten_donor_still_active_later", None),
    ("employer typos", _employer_typos, WORK_FIELDS,
     "employer_spelling_converged_to_donor_dominant", None),
    ("employer typo convergence", _employer_typos, WORK_FIELDS,
     "employer_spelling_converged_to_donor_dominant", None),
    ("repair retired self-employment", _selfemployed_while_retired, WORK_FIELDS,
     "self_employed_collapsed_donor_mostly_retired", None),
    ("employer substring variants", _employer_substring_variants, WORK_FIELDS,
     "employer_substring_merged_to_donor_dominant", None),
    ("repair swapped retired fields", _swapped_emp_occ_retired, WORK_FIELDS,
     "stray_employer_collapsed_donor_retired", None),
    ("fill employer from donor", _fill_employer_from_donor, WORK_FIELDS,
     "employer_filled_from_donor_latest_filing", None),
    ("fill occupation from donor", _fill_occupation_from_donor, WORK_FIELDS,
     "occupation_derived_from_donor_same_employer", None),
    ("fill employer from occupation", _fill_employer_from_occupation, WORK_FIELDS,
     "employer_derived_from_occupation_category_or_raw_filings", None),
    ("clear not-applicable people", _not_applicable_individual_sweep, WORK_FIELDS,
     "individual_occupation_status_rederived", None),
    ("recover previous employer", _fill_prev_employer_from_donor, PREVIOUS,
     "previous_employer_from_donor_most_common_real_employer", None),
    ("settle retirement after fills", _once_retired_always_retired, EMPLOYMENT_FIELDS,
     "retired_donor_status_unified", None),
    ("propagate previous employer", _propagate_previous_employer_within_donor, PREVIOUS,
     "previous_employer_propagated_within_donor", None),
    ("sync retired status", _retired_active_sync, EMPLOYMENT_FIELDS,
     "retired_category_employer_demoted_to_previous", None),
    ("clear refusal employers", _null_refusal_employers, WORK_FIELDS,
     "refusal_placeholder_employer_nulled", None),
    ("settle retirement after clearing", _once_retired_always_retired, EMPLOYMENT_FIELDS,
     "retired_donor_status_unified", None),
    ("enforce entity consistency", _reenforce_entity_consistency, ENTITY_AND_WORK,
     "entity_type_unified_by_name", None),
    ("apply entity overrides", _apply_entity_overrides, ENTITY_AND_WORK,
     "curated_entity_type_override", "data/database/entity_overrides.csv"),
    ("sync employer and occupation", _fix_emp_occ_category_consistency, WORK_FIELDS,
     "employer_occupation_category_consistency_fixed", None),
    ("rebuild occupation status", _rederive_occupation_status, WORK_FIELDS,
     "occupation_status_rederived", None),
    ("rebuild occupation category", _rederive_occupation_category, WORK_FIELDS,
     "occupation_category_rederived_from_final_occupation", None),
    ("settle retired employer", _settle_retired_employer, EMPLOYMENT_FIELDS,
     "retired_recovered_company_demoted_to_previous", None),
    ("settle retirement after status", _once_retired_always_retired, EMPLOYMENT_FIELDS,
     "retired_donor_status_unified", None),
    ("repropagate previous employer", _propagate_previous_employer_within_donor, PREVIOUS,
     "previous_employer_propagated_within_donor", None),
    ("clear non-individual employers", _clear_nonindividual_employer_field, WORK_FIELDS,
     "non_individual_employer_cleared", None),
    ("normalize previous employers", _normalize_previous_employer, PREVIOUS,
     "previous_employer_contract_normalized", None),
)


def apply_donor_consistency(df: pd.DataFrame, trail: AuditTrail) -> int:
    """Repair values using donor history."""
    total = 0
    for label, fix, fields, reason, evidence in CONSISTENCY_FIXES:
        step = "donor_" + label.replace(" ", "_").replace("-", "_")
        count = trail.run(df, fix, step, reason, fields, evidence=evidence)
        total += count
        log_count(logger, label, count)
    return total
