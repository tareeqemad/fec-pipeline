"""Fixes that need donor_key, so they can't run in clean.py's safety nets; runs inside unify_donors() after donor matching."""
import pandas as pd

from fec.log import get_logger

from fec.database.post_merge_fixes.names_addresses import _truncated_house_numbers
from fec.database.post_merge_fixes.retired import (
    _retired_while_active, _selfemployed_while_retired,
    _swapped_emp_occ_retired, _once_retired_always_retired,
    _fill_prev_employer_from_donor, _propagate_previous_employer_within_donor,
    _retired_active_sync, _normalize_previous_employer,
)
from fec.database.post_merge_fixes.employer import (
    _employer_typos, _employer_substring_variants, _null_refusal_employers,
    _fill_employer_from_donor, _fill_employer_from_occupation,
)
from fec.database.post_merge_fixes.occupation import (
    _rederive_occupation_status, _rederive_occupation_category,
    _occupation_consolidation,
    _fill_occupation_from_donor, _not_applicable_individual_sweep,
)
from fec.database.post_merge_fixes.entity import (
    _clear_nonindividual_employer_field, _apply_entity_overrides,
    _reenforce_entity_consistency,
)

logger = get_logger(__name__)


def apply_post_merge_fixes(df: pd.DataFrame) -> int:
    """Run all fixes that require donor_key. Returns total records fixed."""
    total = 0
    # labels are slugs of the function names; the trailing [letter] is the historical step tag
    steps = [
        ("truncated-house-numbers [Z]",              _truncated_house_numbers),
        ("retired-while-active -> real employer [AA]", _retired_while_active),
        ("employer-typos (pass 1) [AB]",             _employer_typos),
        ("employer-typos (pass 2) [AB]",             _employer_typos),
        ("occupation-consolidation (pass 1) [AE]",   _occupation_consolidation),
        ("occupation-consolidation (pass 2) [AE]",   _occupation_consolidation),
        ("selfemployed-while-retired [AF]",          _selfemployed_while_retired),
        ("employer-substring-variants [AG]",         _employer_substring_variants),
        ("swapped-emp-occ-retired [AH]",             _swapped_emp_occ_retired),
        ("fill-employer-from-donor [AI]",            _fill_employer_from_donor),
        ("fill-occupation-from-donor [AJ]",          _fill_occupation_from_donor),
        ("fill-employer-from-occupation [AK]",       _fill_employer_from_occupation),
        ("not-applicable-individual-sweep [AM]",     _not_applicable_individual_sweep),
        ("fill-prev-employer-from-donor [AN]",       _fill_prev_employer_from_donor),
        ("once-retired-always-retired [AO]",         _once_retired_always_retired),
        ("propagate-prev-employer-within-donor [AP]", _propagate_previous_employer_within_donor),
        ("retired-active-sync [AQ]",                 _retired_active_sync),
        # must run after all employer-fill steps (AI/AJ/AK/AL) and before AO pass 2
        # (blanking a placeholder can itself create a once-retired case)
        ("null-refusal-employers [AS]",              _null_refusal_employers),
        # convergence: the fills and AS can expose retired pairs pass 1 couldn't see
        ("once-retired-always-retired (pass 2) [AO]", _once_retired_always_retired),
        # entity types must settle before the derive steps below (AU/AV re-type rows)
        ("reenforce-entity-consistency [AU]",        _reenforce_entity_consistency),
        # hand-curated fixes; wins over AU
        ("apply-entity-overrides [AV]",              _apply_entity_overrides),
        # every preceding step can change occupation/employer, leaving the status stale
        ("rederive-occupation-status [AR]",          _rederive_occupation_status),
        # the occupation fills above can leave the category stale
        ("rederive-occupation-category [AT]",        _rederive_occupation_category),
        # must run after AU/AV re-typing so re-typed rows get their employer cleared
        ("clear-nonindividual-employer-field [AW]",  _clear_nonindividual_employer_field),
        # one pass owning the previous_employer contract, after AN/AP/AQ all wrote it
        ("normalize-previous-employer [AX]",         _normalize_previous_employer),
    ]
    for label, fn in steps:
        n = fn(df)
        if n:
            logger.info(f"    {label}: {n:,} fixed")
        total += n
    return total
