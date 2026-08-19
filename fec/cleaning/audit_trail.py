"""Record cleaning changes."""
from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd

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
    values = series.astype(object).where(series.notna(), "").astype(str)
    return values.where(~values.isin(_BLANK_TEXT), "")


def _keys(df: pd.DataFrame):
    return df["sub_id"].to_numpy() if "sub_id" in df.columns else df.index.to_numpy()


def _by_key(df: pd.DataFrame, field: str) -> pd.Series:
    return pd.Series(df[field].to_numpy(copy=True), index=_keys(df))


def _per_row(value, df: pd.DataFrame, keys) -> list:
    if value is None or isinstance(value, str):
        return [value] * len(keys)
    series = pd.Series(value(df).to_numpy(), index=_keys(df)).reindex(keys)
    return [None if pd.isna(item) else str(item) for item in series.to_numpy()]


class AuditTrail:
    """Track changes by sub_id and field."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._raw: dict[str, pd.Series] = {}
        self._expected: dict[str, dict] = {}

    def start(self, df: pd.DataFrame) -> None:
        unique = df.drop_duplicates("sub_id", keep="first") if "sub_id" in df.columns else df
        for field in AUDITED_FIELDS:
            if field in unique.columns:
                self._raw[field] = _by_key(unique, field)

    def run(
        self,
        df: pd.DataFrame,
        transform: Callable,
        step: str,
        reason,
        fields: Sequence[str],
        source=None,
    ):
        before = {
            field: _by_key(df, field) if field in df.columns else None
            for field in fields
        }
        result = transform(df)
        after = result[0] if isinstance(result, tuple) else result
        if not isinstance(after, pd.DataFrame):
            after = df
        self._record(after, before, step, reason, source)
        return result

    def _record(self, df, before, step, reason, source) -> None:
        for field, old in before.items():
            if field not in df.columns:
                continue

            new = _by_key(df, field)
            if old is None:
                self._raw.setdefault(field, new)
                continue
            self._raw.setdefault(field, old)
            old = old.reindex(new.index)
            old_text = _text(old)
            new_text = _text(new)
            changed = old_text.to_numpy() != new_text.to_numpy()
            if not changed.any():
                continue

            keys = new_text.index[changed]
            reasons = _per_row(reason, df, keys)
            sources = _per_row(source, df, keys)
            raw = self._raw.get(field)
            expected_values = self._expected.setdefault(field, {})

            for key, old_value, new_value, row_reason, row_source in zip(
                keys, old_text[changed], new_text[changed], reasons, sources,
            ):
                expected = expected_values.get(key)
                if expected is None and raw is not None:
                    expected = _raw_text(raw, key)
                if expected is not None and expected != old_value:
                    self.records.append(
                        _row(key, field, expected, old_value, UNTRACKED_STEP, UNTRACKED_REASON)
                    )
                self.records.append(
                    _row(key, field, old_value, new_value, step, row_reason, row_source)
                )
                expected_values[key] = new_value

    def finish(self, df: pd.DataFrame) -> int:
        """Record changes made outside tracked steps."""
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
            changed = expected.to_numpy() != final_text.to_numpy()
            for key in final.index[changed]:
                self.records.append(
                    _row(
                        key,
                        field,
                        expected[key],
                        final_text[key],
                        UNTRACKED_STEP,
                        UNTRACKED_REASON,
                    )
                )
                unexplained += 1
        return unexplained

    def net_records(self) -> list[dict]:
        """Remove changes undone by a later step."""
        chains: dict[tuple, list[dict]] = {}
        for record in self.records:
            chain = chains.setdefault((record["sub_id"], record["field"]), [])
            undone = next(
                (index for index, earlier in enumerate(chain) if earlier["before"] == record["after"]),
                None,
            )
            if undone is None:
                chain.append(record)
            else:
                del chain[undone:]
        keep = {id(record) for chain in chains.values() for record in chain}
        return [record for record in self.records if id(record) in keep]


def summarize(records: list[dict]) -> dict:
    """Count changes by step and reason."""
    steps: dict[str, dict] = {}
    for record in records:
        entry = steps.setdefault(record["step"], {"changes": 0, "reasons": {}})
        entry["changes"] += 1
        reason = record["reason"] or ""
        entry["reasons"][reason] = entry["reasons"].get(reason, 0) + 1
    return steps


def _row(key, field, before, after, step, reason, source=None) -> dict:
    return {
        "sub_id": str(key),
        "field": field,
        "before": before,
        "after": after,
        "step": step,
        "reason": reason,
        "source": source,
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
