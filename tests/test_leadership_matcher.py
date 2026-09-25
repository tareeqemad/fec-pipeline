import pytest

from fec.database.leadership_matcher import find_or_create_donor
from fec.database.loader.people import _boolean
from fec.donor_match import rules as R


class Cursor:
    def __init__(self, results):
        self.results = iter(results)
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return next(self.results)


def test_editorial_donor_uses_exact_key():
    cur = Cursor([(42,)])

    donor_id, method = find_or_create_donor(
        cur, "abc123", False, "PERSON, ONE", "ONE", "PERSON"
    )

    assert (donor_id, method) == (42, "donor_key_exact")
    assert len(cur.queries) == 1
    assert cur.queries[0][1] == ("abc123",)


def test_merged_away_key_links_to_the_surviving_donor(monkeypatch):
    monkeypatch.setattr(R, "KEY_MERGES", {"dropped": "kept"})
    cur = Cursor([(42,)])

    donor_id, method = find_or_create_donor(
        cur, "dropped", True, "WULIGER, TIM", "TIM", "WULIGER"
    )

    assert (donor_id, method) == (42, "donor_key_merged")
    assert len(cur.queries) == 1
    assert cur.queries[0][1] == ("kept",)


def test_unknown_fec_donor_fails():
    cur = Cursor([None])

    with pytest.raises(ValueError, match="Unknown donor_key"):
        find_or_create_donor(
            cur, "missing", False, "PERSON, ONE", "ONE", "PERSON"
        )

    assert len(cur.queries) == 1


def test_explicit_editorial_donor_can_be_created():
    cur = Cursor([None, (73,)])

    donor_id, method = find_or_create_donor(
        cur, "editorial", True, "Person One", "One", "Person"
    )

    assert (donor_id, method) == (73, "created")
    assert len(cur.queries) == 2
    assert cur.queries[1][1] == ("editorial", "ONE", "PERSON")


def test_editorial_boolean_is_strict():
    assert _boolean({"create_if_missing": "true"}, "create_if_missing", "x.csv", "X")
    assert not _boolean(
        {"create_if_missing": "false"}, "create_if_missing", "x.csv", "X"
    )
    with pytest.raises(ValueError, match="true or false"):
        _boolean({}, "create_if_missing", "x.csv", "X")


# --- roster employments point at the employer's workplace like FEC ones -------------

from fec.database.leadership_matcher import employment_address_id, upsert_leader_employment  # noqa: E402
from fec.database.loader.employment_locations import _location_index  # noqa: E402


def _office(name, street, city, state, zip_code, primary, lat="", lng=""):
    return {"employer_name": name, "employer_address": street, "employer_city": city,
            "employer_state": state, "employer_zip": zip_code, "employer_latitude": lat,
            "employer_longitude": lng, "is_primary": primary}


# built by the FEC loader's own index, so the roster sees exactly the structure FEC employments use
LOCATIONS = _location_index([
    _office("ACME HOLDINGS", "1 MAIN ST", "NEW YORK", "NY", "10022", True),
    _office("ACME HOLDINGS", "500 MARKET ST", "SAN FRANCISCO", "CA", "94105", False, "37.79", "-122.40"),
])
ADDRESSES = {
    ("1 MAIN ST", "", "NEW YORK", "NY", "10022"): 501,
    ("500 MARKET ST", "", "SAN FRANCISCO", "CA", "94105"): 502,
}


class EmploymentCursor:
    """Fake DB for upsert_leader_employment: answers by statement, records inserts."""

    def __init__(self, has_fec_employment=False, addresses=ADDRESSES):
        self.has_fec_employment = has_fec_employment
        self.addresses = dict(addresses)
        self.statements = []
        self.employments = []
        self.inserted_addresses = []
        self._next = None

    def execute(self, query, params=None):
        self.statements.append(query)
        if "FROM donor_employments" in query:
            self._next = (1,) if self.has_fec_employment else None
        elif "SELECT employer_id FROM employers" in query:
            self._next = (11,)
        elif "FROM occupation_categories" in query:
            self._next = (3,)
        elif "SELECT address_id FROM addresses" in query:
            found = self.addresses.get(tuple(params))
            self._next = (found,) if found else None
        elif "INSERT INTO addresses" in query:
            self.inserted_addresses.append(params)
            self._next = (900,)
        elif "INSERT INTO donor_employments" in query:
            self.employments.append(params)
            self._next = None
        else:
            self._next = None

    def fetchone(self):
        return self._next


def test_active_roster_employment_gets_the_same_state_office():
    cur = EmploymentCursor()
    upsert_leader_employment(cur, 7, "ACME HOLDINGS", "CEO", state="CA", zip_5="94107", locations=LOCATIONS)
    (donor_id, employer_id, occupation, _, status, address_id), = cur.employments
    assert (donor_id, employer_id, occupation, status, address_id) == (7, 11, "CEO", "active", 502)


def test_without_a_same_state_office_the_default_location_is_used():
    cur = EmploymentCursor()
    upsert_leader_employment(cur, 7, "ACME HOLDINGS", "CEO", state="TX", zip_5="78741", locations=LOCATIONS)
    assert cur.employments[0][-1] == 501
    # a foreign roster address (no US state) also falls back to the default location, as in the FEC path
    cur = EmploymentCursor()
    upsert_leader_employment(cur, 7, "ACME HOLDINGS", "CEO", state="", zip_5="9378322", locations=LOCATIONS)
    assert cur.employments[0][-1] == 501


def test_an_employer_with_no_known_location_leaves_the_address_empty():
    cur = EmploymentCursor()
    upsert_leader_employment(cur, 7, "NEWS CORP", "CHAIRMAN", state="NY", zip_5="10036", locations=LOCATIONS)
    assert cur.employments[0][-1] is None
    assert not any("FROM addresses" in query for query in cur.statements)


def test_retired_and_not_employed_rows_get_no_company_address():
    for employer in ("RETIRED", "NOT EMPLOYED"):
        cur = EmploymentCursor()
        upsert_leader_employment(cur, 7, employer, employer, state="NY", zip_5="10022", locations=LOCATIONS)
        (_, employer_id, _, _, status, address_id), = cur.employments
        assert employer_id is None and address_id is None and status != "active"


def test_an_office_the_loader_pruned_is_stored_again_with_its_coordinates():
    cur = EmploymentCursor(addresses={("1 MAIN ST", "", "NEW YORK", "NY", "10022"): 501})
    upsert_leader_employment(cur, 7, "ACME HOLDINGS", "CEO", state="CA", zip_5="94105", locations=LOCATIONS)
    assert cur.inserted_addresses == [("500 MARKET ST", None, "SAN FRANCISCO", "CA", "94105", 37.79, -122.40)]
    assert cur.employments[0][-1] == 900


def test_address_lookup_matches_the_loaders_blank_as_empty_key():
    # load_address_dimension keys addresses on COALESCE(col, ''), so a blank ZIP is '' in the lookup
    locations = _location_index([_office("NO ZIP INC", "9 ELM ST", "AUSTIN", "TX", None, True)])
    cur = EmploymentCursor(addresses={("9 ELM ST", "", "AUSTIN", "TX", ""): 77})
    assert employment_address_id(cur, "NO ZIP INC", locations, "TX", "78701") == 77


def test_fec_employment_wins_over_the_roster():
    cur = EmploymentCursor(has_fec_employment=True)
    upsert_leader_employment(cur, 7, "ACME HOLDINGS", "CEO", state="CA", zip_5="94105", locations=LOCATIONS)
    assert cur.employments == [] and cur.inserted_addresses == []
