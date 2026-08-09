"""schema.sql on an ephemeral Postgres: objects, constraints, view correctness."""
import psycopg2
import psycopg2.errors
import pytest

pytestmark = pytest.mark.db


def test_expected_objects_exist(db):
    cur = db.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tables = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    views = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname='public'")
    matviews = {r[0] for r in cur.fetchall()}
    assert {"donors", "contributions", "addresses", "employers",
            "donor_employments", "committees", "key_accomplices",
            "leaders", "leader_committees"} <= tables
    assert {"v_donor_current_address", "v_donor_newest_address",
            "v_contributions_cleaned", "v_key_accomplices", "v_leaders"} <= views
    assert "mv_donor_profile" in matviews


def test_entity_type_check(db):
    cur = db.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO donors (donor_key, entity_type) VALUES ('k1','ALIEN')")


def test_lat_lng_range_check(db):
    cur = db.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO addresses (latitude) VALUES (999)")


def test_committee_negative_raised_check(db):
    cur = db.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO committees (committee_name, raised) VALUES ('C', -5)")


def test_us_states_code_unique(db):
    cur = db.cursor()
    cur.execute("INSERT INTO us_states VALUES ('01','AL','Alabama')")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("INSERT INTO us_states VALUES ('99','AL','Dup')")


def test_donor_employments_unique_nulls_not_distinct(db):
    cur = db.cursor()
    cur.execute("INSERT INTO donors (donor_key, entity_type) VALUES ('d1','INDIVIDUAL') RETURNING donor_id")
    did = cur.fetchone()[0]
    cur.execute("INSERT INTO donor_employments (donor_id, employer_id, occupation) VALUES (%s, NULL, NULL)", (did,))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("INSERT INTO donor_employments (donor_id, employer_id, occupation) VALUES (%s, NULL, NULL)", (did,))


def test_contribution_fk_violation(db):
    cur = db.cursor()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute(
            "INSERT INTO contributions (sub_id, transaction_id, donor_id, committee_id, amount, receipt_date, election_cycle)"
            " VALUES (1,'t',99999,99999,10,'2024-01-01',2024)"
        )


def _seed_donor(cur, key="dk", entity="INDIVIDUAL", first="JANE", last="DOE"):
    cur.execute("INSERT INTO donors (donor_key, entity_type, first_name, last_name)"
                " VALUES (%s,%s,%s,%s) RETURNING donor_id", (key, entity, first, last))
    return cur.fetchone()[0]


def test_v_donor_current_address_picks_newest(db):
    cur = db.cursor()
    did = _seed_donor(cur)
    cur.execute("INSERT INTO committees (committee_name) VALUES ('PAC') RETURNING committee_id")
    cid = cur.fetchone()[0]
    ids = []
    for street in ("1 OLD ST", "2 NEW ST"):
        cur.execute("INSERT INTO addresses (street_1) VALUES (%s) RETURNING address_id", (street,))
        aid = cur.fetchone()[0]
        cur.execute("INSERT INTO donor_addresses (donor_id, address_id) VALUES (%s,%s) RETURNING donor_address_id", (did, aid))
        ids.append(cur.fetchone()[0])
    cur.execute("INSERT INTO contributions VALUES (10,'a',%s,%s,%s,NULL,5,'2020-01-01',2020)", (did, cid, ids[0]))
    cur.execute("INSERT INTO contributions VALUES (11,'b',%s,%s,%s,NULL,5,'2024-01-01',2024)", (did, cid, ids[1]))
    cur.execute("SELECT street_1 FROM v_donor_current_address WHERE donor_id=%s", (did,))
    assert cur.fetchone()[0] == "2 NEW ST"


def test_v_donor_newest_address_covers_non_donor(db):
    """A donor with an address but NO contribution still resolves (leaders case)."""
    cur = db.cursor()
    did = _seed_donor(cur, key="leader")
    cur.execute("INSERT INTO addresses (street_1) VALUES ('5 LEADER WAY') RETURNING address_id")
    aid = cur.fetchone()[0]
    cur.execute("INSERT INTO donor_addresses (donor_id, address_id) VALUES (%s,%s)", (did, aid))
    cur.execute("SELECT street_1 FROM v_donor_newest_address WHERE donor_id=%s", (did,))
    assert cur.fetchone()[0] == "5 LEADER WAY"


def test_v_contributions_cleaned_name_case_and_null_surname(db):
    cur = db.cursor()
    # literal surname "NULL" must survive as text, not become SQL NULL
    did = _seed_donor(cur, key="nulldk", first="JAMES", last="NULL")
    cur.execute("INSERT INTO committees (committee_number, committee_name) VALUES ('C00','PAC') RETURNING committee_id")
    cid = cur.fetchone()[0]
    cur.execute("INSERT INTO contributions VALUES (20,'t',%s,%s,NULL,NULL,5,'2024-01-01',2024)", (did, cid))
    cur.execute("SELECT contributor_name, contributor_last_name FROM v_contributions_cleaned WHERE sub_id=20")
    name, last = cur.fetchone()
    assert name == "NULL, JAMES"  # individual renders as "LAST, FIRST"
    assert last == "NULL"


def test_v_contributions_uses_selected_workplace(db):
    cur = db.cursor()
    donor_id = _seed_donor(cur)
    cur.execute(
        "INSERT INTO committees (committee_name) VALUES ('PAC') RETURNING committee_id"
    )
    committee_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO addresses (street_1) VALUES ('DEFAULT OFFICE') RETURNING address_id"
    )
    default_address = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO addresses (street_1) VALUES ('NEAREST OFFICE') RETURNING address_id"
    )
    nearest_address = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO employers (name, address_id) VALUES ('ACME', %s) RETURNING employer_id",
        (default_address,),
    )
    employer_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO donor_employments "
        "(donor_id, employer_id, employer_status, address_id) "
        "VALUES (%s, %s, 'active', %s) RETURNING donor_employment_id",
        (donor_id, employer_id, nearest_address),
    )
    employment_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO contributions VALUES "
        "(30, 't', %s, %s, NULL, %s, 5, '2024-01-01', 2024)",
        (donor_id, committee_id, employment_id),
    )

    cur.execute(
        "SELECT employer_address FROM v_contributions_cleaned WHERE sub_id = 30"
    )

    assert cur.fetchone()[0] == "NEAREST OFFICE"


def test_v_donor_stats_aggregates(db):
    cur = db.cursor()
    did = _seed_donor(cur)
    cur.execute("INSERT INTO committees (committee_name) VALUES ('P') RETURNING committee_id")
    cid = cur.fetchone()[0]
    cur.execute("INSERT INTO contributions VALUES (1,'a',%s,%s,NULL,NULL,100,'2020-01-01',2020)", (did, cid))
    cur.execute("INSERT INTO contributions VALUES (2,'b',%s,%s,NULL,NULL,300,'2024-01-01',2024)", (did, cid))
    cur.execute("SELECT donation_count, total_amount, first_donation, last_donation, election_cycles"
                " FROM v_donor_stats WHERE donor_id=%s", (did,))
    cnt, total, first, last, cycles = cur.fetchone()
    assert cnt == 2 and float(total) == 400 and cycles == 2
    assert str(first) == "2020-01-01" and str(last) == "2024-01-01"


def test_v_donor_profile_and_matview(db):
    cur = db.cursor()
    did = _seed_donor(cur)
    cur.execute("INSERT INTO committees (committee_name) VALUES ('P') RETURNING committee_id")
    cid = cur.fetchone()[0]
    cur.execute("INSERT INTO contributions VALUES (1,'a',%s,%s,NULL,NULL,250,'2024-01-01',2024)", (did, cid))
    cur.execute("SELECT total_amount, donation_count FROM v_donor_profile WHERE donor_id=%s", (did,))
    total, cnt = cur.fetchone()
    assert float(total) == 250 and cnt == 1
    cur.execute("REFRESH MATERIALIZED VIEW mv_donor_profile")
    cur.execute("SELECT total_amount FROM mv_donor_profile WHERE donor_id=%s", (did,))
    assert float(cur.fetchone()[0]) == 250


def test_v_key_accomplices_joins_identity_address_committee(db):
    cur = db.cursor()
    did = _seed_donor(cur, key="acc", first="ADAM", last="MILSTEIN")
    cur.execute("INSERT INTO committees (committee_name, logo_path) VALUES ('AIPAC','/l.png') RETURNING committee_id")
    cid = cur.fetchone()[0]
    cur.execute("INSERT INTO addresses (street_1, city, state_code) VALUES ('1 WAY','LA','CA') RETURNING address_id")
    aid = cur.fetchone()[0]
    cur.execute("INSERT INTO donor_addresses (donor_id, address_id) VALUES (%s,%s)", (did, aid))
    cur.execute("INSERT INTO key_accomplices (donor_id, sign, committee_id, display_order) VALUES (%s,'Ace',%s,1)", (did, cid))
    cur.execute("SELECT full_name, committee_name, committee_logo, city FROM v_key_accomplices WHERE donor_id=%s", (did,))
    full, cname, logo, city = cur.fetchone()
    assert full == "ADAM MILSTEIN"  # CONCAT_WS(first, last)
    assert cname == "AIPAC" and logo == "/l.png" and city == "LA"


def test_v_leaders_committee_via_junction(db):
    """v_leaders carries identity only; committee membership via leader_committees."""
    cur = db.cursor()
    did = _seed_donor(cur, key="ldr", first="HOWARD", last="KOHR")
    cur.execute("INSERT INTO committees (committee_name, committee_short) VALUES ('AIPAC','AIPAC') RETURNING committee_id")
    cid = cur.fetchone()[0]
    cur.execute("INSERT INTO leaders (donor_id) VALUES (%s) RETURNING leader_id", (did,))
    lid = cur.fetchone()[0]
    cur.execute("INSERT INTO leader_committees (leader_id, committee_id) VALUES (%s,%s)", (lid, cid))
    cur.execute("SELECT full_name, leader_id FROM v_leaders WHERE donor_id=%s", (did,))
    full, got_lid = cur.fetchone()
    assert full == "HOWARD KOHR" and got_lid == lid
    cur.execute(
        "SELECT cm.committee_short FROM v_leaders v "
        "JOIN leader_committees lc ON lc.leader_id = v.leader_id "
        "JOIN committees cm ON cm.committee_id = lc.committee_id "
        "WHERE v.donor_id = %s",
        (did,),
    )
    assert [r[0] for r in cur.fetchall()] == ["AIPAC"]


def test_healthcheck_sql_runs(db):
    """Every healthcheck query is valid against a freshly-created schema."""
    from fec.database.healthcheck import run_checks
    from fec.database.query_checks import CHECKS
    results = run_checks(db.cursor())
    by_name = {name: sev for sev, name, _ in results}
    assert by_name.get("required_views") == "ok"
    assert {c.name for c in CHECKS} <= set(by_name)  # entire shared registry included
    assert "sub_id fits BIGINT" in by_name
    assert "geocode_coverage" in by_name  # live-only extras still reported
    assert "total_amount_vs_csv" in by_name
