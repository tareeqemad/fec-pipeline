"""Shared status values and employer patterns."""

from fec.vocabulary.tables import ordered_words, words

# Occupations that are really life statuses.
STATUS_WORDS = words("status_words")
# Values that are not occupations; HOUSEWIFE becomes HOMEMAKER.
SKIP_OCCUPATIONS = (STATUS_WORDS - {"HOUSEWIFE"}) | {""}
# Values that are not employer names: statuses, placeholders, typos.
SKIP_EMPLOYERS = SKIP_OCCUPATIONS | words("skip_employers_own")
# Life statuses for matching; narrower than NOT_REAL_EMPLOYER.
EMPLOYER_STATUS_VALUES = SKIP_OCCUPATIONS | words("employer_status_values_own")
# Refusals and non-answers; the shared base for null employers.
REFUSAL_EMPLOYERS = words("refusal_employers")
# Employer abbreviations that are real companies; confirmed entries only.
OK_SHORT_EMPLOYERS = words("ok_short_employers")
# A status word with a slip (SELF-EMPOLYED, RETIERD) is read by
# fec.cleaning.work.status_spellings; the few it cannot read are listed here.
STATUS_SLIPS_BEYOND_RULE = words("status_slips_beyond_rule")
# Structural junk checked against the whole uppercased employer value: symbols, dates, numbers, masked digits.
JUNK_EMPLOYER_RE = "^(?:" + "|".join(ordered_words("junk_employer_patterns")) + ")$"
# Whole-value admin notes and refusals; company names stay.
ADMIN_NOTE_EMPLOYER_RE = "^(?:" + "|".join(ordered_words("admin_note_employer_patterns")) + ")$"
