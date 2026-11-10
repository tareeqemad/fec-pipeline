"""Focused record-level safety rules, in one declared order."""
from fec.cleaning.reports.trail import (
    WORK_FIELDS,
)
from fec.cleaning.rules.employer import (
    _fix_choose_prefix,
    _null_sector_as_employer,
)
from fec.cleaning.rules.name_fields import (
    _fix_own_name_as_employer,
)
from fec.cleaning.rules.occupation import (
    _fill_null_occupation_category,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
)
from fec.cleaning.rules.occupation_context import _disambiguate_vague_occupation

ALL_ROWS = "all"
INDIVIDUALS = "individuals"

# (rule, scope, fields it writes, audit reason)
SAFETY_RULES = (
    (_fill_null_occupation_category, INDIVIDUALS, WORK_FIELDS,
     'null_occupation_category_filled'),
    (_fix_choose_prefix, ALL_ROWS, WORK_FIELDS,
     'choose_prefix_stripped'),
    (_null_sector_as_employer, ALL_ROWS, WORK_FIELDS,
     'sector_word_employer_nulled'),
    (_fix_own_name_as_employer, ALL_ROWS, WORK_FIELDS,
     'own_name_employer_self_employed'),
    (_fix_web_artifact_occupation, ALL_ROWS, WORK_FIELDS,
     'web_artifact_occupation_nulled'),
    (_null_junk_occupation, ALL_ROWS, WORK_FIELDS,
     'email_or_company_in_occupation_moved_or_nulled'),
    (_disambiguate_vague_occupation, ALL_ROWS, WORK_FIELDS,
     'vague_occupation_specialised_from_employer_context'),
)
