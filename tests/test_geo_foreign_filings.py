"""Foreign contributor filings the detector missed are now kept as filed.

MADRID / IL / 28012 (Calle Doctor Fourquet, Madrid) was geocoded to Hanover Park IL,
EFRAT / IL / 90435 (Israel read as Illinois) lost its postcode, and
TORONTO / OH / 19273 (Briar Hill Ave, Toronto, Ontario) was pinned in Toronto, Ohio.

Toronto, Ohio is a real place, so that filing is flagged by its reviewed sub_id: a
ZIP from another state proves only a ZIP typo, never an address abroad.
"""
from pathlib import Path

import pandas as pd
import pytest

from fec.cleaning.addresses.foreign import foreign_address_mask

RAW_CSV = Path(__file__).resolve().parents[1] / "data" / "contributions.csv"
MISSED_SUB_IDS = {
    "4072420241978925325",   # ENRICH, POL - Madrid
    "4072420241978925343",   # ENRICH, POL - Madrid
    "4052520221493485193",   # DANZIG, GABRIEL - Efrat
    "4062420241962026749",   # FISHER, HAROLD - Toronto, Ontario
}


def _frame(rows):
    return pd.DataFrame(rows, columns=[
        "sub_id", "contributor_street_1", "contributor_street_2",
        "contributor_city", "contributor_state", "contributor_zip",
    ])


def test_missed_foreign_filings_are_detected():
    df = _frame([
        ("1", "CALLE DOCTOR FOURQUET 33", "", "MADRID", "IL", "28012"),
        ("2", "37/1 PITOM HAKETORET", "", "EFRAT", "IL", "90435"),
        ("4062420241962026749", "540 BRIAR HILL AVE.", "", "TORONTO", "OH", "19273"),
        ("4", "1 HAGEFEN ST", "", "BEIT SHEMESH", "NJ", "07666"),
    ])
    assert foreign_address_mask(df).tolist() == [True, True, True, True]


def test_us_places_with_those_names_stay_american():
    df = _frame([
        ("1", "100 MAIN ST", "", "MADRID", "IA", "50156"),        # Madrid, Iowa
        ("2", "12 ELM ST", "", "TORONTO", "OH", "43964"),         # Toronto, Ohio with its own ZIP
        ("3", "100 MAIN ST", "", "LONDON", "KY", "40741"),        # London, Kentucky
        ("4", "4075 LINGLESTOWN RD", "", "HARRISBURG", "IL", "17112"),  # a US state typo, not abroad
        ("5", "100 MAIN ST", "", "TORONTO", "OH", ""),            # no ZIP to contradict the state
        ("6", "100 MAIN ST", "", "LONDON", "KY", "4074"),         # malformed ZIP proves nothing
        # border metros: a ZIP from the neighbouring state is a typo, not a foreign address
        ("7", "100 MAIN ST", "", "VANCOUVER", "WA", "97201"),     # Portland ZIP
        ("8", "100 MAIN ST", "", "MOSCOW", "ID", "99163"),        # Pullman WA ZIP
        ("9", "100 MAIN ST", "", "BERLIN", "MD", "19975"),        # Selbyville DE ZIP
        ("10", "100 MAIN ST", "", "PARIS", "TN", "42001"),        # Kentucky ZIP
        ("11", "540 BRIAR HILL AVE.", "", "TORONTO", "OH", "19273"),  # same text, not the reviewed filing
    ])
    assert foreign_address_mask(df).tolist() == [False] * 11


def test_mask_works_without_a_sub_id_column():
    df = _frame([("1", "CALLE DOCTOR FOURQUET 33", "", "MADRID", "IL", "28012")]).drop(columns="sub_id")
    assert foreign_address_mask(df).tolist() == [True]


def test_the_raw_filings_behind_the_audit_are_flagged():
    if not RAW_CSV.exists():
        pytest.skip("data/contributions.csv not present")
    raw = pd.read_csv(RAW_CSV, dtype=str, keep_default_na=False)
    present = raw[raw["sub_id"].isin(MISSED_SUB_IDS)]
    if present.empty:
        pytest.skip("audit rows not in this pull")
    flagged = set(present.loc[foreign_address_mask(present), "sub_id"])
    assert flagged == set(present["sub_id"])
