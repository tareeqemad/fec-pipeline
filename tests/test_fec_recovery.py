"""recover_addresses_from_fec applies cached FEC addresses (no network)."""
import json

import pandas as pd

from fec.cleaning.pipeline.fec_recovery import recover_addresses_from_fec


def _seed(tmp_path, entries):
    (tmp_path / "fec_address_cache.json").write_text(json.dumps(entries), encoding="utf-8")


def _row(street, name="GHITIS, LEO", city="GOLDEN BEACH", state="FL", zip_="", et="INDIVIDUAL"):
    return {"contributor_street_1": street, "contributor_name": name,
            "contributor_city": city, "contributor_state": state,
            "contributor_zip": zip_, "entity_type": et}


def test_no_outdir_is_a_noop():
    df = pd.DataFrame([_row("GOLDEN BEACH")])
    assert recover_addresses_from_fec(df, None) == 0


def test_applies_cached_address_and_normalizes(tmp_path):
    # raw FEC street has a trailing "ST." that must be normalised
    _seed(tmp_path, {"GHITIS, LEO|FL": {
        "street": "240 GOLDEN BEACH DR.", "city": "GOLDEN BEACH",
        "zip": "33160", "n": 22, "method": "fec_api"}})
    df = pd.DataFrame([_row("GOLDEN BEACH", zip_="")])
    n = recover_addresses_from_fec(df, str(tmp_path))   # all cached -> no network
    assert n == 1
    assert df.loc[0, "contributor_street_1"] == "240 GOLDEN BEACH DR"  # normalised
    assert df.loc[0, "contributor_zip"] == "33160"                     # blank zip filled


def test_different_city_is_left_for_review(tmp_path):
    # FEC address in a different city -> possible namesake -> skip
    _seed(tmp_path, {"MENACHE, DANIELE|NY": {
        "street": "130 W 67TH ST", "city": "NEW YORK", "n": 9, "method": "fec_api"}})
    df = pd.DataFrame([_row("ELVENTS", name="MENACHE, DANIELE", city="WATER MILL", state="NY")])
    n = recover_addresses_from_fec(df, str(tmp_path))
    assert n == 0
    assert df.loc[0, "contributor_street_1"] == "ELVENTS"


def test_usable_street_is_never_touched(tmp_path):
    _seed(tmp_path, {"GHITIS, LEO|FL": {"street": "999 OTHER ST", "city": "GOLDEN BEACH", "method": "fec_api"}})
    df = pd.DataFrame([_row("240 GOLDEN BEACH DR")])   # already usable
    assert recover_addresses_from_fec(df, str(tmp_path)) == 0
    assert df.loc[0, "contributor_street_1"] == "240 GOLDEN BEACH DR"


def test_cache_miss_no_key_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("FEC_API_KEY", raising=False)
    _seed(tmp_path, {})   # empty cache, no key -> nothing fetched, nothing applied
    df = pd.DataFrame([_row("GOLDEN BEACH")])
    assert recover_addresses_from_fec(df, str(tmp_path)) == 0
    assert df.loc[0, "contributor_street_1"] == "GOLDEN BEACH"
