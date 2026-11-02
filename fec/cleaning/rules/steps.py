"""Focused record-level safety rules, in one declared order."""
from fec.cleaning.reports.trail import (
    EMPLOYMENT_FIELDS,
    PEOPLE_FIELDS,
    PLACE_FIELDS,
    WORK_FIELDS,
)
from fec.cleaning.rules.addresses import (
    _fix_foreign_addresses,
    _fix_garbage_city_names,
    _fix_pr_zip_wrong_state,
)
from fec.cleaning.rules.committee import (
    _fix_misclassified_foundation,
)
from fec.cleaning.rules.employer import (
    _clear_fake_employers,
    _fix_choose_prefix,
    _fix_retired_typos,
    _null_sector_as_employer,
)
from fec.cleaning.rules.employer_swaps import (
    _fix_employer_equals_occupation,
    _fix_employer_is_occupation_word,
    _fix_role_as_employer,
    _fix_self_employed_consistency,
    _swap_role_employer_with_known_company,
)
from fec.cleaning.rules.name_fields import (
    _fix_company_name_as_occupation,
    _fix_occ_emp_both_swapped,
    _fix_own_name_as_employer,
)
from fec.cleaning.rules.occupation import (
    _fill_null_occupation_category,
    _fix_not_disclosed_self_employed,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
)
from fec.cleaning.rules.occupation_category import (
    _fix_emp_occ_category_consistency,
    _reclassify_other_category,
)
from fec.cleaning.rules.occupation_context import _disambiguate_vague_occupation

ALL_ROWS = "all"
INDIVIDUALS = "individuals"

# (rule, scope, fields it writes, audit reason)
SAFETY_RULES = (
    (_fill_null_occupation_category, INDIVIDUALS, WORK_FIELDS,
     'null_occupation_category_filled'),
    (_fix_not_disclosed_self_employed, INDIVIDUALS, WORK_FIELDS,
     'not_disclosed_self_employed_contradiction_fixed'),
    (_clear_fake_employers, INDIVIDUALS, WORK_FIELDS,
     'refusal_or_email_employer_nulled'),
    (_fix_retired_typos, INDIVIDUALS, EMPLOYMENT_FIELDS,
     'retired_variant_normalized_or_tail_split_to_previous_employer'),
    (_fix_employer_equals_occupation, INDIVIDUALS, WORK_FIELDS,
     'employer_equals_occupation_self_employed'),
    (_fix_employer_is_occupation_word, INDIVIDUALS, WORK_FIELDS,
     'occupation_word_in_employer_resolved'),
    (_fix_misclassified_foundation, ALL_ROWS, PEOPLE_FIELDS,
     'org_parsed_as_person_reclassified'),
    (_fix_choose_prefix, ALL_ROWS, WORK_FIELDS,
     'choose_prefix_stripped'),
    # Preserve the real company before a bare role becomes SELF-EMPLOYED.
    (_swap_role_employer_with_known_company, ALL_ROWS, WORK_FIELDS,
     'role_employer_swapped_with_known_company'),
    (_fix_role_as_employer, ALL_ROWS, WORK_FIELDS,
     'role_as_employer_swapped_or_self_employed'),
    (_null_sector_as_employer, ALL_ROWS, WORK_FIELDS,
     'sector_word_employer_nulled'),
    (_fix_self_employed_consistency, ALL_ROWS, WORK_FIELDS,
     'self_employed_row_employer_normalized'),
    (_fix_own_name_as_employer, ALL_ROWS, WORK_FIELDS,
     'own_name_employer_self_employed'),
    (_fix_foreign_addresses, ALL_ROWS, PLACE_FIELDS,
     'foreign_city_us_state_mismatch_nulled'),
    (_fix_garbage_city_names, ALL_ROWS, PLACE_FIELDS,
     'garbage_city_name_nulled'),
    (_fix_pr_zip_wrong_state, ALL_ROWS, PLACE_FIELDS,
     'state_corrected_from_pr_vi_zip'),
    (_fix_company_name_as_occupation, ALL_ROWS, WORK_FIELDS,
     'company_in_occupation_moved_to_employer'),
    (_fix_occ_emp_both_swapped, ALL_ROWS, WORK_FIELDS,
     'curated_company_in_occupation_swapped'),
    (_fix_web_artifact_occupation, ALL_ROWS, WORK_FIELDS,
     'web_artifact_occupation_nulled'),
    (_null_junk_occupation, ALL_ROWS, WORK_FIELDS,
     'email_or_company_in_occupation_moved_or_nulled'),
    (_fix_emp_occ_category_consistency, ALL_ROWS, WORK_FIELDS,
     'employer_occupation_category_consistency_fixed'),
    (_disambiguate_vague_occupation, ALL_ROWS, WORK_FIELDS,
     'vague_occupation_specialised_from_employer_context'),
    (_reclassify_other_category, ALL_ROWS, WORK_FIELDS,
     'other_category_reclassified'),
)
