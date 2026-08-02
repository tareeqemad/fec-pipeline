"""Per-donor address correction and recovery, run after field-level cleaning; scoped to one person so genuine distinct addresses are never merged."""
from .state_zip import (  # noqa: F401
    _fix_impossible_city_states, _fix_state_zip_mismatches,
)
from .unify import (  # noqa: F401
    _unify_street_spellings, _unify_street_spacing,
    _unit_core, _unify_unit_designators,
)
from .recovery import (  # noqa: F401
    _recover_null_streets, _is_usable_street,
    _recover_nonstreet_from_donor, _recover_house_number_from_donor,
    _recover_address_from_same_street,
)
