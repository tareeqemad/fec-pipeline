"""healthcheck.py — assertions against the REAL fec_db (read-only).

Unlike the schema tests (tiny seeded rows on a throwaway Postgres, which check
the schema LOGIC), this validates the ACTUAL loaded database. The bulk of the
checks come from fec.database.query_checks.CHECKS — the ONE registry that
tests/test_db_live.py also parametrizes over — so the panel and CI can never
drift apart again. On top of the registry, this module keeps only the checks
that are meaningful against a live database (and its neighbouring CSV):
required views/matviews exist, geocode coverage %, matview freshness,
'NULL'-surname info, and DB-total-vs-cleaned-CSV reconciliation.
Meant to run after every loader run.

    python -m fec.database.healthcheck

Exits non-zero if any CRITICAL check fails (e.g. v_key_accomplices missing —
the exact incident that once broke the site). WARN checks never fail the run.
"""
from __future__ import annotations

import sys

import psycopg2

from fec.database.query_checks import CHECKS
from fec.database.query_checks import CRIT as QC_CRIT

CRIT, WARN, OK = "FAIL", "WARN", "ok"

# Views / matview the web app depends on (their absence is what broke the site).
REQUIRED_VIEWS = [
    "v_contributions_cleaned", "v_donor_profile", "v_donor_stats",
    "v_donor_current_address", "v_donor_newest_address",
    "v_donor_current_employment", "v_donor_newest_employment",
    "v_key_accomplices", "v_leaders",
]
REQUIRED_MATVIEWS = ["mv_donor_profile"]


def _scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def run_checks(cur) -> list[tuple[str, str, str]]:
    """Return [(severity, name, detail)].

    Composition:
      1. required views/matviews exist — runs FIRST so a missing view is
         reported by name instead of surfacing as N cryptic query errors;
      2. the shared query_checks.CHECKS registry — crit → FAIL, warn → WARN.
         A check whose query cannot execute reports as a failure at the
         check's own severity (Check.run catches the error itself);
      3. live-DB-only extras that have no meaning in the CI test harness.
    """
    out = []

    # ── 1. Required views / matviews exist (CRITICAL) ──
    cur.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    views = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname='public'")
    mviews = {r[0] for r in cur.fetchall()}
    missing = [v for v in REQUIRED_VIEWS if v not in views] + \
              [v for v in REQUIRED_MATVIEWS if v not in mviews]
    out.append((OK if not missing else CRIT, "required_views",
                "all present" if not missing else f"MISSING: {', '.join(missing)}"))

    # ── 2. The shared registry (single source of truth — see query_checks.py) ──
    for check in CHECKS:
        ok, detail = check.run(cur)
        sev = OK if ok else (CRIT if check.severity == QC_CRIT else WARN)
        out.append((sev, check.name, detail))

    # ── 3. Live-DB-only extras ──

    # Geocode coverage — a threshold on a percentage, not a zero/nonzero rule.
    try:
        tot = _scalar(cur, "SELECT count(*) FROM addresses")
        geo = _scalar(cur, "SELECT count(*) FROM addresses WHERE latitude IS NOT NULL")
        pct = (geo / tot * 100) if tot else 100
        out.append((OK if pct >= 80 else WARN, "geocode_coverage", f"{geo:,}/{tot:,} ({pct:.0f}%)"))
    except psycopg2.Error as e:
        out.append((WARN, "geocode_coverage", f"error: {str(e).strip()}"))

    # Matview freshness — mv rows vs donors. Complements the registry's
    # mv-vs-view sync checks with a "was REFRESH run at all" signal.
    try:
        nd = _scalar(cur, "SELECT count(*) FROM donors")
        nm = _scalar(cur, "SELECT count(*) FROM mv_donor_profile")
        out.append((OK if nm == nd else WARN, "mv_donor_profile_fresh",
                    f"{nm:,} rows vs {nd:,} donors" + ("" if nm == nd else " — REFRESH needed")))
    except psycopg2.Error as e:
        out.append((WARN, "mv_donor_profile_fresh", f"error: {str(e).strip()}"))

    # Informational: literal 'NULL' surnames survived the load (never a failure —
    # the registry separately asserts none were LOST in v_contributions_cleaned).
    try:
        n_null = _scalar(cur, "SELECT count(*) FROM donors WHERE last_name='NULL'")
        out.append((OK, "null_surname_preserved", f"{n_null:,} real 'NULL' surname(s) kept"))
    except psycopg2.Error as e:
        out.append((WARN, "null_surname_preserved", f"error: {str(e).strip()}"))

    # DB total vs the cleaned CSV sitting next to it (the registry can't see
    # files) — a mismatch means the DB is NOT loaded from this CSV.
    out.append(_csv_total_check(cur))

    return out


def _csv_total_check(cur) -> tuple[str, str, str]:
    """Compare sum(contributions.amount) against contributions_cleaned.csv.

    Exact Decimal arithmetic on the CSV side; a sub-cent tolerance absorbs the
    NUMERIC(15,2) rounding the DB applies at insert. Skips (OK) when the CSV
    is not present on this machine.
    """
    from decimal import Decimal, InvalidOperation
    try:
        from fec.env import CLEANED_CSV
        if not CLEANED_CSV.exists():
            return (OK, "total_amount_vs_csv", "cleaned CSV not present — skipped")
        import pandas as pd
        col = pd.read_csv(CLEANED_CSV, usecols=["contribution_receipt_amount"],
                          dtype=str, keep_default_na=False)["contribution_receipt_amount"]
        csv_total = Decimal(0)
        for v in col:
            v = v.strip()
            if v:
                csv_total += Decimal(v)
        db_total = Decimal(_scalar(cur, "SELECT COALESCE(sum(amount),0) FROM contributions"))
        ok = abs(db_total - csv_total) < Decimal("0.01")
        return (OK if ok else CRIT, "total_amount_vs_csv",
                f"${db_total:,.2f} both" if ok else f"db={db_total} csv={csv_total}")
    except (psycopg2.Error, OSError, KeyError, ValueError, InvalidOperation) as e:
        return (CRIT, "total_amount_vs_csv", f"error: {str(e).strip()}")


def main() -> int:
    try:
        from fec.database.loader import connect   # uses the project's .env config
        conn = connect()
    except Exception as e:
        print(f"  cannot connect to fec_db: {e}", file=sys.stderr)
        return 2
    conn.set_session(readonly=True, autocommit=True)

    print("=" * 60)
    print("  fec_db health check (live database)")
    print("=" * 60)
    results = run_checks(conn.cursor())
    conn.close()

    icon = {OK: "[ ok ]", WARN: "[warn]", CRIT: "[FAIL]"}
    for sev, name, detail in results:
        print(f"  {icon[sev]}  {name:55} {detail}")

    n_fail = sum(1 for s, _, _ in results if s == CRIT)
    n_warn = sum(1 for s, _, _ in results if s == WARN)
    print("-" * 60)
    print(f"  {len(results)} checks — {n_fail} FAIL, {n_warn} WARN")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
