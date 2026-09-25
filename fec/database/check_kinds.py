"""The kinds of read-only database check: zero, none, equal, positive, at most."""
from __future__ import annotations

from textwrap import dedent

CRIT, WARN = "crit", "warn"


class Check:
    __slots__ = ("name", "severity", "fn")

    # store the check's name, severity and query function
    def __init__(self, name, severity, fn):
        self.name, self.severity, self.fn = name, severity, fn

    # run the check, turning any exception into a failure
    def run(self, cur) -> tuple[bool, str]:
        try:
            return self.fn(cur)
        except Exception as error:  # a broken query is itself a failure
            return False, f"error: {type(error).__name__}: {str(error).strip()[:200]}"


# dedent and strip a multi-line SQL query for readability
def _sql(query: str) -> str:
    """Keep multi-line SQL readable without sending its indentation."""
    return dedent(query).strip()


# run a query and return its single scalar result
def _scalar(cur, query):
    cur.execute(query)
    return cur.fetchone()[0]


# check passes when the violation count is zero
def _zero(name, query, label, severity=CRIT):
    """Pass when the violation count is zero."""

    # compute the violation count and format the detail message
    def fn(cur, query=query, label=label):
        count = _scalar(cur, query)
        detail = f"0 {label}" if count == 0 else f"{count:,} {label}"
        return count == 0, detail

    return Check(name, severity, fn)


# check passes when the query returns no names
def _none(name, query, label, severity=CRIT):
    """Pass when the query returns no names; the detail lists them."""

    # collect matching names and format the detail message
    def fn(cur, query=query, label=label):
        cur.execute(query)
        names = [row[0] for row in cur.fetchall()]
        return not names, f"{len(names)} {label}" + (f": {', '.join(names)}" if names else "")

    return Check(name, severity, fn)


# build a check that passes when two scalar queries agree
def _equal(name, query_a, query_b, label, severity=CRIT):
    """Pass when two scalar queries return the same value."""

    # compare the two scalar values and format the detail message
    def fn(cur, query_a=query_a, query_b=query_b, label=label):
        value_a = _scalar(cur, query_a)
        value_b = _scalar(cur, query_b)
        operator = "=" if value_a == value_b else "!="
        return value_a == value_b, f"{label}: {value_a:,} {operator} {value_b:,}"

    return Check(name, severity, fn)


# check passes when the returned count is positive
def _positive(name, query, label, severity=CRIT):
    """Pass when the returned count is greater than zero."""

    # compute the count and format the detail message
    def fn(cur, query=query, label=label):
        count = _scalar(cur, query)
        return count > 0, f"{count:,} {label}"

    return Check(name, severity, fn)


# check passes when a scalar stays under maximum
def _at_most(name, query, maximum, label, severity=CRIT):
    """Pass when a scalar is at most the configured maximum."""

    # compute the scalar and format the detail message
    def fn(cur, query=query, maximum=maximum, label=label):
        value = _scalar(cur, query)
        return value <= maximum, f"{label}={value:,}"

    return Check(name, severity, fn)
