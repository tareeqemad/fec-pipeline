"""Donor profile, current-row and materialized views agree with the base tables."""
from __future__ import annotations

from textwrap import dedent

from fec.database.check_kinds import Check, _equal, _positive, _sql, _zero

# Composed donor profile
VIEW_CHECKS: list[Check] = [
    _equal(
        "v_donor_profile: one row per donor",
        "SELECT COUNT(*) FROM v_donor_profile",
        "SELECT COUNT(*) FROM donors",
        "rows",
    ),
    _zero(
        "v_donor_profile: total matches stats",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            JOIN v_donor_stats AS stats
              ON stats.donor_id = profile.donor_id
            WHERE profile.total_amount IS DISTINCT FROM stats.total_amount
        """),
        "donor(s) with wrong total",
    ),
    _zero(
        "v_donor_profile: count matches stats",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            JOIN v_donor_stats AS stats
              ON stats.donor_id = profile.donor_id
            WHERE profile.donation_count IS DISTINCT FROM stats.donation_count
        """),
        "donor(s) with wrong count",
    ),
    _zero(
        "v_donor_profile: non-contributors have no stats",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            WHERE NOT EXISTS (
                SELECT 1
                FROM contributions AS contribution
                WHERE contribution.donor_id = profile.donor_id
            )
              AND profile.donation_count IS NOT NULL
        """),
        "non-contributor(s) with stats",
    ),
    _zero(
        "v_donor_profile: identity matches donors",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            JOIN donors AS donor
              ON donor.donor_id = profile.donor_id
            WHERE profile.donor_key <> donor.donor_key
               OR profile.entity_type <> donor.entity_type
               OR profile.first_name IS DISTINCT FROM donor.first_name
               OR profile.last_name IS DISTINCT FROM donor.last_name
        """),
        "donor(s) with mismatched identity",
    ),
    _zero(
        "v_donor_profile: current_employer matches employment view",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            JOIN v_donor_current_employment AS employment
              ON employment.donor_id = profile.donor_id
            LEFT JOIN employers AS employer
              ON employer.employer_id = employment.employer_id
            WHERE profile.current_employer IS DISTINCT FROM employer.name
        """),
        "donor(s) with wrong current_employer",
    ),
    _zero(
        "v_donor_profile: previous_employer matches employment view",
        _sql("""
            SELECT COUNT(*)
            FROM v_donor_profile AS profile
            LEFT JOIN v_donor_current_employment AS employment
              ON employment.donor_id = profile.donor_id
            LEFT JOIN employers AS previous
              ON previous.employer_id = employment.previous_employer_id
            WHERE profile.previous_employer IS DISTINCT FROM (
                CASE
                    WHEN previous.name IS NOT NULL THEN previous.name
                    WHEN employment.previous_self_employed THEN 'SELF-EMPLOYED'
                END
            )
        """),
        "donor(s) with wrong previous_employer",
    ),
]


# Current address and employment views share these two ownership checks.
_CURRENT_VIEW_OWNERSHIP = [
    (
        "v_donor_current_address",
        """NOT EXISTS (
            SELECT 1
            FROM donor_addresses AS donor_address
            WHERE donor_address.donor_id = current_row.donor_id
              AND donor_address.address_id = current_row.address_id
        )""",
    ),
    (
        "v_donor_current_employment",
        """NOT EXISTS (
            SELECT 1
            FROM donor_employments AS employment
            WHERE employment.donor_employment_id = current_row.donor_employment_id
              AND employment.donor_id = current_row.donor_id
        )""",
    ),
]

for view_name, ownership_condition in _CURRENT_VIEW_OWNERSHIP:
    VIEW_CHECKS += [
        _zero(
            f"{view_name}: one row per donor",
            _sql(f"""
                SELECT COUNT(*)
                FROM (
                    SELECT donor_id
                    FROM {view_name}
                    GROUP BY donor_id
                    HAVING COUNT(*) > 1
                ) AS duplicates
            """),
            "donor(s) with >1 row",
        ),
        _zero(
            f"{view_name}: row belongs to the donor",
            _sql(f"""
                SELECT COUNT(*)
                FROM {view_name} AS current_row
                WHERE {dedent(ownership_condition).strip()}
            """),
            "row(s) not owned by donor",
        ),
    ]


VIEW_CHECKS += [
    _zero(
        "v_donor_current_address: is the newest contribution's address",
        _sql("""
            WITH latest_address AS (
                SELECT DISTINCT ON (contribution.donor_id)
                       contribution.donor_id,
                       donor_address.address_id
                FROM contributions AS contribution
                JOIN donor_addresses AS donor_address
                  ON donor_address.donor_address_id = contribution.donor_address_id
                ORDER BY contribution.donor_id,
                         contribution.receipt_date DESC,
                         contribution.sub_id DESC
            )
            SELECT COUNT(*)
            FROM v_donor_current_address AS current_address
            JOIN latest_address
              ON latest_address.donor_id = current_address.donor_id
            WHERE current_address.address_id <> latest_address.address_id
        """),
        "donor(s) not on newest address",
    ),
]


# Leadership and key accomplice rosters, read straight from the tables
VIEW_CHECKS += [
    _zero(
        "key_accomplices: every row named",
        _sql("""
            SELECT COUNT(*)
            FROM key_accomplices AS accomplice
            JOIN donors AS donor
              ON donor.donor_id = accomplice.donor_id
            WHERE CONCAT_WS(' ', donor.first_name, donor.last_name) = ''
        """),
        "unnamed row(s)",
    ),
    _positive(
        "leader_committees: membership is populated",
        "SELECT COUNT(*) FROM leader_committees",
        "junction row(s)",
    ),
    _zero(
        "leader_committees: every row maps to a visible leader",
        _sql("""
            SELECT COUNT(*)
            FROM leader_committees AS membership
            WHERE NOT EXISTS (
                SELECT 1
                FROM leaders AS leader
                JOIN donors AS donor
                  ON donor.donor_id = leader.donor_id
                WHERE leader.leader_id = membership.leader_id
            )
        """),
        "orphan junction row(s)",
    ),
]


# Materialized view sync
VIEW_CHECKS += [
    _equal(
        "mv_donor_profile: same row count as view",
        "SELECT COUNT(*) FROM mv_donor_profile",
        "SELECT COUNT(*) FROM v_donor_profile",
        "rows",
    ),
    _zero(
        "mv_donor_profile: in sync with view (per donor)",
        _sql("""
            SELECT COUNT(*)
            FROM mv_donor_profile AS materialized
            FULL JOIN v_donor_profile AS current
              ON current.donor_id = materialized.donor_id
            WHERE materialized.donor_id IS NULL
               OR current.donor_id IS NULL
               OR materialized.total_amount IS DISTINCT FROM current.total_amount
        """),
        "donor(s) out of sync (REFRESH needed)",
    ),
]
