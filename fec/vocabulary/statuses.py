"""Shared status values and employer patterns."""

from fec.vocabulary.tables import words

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

# Whitelists for short but real values; confirmed entries only.

# employer abbreviations that are real companies
OK_SHORT_EMPLOYERS = words("ok_short_employers")

# A status word with a slip (SELF-EMPOLYED, RETIERD) is read by
# fec.cleaning.work.status_words; the few it cannot read are listed here.
STATUS_SLIPS_BEYOND_RULE = words("status_slips_beyond_rule")

# Structural junk checked against the whole uppercased employer value.
JUNK_EMPLOYER_RE = (
    r"^(?:"
    r"[\W_]+"  # pure punctuation / symbols
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"  # date-like: 01-29-2022, 1/2/22
    r"|[\d\s\-./]+"  # all-numeric strings / phone numbers
    r"|X{3,}(?:[-\s]?X+)*"  # masked digits: XXX-XX-XXXX, XXXXXXXXX
    r")$"
)

# Whole-value admin notes and refusals; company names stay.
ADMIN_NOTE_EMPLOYER_RE = (
    r"^(?:"
    r"(?:PER\s+)?BEST\s+EFFORTS?"
    r"|(?:INFO(?:RMATION)?\s+)?REQUEST(?:ED)?(?:\s+(?:SENT|MADE|PENDING|PER\s+BEST\s+EFFORTS?|VIA\s+E?-?MAIL))?"
    r"|INFO\s+REQ(?:UEST)?|REQ\s+SENT"
    r"|NO\s+RESPONSE|NO\s+REPLY"
    r"|(?:WILL|TO)\s+(?:BE\s+)?PROVIDED?"
    r"|(?:SAME\s+AS\s+|SEE\s+)ABOVE"
    r"|PENDING"
    r"|DECLINED?(?:\s+TO\s+(?:STATE|ANSWER|DISCLOSE|PROVIDE|RESPOND|COMMENT))?"
    r"|PREFERS?\s+NOT\s+TO\s+(?:SAY|ANSWER|DISCLOSE|STATE|PROVIDE|COMMENT)"
    r"|NOT\s+(?:PROVIDED|GIVEN|DISCLOSED|AVAILABLE|APPLICABLE)"
    r"(?:\s+BY\s+(?:THE\s+)?(?:CONTRIBUTOR|DONOR|INDIVIDUAL|FILER))?"
    r")$"
)
