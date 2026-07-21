"""tests/test_committee_guard.py — clean.py aborts when a pulled committee is
missing from committees.csv (otherwise the loader silently drops all its rows)."""
import pandas as pd
import pytest

import clean
from fec.committees import load_committees


def _known():
    return sorted({r["committee_short"] for r in load_committees() if r.get("committee_short")})


def test_known_committees_pass():
    known = _known()
    if not known:
        pytest.skip("committees.csv not available in this environment")
    df = pd.DataFrame({"recipient_committee": known + [None, None]})
    clean._assert_known_committees(df)  # must not raise


def test_unknown_committee_aborts():
    df = pd.DataFrame({"recipient_committee": ["AIPAC", "C99999999", "C99999999"]})
    with pytest.raises(SystemExit):
        clean._assert_known_committees(df)


def test_missing_column_is_noop():
    clean._assert_known_committees(pd.DataFrame({"foo": [1, 2]}))  # no column → no raise
