"""One previous employer per retired donor."""
import pandas as pd

from fec.cleaning.rules.manual import CLEAR
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.employer_names import expand_abbreviation_text
from fec.cleaning.work.previous import normalize_previous_employer_value
from fec.cleaning.work.status import is_real_employer
from fec.config.tables import read_rows
from fec.env import PREVIOUS_EMPLOYERS_CSV
from fec.log import get_logger

logger = get_logger(__name__)


def _usable(value) -> str:
    cleaned = normalize_previous_employer_value(value)
    return cleaned if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned) else ""


# The chosen name in previous-employer form.
def _clean(value: str) -> str:
    expanded = expand_abbreviation_text(pd.Series([_usable(value)])).iloc[0]
    return reviewed_employer_name(expanded) if expanded else ""


# Reviewed answers by donor; an empty answer clears.
def _reviewed(donors: dict) -> dict:
    found, missing = {}, []
    for row in read_rows(PREVIOUS_EMPLOYERS_CSV):
        value = (row.get("previous_employer") or "").strip()
        donor = donors.get(row["sub_id"].strip())
        if not donor:
            missing.append(row["sub_id"])
        elif value:
            found[donor] = "" if value.upper() == CLEAR else value
    if missing:
        logger.warning("WARNING: %s previous employer rules found no filing: %s", len(missing), ", ".join(missing[:5]))
    return found


def _address(rows: pd.DataFrame) -> pd.Series:
    zips = rows["contributor_zip"].fillna("").astype(str).str[:5]
    return rows["contributor_street_1"].fillna("").astype(str) + "|" + zips


# Each donor's latest usable filed name.
def _latest(rows: pd.DataFrame, column: str) -> dict:
    named = rows[rows[column].map(_usable).ne("")].sort_values("contribution_receipt_date")
    return named.groupby("donor_key")[column].last().to_dict()


def previous_employers(df: pd.DataFrame, status: pd.Series) -> pd.Series:
    """Reviewed rule, retired filing, then latest filed employer."""
    retired = status.eq("retired")
    donors = dict(zip(df["sub_id"].astype(str), df["donor_key"]))
    working = df[df["entity_type"].eq("INDIVIDUAL") & status.isin({"active", "self_employed"})]
    # Only work filed at an address the donor also used when retired.
    homes = set(zip(df.loc[retired, "donor_key"], _address(df[retired])))
    working = working[[key in homes for key in zip(working["donor_key"], _address(working))]]
    found = {
        **_latest(working, "contributor_employer"),
        **_latest(df[retired], "previous_employer"),
        **_reviewed(donors),
    }
    result = pd.Series("", index=df.index, dtype=object)
    found = {donor: _clean(name) for donor, name in found.items()}
    result[retired] = df.loc[retired, "donor_key"].map(found).fillna("")
    # Unconfirmed donors keep only their own filing's answer.
    held = retired & df["identity_status"].ne("confirmed")
    result[held] = df.loc[held, "previous_employer"].fillna("")
    return result
