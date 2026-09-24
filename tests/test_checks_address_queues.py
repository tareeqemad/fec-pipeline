"""Address review queues: auto-fixed street_2 kept apart, mailboxes only for people,
queues built from the final rows (donor_key, later repairs, foreign filings left out)."""
import csv

import numpy as np
import pandas as pd

from fec.cleaning.address_review import (
    AUTO_FIXED,
    OPEN,
    apply_street2_fixes,
    build_review_queues,
    queue_counts,
)
from fec.cleaning.audit_trail import AuditTrail
from fec.cleaning.pipeline.address_stage import _report_address_issues
from fec.cleaning.pipeline.core import _write_address_queues


def _row(sub_id, name, street_1, street_2=np.nan, city="DENVER", state="CO", zip_="80202",
         entity="INDIVIDUAL", **extra):
    return {"sub_id": sub_id, "entity_type": entity, "contributor_name": name,
            "contributor_street_1": street_1, "contributor_street_2": street_2,
            "contributor_city": city, "contributor_state": state, "contributor_zip": zip_, **extra}


def _address_stage_frame():
    return pd.DataFrame([
        _row("1", "ZAKOWSKI, A", "10940 WILSHIRE BLVD", "STE", "LOS ANGELES", "CA", "90024"),
        _row("2", "OVES, LYNN", "55 S BATTERY PL", "# CA", "NEW YORK", "NY", "10004"),
        _row("3", "GRYCZMAN, M", "", np.nan, "MIAMI", "FL", "33139"),
        _row("4", "REHOVOT FILER", "5 HERZL ST", "# NY1179", "REHOVOT", "CA", "90000"),
        _row("5", "FINE FOR CONGRESS", "PO BOX 1", entity="COMMITTEE/PAC"),
        _row("6", "SMITH, JOHN", "PO BOX 2"),
    ])


def test_emptied_street2_is_auto_fixed_not_an_open_item():
    df = _address_stage_frame()
    df, auto_fixed, emptied = apply_street2_fixes(df)
    assert emptied == 3
    assert df.set_index("sub_id").loc[["1", "2", "4"], "contributor_street_2"].isna().all()

    review, regeocode = build_review_queues(df, auto_fixed)
    counts = queue_counts(review, regeocode)
    fixed = review[review["status"] == AUTO_FIXED]
    assert set(fixed["sub_id"]) == {"1", "2", "4"}
    assert set(fixed["contributor_street_2"]) == {"STE", "# CA", "# NY1179"}   # value before emptying
    assert not review.loc[review["status"] == OPEN, "sub_id"].isin(["1", "2", "4"]).any()
    assert counts == {"manual_review": int((review["status"] == OPEN).sum()),
                      "auto_fixed": 3, "regeocode": len(regeocode)}
    # open items come first
    assert list(review["status"]).index(AUTO_FIXED) >= int((review["status"] == OPEN).sum())


def test_mailboxes_are_queued_for_individuals_only():
    df = _address_stage_frame()
    _, regeocode = build_review_queues(df)
    mailbox = regeocode[regeocode["review_reason"].str.contains("PO Box|PMB")]
    assert set(mailbox["sub_id"]) == {"6"}
    # other re-geocode reasons are kept for every entity type
    assert "3" in set(regeocode.loc[regeocode["review_reason"].str.startswith("missing street_1"), "sub_id"])


def test_queues_from_final_rows_carry_donor_key_and_skip_later_repairs():
    df = _address_stage_frame()
    df, auto_fixed, _ = apply_street2_fixes(df)

    final = df.copy()
    final["donor_key"] = ["k1", "k2", "k3", "k4", "c5", "k6"]
    final.loc[final.sub_id == "3", "contributor_street_1"] = "129 ALTA AVE"   # filled by the donor stage
    final.loc[final.sub_id == "4", "contributor_street_2"] = "# NY1179"      # foreign row restored as filed
    review, regeocode = build_review_queues(final, auto_fixed, exclude_sub_ids=pd.Index(["4"]))

    assert "3" not in set(regeocode["sub_id"])                 # no longer missing street_1
    fixed = review[review["status"] == AUTO_FIXED]
    assert set(fixed["sub_id"]) == {"1", "2"}                  # the restored foreign value is not listed
    assert dict(zip(fixed["sub_id"], fixed["donor_key"])) == {"1": "k1", "2": "k2"}
    assert "4" not in set(review["sub_id"]) | set(regeocode["sub_id"])


def test_restored_street2_is_dropped_even_without_exclusion():
    df = _address_stage_frame()
    df, auto_fixed, _ = apply_street2_fixes(df)
    final = df.copy()
    final.loc[final.sub_id == "4", "contributor_street_2"] = "# NY1179"
    review, _ = build_review_queues(final, auto_fixed)
    assert set(review.loc[review["status"] == AUTO_FIXED, "sub_id"]) == {"1", "2"}


def test_near_duplicates_group_by_donor_key_once_known():
    rows = [
        _row("1", "BRITVAN, J ALLEN", "8 SPENCEHILL CT", city="PLEASANTVILLE", state="NY", zip_="10570", donor_key="b"),
        _row("2", "BRITVAN, J. ALLEN", "8 SPENCEHIL CT", city="PLEASANTVILLE", state="NY", zip_="10570", donor_key="b"),
        # a different person at the same house is never grouped with him
        _row("3", "BRITVAN, SARAH", "8 SPENCEHILS CT", city="PLEASANTVILLE", state="NY", zip_="10570", donor_key="s"),
    ]
    review, _ = build_review_queues(pd.DataFrame(rows))
    near = review[review["review_reason"].str.startswith("near-duplicate")]
    assert set(near["sub_id"]) == {"1", "2"}

    # before donors exist the raw name is the grouping key, so the two spellings stay apart
    review, _ = build_review_queues(pd.DataFrame(rows).drop(columns="donor_key"))
    assert review.empty or not review["review_reason"].str.startswith("near-duplicate").any()


def test_address_stage_defers_queues_when_the_pipeline_collects_them(tmp_path):
    logs = []
    reports = {}
    df = _address_stage_frame()
    df = _report_address_issues(df, AuditTrail(), str(tmp_path), logs.append, reports)
    assert not (tmp_path / "address_manual_review.csv").exists()   # written later, from the final rows
    assert set(reports["street2_auto_fixed"]["sub_id"]) == {"1", "2", "4"}
    assert df.set_index("sub_id").loc[["1", "2", "4"], "contributor_street_2"].isna().all()

    final = df.assign(donor_key=["k1", "k2", "k3", "k4", "c5", "k6"])
    _write_address_queues(final, str(tmp_path), reports, pd.Index(["4"]))
    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    regeo = list(csv.DictReader(open(tmp_path / "address_regeocode_suspects.csv", encoding="utf-8")))
    assert {r["sub_id"] for r in review if r["status"] == AUTO_FIXED} == {"1", "2"}
    assert all(r["donor_key"] for r in review + regeo)
    assert {r["sub_id"] for r in regeo if "PO Box" in r["review_reason"]} == {"6"}


def test_address_stage_still_writes_queues_on_its_own(tmp_path):
    df = _address_stage_frame()
    _report_address_issues(df, AuditTrail(), str(tmp_path), lambda _msg: None)
    review = list(csv.DictReader(open(tmp_path / "address_manual_review.csv", encoding="utf-8")))
    assert {r["sub_id"] for r in review if r["status"] == AUTO_FIXED} == {"1", "2", "4"}


def test_street2_step_edits_are_recorded_under_address_review():
    trail = AuditTrail()
    df = _address_stage_frame()
    trail.start(df)
    _report_address_issues(df, trail, None, lambda _msg: None, {})
    changed = {(r["sub_id"], r["field"], r["step"], r["after"]) for r in trail.records}
    assert changed == {(s, "contributor_street_2", "address_review", "") for s in ("1", "2", "4")}
