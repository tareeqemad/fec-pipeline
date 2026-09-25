"""Shared read-only correctness checks for the loaded FEC database, in run order."""
from __future__ import annotations

from fec.database.access_checks import ACCESS_CHECKS
from fec.database.check_kinds import CRIT, WARN, Check
from fec.database.data_checks import CLEANING_CHECKS, VALUE_CHECKS
from fec.database.table_checks import INTEGRITY_CHECKS, MONEY_CHECKS
from fec.database.view_checks import VIEW_CHECKS

__all__ = ["CHECKS", "CRIT", "WARN", "Check"]

CHECKS: list[Check] = (
    INTEGRITY_CHECKS + MONEY_CHECKS + VIEW_CHECKS + CLEANING_CHECKS + VALUE_CHECKS
    + ACCESS_CHECKS
)
