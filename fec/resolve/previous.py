"""Resolve previous employers for retired receipts."""
import pandas as pd

from fec.cleaning.rules.manual import CLEAR
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.employer_names import expand_abbreviation_text
from fec.cleaning.work.previous import normalize_previous_employer_value
from fec.cleaning.work.status import is_real_employer
from fec.config.tables import read_rows
from fec.env import FILING_PREVIOUS_EMPLOYERS_CSV, PREVIOUS_EMPLOYERS_CSV
from fec.log import get_logger

logger = get_logger(__name__)


def _usable(value) -> str:
    cleaned = normalize_previous_employer_value(value)
    return cleaned if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned) else ""


# The chosen name in previous-employer form.
def _clean(value: str) -> str:
    if str(value).strip().upper() == CLEAR:
        return ""
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
            found[donor] = value
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
    sub_ids = df["sub_id"].astype(str)
    donors = dict(zip(sub_ids, df["donor_key"]))
    filing_rules = {row["sub_id"]: _clean(row["previous_employer"])
                    for row in read_rows(FILING_PREVIOUS_EMPLOYERS_CSV)}
    filing_answers = sub_ids.map(filing_rules)
    working = df[df["entity_type"].eq("INDIVIDUAL") & status.isin({"active", "self_employed"})]
    # Only work filed at an address the donor also used when retired.
    homes = set(zip(df.loc[retired, "donor_key"], _address(df[retired])))
    same_home = [key in homes for key in zip(working["donor_key"], _address(working))]
    working = working.loc[same_home]
    found = {
        **_latest(working, "contributor_employer"),
        # A filing-specific answer must stay local even when resolve runs twice.
        **_latest(df[retired & filing_answers.isna()], "previous_employer"),
        **_reviewed(donors),
    }
    result = pd.Series("", index=df.index, dtype=object)
    found = {donor: _clean(name) for donor, name in found.items()}
    result[retired] = df.loc[retired, "donor_key"].map(found).fillna("")
    # Unconfirmed donors keep only their own filing's answer.
    held = retired & df["identity_status"].ne("confirmed")
    result[held] = df.loc[held, "previous_employer"].fillna("")
    return result.mask(retired & ~held & filing_answers.notna(), filing_answers)
