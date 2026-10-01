"""Per-stage column contract and the run id shared from clean.py to the loader."""
import pandas as pd
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
from fec.pipeline_run import RunMismatch, check_same_run, record, start_run

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


def _write(path, value):
    pd.DataFrame({"sub_id": [value]}).to_csv(path, index=False)


def test_files_of_one_run_pass(tmp_path):
    cleaned, locations = tmp_path / "contributions_cleaned.csv", tmp_path / "employer_locations.csv"
    _write(cleaned, "1")
    run_id = start_run(cleaned)
    record("clean", cleaned)
    _write(locations, "A")
    record("employers", cleaned, locations)
    assert check_same_run(cleaned, locations) == run_id


def test_a_file_left_from_an_older_run_is_refused(tmp_path):
    cleaned, locations = tmp_path / "contributions_cleaned.csv", tmp_path / "employer_locations.csv"
    _write(cleaned, "1")
    start_run(cleaned)
    _write(locations, "A")
    record("employers", cleaned, locations)
    # clean.py runs again; employer_locations.csv is from the run before
    _write(cleaned, "2")
    start_run(cleaned)
    record("clean", cleaned)
    with pytest.raises(RunMismatch, match="employer_locations.csv was not written in run"):
        check_same_run(cleaned, locations)


def test_a_file_changed_after_its_stage_is_refused(tmp_path):
    cleaned = tmp_path / "contributions_cleaned.csv"
    _write(cleaned, "1")
    start_run(cleaned)
    record("resolve", cleaned)
    _write(cleaned, "edited")
    with pytest.raises(RunMismatch, match="changed after resolve wrote it"):
        check_same_run(cleaned)


def test_no_run_before_clean(tmp_path):
    cleaned = tmp_path / "contributions_cleaned.csv"
    _write(cleaned, "1")
    with pytest.raises(RunMismatch, match="run clean.py first"):
        check_same_run(cleaned)
