"""Per-stage column contract."""
import pytest

from fec.contract import (
    CLEAN_COLUMNS,
    LOADED_COLUMNS,
    STAGES,
    ContractError,
    check_input,
    check_loadable,
    check_output,
)

ORDER = ("clean", "geocode", "resolve", "employer_geocode", "employers")


def test_the_stages_in_order_end_with_the_loaded_columns():
    columns: set[str] = set()
    for name in ORDER:
        check_input(name, columns)
        stage = STAGES[name]
        columns = (columns | set(stage.adds)) - set(stage.drops)
    assert columns == set(LOADED_COLUMNS)
    check_loadable(LOADED_COLUMNS)


def test_a_stage_run_out_of_order_names_the_stage_to_run_first():
    with pytest.raises(ContractError, match=r"geocode.py --employer-only needs .*run resolve.py --apply first"):
        check_input("employer_geocode", CLEAN_COLUMNS)


def test_resolve_refuses_a_file_it_already_resolved():
    # it reads previous_employer as filed: on its own output it reads back its answers
    finished = LOADED_COLUMNS
    with pytest.raises(ContractError, match=r"resolve.py --apply already ran on this file.*run clean.py first"):
        check_input("resolve", finished)
    check_input("resolve", CLEAN_COLUMNS + ("latitude", "longitude"))


@pytest.mark.parametrize("written, problem", [
    (CLEAN_COLUMNS + ("latitude", "longitude", "geocode_level"), "unexpected geocode_level"),
    (CLEAN_COLUMNS + ("latitude",), "missing longitude"),
    (CLEAN_COLUMNS + ("latitude", "longitude", "_street_inferred"), "working columns _street_inferred"),
    (CLEAN_COLUMNS + ("latitude", "longitude", "latitude"), "duplicated latitude"),
])
def test_a_broken_write_is_refused(written, problem):
    with pytest.raises(ContractError, match=problem):
        check_output("geocode", CLEAN_COLUMNS, written)


def test_the_employer_stage_drops_what_it_consumed():
    before = CLEAN_COLUMNS + STAGES["resolve"].adds
    after = [column for column in before if column not in STAGES["employer_geocode"].drops]
    check_output("employer_geocode", before, after + ["employer_latitude", "employer_longitude"])
    with pytest.raises(ContractError, match="unexpected resolve_confidence, resolve_method"):
        check_output("employer_geocode", before, list(before) + ["employer_latitude", "employer_longitude"])


def test_the_loader_takes_only_the_finished_file():
    with pytest.raises(ContractError, match="missing employer_status; run resolve.py --apply"):
        check_loadable(CLEAN_COLUMNS + ("latitude", "longitude"))
    with pytest.raises(ContractError, match="unexpected employer_address"):
        check_loadable(LOADED_COLUMNS + ("employer_address",))
