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
from fec.cleaning.donor_consistency.employer import (
    _fill_employer_from_donor,
    _fill_employer_from_occupation,
    _fill_employer_from_raw_filings,
    _own_firm_absorbs_self_employed,
)
from fec.cleaning.donor_consistency.employer_variants import (
    _employer_acronym_variants,
    _employer_substring_variants,
    _employer_typos,
)
from fec.cleaning.donor_consistency.entity import (
    _apply_entity_overrides,
    _clear_nonindividual_employer_field,
    _reenforce_entity_consistency,
)
from fec.cleaning.donor_consistency.occupation import (
    _converge_occupation_within_employer,
    _fill_occupation_from_donor,
    _fill_self_employed_occupation_from_donor,
    _rederive_occupation_category,
)
from fec.cleaning.donor_consistency.retired import (
    _fill_prev_employer_from_donor,
    _normalize_previous_employer,
    _settle_retired_employer,
)
from fec.cleaning.pipeline.address_fixes.recovery import (
    _recover_missing_streets,
    _truncated_house_numbers,
)
from fec.cleaning.record_junk import _clean_junk_status_word_employer
from fec.cleaning.safety_nets.occupation import _fix_emp_occ_category_consistency
from fec.log import get_logger, log_count

logger = get_logger(__name__)

PREVIOUS = ("previous_employer",)
ENTITY_AND_WORK = ENTITY_FIELDS + NAME_FIELDS + WORK_FIELDS

# (label, fix, fields, reason, source)
CONSISTENCY_FIXES = (
    ("recover missing streets", _recover_missing_streets, STREET_FIELDS,
     "street_filled_from_donor_only_street_same_place", None),
    ("repair house numbers", _truncated_house_numbers, STREET_FIELDS,
     "truncated_house_number_repaired_from_donor_common_form", None),
    ("employer typos", _employer_typos, WORK_FIELDS,
     "employer_spelling_converged_to_donor_dominant", None),
    ("employer substring variants", _employer_substring_variants, WORK_FIELDS,
     "employer_short_form_merged_into_full_name_same_donor", None),
    ("employer acronym variants", _employer_acronym_variants, WORK_FIELDS,
     "employer_acronym_expanded_same_donor", None),
    ("own firm absorbs self-employed", _own_firm_absorbs_self_employed, WORK_FIELDS,
     "self_employed_rows_moved_to_donors_own_firm", None),
    ("fill employer from donor", _fill_employer_from_donor, WORK_FIELDS,
     "employer_filled_from_donor_latest_filing", None),
    ("fill occupation from donor", _fill_occupation_from_donor, WORK_FIELDS,
     "occupation_derived_from_donor_same_employer", None),
    ("fill self-employed occupation from donor", _fill_self_employed_occupation_from_donor, WORK_FIELDS,
     "self_employed_occupation_replaced_by_donors_real_one", None),
    ("fill employer from occupation", _fill_employer_from_occupation, WORK_FIELDS,
     "employer_derived_from_occupation_or_category", None),
    ("fill employer from raw filings", _fill_employer_from_raw_filings, WORK_FIELDS,
     "employer_recovered_from_donors_other_raw_filings", None),
    ("recover previous employer", _fill_prev_employer_from_donor, PREVIOUS,
     "previous_employer_from_latest_earlier_filing", None),
    ("clear refusal employers", _clean_junk_status_word_employer, WORK_FIELDS,
     "refusal_placeholder_employer_nulled", None),
    ("enforce entity consistency", _reenforce_entity_consistency, ENTITY_AND_WORK,
     "entity_type_unified_by_name", None),
    ("apply entity overrides", _apply_entity_overrides, ENTITY_AND_WORK,
     "curated_entity_type_override", "data/database/entity_overrides.csv"),
    ("converge occupation within employer", _converge_occupation_within_employer, WORK_FIELDS,
     "same_job_spelling_unified_to_donor_dominant", None),
    ("sync employer and occupation", _fix_emp_occ_category_consistency, WORK_FIELDS,
     "employer_occupation_category_consistency_fixed", None),
    ("rebuild occupation category", _rederive_occupation_category, WORK_FIELDS,
     "occupation_category_rederived_from_final_occupation", None),
    ("settle retired employer", _settle_retired_employer, EMPLOYMENT_FIELDS,
     "retired_recovered_company_demoted_to_previous", None),
    ("clear non-individual employers", _clear_nonindividual_employer_field, WORK_FIELDS,
     "non_individual_employer_cleared", None),
    ("normalize previous employers", _normalize_previous_employer, PREVIOUS,
     "previous_employer_contract_normalized", None),
)


# steps whose employer or occupation comes from the donor's OTHER filings
INFERRED_WORK_STEPS = frozenset({
    "donor_own_firm_absorbs_self_employed",
    "donor_fill_employer_from_donor",
    "donor_fill_occupation_from_donor",
    "donor_fill_self_employed_occupation_from_donor",
    "donor_fill_employer_from_raw_filings",
})


# run each donor-consistency fix in order, logging counts
def apply_donor_consistency(df: pd.DataFrame, trail: AuditTrail) -> int:
    """Repair values using donor history."""
    total = 0
    for label, fix, fields, reason, source in CONSISTENCY_FIXES:
        step = "donor_" + label.replace(" ", "_").replace("-", "_")
        count = trail.run(df, fix, step, reason, fields, source=source)
        total += count
        log_count(logger, label, count)
    return total
