"""Cleaned values follow the cleaning contract and fall in sane ranges."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.config.constants import SKIP_EMPLOYERS
from fec.config.employers import EMPLOYER_ABBREVIATIONS
from fec.database.check_kinds import WARN, Check, _none, _sql, _zero
from fec.geocoding.address_kind import is_foreign_address

_STATUS_WORDS = ",".join(f"'{word}'" for word in sorted(SKIP_EMPLOYERS) if word)
_EMPLOYER_ABBREVIATIONS = "|".join(EMPLOYER_ABBREVIATIONS)

_ADDRESS_COLUMNS = "street_1, street_2, city, state_code, zip_code"


# addresses abroad, by the pipeline's own rules; they are kept as filed
def _foreign(rows: list[tuple]) -> list[bool]:
    frame = pd.DataFrame(rows, columns=[
        "contributor_street_1", "contributor_street_2", "contributor_city",
        "contributor_state", "contributor_zip",
    ]).fillna("")
    filed_abroad = foreign_address_mask(frame)
    return [
        abroad or is_foreign_address(state, zipcode)
        for abroad, state, zipcode in zip(filed_abroad, frame["contributor_state"], frame["contributor_zip"])
    ]


# pass when no US address matches the query; a foreign one is kept as filed
def _no_us_address(name, query, label, severity=WARN):
    """The query selects street_1, street_2, city, state_code, zip_code."""

    # drop foreign addresses and list the US ones that remain
    def fn(cur, query=query, label=label):
        cur.execute(query)
        rows = cur.fetchall()
        us = [row for row, abroad in zip(rows, _foreign(rows) if rows else []) if not abroad]
        shown = "; ".join(" ".join(str(part) for part in row if part) for row in us[:5])
        detail = f"{len(us):,} {label}" + (f": {shown}" if us else "")
        return not us, detail

    return Check(name, severity, fn)


# Cleaned values follow the cleaning contract
CLEANING_CHECKS: list[Check] = [
    _zero(
        "no employer is a status word",
        _sql(f"""
            SELECT COUNT(*)
            FROM employers
            WHERE UPPER(name) IN ({_STATUS_WORDS})
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
    _none(
        "employer names not abbreviated",
        _sql(rf"""
            SELECT DISTINCT name
            FROM employers
            WHERE name ~ '\m({_EMPLOYER_ABBREVIATIONS})\M'
            ORDER BY name
        """),
        "employer(s) still abbreviated (same table quality_scan surfaces)",
        WARN,
    ),
]


# Stored values fall in sane ranges
VALUE_CHECKS: list[Check] = [
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
    _no_us_address(
        "zip codes are 5 digits",
        _sql(rf"""
            SELECT {_ADDRESS_COLUMNS}
            FROM addresses
            WHERE COALESCE(zip_code, '') <> ''
              AND zip_code !~ '^[0-9]{{5}}$'
        """),
        "non-5-digit US zip(s)",
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
    _no_us_address(
        "zip agrees with state",
        _sql("""
            SELECT address.street_1, address.street_2, address.city, address.state_code, address.zip_code
            FROM addresses AS address
            JOIN zcta_state_rel AS zip_state
              ON zip_state.zcta5 = address.zip_code
            JOIN us_states AS state
              ON state.state_fips = zip_state.state_fips
            WHERE COALESCE(address.state_code, '') <> ''
              AND address.state_code <> state.code
        """),
        "US zip/state mismatch(es)",
    ),
]
