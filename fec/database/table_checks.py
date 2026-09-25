"""Row counts, orphans, unique keys and conserved money."""
from __future__ import annotations

from fec.database.check_kinds import (
    CRIT,
    Check,
    _at_most,
    _equal,
    _positive,
    _scalar,
    _sql,
    _zero,
)

BIGINT_MAX = 9223372036854775807


# (child table, foreign key, parent table, primary key)
_FOREIGN_KEYS = [
    ("contributions", "donor_id", "donors", "donor_id"),
    ("contributions", "committee_id", "committees", "committee_id"),
    ("contributions", "donor_address_id", "donor_addresses", "donor_address_id"),
    ("contributions", "donor_employment_id", "donor_employments", "donor_employment_id"),
    ("donor_addresses", "donor_id", "donors", "donor_id"),
    ("donor_addresses", "address_id", "addresses", "address_id"),
    ("donor_employments", "donor_id", "donors", "donor_id"),
    ("donor_employments", "employer_id", "employers", "employer_id"),
    ("donor_employments", "occupation_category_id", "occupation_categories", "occupation_category_id"),
    ("donor_employments", "previous_employer_id", "employers", "employer_id"),
    ("employers", "address_id", "addresses", "address_id"),
    ("key_accomplices", "donor_id", "donors", "donor_id"),
    ("key_accomplices", "committee_id", "committees", "committee_id"),
    ("leaders", "donor_id", "donors", "donor_id"),
    ("leader_committees", "leader_id", "leaders", "leader_id"),
    ("leader_committees", "committee_id", "committees", "committee_id"),
]


# build a check for orphaned foreign-key references
def _orphan_check(child, foreign_key, parent, primary_key) -> Check:
    return _zero(
        f"orphans: {child}.{foreign_key}",
        _sql(f"""
            SELECT COUNT(*)
            FROM {child} AS child
            LEFT JOIN {parent} AS parent
              ON parent.{primary_key} = child.{foreign_key}
            WHERE child.{foreign_key} IS NOT NULL
              AND parent.{primary_key} IS NULL
        """),
        f"orphan {child}.{foreign_key}",
    )


# verify total contributions match summed donor stats
def _money_conserved(cur):
    contributions = _scalar(
        cur, "SELECT COALESCE(SUM(amount), 0) FROM contributions"
    )
    stats = _scalar(
        cur, "SELECT COALESCE(SUM(total_amount), 0) FROM v_donor_stats"
    )
    if contributions == stats:
        return True, f"${contributions:,.0f} conserved"
    return False, f"base={contributions} stats={stats}"


# Row counts, orphans and unique keys
INTEGRITY_CHECKS: list[Check] = [
    _positive(f"rows: {table}", f"SELECT COUNT(*) FROM {table}", "row(s)")
    for table in ("donors", "contributions", "committees", "addresses", "employers")
]
INTEGRITY_CHECKS += [_orphan_check(*foreign_key) for foreign_key in _FOREIGN_KEYS]
INTEGRITY_CHECKS += [
    _zero(
        "unique: donors.donor_key",
        _sql("""
            SELECT COUNT(*)
            FROM (
                SELECT donor_key
                FROM donors
                GROUP BY donor_key
                HAVING COUNT(*) > 1
            ) AS duplicates
        """),
        "duplicate donor_key(s)",
    ),
    _zero(
        "unique: contributions.sub_id",
        _sql("""
            SELECT COUNT(*)
            FROM (
                SELECT sub_id
                FROM contributions
                GROUP BY sub_id
                HAVING COUNT(*) > 1
            ) AS duplicates
        """),
        "duplicate sub_id(s)",
    ),
    _zero(
        "unique: committees.committee_number",
        _sql("""
            SELECT COUNT(*)
            FROM (
                SELECT committee_number
                FROM committees
                WHERE committee_number IS NOT NULL
                GROUP BY committee_number
                HAVING COUNT(*) > 1
            ) AS duplicates
        """),
        "duplicate committee_number(s)",
    ),
    _zero(
        "dedup: donor_employments (donor,employer,occupation,status)",
        _sql("""
            SELECT COUNT(*)
            FROM (
                SELECT donor_id, employer_id, occupation, employer_status
                FROM donor_employments
                GROUP BY donor_id, employer_id, occupation, employer_status
                HAVING COUNT(*) > 1
            ) AS duplicates
        """),
        "duplicate employment group(s)",
    ),
    _zero(
        "previous employer only on retired rows",
        _sql("""
            SELECT COUNT(*)
            FROM donor_employments
            WHERE employer_status IS DISTINCT FROM 'retired'
              AND (previous_employer_id IS NOT NULL
                   OR previous_self_employed)
        """),
        "non-retired row(s) with a previous employer",
    ),
    _zero(
        "previous employer is a company or SELF-EMPLOYED, not both",
        _sql("""
            SELECT COUNT(*)
            FROM donor_employments
            WHERE previous_self_employed
              AND previous_employer_id IS NOT NULL
        """),
        "row(s) with both kinds of previous employer",
    ),
    _at_most(
        "sub_id fits BIGINT",
        "SELECT COALESCE(MAX(sub_id), 0) FROM contributions",
        BIGINT_MAX,
        "max",
    ),
]


# Money is conserved across tables and aggregates
MONEY_CHECKS: list[Check] = [
    Check("money conserved (contributions = stats)", CRIT, _money_conserved),
    _zero(
        "v_donor_stats = direct aggregate",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_stats AS stats
            JOIN (
                SELECT donor_id,
                       SUM(amount) AS total_amount,
                       COUNT(*) AS donation_count
                FROM contributions
                GROUP BY donor_id
            ) AS direct
              ON direct.donor_id = stats.donor_id
            WHERE stats.total_amount <> direct.total_amount
               OR stats.donation_count <> direct.donation_count
        """),
        "donor(s) with wrong total/count",
    ),
    _equal(
        "v_donor_stats: one row per contributing donor",
        "SELECT COUNT(*) FROM v_donor_stats",
        "SELECT COUNT(DISTINCT donor_id) FROM contributions",
        "rows vs donors",
    ),
    _zero(
        "v_donor_stats: avg = total/count",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_stats
            WHERE donation_count > 0
              AND ABS(avg_amount - total_amount / donation_count) > 0.01
        """),
        "donor(s) with wrong avg",
    ),
    _zero(
        "v_donor_stats: election_cycles correct",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_stats AS stats
            JOIN (
                SELECT donor_id,
                       COUNT(DISTINCT election_cycle) AS election_cycles
                FROM contributions
                GROUP BY donor_id
            ) AS direct
              ON direct.donor_id = stats.donor_id
            WHERE stats.election_cycles <> direct.election_cycles
        """),
        "donor(s) with wrong cycle count",
    ),
    _zero(
        "v_donor_stats: first <= last donation",
        "SELECT COUNT(*) FROM v_donor_stats WHERE first_donation > last_donation",
        "donor(s) with first>last",
    ),
]
