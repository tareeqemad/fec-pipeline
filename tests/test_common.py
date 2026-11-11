"""Tests of the shared column checks between stages."""

import pytest

from fec.common.columns import CLEAN_COLUMNS, LOADED_COLUMNS, ContractError, check_input, check_loadable, check_output


def test_each_stage_accepts_the_file_the_stage_before_it_writes():
    check_input("geocode.py", CLEAN_COLUMNS)
    geocoded = [*CLEAN_COLUMNS, "latitude", "longitude"]
    check_input("resolve.py --apply", geocoded, "employer_status")
    check_loadable([*geocoded, "employer_status"])
    assert set(LOADED_COLUMNS) == {*geocoded, "employer_status"}


def test_a_stage_refuses_its_own_output_and_a_file_missing_a_column():
    with pytest.raises(ContractError, match="already ran"):
        check_input("resolve.py --apply", LOADED_COLUMNS, "employer_status")
    with pytest.raises(ContractError, match="needs columns sub_id"):
        check_input("geocode.py", CLEAN_COLUMNS[1:])
    with pytest.raises(ContractError, match="not the finished file: missing employer_status"):
        check_loadable([*CLEAN_COLUMNS, "latitude", "longitude"])


def test_a_stage_refuses_to_write_missing_extra_or_repeated_columns():
    with pytest.raises(ContractError, match="missing b; unexpected _work; duplicated a"):
        check_output("clean.py", ["a", "a", "_work"], ["a", "b"])
