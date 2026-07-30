"""The clean command aborts when a pulled committee is missing from committees.csv (the loader would drop its rows)."""
import pandas as pd
import pytest

from fec.cleaning import cli
from fec.committees import load_committees


def _known():
    return sorted({r["committee_short"] for r in load_committees() if r.get("committee_short")})


def test_known_committees_pass():
    known = _known()
    if not known:
        pytest.skip("committees.csv not available in this environment")
    df = pd.DataFrame({"recipient_committee": known + [None, None]})
    cli._assert_known_committees(df)  # must not raise


def test_unknown_committee_aborts():
    df = pd.DataFrame({"recipient_committee": ["AIPAC", "C99999999", "C99999999"]})
    with pytest.raises(SystemExit):
        cli._assert_known_committees(df)
