"""tools/roster_editorial_apply.py writes sourced office streets in the pipeline's own style."""
import importlib.util

from fec.env import PROJECT_ROOT


def _tool():
    spec = importlib.util.spec_from_file_location(
        "roster_editorial_apply", PROJECT_ROOT / "tools" / "roster_editorial_apply.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_us_office_street_goes_through_the_pipeline_normaliser():
    tool = _tool()
    assert tool.office_streets({"street_1": "50 Beale Street, Suite 2300", "country": "US"}) == \
        ("50 BEALE ST", "STE 2300")
    assert tool.office_streets({"street_1": "1800 Avenue of the Stars", "street_2": "Suite 1400"}) == \
        ("1800 AVE OF THE STARS", "STE 1400")


def test_foreign_office_street_is_kept_exactly_as_sourced():
    tool = _tool()
    assert tool.office_streets({"street_1": "12 Abba Eban Blvd", "country": "IL"}) == ("12 Abba Eban Blvd", "")
