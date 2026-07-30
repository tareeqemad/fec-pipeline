"""Live query-correctness suite for the real fec_db (read-only); skips when unreachable or empty."""
from __future__ import annotations

import pytest

from fec.database.query_checks import CHECKS, CRIT, WARN

pytestmark = pytest.mark.live

_CRIT = [c for c in CHECKS if c.severity == CRIT]
_WARN = [c for c in CHECKS if c.severity == WARN]


@pytest.fixture(scope="module")
def live():
    """A read-only cursor on the live fec_db; skip if unreachable / empty."""
    try:
        from fec.database.loader import connect
        conn = connect()
    except Exception as e:  # no DB configured / not running (e.g. CI)
        pytest.skip(f"fec_db not reachable: {e}")
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM contributions")
    if cur.fetchone()[0] == 0:
        conn.close()
        pytest.skip("fec_db has no contributions loaded")
    yield cur
    conn.close()


@pytest.mark.parametrize("check", _CRIT, ids=[c.name for c in _CRIT])
def test_query_correctness(live, check):
    """Every correctness check must hold on the real data."""
    ok, detail = check.run(live)
    assert ok, f"{check.name} — {detail}"


@pytest.mark.parametrize("check", _WARN, ids=[c.name for c in _WARN])
def test_data_quality_signal(live, check):
    """Data-quality signals are reported but never fail CI."""
    ok, detail = check.run(live)
    if not ok:
        pytest.skip(f"signal: {check.name} — {detail}")
