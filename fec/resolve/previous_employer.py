"""The previous employer of a donor whose latest contribution says retired.

In order: a reviewed answer (data/rules/previous_employers.csv, for people
whose former work is known), the company the retired filing itself names
(RETIRED FROM ACME), else the company on the donor's latest earlier
contribution, to any committee, where they still filed an employer.
"""

import pandas as pd

from fec.cleaning.filing_corrections import CLEAR
from fec.cleaning.work.work_fields import companies_named, company_named
from fec.common.log import get_logger
from fec.common.settings import PREVIOUS_EMPLOYERS_CSV
from fec.vocabulary.tables import read_rows

logger = get_logger(__name__)


def _read_reviewed_previous_employers(donors: dict) -> dict:
    found, missing = {}, []
    for row in read_rows(PREVIOUS_EMPLOYERS_CSV):
        value = (row.get("previous_employer") or "").strip()
        donor = donors.get(row["sub_id"].strip())
        if not donor:
            missing.append(row["sub_id"])
        elif value:
            found[donor] = "" if value.upper() == CLEAR else company_named(value)
    if missing:
        logger.warning(
            "WARNING: %s previous employer rules found no filing: %s",
            len(missing),
            ", ".join(missing[:5]),
        )
    return found


def _find_latest_companies(rows: pd.DataFrame, column: str) -> dict:
    names = companies_named(rows[column])
    named = rows.assign(company=names)[names.ne("")].sort_values("contribution_receipt_date")
    return named.groupby("donor_key")["company"].last().to_dict()


def previous_employers(df: pd.DataFrame, status: pd.Series) -> pd.Series:
    """Return the previous employer on the retired filings of donors who are retired now."""
    people = df["entity_type"].eq("INDIVIDUAL")
    retired = people & status.eq("retired")
    latest = df[people].sort_values("contribution_receipt_date").groupby("donor_key").tail(1)
    retired_now = set(latest.loc[status[latest.index].eq("retired"), "donor_key"])
    donor_of_filing = dict(zip(df["sub_id"].astype(str), df["donor_key"]))
    found = {
        **_find_latest_companies(df[people & status.eq("active")], "contributor_employer"),
        **_find_latest_companies(df[retired], "previous_employer"),
        **_read_reviewed_previous_employers(donor_of_filing),
    }
    result = pd.Series("", index=df.index, dtype=object)
    target = retired & df["donor_key"].isin(retired_now)
    result[target] = df.loc[target, "donor_key"].map(found).fillna("")
    # A donor whose identity is not confirmed keeps only what their own filing names.
    held = retired & df["identity_status"].ne("confirmed")
    result[held] = companies_named(df.loc[held, "previous_employer"])
    return result
