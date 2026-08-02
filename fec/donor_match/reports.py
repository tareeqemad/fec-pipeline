"""Donor-dedup review report: candidate same-surname pairs for human triage."""

from itertools import combinations
from pathlib import Path

import pandas as pd

from fec.log import get_logger

from .constants import NICKNAME_MAP, _is_blocked_merge

logger = get_logger(__name__)


def build_donor_dedup_review(df: pd.DataFrame, out_dir) -> int:
    """Write data/donor_dedup_review.csv of likely-same-person donor pairs (shared ZIP + surname, related first names) for human review; never merges; returns pairs written."""
    if not out_dir:
        return 0
    need = {"donor_key", "entity_type", "contributor_last_name",
            "contributor_first_name", "contributor_city",
            "contributor_state", "contributor_zip"}
    if not need <= set(df.columns):
        return 0
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    if ind.empty:
        return 0
    amt = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").fillna(0)
    work = df.assign(_amt=amt).loc[ind.index]

    def _first_str(s):
        s = s.dropna().astype(str)
        s = s[s.str.strip() != ""]
        return s.iloc[0] if len(s) else ""

    g = work.groupby("donor_key")
    summ = pd.DataFrame({
        "last":   g["contributor_last_name"].agg(_first_str).str.upper(),
        "first":  g["contributor_first_name"].agg(_first_str).str.upper(),
        "city":   g["contributor_city"].agg(_first_str),
        "state":  g["contributor_state"].agg(_first_str),
        "n":      g.size(),
        "amount": g["_amt"].sum(),
    })

    # blocked by (ZIP, surname); a donor with two ZIPs appears in both blocks
    zp = ind[["donor_key", "contributor_zip"]].dropna()
    zp = zp[zp["contributor_zip"].astype(str).str.strip() != ""].drop_duplicates()
    zp = zp.join(summ["last"], on="donor_key")

    def _root(first):
        f0 = first.split()[0] if first else ""
        return NICKNAME_MAP.get(f0, f0)

    def _relation(fa, fb):
        a0 = fa.split()[0] if fa else ""
        b0 = fb.split()[0] if fb else ""
        if not a0 or not b0:
            return ""
        if a0 == b0:
            return "same first name (different city?)"
        if _root(fa) == _root(fb):
            return f"nickname {a0}/{b0}"
        short, long = sorted((a0, b0), key=len)
        if long.startswith(short) and short != long:
            return f"initial/prefix {short}->{long}"
        return ""

    seen, out = set(), []
    for (z, last), grp in zp.groupby(["contributor_zip", "last"]):
        if not last:
            continue
        keys = sorted(grp["donor_key"].unique())
        if len(keys) < 2:
            continue
        for ka, kb in combinations(keys, 2):
            if (ka, kb) in seen:
                continue
            seen.add((ka, kb))
            fa, fb = summ.at[ka, "first"], summ.at[kb, "first"]
            reason = _relation(fa, fb)
            if not reason:
                continue
            if _is_blocked_merge(f"{fa} {last}", f"{fb} {last}"):
                continue
            out.append({
                "reason": reason, "zip": z, "last_name": last,
                "donor_key_a": ka, "first_a": fa,
                "city_a": summ.at[ka, "city"], "state_a": summ.at[ka, "state"],
                "donations_a": int(summ.at[ka, "n"]), "amount_a": round(float(summ.at[ka, "amount"]), 2),
                "donor_key_b": kb, "first_b": fb,
                "city_b": summ.at[kb, "city"], "state_b": summ.at[kb, "state"],
                "donations_b": int(summ.at[kb, "n"]), "amount_b": round(float(summ.at[kb, "amount"]), 2),
                "combined_amount": round(float(summ.at[ka, "amount"] + summ.at[kb, "amount"]), 2),
            })
    if not out:
        return 0
    out.sort(key=lambda r: -r["combined_amount"])
    path = Path(out_dir) / "donor_dedup_review.csv"
    pd.DataFrame(out).to_csv(path, index=False, na_rep="")
    logger.info(f"  Donor-dedup review -> {path} ({len(out):,} candidate pairs)")
    return len(out)
