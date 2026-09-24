"""tools/audits/address_audit.py ZIP check: a cleaned city no other filing pairs with the row's ZIP5
is reported in full (never sampled away as a 'format' flag) and fails the audit."""
import importlib.util
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
ADDR = ["contributor_street_1", "contributor_street_2", "contributor_city", "contributor_state", "contributor_zip"]


def _audit():
    spec = importlib.util.spec_from_file_location("address_audit", REPO / "tools" / "audits" / "address_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames(rows):
    """rows: (sub_id, donor_key, entity, name, street, raw_city, cleaned_city, state, raw_zip, cleaned_zip)."""
    raw = pd.DataFrame([
        {"sub_id": r[0], "contributor_street_1": r[4], "contributor_street_2": "", "contributor_city": r[5],
         "contributor_state": r[7], "contributor_zip": r[8]} for r in rows])
    cln = pd.DataFrame([
        {"sub_id": r[0], "donor_key": r[1], "entity_type": r[2], "contributor_name": r[3], "contributor_street_1": r[4],
         "contributor_street_2": "", "contributor_city": r[6], "contributor_state": r[7], "contributor_zip": r[9]}
        for r in rows])
    return raw, cln


# Other filers put MANHATTAN BEACH at 90266 (139 in the real pull); NEWMAN also files LOS ANGELES (a work address at 90013),
# so the donor-history test passes for his rows, but no one files LOS ANGELES with 90266.
ROWS = (
    [(f"mb{i}", f"k{i}", "INDIVIDUAL", f"OTHER{i}, A", f"{i} OCEAN DR", "MANHATTAN BEACH", "MANHATTAN BEACH", "CA",
      "902661234", "90266") for i in range(5)]
    + [
        ("n1", "newman", "INDIVIDUAL", "NEWMAN, SAMUEL", "118 S POINSETTIA AVE", "MANHATTAN BEACH", "LOS ANGELES", "CA", "902666642", "90266"),
        ("n2", "newman", "INDIVIDUAL", "NEWMAN, SAMUEL", "118 S POINSETTIA AVE", "LOS ANGELS", "LOS ANGELES", "CA", "90266", "90266"),
        ("n3", "newman", "INDIVIDUAL", "NEWMAN, SAMUEL", "555 W 5TH ST", "LOS ANGELES", "LOS ANGELES", "CA", "900131007", "90013"),
        ("h1", "hoffman", "INDIVIDUAL", "HOFFMAN, BRADLEY", "PO BOX 280952", "EAST HARTFORD", "WEST HARTFORD", "CT", "06128", "06128"),
        ("w1", "k9", "INDIVIDUAL", "WEST, ANN", "1 FARMINGTON AVE", "WEST HARTFORD", "WEST HARTFORD", "CT", "06107", "06107"),
        # abbreviation expansions and a supported typo fix are not contradictions
        ("m1", "mb", "INDIVIDUAL", "HELD, JERRY", "1 CRESTLINE RD", "MOUNTAIN BRK", "MOUNTAIN BROOK", "AL", "352434817", "35243"),
        ("m2", "mb2", "INDIVIDUAL", "MEYERSON, ED", "2 CRESTLINE RD", "BIRMINGHAM", "MOUNTAIN BROOK", "AL", "35243", "35243"),
        ("g1", "pat", "INDIVIDUAL", "PATTERSON, ADAM", "45 HIGHFIELD RD", "GLENCOVE", "GLEN COVE", "NY", "11542", "11542"),
        ("g2", "k10", "INDIVIDUAL", "OTHER, B", "9 GLEN ST", "GLEN COVE", "GLEN COVE", "NY", "11542", "11542"),
        # the check covers committees too
        ("c1", "comm", "COMMITTEE/PAC", "SOME PAC", "PO BOX 1", "NY", "NEW YORK", "NY", "11204", "11204"),
        ("b1", "k11", "INDIVIDUAL", "OTHER, C", "5817 21ST AVE", "BROOKLYN", "BROOKLYN", "NY", "11204", "11204"),
        # a reviewed same-place rename is listed but does not fail the audit
        ("a1", "agar", "INDIVIDUAL", "AGAR, JOHN", "1 SEA PINES", "HILTON HEAD", "HILTON HEAD ISLAND", "SC", "299262601", "29926"),
    ]
)


def test_same_city_name_allows_abbreviations_but_not_other_places():
    audit = _audit()
    assert audit.same_city_name("MOUNTAIN BROOK", "MOUNTAIN BRK")
    assert audit.same_city_name("MAYFIELD HTS", "MAYFIELD HEIGHTS")
    assert audit.same_city_name("COLORADO SPGS", "COLORADO SPRINGS")
    assert not audit.same_city_name("E HARTFORD", "W HARTFORD")      # EAST/WEST after norm()
    assert not audit.same_city_name("N BRUNSWICK", "NEW BRUNSWICK")  # a direction letter is not an abbreviation
    assert not audit.same_city_name("LOS ANGELS", "LOS ANGELES")     # a typo is a different spelling, not a short form
    assert not audit.same_city_name("NY", "NEW YORK")
    assert not audit.same_city_name("PALM BEACH", "PALM BEACH GARDENS")


def test_zip_contradictions_catch_the_swaps_and_spare_supported_changes():
    audit = _audit()
    raw, cln = _frames(ROWS)
    z = audit.zip_city_contradictions(raw.set_index("sub_id"), cln.set_index("sub_id"))
    assert set(z.index) == {"n1", "n2", "h1", "c1", "a1"}
    assert z.loc["h1", "cities_filed_at_zip"] == "(no other filing)"   # its own filing does not count
    assert z.loc["n1", "cities_filed_at_zip"] == "MANHATTAN BEACH x5; LOS ANGELS x1"   # 5 other filers + the other Newman typo row
    assert z.loc["c1", "entity_type"] == "COMMITTEE/PAC"
    assert list(z.index[z.reviewed_same_place]) == ["a1"]


def _write_data(tmp_path, rows, changes):
    raw, cln = _frames(rows)
    data = tmp_path / "data"
    data.mkdir()
    raw.to_csv(data / "contributions.csv", index=False)
    cln.to_csv(data / "contributions_cleaned.csv", index=False)
    pd.DataFrame(changes, columns=["sub_id", "field", "step"]).to_csv(data / "audit_changes.csv", index=False)


def test_audit_lists_zip_swaps_in_full_and_exits_1(tmp_path, monkeypatch, capsys):
    changes = [(r[0], "contributor_city", "cities_normalize") for r in ROWS if r[5] != r[6]]
    changes = [c if c[0] != "n1" else ("n1", "contributor_city", "address_same_street_align") for c in changes]
    # plenty of other format-step flags, so a sample of 30 would easily miss the swap
    filler = [(f"f{i}", f"fk{i}", "INDIVIDUAL", f"FILL{i}, X", f"{i} ELM ST", "SPRINGFELD", "SPRINGFIELD", "IL",
               "62701", "62701") for i in range(60)]
    filler.append(("f_ok", "fk_ok", "INDIVIDUAL", "OK, Y", "1 ELM ST", "SPRINGFIELD", "SPRINGFIELD", "IL", "62701", "62701"))
    changes += [(r[0], "contributor_city", "cities_normalize") for r in filler if r[5] != r[6]]
    _write_data(tmp_path, ROWS + filler, changes)
    monkeypatch.chdir(tmp_path)

    code = _audit().main(["--out-dir", str(tmp_path / "out")])

    out = capsys.readouterr().out
    assert code == 1
    assert "ZIP CHECK" in out and "FAIL: 4 ZIP-contradicted" in out
    for sub_id in ("h1", "n1", "n2", "c1"):
        assert sub_id in out
    flags = pd.read_csv(tmp_path / "out" / "address_audit_flags.csv", dtype=str, keep_default_na=False)
    assert set(flags.loc[flags.flag == "contributor_city_not_filed_with_zip", "sub_id"]) == {"h1", "n1", "n2", "c1", "a1"}
    assert (tmp_path / "out" / "address_audit_zip_city.csv").exists()
    assert not (tmp_path / "data" / "_review").exists()      # --out-dir keeps data/ read-only


def test_audit_passes_when_cities_agree_with_their_zip(tmp_path, monkeypatch, capsys):
    rows = [r for r in ROWS if r[0] not in {"n1", "n2", "h1", "c1"}]
    _write_data(tmp_path, rows, [(r[0], "contributor_city", "cities_normalize") for r in rows if r[5] != r[6]])
    monkeypatch.chdir(tmp_path)
    assert _audit().main(["--out-dir", str(tmp_path / "out")]) == 0
    assert "0 row(s) carry a cleaned city" in capsys.readouterr().out


def test_zip_scoped_review_entry_covers_only_its_zip():
    audit = _audit()
    rows = [
        ("l1", "levy", "INDIVIDUAL", "LEVY, STEVEN", "BROOKLYN", "NY", "BROOKLYN", "NY", "11239", "11239"),
        ("l2", "other", "INDIVIDUAL", "OTHER, D", "1 MAIN ST", "NY", "BROOKLYN", "NY", "10022", "10022"),
        ("l3", "k12", "INDIVIDUAL", "OTHER, E", "2 MAIN ST", "NEW YORK", "NEW YORK", "NY", "10022", "10022"),
    ]
    raw, cln = _frames(rows)
    z = audit.zip_city_contradictions(raw.set_index("sub_id"), cln.set_index("sub_id"))
    assert z.reviewed_same_place.to_dict() == {"l1": True, "l2": False}
