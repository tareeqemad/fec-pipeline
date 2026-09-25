"""Read-only health check of the live fec_db: the shared query_checks.CHECKS registry plus live-only extras; exits non-zero on any CRITICAL failure."""
from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation

import pandas as pd
import psycopg2

from fec.database.loader import connect
from fec.database.query_checks import CHECKS
from fec.database.query_checks import CRIT as QC_CRIT
from fec.env import CLEANED_CSV

CRIT, WARN, OK = "FAIL", "WARN", "ok"

# views that build mv_donor_profile, the matview the web app reads
REQUIRED_VIEWS = [
    "v_donor_profile", "v_donor_stats",
    "v_donor_current_address", "v_donor_current_employment",
]
REQUIRED_MATVIEWS = ["mv_donor_profile"]


# run a query and return its single scalar result
def _scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


# check required views, run shared registry, add live-only extras
def run_checks(cur) -> list[tuple[str, str, str]]:
    """Return [(severity, name, detail)]: view existence first (so a missing view is reported by name, not as N query errors), then the shared registry, then live-only extras."""
    out = []

    # required views/matviews exist (critical)
    cur.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    views = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname='public'")
    mviews = {row[0] for row in cur.fetchall()}
    missing = ([view for view in REQUIRED_VIEWS if view not in views]
               + [view for view in REQUIRED_MATVIEWS if view not in mviews])
    out.append((OK if not missing else CRIT, "required_views",
                "all present" if not missing else f"MISSING: {', '.join(missing)}"))

    # the shared registry
    for check in CHECKS:
        ok, detail = check.run(cur)
        severity = OK if ok else (CRIT if check.severity == QC_CRIT else WARN)
        out.append((severity, check.name, detail))

    # live-DB-only extras

    # geocode coverage: a threshold on a percentage, not a zero/nonzero rule
    try:
        total = _scalar(cur, "SELECT count(*) FROM addresses")
        geocoded = _scalar(cur, "SELECT count(*) FROM addresses WHERE latitude IS NOT NULL")
        percent = (geocoded / total * 100) if total else 100
        out.append((OK if percent >= 80 else WARN, "geocode_coverage",
                    f"{geocoded:,}/{total:,} ({percent:.0f}%)"))
    except psycopg2.Error as error:
        out.append((WARN, "geocode_coverage", f"error: {str(error).strip()}"))

    # matview freshness: a "was REFRESH run at all" signal on top of the registry's sync checks
    try:
        n_donors = _scalar(cur, "SELECT count(*) FROM donors")
        n_matview = _scalar(cur, "SELECT count(*) FROM mv_donor_profile")
        out.append((OK if n_matview == n_donors else WARN, "mv_donor_profile_fresh",
                    f"{n_matview:,} rows vs {n_donors:,} donors"
                    + ("" if n_matview == n_donors else " - REFRESH needed")))
    except psycopg2.Error as error:
        out.append((WARN, "mv_donor_profile_fresh", f"error: {str(error).strip()}"))

    # informational only; the registry separately asserts no 'NULL' surname was lost
    try:
        n_null = _scalar(cur, "SELECT count(*) FROM donors WHERE last_name='NULL'")
        out.append((OK, "null_surname_preserved", f"{n_null:,} real 'NULL' surname(s) kept"))
    except psycopg2.Error as error:
        out.append((WARN, "null_surname_preserved", f"error: {str(error).strip()}"))

    # DB total vs the neighbouring cleaned CSV (the registry can't see files);
    # a mismatch means the DB is not loaded from this CSV
    out.append(_csv_total_check(cur))
    # every filing's employment row carries the status that filing reported
    out.append(_csv_employment_status_check(cur))

    return out


# count INDIVIDUAL filings missing or differing from the DB
def _employment_status_mismatches(csv_rows, db_status: dict[int, str | None]) -> tuple[int, int]:
    """(missing, differing) INDIVIDUAL filings: csv_rows yields (sub_id, entity_type, employer_status) strings."""
    missing = differing = 0
    for sub_id, entity_type, status in csv_rows:
        if entity_type.strip() != "INDIVIDUAL":
            continue
        sub_id = int(sub_id)
        if sub_id not in db_status:
            missing += 1
        elif (db_status[sub_id] or "") != status.strip():
            differing += 1
    return missing, differing


# verify each filing's DB employment status matches the CSV
def _csv_employment_status_check(cur) -> tuple[str, str, str]:
    """Each INDIVIDUAL filing's donor_employments.employer_status equals its CSV employer_status (the check the (donor, employer, occupation) key used to fail); OK-skips when the CSV is absent."""
    name = "employment_status_vs_csv"
    try:
        if not CLEANED_CSV.exists():
            return (OK, name, "cleaned CSV not present - skipped")
        csv = pd.read_csv(CLEANED_CSV, usecols=["sub_id", "entity_type", "employer_status"],
                          dtype=str, keep_default_na=False)
        cur.execute("""
            SELECT c.sub_id, e.employer_status
            FROM contributions c
            JOIN donor_employments e ON e.donor_employment_id = c.donor_employment_id
        """)
        db_status = {int(sub_id): status for sub_id, status in cur.fetchall()}
        missing, differing = _employment_status_mismatches(
            csv[["sub_id", "entity_type", "employer_status"]].itertuples(index=False, name=None),
            db_status,
        )
        ok = missing == 0 and differing == 0
        detail = ("every filing keeps its status" if ok else
                  f"{differing:,} filing(s) with another status, {missing:,} without an employment")
        return (OK if ok else CRIT, name, detail)
    except (psycopg2.Error, OSError, KeyError, ValueError) as error:
        return (CRIT, name, f"error: {str(error).strip()}")


# compare total contribution amount between DB and CSV
def _csv_total_check(cur) -> tuple[str, str, str]:
    """Compare sum(contributions.amount) to the CSV with exact Decimals (sub-cent tolerance absorbs NUMERIC(15,2) rounding); OK-skips when the CSV is absent."""
    try:
        if not CLEANED_CSV.exists():
            return (OK, "total_amount_vs_csv", "cleaned CSV not present - skipped")
        amounts = pd.read_csv(CLEANED_CSV, usecols=["contribution_receipt_amount"],
                              dtype=str, keep_default_na=False)["contribution_receipt_amount"]
        csv_total = Decimal(0)
        for value in amounts:
            value = value.strip()
            if value:
                csv_total += Decimal(value)
        db_total = Decimal(_scalar(cur, "SELECT COALESCE(sum(amount),0) FROM contributions"))
        ok = abs(db_total - csv_total) < Decimal("0.01")
        return (OK if ok else CRIT, "total_amount_vs_csv",
                f"${db_total:,.2f} both" if ok else f"db={db_total} csv={csv_total}")
    except (psycopg2.Error, OSError, KeyError, ValueError, InvalidOperation) as error:
        return (CRIT, "total_amount_vs_csv", f"error: {str(error).strip()}")


# connect, run health checks, print results, and set exit code
def main() -> int:
    try:
        conn = connect()
    except Exception as error:
        print(f"  cannot connect to fec_db: {error}", file=sys.stderr)
        return 2
    conn.set_session(readonly=True, autocommit=True)

    print("=" * 60)
    print("  fec_db health check (live database)")
    print("=" * 60)
    results = run_checks(conn.cursor())
    conn.close()

    icon = {OK: "[ ok ]", WARN: "[warn]", CRIT: "[FAIL]"}
    for severity, name, detail in results:
        print(f"  {icon[severity]}  {name:55} {detail}")

    n_fail = sum(1 for severity, _, _ in results if severity == CRIT)
    n_warn = sum(1 for severity, _, _ in results if severity == WARN)
    print("-" * 60)
    print(f"  {len(results)} checks - {n_fail} FAIL, {n_warn} WARN")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
