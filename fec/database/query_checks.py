"""Shared read-only correctness checks for the loaded FEC database."""
from __future__ import annotations

from textwrap import dedent

from fec.config.constants import SKIP_EMPLOYERS
from fec.config.employers import EMPLOYER_ABBREVIATIONS

CRIT, WARN = "crit", "warn"
BIGINT_MAX = 9223372036854775807


class Check:
    __slots__ = ("name", "severity", "fn")

    def __init__(self, name, severity, fn):
        self.name, self.severity, self.fn = name, severity, fn

    def run(self, cur) -> tuple[bool, str]:
        try:
            return self.fn(cur)
        except Exception as error:  # a broken query is itself a failure
            return False, f"error: {type(error).__name__}: {str(error).strip()[:200]}"


def _sql(query: str) -> str:
    """Keep multi-line SQL readable without sending its indentation."""
    return dedent(query).strip()


def _scalar(cur, query):
    cur.execute(query)
    return cur.fetchone()[0]


def _zero(name, query, label, severity=CRIT):
    """Pass when the violation count is zero."""

    def fn(cur, query=query, label=label):
        count = _scalar(cur, query)
        detail = f"0 {label}" if count == 0 else f"{count:,} {label}"
        return count == 0, detail

    return Check(name, severity, fn)


def _equal(name, query_a, query_b, label, severity=CRIT):
    """Pass when two scalar queries return the same value."""

    def fn(cur, query_a=query_a, query_b=query_b, label=label):
        value_a = _scalar(cur, query_a)
        value_b = _scalar(cur, query_b)
        operator = "=" if value_a == value_b else "!="
        return value_a == value_b, f"{label}: {value_a:,} {operator} {value_b:,}"

    return Check(name, severity, fn)


def _positive(name, query, label, severity=CRIT):
    """Pass when the returned count is greater than zero."""

    def fn(cur, query=query, label=label):
        count = _scalar(cur, query)
        return count > 0, f"{count:,} {label}"

    return Check(name, severity, fn)


def _at_most(name, query, maximum, label, severity=CRIT):
    """Pass when a scalar is at most the configured maximum."""

    def fn(cur, query=query, maximum=maximum, label=label):
        value = _scalar(cur, query)
        return value <= maximum, f"{label}={value:,}"

    return Check(name, severity, fn)


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


def _integrity_checks() -> list[Check]:
    """Row counts, orphans and unique keys."""
    checks: list[Check] = []
    for table in ("donors", "contributions", "committees", "addresses", "employers"):
        checks.append(
            _positive(f"rows: {table}", f"SELECT COUNT(*) FROM {table}", "row(s)")
        )
    checks.extend(_orphan_check(*foreign_key) for foreign_key in _FOREIGN_KEYS)

    checks += [
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
    return checks


def _money_checks() -> list[Check]:
    """Money is conserved across tables and aggregates."""
    checks: list[Check] = []
    checks += [
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

    checks += VIEW_CHECKS
    return checks


def _cleaning_checks() -> list[Check]:
    """Cleaned values follow the cleaning contract."""
    checks: list[Check] = []
    status_words = ",".join(
        f"'{word}'" for word in sorted(SKIP_EMPLOYERS) if word
    )
    employer_abbreviations = "|".join(EMPLOYER_ABBREVIATIONS)
    checks += [
        _zero(
            "no employer is a status word",
            _sql(f"""
                SELECT COUNT(*)
                FROM employers
                WHERE UPPER(name) IN ({status_words})
            """),
            "employer(s) that are status words",
        ),
        _zero(
            "individuals have a surname",
            _sql("""
                SELECT COUNT(*)
                FROM donors
                WHERE entity_type = 'INDIVIDUAL'
                  AND COALESCE(last_name, '') = ''
            """),
            "individual(s) missing surname",
            WARN,
        ),
        _zero(
            "committees: raised/spent >= 0",
            "SELECT COUNT(*) FROM committees WHERE raised < 0 OR spent < 0",
            "committee(s) with negative totals",
        ),
        _zero(
            "occupations are categorized",
            _sql("""
                SELECT COUNT(*)
                FROM donor_employments
                WHERE COALESCE(occupation, '') <> ''
                  AND occupation_category_id IS NULL
            """),
            "occupation(s) without a category",
            WARN,
        ),
        _zero(
            "active status implies an employer",
            _sql("""
                SELECT COUNT(*)
                FROM donor_employments
                WHERE employer_status = 'active'
                  AND employer_id IS NULL
            """),
            "active row(s) without an employer",
            WARN,
        ),
        _zero(
            "employer names not abbreviated",
            _sql(rf"""
                SELECT COUNT(DISTINCT name)
                FROM employers
                WHERE name ~ '\m({employer_abbreviations})\M'
            """),
            "employer(s) still abbreviated (same table quality_scan surfaces)",
            WARN,
        ),
    ]
    return checks


def _value_checks() -> list[Check]:
    """Stored values fall in sane ranges."""
    checks: list[Check] = []
    checks += [
        _zero(
            "no future receipt dates",
            "SELECT COUNT(*) FROM contributions WHERE receipt_date > CURRENT_DATE",
            "future-dated row(s)",
        ),
        _zero(
            "election cycles even & in range",
            _sql("""
                SELECT COUNT(*)
                FROM contributions
                WHERE MOD(election_cycle, 2) = 1
                   OR election_cycle < 1980
                   OR election_cycle > 2030
            """),
            "bad election_cycle row(s)",
            WARN,
        ),
        _zero(
            "zip codes are 5 digits",
            _sql(r"""
                SELECT COUNT(*)
                FROM addresses
                WHERE COALESCE(zip_code, '') <> ''
                  AND zip_code !~ '^[0-9]{5}$'
            """),
            "non-5-digit zip(s)",
            WARN,
        ),
        _zero(
            "coordinates in valid lat/lng range",
            _sql("""
                SELECT COUNT(*)
                FROM addresses
                WHERE latitude IS NOT NULL
                  AND (
                      latitude NOT BETWEEN -90 AND 90
                      OR longitude NOT BETWEEN -180 AND 180
                  )
            """),
            "row(s) with out-of-range coords",
            WARN,
        ),
        _zero(
            "negative amounts (refunds/memo)",
            "SELECT COUNT(*) FROM contributions WHERE amount < 0",
            "negative amount(s)",
            WARN,
        ),
        _zero(
            "no ancient receipt dates",
            _sql("""
                SELECT COUNT(*)
                FROM contributions
                WHERE receipt_date < DATE '1980-01-01'
            """),
            "pre-1980 row(s)",
            WARN,
        ),
        _zero(
            "state codes exist in us_states",
            _sql("""
                SELECT COUNT(DISTINCT address.state_code)
                FROM addresses AS address
                WHERE address.state_code IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM us_states AS state
                      WHERE state.code = address.state_code
                  )
            """),
            "code(s) not in us_states (territories, expected)",
            WARN,
        ),
        _zero(
            "zip agrees with state",
            _sql("""
                SELECT COUNT(*)
                FROM addresses AS address
                JOIN zcta_state_rel AS zip_state
                  ON zip_state.zcta5 = address.zip_code
                JOIN us_states AS state
                  ON state.state_fips = zip_state.state_fips
                WHERE COALESCE(address.state_code, '') <> ''
                  AND address.state_code <> state.code
            """),
            "zip/state mismatch(es)",
            WARN,
        ),
    ]
    return checks


def _build() -> list[Check]:
    return _integrity_checks() + _money_checks() + _cleaning_checks() + _value_checks()


CHECKS = _build()
