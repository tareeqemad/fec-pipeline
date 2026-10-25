"""Keep each donor consistent across filings."""
import pandas as pd

from fec.cleaning.reports.trail import (
    EMPLOYMENT_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.donors.employer import _fill_employer_from_occupation
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.donors.entity import (
    _apply_entity_overrides,
    _apply_source_entity_types,
    _clear_nonindividual_employer_field,
    _reenforce_entity_consistency,
)
from fec.cleaning.donors.occupation import (
    _fill_occupation_from_donor,
    _fill_self_employed_occupation_from_donor,
    _rederive_occupation_category,
)
from fec.cleaning.donors.retired import (
    _fill_prev_employer_from_donor,
    _normalize_previous_employer,
    _settle_retired_employer,
)
from fec.cleaning.rules.manual import OVERRIDE_FIELDS, apply_manual_employer_overrides
from fec.cleaning.rules.junk import _clean_junk_status_word_employer
from fec.cleaning.rules.occupation_category import (
    _fix_emp_occ_category_consistency,
)

PREVIOUS = ("previous_employer",)
ENTITY_AND_WORK = ENTITY_FIELDS + NAME_FIELDS + WORK_FIELDS

# (audit step, fix, fields, reason, source)
CONSISTENCY_FIXES = (
    ("donor_reviewed_employers", apply_reviewed_employer_names, EMPLOYMENT_FIELDS,
     "documented_company_alias", "data/rules/employer_name_rules.csv"),
    ("donor_fill_occupation_from_donor", _fill_occupation_from_donor, WORK_FIELDS,
     "occupation_derived_from_donor_same_employer", None),
    ("donor_fill_self_employed_occupation_from_donor",
     _fill_self_employed_occupation_from_donor, WORK_FIELDS,
     "self_employed_occupation_replaced_by_donors_real_one", None),
    ("donor_fill_employer_from_occupation", _fill_employer_from_occupation, WORK_FIELDS,
     "employer_derived_from_occupation_or_category", None),
    ("donor_recover_previous_employer", _fill_prev_employer_from_donor, PREVIOUS,
     "previous_employer_from_latest_earlier_filing", None),
    ("donor_clear_refusal_employers", _clean_junk_status_word_employer, WORK_FIELDS,
     "refusal_placeholder_employer_nulled", None),
    ("donor_enforce_entity_consistency", _reenforce_entity_consistency, ENTITY_AND_WORK,
     "entity_type_unified_by_name", None),
    ("donor_apply_fec_source_entity_type", _apply_source_entity_types, ENTITY_AND_WORK,
     "entity_type_as_fec_reports_it", None),
    ("donor_apply_entity_overrides", _apply_entity_overrides, ENTITY_AND_WORK,
     "curated_entity_type_override", "data/rules/entity_overrides.csv"),
    ("donor_sync_employer_and_occupation", _fix_emp_occ_category_consistency, WORK_FIELDS,
     "employer_occupation_category_consistency_fixed", None),
    ("donor_rebuild_occupation_category", _rederive_occupation_category, WORK_FIELDS,
     "occupation_category_rederived_from_final_occupation", None),
    ("donor_settle_retired_employer", _settle_retired_employer, EMPLOYMENT_FIELDS,
     "retired_recovered_company_demoted_to_previous", None),
    ("donor_clear_non_individual_employers", _clear_nonindividual_employer_field, WORK_FIELDS,
     "non_individual_employer_cleared", None),
    ("donor_normalize_previous_employers", _normalize_previous_employer, PREVIOUS,
     "previous_employer_contract_normalized", None),
)


# Work fields inferred from other filings.
INFERRED_WORK_STEPS = frozenset({
    "donor_fill_occupation_from_donor",
    "donor_fill_self_employed_occupation_from_donor",
})


def apply_donor_consistency(df: pd.DataFrame, trail: AuditTrail) -> None:
    """Repair values using donor history."""
    for step, fix, fields, reason, source in CONSISTENCY_FIXES:
        trail.run(df, fix, step, reason, fields, source=source)
        # Keep curated values before the next rule uses them.
        trail.run(
            df, lambda frame: apply_manual_employer_overrides(frame, company_names_only=True),
            "manual_overrides_kept", "curated_row_override", OVERRIDE_FIELDS,
            source="data/rules/manual_employer_overrides.csv",
        )
