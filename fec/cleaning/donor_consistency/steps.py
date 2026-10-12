"""Keep each donor consistent across filings."""
import pandas as pd

from fec.cleaning.addresses.fixes.house_numbers import truncated_house_numbers
from fec.cleaning.addresses.fixes.recovery import recover_missing_streets
from fec.cleaning.audit_trail import (
    EMPLOYMENT_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    STREET_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.donor_consistency.employer import (
    fill_employer_from_donor,
    fill_employer_from_occupation,
    fill_employer_from_raw_filings,
    own_firm_absorbs_self_employed,
)
from fec.cleaning.donor_consistency.employer_variants import (
    employer_acronym_variants,
    employer_substring_variants,
    employer_typos,
)
from fec.cleaning.donor_consistency.entity import (
    apply_entity_overrides,
    apply_source_entity_types,
    clear_nonindividual_employer_field,
    reenforce_entity_consistency,
)
from fec.cleaning.donor_consistency.occupation import (
    fill_occupation_from_donor,
    fill_self_employed_occupation_from_donor,
    rederive_occupation_category,
)
from fec.cleaning.donor_consistency.retired import (
    fill_prev_employer_from_donor,
    settle_retired_employer,
)
from fec.cleaning.manual_overrides import OVERRIDE_FIELDS, apply_manual_employer_overrides
from fec.cleaning.previous_employer import normalize_previous_employer_column
from fec.cleaning.record_junk import clean_junk_status_word_employer
from fec.cleaning.safety_nets.occupation_category import (
    fix_emp_occ_category_consistency,
)
from fec.log import get_logger, log_count

logger = get_logger(__name__)

PREVIOUS = ("previous_employer",)
ENTITY_AND_WORK = ENTITY_FIELDS + NAME_FIELDS + WORK_FIELDS

# (label, fix, fields, reason, source)
CONSISTENCY_FIXES = (
    ("recover missing streets", recover_missing_streets, STREET_FIELDS,
     "street_filled_from_donor_only_street_same_place", None),
    ("repair house numbers", truncated_house_numbers, STREET_FIELDS,
     "truncated_house_number_repaired_from_donor_common_form", None),
    ("employer typos", employer_typos, WORK_FIELDS,
     "employer_spelling_converged_to_donor_dominant", None),
    ("employer substring variants", employer_substring_variants, WORK_FIELDS,
     "employer_short_form_merged_into_full_name_same_donor", None),
    ("employer acronym variants", employer_acronym_variants, WORK_FIELDS,
     "employer_acronym_expanded_same_donor", None),
    ("own firm absorbs self-employed", own_firm_absorbs_self_employed, WORK_FIELDS,
     "self_employed_rows_moved_to_donors_own_firm", None),
    ("fill employer from donor", fill_employer_from_donor, WORK_FIELDS,
     "employer_filled_from_donor_latest_filing", None),
    ("fill occupation from donor", fill_occupation_from_donor, WORK_FIELDS,
     "occupation_derived_from_donor_same_employer", None),
    ("fill self-employed occupation from donor", fill_self_employed_occupation_from_donor, WORK_FIELDS,
     "self_employed_occupation_replaced_by_donors_real_one", None),
    ("fill employer from occupation", fill_employer_from_occupation, WORK_FIELDS,
     "employer_derived_from_occupation_or_category", None),
    ("fill employer from raw filings", fill_employer_from_raw_filings, WORK_FIELDS,
     "employer_recovered_from_donors_other_raw_filings", None),
    ("recover previous employer", fill_prev_employer_from_donor, PREVIOUS,
     "previous_employer_from_latest_earlier_filing", None),
    ("clear refusal employers", clean_junk_status_word_employer, WORK_FIELDS,
     "refusal_placeholder_employer_nulled", None),
    ("enforce entity consistency", reenforce_entity_consistency, ENTITY_AND_WORK,
     "entity_type_unified_by_name", None),
    ("apply fec source entity type", apply_source_entity_types, ENTITY_AND_WORK,
     "entity_type_as_fec_reports_it", None),
    ("apply entity overrides", apply_entity_overrides, ENTITY_AND_WORK,
     "curated_entity_type_override", "data/rules/entity_overrides.csv"),
    ("sync employer and occupation", fix_emp_occ_category_consistency, WORK_FIELDS,
     "employer_occupation_category_consistency_fixed", None),
    ("rebuild occupation category", rederive_occupation_category, WORK_FIELDS,
     "occupation_category_rederived_from_final_occupation", None),
    ("settle retired employer", settle_retired_employer, EMPLOYMENT_FIELDS,
     "retired_recovered_company_demoted_to_previous", None),
    ("clear non-individual employers", clear_nonindividual_employer_field, WORK_FIELDS,
     "non_individual_employer_cleared", None),
    ("normalize previous employers", normalize_previous_employer_column, PREVIOUS,
     "previous_employer_contract_normalized", None),
)


# steps that infer work from other filings
INFERRED_WORK_STEPS = frozenset({
    "donor_own_firm_absorbs_self_employed",
    "donor_fill_employer_from_donor",
    "donor_fill_occupation_from_donor",
    "donor_fill_self_employed_occupation_from_donor",
    "donor_fill_employer_from_raw_filings",
})


# run each donor-consistency fix in order
def apply_donor_consistency(df: pd.DataFrame, trail: AuditTrail) -> int:
    total = 0
    for label, fix, fields, reason, source in CONSISTENCY_FIXES:
        step = "donor_" + label.replace(" ", "_").replace("-", "_")
        count = trail.run(df, fix, step, reason, fields, source=source)
        total += count
        log_count(logger, label, count)
        # restore overrides before any later step runs
        trail.run(df, _keep_overrides, "manual_overrides_kept", "curated_row_override",
                  OVERRIDE_FIELDS, source="data/rules/manual_employer_overrides.csv")
    return total


# re-apply overrides' cleared cells and company names
def _keep_overrides(df: pd.DataFrame) -> int:
    return apply_manual_employer_overrides(df, company_names_only=True)
