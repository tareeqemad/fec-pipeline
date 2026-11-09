"""Shared status values and employer patterns."""

# Occupations that are really life statuses.
STATUS_WORDS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'HOUSEWIFE',
    'SELF-EMPLOYED', 'STUDENT', 'UNEMPLOYED',
})

STATUS_CATEGORIES = STATUS_WORDS - {'HOUSEWIFE', 'UNEMPLOYED'}
NOT_EMPLOYED_VARIANTS = frozenset({'NOT EMPLOYED', 'UNEMPLOYED'})

# Values that are not occupations; HOUSEWIFE becomes HOMEMAKER.
SKIP_OCCUPATIONS = (STATUS_WORDS - {'HOUSEWIFE'}) | {''}

# Values that are not employer names: statuses, placeholders, typos.
SKIP_EMPLOYERS = SKIP_OCCUPATIONS | frozenset({
    'NOT DISCLOSED', 'NONE', 'N/A', 'NA',
    # Status slips are read by fec.cleaning.work.status_words.
    'RETIRED.', 'SELF EMPLOYED',
    'NOT APPLICABLE', 'NOT APPLICAABLE', 'PHYSICAN',
})

# Life statuses for matching; narrower than NOT_REAL_EMPLOYER.
EMPLOYER_STATUS_VALUES = SKIP_OCCUPATIONS | frozenset({
    'SELF EMPLOYED', 'NOT DISCLOSED', 'NONE', 'N/A', 'NA', 'NAN',
})

# Real brands containing a slash, kept verbatim and gate-checked.
SLASH_BRAND_EMPLOYERS = frozenset({
    'BRIDGESTONE/FIRESTONE',
})

# Refusals and non-answers; the shared base for null employers.
REFUSAL_EMPLOYERS = frozenset({
    'PRIVATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'DECLINED TO ANSWER', 'REFUSED', 'NOT PROVIDED', 'NOT PROVIDED BY CONTRIBUTOR',
    'DECLINED TO STATE', 'NONE OF YOUR BUSINESS',
    'DECLINE TO STATE', 'PREFER NOT TO SAY',
    'NOT YOUR BUSINESS', 'NOTYOURBUSINESS',
    # Placeholders with no employer info: HOME, TBD, VOLUNTEER.
    'NON', 'CHILD', 'UGH', 'SSSZSS', 'HOME', 'TBD', 'VOLUNTEER',
    # misspelled FEC admin note that was surviving into employer locations
    'INFORMATION REQESTED',
    # Filer refusals; CTR is one donor's truncated CONTRACTOR.
    'NOT IMPORTANT', 'NOT SURE WHY YOU NEED THIS',
    "EMPLOYER DOESN'T WANT ME TO DISCLOSE", 'NOT SPECIFIED',
    'FAMILY', 'YES', 'CTR', 'COMPANY NAME', 'COMPANY NAME (OPTIONAL)',
    # PERSONAL answers the question; it is no firm.
    'PERSONAL', 'DO NOT WISH TO DISCLOSE',
    'RATHER NOT SAY', 'PRIVATE COMPANY', 'COMPANY', 'INDIVIDUAL',
    'RESIDENCE', 'BLANK', 'VARIOUS STUDIOS', 'HOMEOWNER',
    'XYX', 'MALE', 'NO E', 'GROUP', 'UNKEMPT CONCERT DONATION',
})

# Whitelists for short but real values; confirmed entries only.

# employer abbreviations that are real companies
OK_SHORT_EMPLOYERS = frozenset({
    '3M', 'HP', 'BP', 'GE', 'GM', 'LG', 'EY', 'PW',
    'BJ', 'C3', 'AT', 'QC',
    # F5, UW and KP are real names, kept as filed.
    'F5', 'UW', 'KP',
})

# occupations and credentials that are real, not truncated garbage
OK_SHORT_OCCUPATIONS = frozenset({
    # medical
    'MD', 'DO', 'DDS', 'DMD', 'RN', 'LPN', 'NP', 'PA', 'PT', 'OT', 'DVM', 'GP',
    # legal / finance credentials
    'ESQ', 'JD', 'CPA', 'PHD', 'MBA',
    # executive titles
    'CEO', 'CFO', 'COO', 'CTO', 'CIO', 'CMO', 'CLO', 'CGO', 'CRO', 'CHRO',
    'VP', 'EVP', 'SVP', 'GM',
    # fields and roles
    'IT', 'HR', 'PR', 'RE', 'SW', 'UX', 'QA', 'VFX', 'CPT', 'RSM',
    'PM', 'PMO', 'SRE', 'DJ',
})

# A status word with a slip (SELF-EMPOLYED, RETIERD) is read by
# fec.cleaning.work.status_words; the few it cannot read are listed here.
STATUS_SLIPS_BEYOND_RULE = frozenset({'SLEF', 'SELP', 'SWLF', 'RETIREFT', 'RETIREF'})

# Structural junk checked against the whole uppercased employer value.
JUNK_EMPLOYER_RE = (
    r'^(?:'
    r'[\W_]+'                            # pure punctuation / symbols
    r'|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}'  # date-like: 01-29-2022, 1/2/22
    r'|[\d\s\-./]+'                      # all-numeric strings / phone numbers
    r'|X{3,}(?:[-\s]?X+)*'               # masked digits: XXX-XX-XXXX, XXXXXXXXX
    r')$'
)

# Whole-value admin notes and refusals; company names stay.
ADMIN_NOTE_EMPLOYER_RE = (
    r'^(?:'
    r'(?:PER\s+)?BEST\s+EFFORTS?'
    r'|(?:INFO(?:RMATION)?\s+)?REQUEST(?:ED)?(?:\s+(?:SENT|MADE|PENDING|PER\s+BEST\s+EFFORTS?|VIA\s+E?-?MAIL))?'
    r'|INFO\s+REQ(?:UEST)?|REQ\s+SENT'
    r'|NO\s+RESPONSE|NO\s+REPLY'
    r'|(?:WILL|TO)\s+(?:BE\s+)?PROVIDED?'
    r'|(?:SAME\s+AS\s+|SEE\s+)ABOVE'
    r'|PENDING'
    r'|DECLINED?(?:\s+TO\s+(?:STATE|ANSWER|DISCLOSE|PROVIDE|RESPOND|COMMENT))?'
    r'|PREFERS?\s+NOT\s+TO\s+(?:SAY|ANSWER|DISCLOSE|STATE|PROVIDE|COMMENT)'
    r'|NOT\s+(?:PROVIDED|GIVEN|DISCLOSED|AVAILABLE|APPLICABLE)'
    r'(?:\s+BY\s+(?:THE\s+)?(?:CONTRIBUTOR|DONOR|INDIVIDUAL|FILER))?'
    r')$'
)


# Checked against state to avoid false positives (London OH, etc.)
FOREIGN_CITIES_NO_US_STATE = frozenset({
    # Israel
    'REHOVOT', 'NETANYA', 'HERZLIYA', 'HAIFA', 'TEL AVIV',
    'RAANANA', 'MODIIN', 'ASHKELON', 'BEER SHEVA', 'PETAH TIKVA',
    'RISHON LEZION', 'KFAR SABA', 'GIVATAYIM', 'RAMAT GAN', 'SAVYON',
    # Canada-only names; shared US names go in AMBIGUOUS_CITIES.
    'MONTREAL', 'OTTAWA', 'CALGARY',
    # No US namesake, so flagged unconditionally.
    'TOKYO', 'MUMBAI', 'DELHI', 'BANGALORE', 'SAO PAULO', 'MEXICO CITY',
    'KRAGUJEVAC', 'PAPARA',
})

# Cities abroad and in the US: flagged on state mismatch.
AMBIGUOUS_CITIES = {
    'LONDON': {'OH', 'KY', 'KS', 'AR', 'TX'},
    'PARIS': {'TX', 'TN', 'KY', 'IL', 'ME', 'AR'},
    'BERLIN': {'CT', 'MD', 'NH', 'NJ', 'WI', 'PA'},
    'MOSCOW': {'ID', 'PA', 'TN', 'TX'},
    'SYDNEY': {'FL', 'MT', 'NE'},
    'MELBOURNE': {'FL'},
    'TORONTO': {'OH'},
    'VANCOUVER': {'WA'},
}
