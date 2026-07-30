"""ONE registry (CHECKS) of query-correctness checks for the loaded fec_db, consumed by tests/test_db_live.py (crit asserted by pytest) and healthcheck.py (crit -> FAIL, warn -> WARN)."""
from __future__ import annotations

CRIT, WARN = "crit", "warn"
BIGINT_MAX = 9223372036854775807


class Check:
    __slots__ = ("name", "category", "severity", "fn")

    def __init__(self, name, category, severity, fn):
        self.name, self.category, self.severity, self.fn = name, category, severity, fn

    def run(self, cur) -> tuple[bool, str]:
        try:
            return self.fn(cur)
        except Exception as error:  # a broken query is itself a failure
            return False, f"error: {type(error).__name__}: {str(error).strip()[:200]}"


def _scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def _zero(name, category, sql, label, severity=CRIT):
    """Pass when the (violation) count is 0."""
    def fn(cur, sql=sql, label=label):
        count = _scalar(cur, sql)
        return count == 0, (f"0 {label}" if count == 0 else f"{count:,} {label}")
    return Check(name, category, severity, fn)


def _equal(name, category, sql_a, sql_b, label, severity=CRIT):
    """Pass when two scalars are equal (cardinality / conservation)."""
    def fn(cur, a=sql_a, b=sql_b, label=label):
        value_a, value_b = _scalar(cur, a), _scalar(cur, b)
        return value_a == value_b, (f"{label}: {value_a:,} = {value_b:,}" if value_a == value_b
                                    else f"{label}: {value_a:,} != {value_b:,}")
    return Check(name, category, severity, fn)


def _positive(name, category, sql, label, severity=CRIT):
    """Pass when the count is > 0 (table populated)."""
    def fn(cur, sql=sql, label=label):
        count = _scalar(cur, sql)
        return count > 0, f"{count:,} {label}"
    return Check(name, category, severity, fn)


VIEW_CHECKS: list[Check] = []

# v_contributions_cleaned (flat view)
VIEW_CHECKS += [
    _equal("v_contributions_cleaned: 1:1 with contributions", "flat_view",
           "SELECT count(*) FROM v_contributions_cleaned",
           "SELECT count(*) FROM contributions", "rows"),
    _zero("v_contributions_cleaned: no duplicate sub_id", "flat_view",
          "SELECT count(*) FROM (SELECT sub_id FROM v_contributions_cleaned GROUP BY sub_id HAVING count(*)>1) t",
          "duplicate sub_id(s)"),
    _zero("v_contributions_cleaned: no missing sub_id", "flat_view",
          "SELECT count(*) FROM contributions c WHERE NOT EXISTS "
          "(SELECT 1 FROM v_contributions_cleaned v WHERE v.sub_id=c.sub_id)", "missing sub_id(s)"),
    _zero("v_contributions_cleaned: amount matches fact", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned v JOIN contributions c ON c.sub_id=v.sub_id "
          "WHERE v.contribution_receipt_amount IS DISTINCT FROM c.amount", "row(s) with wrong amount"),
    _zero("v_contributions_cleaned: year = year(date)", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned v JOIN contributions c ON c.sub_id=v.sub_id "
          "WHERE v.contributor_year <> EXTRACT(YEAR FROM c.receipt_date)::int", "row(s) with wrong year"),
    _zero("v_contributions_cleaned: individual name = 'LAST, FIRST'", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned WHERE entity_type='INDIVIDUAL' "
          "AND contributor_first_name IS NOT NULL AND contributor_last_name IS NOT NULL "
          "AND contributor_name <> contributor_last_name||', '||contributor_first_name",
          "malformed individual name(s)"),
    _zero("v_contributions_cleaned: literal 'NULL' surname preserved", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned WHERE contributor_last_name='NULL' "
          "AND contributor_name NOT LIKE 'NULL%'", "lost NULL surname(s)"),
    _zero("v_contributions_cleaned: recipient_committee resolves", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned v WHERE v.recipient_committee IS NOT NULL "
          "AND NOT EXISTS (SELECT 1 FROM committees cm WHERE cm.committee_short=v.recipient_committee)",
          "row(s) with unknown committee"),
    _zero("v_contributions_cleaned: entity_type matches donor", "flat_view",
          "SELECT count(*) FROM v_contributions_cleaned v JOIN donors d ON d.donor_key=v.donor_key "
          "WHERE v.entity_type<>d.entity_type", "row(s) with mismatched entity_type"),
]

# v_donor_profile (composed)
VIEW_CHECKS += [
    _equal("v_donor_profile: one row per donor", "profile",
           "SELECT count(*) FROM v_donor_profile", "SELECT count(*) FROM donors", "rows"),
    _zero("v_donor_profile: total matches stats", "profile",
          "SELECT count(*) FROM v_donor_profile p JOIN v_donor_stats s ON s.donor_id=p.donor_id "
          "WHERE p.total_amount IS DISTINCT FROM s.total_amount", "donor(s) with wrong total"),
    _zero("v_donor_profile: count matches stats", "profile",
          "SELECT count(*) FROM v_donor_profile p JOIN v_donor_stats s ON s.donor_id=p.donor_id "
          "WHERE p.donation_count IS DISTINCT FROM s.donation_count", "donor(s) with wrong count"),
    _zero("v_donor_profile: non-contributors have no stats", "profile",
          "SELECT count(*) FROM v_donor_profile p WHERE NOT EXISTS "
          "(SELECT 1 FROM contributions c WHERE c.donor_id=p.donor_id) AND p.donation_count IS NOT NULL",
          "non-contributor(s) with stats"),
    _zero("v_donor_profile: identity matches donors", "profile",
          "SELECT count(*) FROM v_donor_profile p JOIN donors d ON d.donor_id=p.donor_id "
          "WHERE p.donor_key<>d.donor_key OR p.entity_type<>d.entity_type "
          "OR p.first_name IS DISTINCT FROM d.first_name OR p.last_name IS DISTINCT FROM d.last_name",
          "donor(s) with mismatched identity"),
    _zero("v_donor_profile: current_employer matches employment view", "profile",
          "SELECT count(*) FROM v_donor_profile p JOIN v_donor_current_employment ce ON ce.donor_id=p.donor_id "
          "LEFT JOIN employers e ON e.employer_id=ce.employer_id "
          "WHERE p.current_employer IS DISTINCT FROM e.name", "donor(s) with wrong current_employer"),
]

# current / newest sub-views
for view, owner_join in [
    ("v_donor_current_address",
     "NOT EXISTS (SELECT 1 FROM donor_addresses da WHERE da.donor_id=v.donor_id AND da.address_id=v.address_id)"),
    ("v_donor_current_employment",
     "NOT EXISTS (SELECT 1 FROM donor_employments e WHERE e.donor_employment_id=v.donor_employment_id AND e.donor_id=v.donor_id)"),
]:
    VIEW_CHECKS.append(_zero(f"{view}: one row per donor", "sub_views",
                             f"SELECT count(*) FROM (SELECT donor_id FROM {view} GROUP BY donor_id HAVING count(*)>1) t",
                             "donor(s) with >1 row"))
    VIEW_CHECKS.append(_zero(f"{view}: row belongs to the donor", "sub_views",
                             f"SELECT count(*) FROM {view} v WHERE {owner_join}", "row(s) not owned by donor"))
VIEW_CHECKS += [
    _zero("v_donor_current_address: is the newest contribution's address", "sub_views",
          "WITH latest AS (SELECT DISTINCT ON (c.donor_id) c.donor_id, da.address_id "
          "FROM contributions c JOIN donor_addresses da ON da.donor_address_id=c.donor_address_id "
          "ORDER BY c.donor_id, c.receipt_date DESC, c.sub_id DESC) "
          "SELECT count(*) FROM v_donor_current_address v JOIN latest l ON l.donor_id=v.donor_id "
          "WHERE v.address_id<>l.address_id", "donor(s) not on newest address"),
    _zero("v_donor_newest_address: one row per donor", "sub_views",
          "SELECT count(*) FROM (SELECT donor_id FROM v_donor_newest_address GROUP BY donor_id HAVING count(*)>1) t",
          "donor(s) with >1 row"),
    _zero("v_donor_newest_address: row belongs to the donor", "sub_views",
          "SELECT count(*) FROM v_donor_newest_address v WHERE NOT EXISTS "
          "(SELECT 1 FROM donor_addresses da JOIN addresses a ON a.address_id=da.address_id "
          "WHERE da.donor_id=v.donor_id AND a.street_1 IS NOT DISTINCT FROM v.street_1 "
          "AND a.city IS NOT DISTINCT FROM v.city AND a.zip_code IS NOT DISTINCT FROM v.zip_code)",
          "row(s) not owned by donor"),
]

# dashboard views
VIEW_CHECKS += [
    _equal("v_key_accomplices: 1:1 with key_accomplices", "dashboard",
           "SELECT count(*) FROM v_key_accomplices", "SELECT count(*) FROM key_accomplices", "rows"),
    _zero("v_key_accomplices: every row named", "dashboard",
          "SELECT count(*) FROM v_key_accomplices WHERE COALESCE(full_name,'')=''", "unnamed row(s)"),
    _equal("v_leaders: 1:1 with leaders", "dashboard",
           "SELECT count(*) FROM v_leaders", "SELECT count(*) FROM leaders", "rows"),
    # committee membership lives in the leader_committees junction, not on
    # v_leaders; check the junction is populated and maps to visible leaders
    _positive("leader_committees: membership is populated", "dashboard",
              "SELECT count(*) FROM leader_committees", "junction row(s)"),
    _zero("leader_committees: every row maps to a visible leader", "dashboard",
          "SELECT count(*) FROM leader_committees lc "
          "WHERE NOT EXISTS (SELECT 1 FROM v_leaders v WHERE v.leader_id = lc.leader_id)",
          "orphan junction row(s)"),
]

# materialized view sync
VIEW_CHECKS += [
    _equal("mv_donor_profile: same row count as view", "matview",
           "SELECT count(*) FROM mv_donor_profile", "SELECT count(*) FROM v_donor_profile", "rows"),
    _zero("mv_donor_profile: in sync with view (per donor)", "matview",
          "SELECT count(*) FROM mv_donor_profile m FULL JOIN v_donor_profile v ON v.donor_id=m.donor_id "
          "WHERE m.donor_id IS NULL OR v.donor_id IS NULL OR m.total_amount IS DISTINCT FROM v.total_amount",
          "donor(s) out of sync (REFRESH needed)"),
]


# (child, fk, parent, pk) referential integrity
_ORPHANS = [
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


def _build() -> list[Check]:
    checks: list[Check] = []

    # integrity
    for table in ("donors", "contributions", "committees", "addresses", "employers"):
        checks.append(_positive(f"rows: {table}", "integrity",
                                f"SELECT count(*) FROM {table}", "row(s)"))
    for child, fk_column, parent, pk_column in _ORPHANS:
        checks.append(_zero(
            f"orphans: {child}.{fk_column}", "integrity",
            f"SELECT count(*) FROM {child} c LEFT JOIN {parent} p ON p.{pk_column}=c.{fk_column} "
            f"WHERE c.{fk_column} IS NOT NULL AND p.{pk_column} IS NULL",
            f"orphan {child}.{fk_column}"))
    checks += [
        _zero("unique: donors.donor_key", "integrity",
              "SELECT count(*) FROM (SELECT donor_key FROM donors GROUP BY donor_key HAVING count(*)>1) t",
              "duplicate donor_key(s)"),
        _zero("unique: contributions.sub_id", "integrity",
              "SELECT count(*) FROM (SELECT sub_id FROM contributions GROUP BY sub_id HAVING count(*)>1) t",
              "duplicate sub_id(s)"),
        _zero("unique: committees.committee_number", "integrity",
              "SELECT count(*) FROM (SELECT committee_number FROM committees "
              "WHERE committee_number IS NOT NULL GROUP BY committee_number HAVING count(*)>1) t",
              "duplicate committee_number(s)"),
        _zero("dedup: donor_employments (donor,employer,occupation)", "integrity",
              "SELECT count(*) FROM (SELECT donor_id,employer_id,occupation FROM donor_employments "
              "GROUP BY 1,2,3 HAVING count(*)>1) t", "duplicate employment group(s)"),
        Check("sub_id fits BIGINT", "integrity", CRIT, lambda cur: (
            (_scalar(cur, "SELECT COALESCE(max(sub_id),0) FROM contributions") <= BIGINT_MAX),
            f"max={_scalar(cur, 'SELECT COALESCE(max(sub_id),0) FROM contributions'):,}")),
    ]

    # money and aggregates
    def _conserved(cur):
        base = _scalar(cur, "SELECT COALESCE(sum(amount),0) FROM contributions")
        stats = _scalar(cur, "SELECT COALESCE(sum(total_amount),0) FROM v_donor_stats")
        flat = _scalar(cur, "SELECT COALESCE(sum(contribution_receipt_amount),0) FROM v_contributions_cleaned")
        ok = base == stats == flat
        return ok, (f"${base:,.0f} conserved" if ok else f"base={base} stats={stats} flat={flat}")
    checks += [
        Check("money conserved (contributions = stats = flat)", "money", CRIT, _conserved),
        _zero("v_donor_stats = direct aggregate", "money",
              "SELECT count(*) FROM v_donor_stats vs JOIN "
              "(SELECT donor_id,sum(amount) t,count(*) c FROM contributions GROUP BY donor_id) a "
              "ON a.donor_id=vs.donor_id WHERE vs.total_amount<>a.t OR vs.donation_count<>a.c",
              "donor(s) with wrong total/count"),
        _equal("v_donor_stats: one row per contributing donor", "money",
               "SELECT count(*) FROM v_donor_stats",
               "SELECT count(DISTINCT donor_id) FROM contributions", "rows vs donors"),
        _zero("v_donor_stats: avg = total/count", "money",
              "SELECT count(*) FROM v_donor_stats WHERE donation_count>0 AND "
              "abs(avg_amount - total_amount/donation_count) > 0.01", "donor(s) with wrong avg"),
        _zero("v_donor_stats: election_cycles correct", "money",
              "SELECT count(*) FROM v_donor_stats vs JOIN "
              "(SELECT donor_id,count(DISTINCT election_cycle) c FROM contributions GROUP BY donor_id) a "
              "ON a.donor_id=vs.donor_id WHERE vs.election_cycles<>a.c", "donor(s) with wrong cycle count"),
        _zero("v_donor_stats: first <= last donation", "money",
              "SELECT count(*) FROM v_donor_stats WHERE first_donation > last_donation",
              "donor(s) with first>last"),
    ]

    # flat view / profile / sub-views / dashboard / matview
    checks += VIEW_CHECKS

    # cleaning correctness
    checks += [
        _zero("no employer is a status word", "cleaning",
              "SELECT count(*) FROM employers WHERE upper(name) IN "
              "('RETIRED','SELF-EMPLOYED','SELF EMPLOYED','NOT EMPLOYED','UNEMPLOYED','NONE','N/A','HOMEMAKER','STUDENT')",
              "employer(s) that are status words"),
        _zero("individuals have a surname", "cleaning",
              "SELECT count(*) FROM donors WHERE entity_type='INDIVIDUAL' AND COALESCE(last_name,'')=''",
              "individual(s) missing surname", WARN),
        _zero("committees: raised/spent >= 0", "cleaning",
              "SELECT count(*) FROM committees WHERE raised < 0 OR spent < 0", "committee(s) with negative totals"),
        _zero("occupations are categorized", "cleaning",
              "SELECT count(*) FROM donor_employments "
              "WHERE COALESCE(occupation,'')<>'' AND occupation_category_id IS NULL",
              "occupation(s) without a category", WARN),
        _zero("active status implies an employer", "cleaning",
              "SELECT count(*) FROM donor_employments WHERE employer_status='active' AND employer_id IS NULL",
              "active row(s) without an employer", WARN),
        _zero("employer names not abbreviated", "cleaning",
              r"SELECT count(DISTINCT name) FROM employers "
              r"WHERE name ~ '\m(MGMT|MGT|INV|ASSOC|MFG|INTL|GRP|SVCS?)\M'",
              "employer(s) still abbreviated (ties to quality_scan)", WARN),
    ]

    # value sanity
    checks += [
        _zero("no future receipt dates", "sanity",
              "SELECT count(*) FROM contributions WHERE receipt_date > CURRENT_DATE", "future-dated row(s)"),
        # WARN, not CRIT: schema.sql has no CHECK on cycle parity/range, so a
        # bad cycle is dirty upstream data, not a query returning wrong results
        _zero("election cycles even & in range", "sanity",
              "SELECT count(*) FROM contributions WHERE mod(election_cycle,2)=1 "
              "OR election_cycle<1980 OR election_cycle>2030", "bad election_cycle row(s)", WARN),
        _zero("zip codes are 5 digits", "sanity",
              r"SELECT count(*) FROM addresses WHERE COALESCE(zip_code,'')<>'' AND zip_code !~ '^[0-9]{5}$'",
              "non-5-digit zip(s)", WARN),
        _zero("coordinates in valid lat/lng range", "sanity",
              "SELECT count(*) FROM addresses WHERE latitude IS NOT NULL "
              "AND (latitude NOT BETWEEN -90 AND 90 OR longitude NOT BETWEEN -180 AND 180)",
              "row(s) with out-of-range coords", WARN),
        _zero("negative amounts (refunds/memo)", "sanity",
              "SELECT count(*) FROM contributions WHERE amount < 0", "negative amount(s)", WARN),
        _zero("no ancient receipt dates", "sanity",
              "SELECT count(*) FROM contributions WHERE receipt_date < DATE '1980-01-01'",
              "pre-1980 row(s)", WARN),
        _zero("state codes exist in us_states", "sanity",
              "SELECT count(DISTINCT state_code) FROM addresses a "
              "WHERE state_code IS NOT NULL AND NOT EXISTS "
              "(SELECT 1 FROM us_states s WHERE s.code=a.state_code)",
              "code(s) not in us_states (territories, expected)", WARN),
        _zero("zip agrees with state", "sanity",
              "SELECT count(*) FROM addresses a "
              "JOIN zcta_state_rel z ON z.zcta5 = a.zip_code "
              "JOIN us_states s ON s.state_fips = z.state_fips "
              "WHERE COALESCE(a.state_code,'')<>'' AND a.state_code <> s.code",
              "zip/state mismatch(es)", WARN),
    ]

    return checks


CHECKS = _build()
