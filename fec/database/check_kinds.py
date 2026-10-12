"""Kinds of read-only database check."""
from __future__ import annotations

from textwrap import dedent

CRIT, WARN = "crit", "warn"


class Check:
    __slots__ = ("name", "severity", "fn")

    # store the name, severity and query function
    def __init__(self, name, severity, fn):
        self.name, self.severity, self.fn = name, severity, fn

    # run the check, returning pass and detail
    def run(self, cur) -> tuple[bool, str]:
        try:
            return self.fn(cur)
        except Exception as error:  # a broken query is itself a failure
            return False, f"error: {type(error).__name__}: {str(error).strip()[:200]}"


# dedent SQL so indentation is not sent
def clean_sql(query: str) -> str:
    return dedent(query).strip()


# run a query, return its single value
def fetch_scalar(cur, query):
    cur.execute(query)
    return cur.fetchone()[0]


# check passes when the count is zero
def zero_check(name, query, label, severity=CRIT):
    # count violations and format the detail
    def fn(cur, query=query, label=label):
        count = fetch_scalar(cur, query)
        detail = f"0 {label}" if count == 0 else f"{count:,} {label}"
        return count == 0, detail

    return Check(name, severity, fn)


# check passes when query returns no names
def no_names_check(name, query, label, severity=CRIT):
    # collect names and format the detail
    def fn(cur, query=query, label=label):
        cur.execute(query)
        names = [row[0] for row in cur.fetchall()]
        return not names, f"{len(names)} {label}" + (f": {', '.join(names)}" if names else "")

    return Check(name, severity, fn)


# check passes when two scalar queries agree
def equal_check(name, query_a, query_b, label, severity=CRIT):
    # compare both values and format the detail
    def fn(cur, query_a=query_a, query_b=query_b, label=label):
        value_a = fetch_scalar(cur, query_a)
        value_b = fetch_scalar(cur, query_b)
        operator = "=" if value_a == value_b else "!="
        return value_a == value_b, f"{label}: {value_a:,} {operator} {value_b:,}"

    return Check(name, severity, fn)


# check passes when the count is positive
def positive_check(name, query, label, severity=CRIT):
    # compute the count and format the detail
    def fn(cur, query=query, label=label):
        count = fetch_scalar(cur, query)
        return count > 0, f"{count:,} {label}"

    return Check(name, severity, fn)


# check passes when value is within maximum
def at_most_check(name, query, maximum, label, severity=CRIT):
    # fetch the value and format the detail
    def fn(cur, query=query, maximum=maximum, label=label):
        value = fetch_scalar(cur, query)
        return value <= maximum, f"{label}={value:,}"

    return Check(name, severity, fn)
