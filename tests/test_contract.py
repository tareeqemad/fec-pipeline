from fec.contract import LOADED_COLUMNS, STAGES, check_input, check_loadable


ORDER = ("clean", "geocode", "resolve", "employer_geocode", "employers")


def test_the_stages_in_order_end_with_the_loaded_columns():
    columns: set[str] = set()
    for name in ORDER:
        check_input(name, columns)
        stage = STAGES[name]
        columns = (columns | set(stage.adds)) - set(stage.drops)
    assert columns == set(LOADED_COLUMNS)
    check_loadable(LOADED_COLUMNS)
