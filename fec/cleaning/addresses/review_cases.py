"""Address review cases: the open review items grouped into one case per question.

address_manual_review.csv lists one row per item; many items ask the same
question (one donor, one ZIP, one reason). Each case here carries what a
reviewer needs to answer it without opening other files:

* every address form in the case and how the forms differ, part by part
  (house number, direction, street type, unit, PO box) or by spelling only;
* every filing in the full file that uses one of those forms, with its
  address as filed (full, original ZIP) and as cleaned;
* the steps that changed those filings and the reasons they give;
* the evidence: a verified source (a manual override, a postal check) or
  none. That the same donor filed a form elsewhere is shown but is not proof.
* a decision: ``pending`` unless every place-moving change is backed by a
  source (``supported_correction``). A reviewer writes ``keep_original``
  when the filing as filed is right.

Priority: ``high`` when forms or changes differ in a part that may move the
place, ``medium`` for spelling only, ``low`` for care-of, entity and
descriptive items with no such change. A PO box is a geocoding limit (the
point lands on the ZIP), not an address error, and is noted as such.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations

import pandas as pd

from fec.cleaning.addresses.compare import (
    parse_street,
    street_and_unit,
    street_changes,
)

REVIEW_CASES_CSV = "address_review_cases.csv"
S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"
ADDRESS_COLUMNS = (S1, S2, CITY, STATE, ZIP)
PENDING, SUPPORTED, KEEP_ORIGINAL = "pending", "supported_correction", "keep_original"
SPELLING_ONLY, FORMATTING_ONLY = "spelling only", "formatting only"
PO_BOX_NOTE = "PO box: the geocoder places it at the ZIP point; a geocoding limit, not an address error"

# steps that only rewrite the row's own text (no other filing involved)
FORMAT_STEPS = frozenset({
    "streets_normalize", "zips_normalize", "cities_normalize", "streets_safe_fixes", "streets_unify_units",
    "streets_unify_spacing", "streets_unify_spelling", "address_review", "donor_canonical_units",
    "donor_pobox_typos", "safety_fix_garbage_city_names",
})
# steps whose every change names a checked source
SOURCED_STEPS = frozenset({"address_verified_rules", "manual_overrides"})

CASE_COLUMNS = (
    "case_id", "priority", "donor_key", "contributor_name", "review_reason", "zip5",
    "address_forms", "form_differences", "filings", "sub_ids",
    "filed_addresses", "cleaned_addresses", "changes_filed_to_cleaned",
    "changed_by", "evidence", "note", "decision", "decision_basis",
)


# cell text with blanks as ""
def _text(series: pd.Series) -> pd.Series:
    return series.astype(object).where(series.notna(), "").astype(str).str.strip()


# the first five digits of a ZIP
def _zip5(series: pd.Series) -> pd.Series:
    return _text(series).str.replace(r"\D", "", regex=True).str[:5]


# the person a row belongs to: donor_key, else the name
def _person(frame: pd.DataFrame) -> pd.Series:
    key = _text(frame["donor_key"]) if "donor_key" in frame.columns else pd.Series("", index=frame.index)
    return key.where(key.ne(""), _text(frame["contributor_name"]))


# one address as a line: "12 MAIN ST | APT 5 | ..."
def _line(frame: pd.DataFrame) -> pd.Series:
    cells = [_text(frame[column]) if column in frame.columns else "" for column in ADDRESS_COLUMNS]
    return cells[0].str.cat(cells[1:], sep=" | ")


# distinct values with counts, most common first: "A x3; B x1"
def _counted(values) -> str:
    return "; ".join(f"{value} x{count}" for value, count in Counter(values).most_common())


# how address forms differ: place-moving parts, else spelling or formatting
def _form_differences(forms: list[tuple[str, str]]) -> tuple[list[str], str]:
    kinds = set()
    for (a1, a2), (b1, b2) in combinations(forms, 2):
        kinds.update(street_changes(a1, a2, b1, b2))
    if kinds or len(forms) < 2:
        return sorted(kinds), ", ".join(sorted(kinds))
    same = len({street_and_unit(s1, s2) for s1, s2 in forms}) == 1
    return [], FORMATTING_ONLY if same else SPELLING_ONLY


# a PO box appears among the forms
def _has_po_box(forms) -> bool:
    return any(parse_street(street_and_unit(s1, s2)[0]).po_box for s1, s2 in forms)


# trail rows per sub_id for the address fields
def _address_changes(trail_records: list[dict]) -> pd.DataFrame:
    columns = ["sub_id", "field", "step", "reason", "source"]
    changes = pd.DataFrame(trail_records).reindex(columns=columns)
    changes = changes.assign(**{column: _text(changes[column]) for column in columns})
    return changes[changes["field"].isin(ADDRESS_COLUMNS)]


# the same place however written: (street, unit, ZIP5)
def _place_keys(frame: pd.DataFrame) -> pd.Series:
    streets = [street_and_unit(s1, s2) for s1, s2 in zip(_text(frame[S1]), _text(frame[S2]))]
    return pd.Series([(*street, zip5) for street, zip5 in zip(streets, _zip5(frame[ZIP]))], index=frame.index)


# the decision a case starts with, and why
def _decision(moves: list[str], changes: pd.DataFrame) -> tuple[str, str]:
    guessed = changes[~changes["step"].isin(FORMAT_STEPS)]
    unsourced = guessed[guessed["source"].eq("") | ~guessed["step"].isin(SOURCED_STEPS)]
    if moves and not guessed.empty and unsourced.empty:
        return SUPPORTED, "every place-moving change names a checked source"
    if moves and not unsourced.empty:
        return PENDING, "changed from other filings of the same donor, which is not proof"
    return PENDING, "needs a reviewer"


# the evidence a case's changes name
def _evidence(changes: pd.DataFrame, cleaned: pd.DataFrame, person_filed: Counter) -> str:
    sources = changes.loc[changes["source"].ne(""), "source"]
    parts = [f"source: {_counted(sources)}"] if len(sources) else []
    history = [
        f"{line}: filed so x{person_filed[key]}"
        for line, key in dict.fromkeys(zip(_line(cleaned), _place_keys(cleaned)))
    ]
    if history:
        parts.append("same donor's filings (not proof): " + "; ".join(history))
    return " || ".join(parts) or "none"


# priority from place-moving parts and how forms differ
def _priority(moves: list[str], differences: str) -> str:
    if moves:
        return "high"
    return "medium" if differences == SPELLING_ONLY else "low"


# group the open review items into cases with their evidence
def build_review_cases(
    review_df: pd.DataFrame,
    df: pd.DataFrame,
    filed: pd.DataFrame,
    trail_records: list[dict],
) -> pd.DataFrame:
    """One row per (person, review reason, ZIP5) among the open review items.

    ``df`` is the final frame, ``filed`` the address columns as filed (by
    sub_id), ``trail_records`` the audit trail's net records. Never edits data.
    """
    if review_df is None or review_df.empty or "status" not in review_df.columns:
        return pd.DataFrame(columns=CASE_COLUMNS)
    items = review_df[review_df["status"].eq("open")].copy()
    if items.empty:
        return pd.DataFrame(columns=CASE_COLUMNS)
    items["_person"] = _person(items)
    items["_zip5"] = _zip5(items[ZIP])

    rows = df.assign(_sid=df["sub_id"].astype(str), _person=_person(df), _zip5=_zip5(df[ZIP]))
    rows = rows[rows["_person"].isin(set(items["_person"]))]
    filed = filed.assign(_sid=filed["sub_id"].astype(str)).drop_duplicates("_sid").set_index("_sid")
    rows_filed = filed.reindex(rows["_sid"]).set_axis(rows.index).fillna("")
    person_filed = _place_keys(rows_filed).groupby(rows["_person"]).agg(Counter)
    changes = _address_changes(trail_records)
    changes = changes[changes["sub_id"].isin(set(rows["_sid"]))]

    cases = []
    for (person, reason, zip5), group in items.groupby(["_person", "review_reason", "_zip5"], sort=False):
        forms = list(dict.fromkeys(zip(_text(group[S1]), _text(group[S2]))))
        mine = rows[rows["_person"].eq(person) & rows["_zip5"].eq(zip5)]
        mine = mine[pd.Series(list(zip(_text(mine[S1]), _text(mine[S2]))), index=mine.index).isin(forms)]
        sub_ids = list(dict.fromkeys(list(_text(group["sub_id"])) + list(mine["_sid"])))
        mine_filed = filed.reindex(sub_ids).fillna("")
        case_rows = rows.drop_duplicates("_sid").set_index("_sid").reindex(sub_ids).fillna("")
        moved = sorted({
            kind
            for sid in sub_ids
            for kind in street_changes(
                *(_text(mine_filed[column]).get(sid, "") for column in (S1, S2)),
                *(_text(case_rows[column]).get(sid, "") for column in (S1, S2)),
            )
        })
        kinds, differences = _form_differences(forms)
        case_changes = changes[changes["sub_id"].isin(sub_ids)]
        decision, basis = _decision(moved, case_changes)
        cases.append({
            "priority": _priority(moved + kinds, differences),
            "donor_key": _text(group["donor_key"]).iloc[0] if "donor_key" in group.columns else "",
            "contributor_name": _text(group["contributor_name"]).iloc[0],
            "review_reason": reason,
            "zip5": zip5,
            "address_forms": " || ".join(f"{s1} | {s2}" if s2 else s1 for s1, s2 in forms),
            "form_differences": differences,
            "filings": len(sub_ids),
            "sub_ids": ";".join(sub_ids),
            "filed_addresses": _counted(_line(mine_filed)),
            "cleaned_addresses": _counted(_line(case_rows)),
            "changes_filed_to_cleaned": ", ".join(moved),
            "changed_by": _counted(case_changes["step"] + ": " + case_changes["reason"].astype(str)),
            "evidence": _evidence(case_changes, case_rows, person_filed.get(person, Counter())),
            "note": PO_BOX_NOTE if _has_po_box(forms) else "",
            "decision": decision,
            "decision_basis": basis,
        })
    out = pd.DataFrame(cases, columns=[column for column in CASE_COLUMNS if column != "case_id"])
    order = out["priority"].map({"high": 0, "medium": 1, "low": 2})
    out = out.assign(_order=order).sort_values(["_order", "contributor_name", "zip5"], kind="stable")
    out = out.drop(columns="_order").reset_index(drop=True)
    out.insert(0, "case_id", [f"A{number:04d}" for number in range(1, len(out) + 1)])
    return out
