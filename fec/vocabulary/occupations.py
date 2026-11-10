"""Occupation and employer spellings, the categories in use, and cross-field rules."""

from fec.common.settings import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_OVERRIDES_CSV
from fec.vocabulary.not_employers import OCCUPATION_AS_EMPLOYER as _OCCUPATION_AS_EMPLOYER
from fec.vocabulary.statuses import REFUSAL_EMPLOYERS as _REFUSAL_EMPLOYERS
from fec.vocabulary.tables import CONFIG_DIR, read_map, read_set, words

_nan = float("nan")

# No HOMEMAKER: _deep_clean_employer fixes it with occupation.
EMPLOYER_NORMALIZE = read_map(CONFIG_DIR / "employer_spellings.csv")

# Occupation corrections: a filed spelling, abbreviation or typo and the
# occupation it means; [CLEAR] marks a placeholder that means no occupation.
# One table, applied when the text is first read and again after each
# step that can expose a listed form.
CLEAR = "[CLEAR]"

OCCUPATION_CORRECTIONS = {
    written: (None if occupation == CLEAR else occupation)
    for written, occupation in read_map(CONFIG_DIR / "occupation_corrections.csv").items()
}

# The same corrections without the placeholders, for text replacement.
TEXT_CORRECTIONS = {written: occupation for written, occupation in OCCUPATION_CORRECTIONS.items() if occupation}

# Employer spellings first; occupation rows override them.
OCCUPATION_NORMALIZE = {
    **EMPLOYER_NORMALIZE,
    **{written: (_nan if occupation is None else occupation) for written, occupation in OCCUPATION_CORRECTIONS.items()},
}

# Each occupation's category: the model's answers, then the reviewed
# overrides on top. Unlisted texts are classified by the model when the
# pipeline runs (fec.cleaning.work.categorize); until then they are OTHER.
CATEGORY_OVERRIDES = {
    **(read_map(OCCUPATION_CATEGORY_CACHE_CSV) if OCCUPATION_CATEGORY_CACHE_CSV.exists() else {}),
    **(read_map(OCCUPATION_CATEGORY_OVERRIDES_CSV) if OCCUPATION_CATEGORY_OVERRIDES_CSV.exists() else {}),
}


# Occupation status -> employer during the field-level pass.
EMPLOYER_FROM_OCCUPATION = {
    "RETIRED": "RETIRED",
    "NOT EMPLOYED": "NOT EMPLOYED",
    "HOMEMAKER": "NOT EMPLOYED",
    "SELF-EMPLOYED": "SELF-EMPLOYED",
    "STUDENT": "STUDENT",
}

# Donor consistency keeps homemaker distinct and handles aliases.
FINAL_EMPLOYER_FROM_OCCUPATION = {
    **EMPLOYER_FROM_OCCUPATION,
    "HOMEMAKER": "HOMEMAKER",
    "HOUSEWIFE": "HOMEMAKER",
    "UNEMPLOYED": "NOT EMPLOYED",
}

EMPLOYER_FROM_CATEGORY = {
    category: FINAL_EMPLOYER_FROM_OCCUPATION[category]
    for category in ("RETIRED", "NOT EMPLOYED", "HOMEMAKER", "STUDENT", "SELF-EMPLOYED")
}

SWAP_JOB_TITLES = _OCCUPATION_AS_EMPLOYER | words("swap_job_titles_own")

OCCUPATION_KEYWORDS = _OCCUPATION_AS_EMPLOYER | words("occupation_keywords_own")

FINAL_SHORT_OCCUPATIONS = words("final_short_occupations")

FINAL_JUNK_EMPLOYERS = words("final_junk_employers")

FINAL_NULL_EMPLOYERS = _REFUSAL_EMPLOYERS | words("final_null_employers_own")

WEB_ARTIFACT_OCCUPATIONS = words("web_artifact_occupations")

KNOWN_COMPANY_OCCUPATIONS = words("known_company_occupations")

HOMEMAKER_EMPLOYER_VALUES = words("homemaker_employer_values")

VALID_CATEGORIES = read_set(CONFIG_DIR / "occupation_categories.csv")
