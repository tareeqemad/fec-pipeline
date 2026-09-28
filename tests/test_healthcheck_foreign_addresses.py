"""The ZIP health checks count US addresses only; an address abroad is kept as filed."""
from fec.database.data_checks import VALUE_CHECKS


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query):
        pass

    def fetchall(self):
        return self.rows


def _check(name):
    return next(check for check in VALUE_CHECKS if check.name == name)


def test_foreign_postcodes_and_a_foreign_city_with_a_us_state_pass():
    rows = [
        ("York Gate, 100 Marylebone Road", "", "London", "", "NW1 5DX"),
        ("22 ROTHSCHILD BLVD", "", "TEL AVIV", "", "6688218"),
        ("CALLE DOCTOR FOURQUET 33", "", "MADRID", "IL", "28012"),
    ]
    for name in ("zip codes are 5 digits", "zip agrees with state"):
        ok, detail = _check(name).fn(_Cursor(rows))
        assert ok, detail


def test_a_us_address_is_still_reported_by_name():
    ok, detail = _check("zip agrees with state").fn(_Cursor([("1 MAIN ST", "", "CHICAGO", "IL", "10001")]))
    assert not ok and detail.startswith("1 US zip/state") and "CHICAGO IL 10001" in detail
