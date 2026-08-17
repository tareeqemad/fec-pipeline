"""Record field changes with step and reason."""
from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

from fec.cleaning.audit_keys import format_key, street_key

SEMANTIC = "semantic"
FORMAT = "format"
UNTRACKED_STEP = "untracked"
UNTRACKED_REASON = "changed_outside_tracked_steps"

AUDITED_FIELDS = (
    "entity_type",
    "is_individual",
    "contributor_name",
    "contributor_first_name",
    "contributor_last_name",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "contributor_employer",
    "contributor_occupation",
    "occupation_category",
    "previous_employer",
)

ENTITY_FIELDS = ("entity_type", "is_individual")
NAME_FIELDS = ("contributor_name", "contributor_first_name", "contributor_last_name")
STREET_FIELDS = ("contributor_street_1", "contributor_street_2")
PLACE_FIELDS = ("contributor_city", "contributor_state", "contributor_zip")
ADDRESS_FIELDS = STREET_FIELDS + PLACE_FIELDS
WORK_FIELDS = ("contributor_employer", "contributor_occupation", "occupation_category")
EMPLOYMENT_FIELDS = WORK_FIELDS + ("previous_employer",)
PEOPLE_FIELDS = ENTITY_FIELDS + NAME_FIELDS + EMPLOYMENT_FIELDS

_BLANK_TEXT = frozenset({"", "nan", "None", "<NA>"})


def _text(series: pd.Series) -> pd.Series:
    """Text values; blank equals missing."""
    values = series.astype(object).where(series.notna(), "").astype(str)
    return values.where(~values.isin(_BLANK_TEXT), "")


def _keys(df: pd.DataFrame):
    """sub_id when present, else the index."""
    return df["sub_id"].to_numpy() if "sub_id" in df.columns else df.index.to_numpy()


def _by_key(df: pd.DataFrame, field: str) -> pd.Series:
    return pd.Series(df[field].to_numpy(copy=True), index=_keys(df))


def _individuals(df: pd.DataFrame) -> pd.Series | None:
    if "entity_type" in df.columns:
        flags = df["entity_type"].eq("INDIVIDUAL")
    elif "is_individual" in df.columns:
        flags = df["is_individual"].astype(str).str.strip().str.lower().isin(("t", "true", "1", "yes"))
    else:
        return None
    return pd.Series(flags.to_numpy(), index=_keys(df))


def _per_row(value, df: pd.DataFrame, keys) -> list:
    """Per-row value: string or callable(df)."""
    if value is None or isinstance(value, str):
        return [value] * len(keys)
    series = pd.Series(value(df).to_numpy(), index=_keys(df)).reindex(keys)
    return [None if pd.isna(item) else str(item) for item in series.to_numpy()]


class AuditTrail:
    """Change log keyed by (sub_id, field)."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._raw: dict[str, pd.Series] = {}
        self._expected: dict[str, dict] = {}

    def start(self, df: pd.DataFrame) -> None:
        """Remember the raw values."""
        unique = df.drop_duplicates("sub_id", keep="first") if "sub_id" in df.columns else df
        for field in AUDITED_FIELDS:
            if field in unique.columns:
                self._raw[field] = _by_key(unique, field)

    def run(self, df, transform: Callable, step: str, reason, fields: Sequence[str], evidence=None):
        """Run a step and record its changes."""
        wanted = list(fields)
        if any(field in STREET_FIELDS for field in wanted):
            # street_1 and street_2 are compared together
            wanted += [field for field in STREET_FIELDS if field not in wanted]
        before = {field: _by_key(df, field) if field in df.columns else None for field in wanted}
        result = transform(df)
        after = result[0] if isinstance(result, tuple) else result
        if not isinstance(after, pd.DataFrame):
            after = df
        self._record(after, before, step, reason, evidence)
        return result

    def _record(self, df, before: dict, step: str, reason, evidence) -> None:
        diffs = {}
        for field, old in before.items():
            if field not in df.columns:
                continue
            new = _by_key(df, field)
            if old is None:
                # new column: first values are baseline
                self._raw.setdefault(field, new)
                continue
            self._raw.setdefault(field, old)
            if not old.index.equals(new.index):
                old = old.reindex(new.index)
            if old.equals(new):
                continue
            old_text, new_text = _text(old), _text(new)
            changed = old_text.to_numpy() != new_text.to_numpy()
            if changed.any():
                diffs[field] = (old_text, new_text, changed)
        if not diffs:
            return

        individuals = _individuals(df)
        for field, (old_text, new_text, changed) in diffs.items():
            keys = new_text.index[changed]
            kinds = _kinds(field, diffs, changed, individuals)
            reasons = _per_row(reason, df, keys)
            evidences = _per_row(evidence, df, keys)
            raw = self._raw.get(field)
            expected_values = self._expected.setdefault(field, {})
            for key, old, new, kind, row_reason, row_evidence in zip(
                keys, old_text[changed], new_text[changed], kinds, reasons, evidences,
            ):
                expected = expected_values.get(key)
                if expected is None and raw is not None:
                    expected = _raw_text(raw, key)
                if expected is not None and expected != old:
                    self.records.append(_row(key, field, expected, old, UNTRACKED_STEP, UNTRACKED_REASON, SEMANTIC))
                self.records.append(_row(key, field, old, new, step, row_reason, kind, row_evidence))
                expected_values[key] = new

    def finish(self, df: pd.DataFrame) -> int:
        """Flag values changed outside tracked steps."""
        unexplained = 0
        for field, raw in self._raw.items():
            if field not in df.columns:
                continue
            final = _by_key(df, field)
            final = final[final.index.isin(raw.index)]
            expected = _text(raw.reindex(final.index))
            known = pd.Series(self._expected.get(field, {}), dtype=object)
            known = known[known.index.isin(expected.index)]
            expected.loc[known.index] = known.to_numpy()
            final_text = _text(final)
            for key in final.index[expected.to_numpy() != final_text.to_numpy()]:
                self.records.append(_row(key, field, expected[key], final_text[key], UNTRACKED_STEP, UNTRACKED_REASON, SEMANTIC))
                unexplained += 1
        return unexplained

    def net_records(self) -> list[dict]:
        """Records not undone by a later step."""
        chains: dict[tuple, list[dict]] = {}
        for record in self.records:
            chain = chains.setdefault((record["sub_id"], record["field"]), [])
            undone = next((i for i, earlier in enumerate(chain) if earlier["before"] == record["after"]), None)
            if undone is None:
                chain.append(record)
            else:
                del chain[undone:]
        keep = {id(record) for chain in chains.values() for record in chain}
        return [record for record in self.records if id(record) in keep]

    def untracked_count(self) -> int:
        return sum(1 for r in self.records if r["step"] == UNTRACKED_STEP)


def summarize(records: list[dict]) -> dict:
    """Counts per step and the semantic reasons."""
    steps: dict[str, dict] = {}
    for record in records:
        entry = steps.setdefault(record["step"], {"semantic": 0, "format": 0, "reasons": {}})
        entry[record["kind"]] += 1
        if record["kind"] == SEMANTIC:
            reason = record["reason"] or ""
            entry["reasons"][reason] = entry["reasons"].get(reason, 0) + 1
    return steps


def _kinds(field, diffs, changed, individuals):
    """semantic or format per changed row."""
    old_text, new_text, _ = diffs[field]
    if field in STREET_FIELDS:
        old_key, new_key = _street_pair(diffs, changed)
    else:
        old_key = format_key(field, old_text[changed], individuals)
        new_key = format_key(field, new_text[changed], individuals)
    same = old_key.to_numpy() == new_key.to_numpy()
    if field == "occupation_category":
        # filling or clearing a computed field
        same |= (old_text[changed].to_numpy() == "") | (new_text[changed].to_numpy() == "")
    if field in ("contributor_first_name", "contributor_last_name") and individuals is not None:
        # committees have no person name
        same |= ~individuals.reindex(new_text.index[changed]).fillna(False).to_numpy().astype(bool)
    return np.where(same, FORMAT, SEMANTIC)


def _street_pair(diffs, changed):
    """Street keys of street_1 and street_2 joined."""
    def joined(side):
        parts = [diffs[f][side][changed] for f in STREET_FIELDS if f in diffs]
        return parts[0].str.cat(parts[1:], sep=" ").map(street_key)
    return joined(0), joined(1)


def _row(key, field, before, after, step, reason, kind, evidence=None) -> dict:
    return {
        "sub_id": str(key), "field": field, "before": before, "after": after,
        "step": step, "reason": reason, "evidence": evidence, "kind": kind,
    }


def _raw_text(raw: pd.Series, key) -> str | None:
    if key not in raw.index:
        return None
    value = raw.at[key]
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    if pd.isna(value):
        return ""
    value = str(value)
    return "" if value in _BLANK_TEXT else value
