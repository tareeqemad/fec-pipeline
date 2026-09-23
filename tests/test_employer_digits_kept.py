"""Digits stuck to a word are noise in an occupation but part of a company's name."""
import pandas as pd

from fec.cleaning.occupations.normalize import EMPLOYER_STATUS_TEXT, _normalize_text
from fec.config.employers import EMPLOYER_NORMALIZE


def test_employer_names_keep_their_digits():
    raw = pd.Series(["GENESIS10", "ROC360", "CENTURY21 KING REALTY", "MCJ2 JEWELS", "NUTRITION21", "HAWKEYE360"])
    out, _ = _normalize_text(raw, EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT)
    assert out.tolist() == raw.tolist()


def test_a_status_word_with_stray_digits_is_still_recognised():
    out, _ = _normalize_text(pd.Series(["SELF EMPLOYED47"]), {}, digits_only_before=EMPLOYER_STATUS_TEXT)
    assert out.iloc[0] == "SELF EMPLOYED"


def test_occupations_still_lose_keying_digits():
    out, _ = _normalize_text(pd.Series(["OWNER3", "ATTORNEY2"]), {})
    assert out.tolist() == ["OWNER", "ATTORNEY"]
