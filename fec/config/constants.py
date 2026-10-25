"""Shared status values and employer patterns."""

# Occupations that are really life statuses.
STATUS_WORDS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'HOUSEWIFE',
    'SELF-EMPLOYED', 'STUDENT', 'UNEMPLOYED',
})

STATUS_CATEGORIES = STATUS_WORDS - {'HOUSEWIFE', 'UNEMPLOYED'}
CANONICAL_EMPLOYER_SKIP_VALUES = (
    STATUS_CATEGORIES - {'HOMEMAKER', 'STUDENT'}
) | {'NOT DISCLOSED'}
NOT_EMPLOYED_VARIANTS = frozenset({'NOT EMPLOYED', 'UNEMPLOYED'})

# Values that are NOT real occupations. HOUSEWIFE is normalized to HOMEMAKER.
SKIP_OCCUPATIONS = (STATUS_WORDS - {'HOUSEWIFE'}) | {''}

# Values that are NOT real employer names: occupation statuses plus employer-only
# placeholders and typos. Keep the additions here instead of copying statuses.
SKIP_EMPLOYERS = SKIP_OCCUPATIONS | frozenset({
    'NOT DISCLOSED', 'NONE', 'N/A', 'NA',
    # status-word typos, so a misspelling never survives as a "real" employer
    'NOT EMOLOYED', 'NOT EMPLOYEDD', 'NOT EMPLOYE', 'UNEMPLOYE',
    'RETIRED.', 'RETIRD', 'SELF EMPLOYED',
    'NOT APPLICABLE', 'NOT APPLICAABLE', 'SELP EMPLOYED', 'PHYSICAN',
})

# Life statuses/placeholders used by donor matching and consistency checks.
# This is narrower than NOT_REAL_EMPLOYER: it excludes refusals, roles and sectors.
EMPLOYER_STATUS_VALUES = SKIP_OCCUPATIONS | frozenset({
    'SELF EMPLOYED', 'NOT DISCLOSED', 'NONE', 'N/A', 'NA', 'NAN',
})

# Real brand names that actually contain a slash; the slash-resolver keeps
# them verbatim and the quality gate asserts they survived.
SLASH_BRAND_EMPLOYERS = frozenset({
    'BRIDGESTONE/FIRESTONE',
})

# Refusals and non-answers. FINAL_NULL_EMPLOYERS extends this shared base;
# SKIP_EMPLOYERS values are kept as a status instead.
REFUSAL_EMPLOYERS = frozenset({
    'PRIVATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'DECLINED TO ANSWER', 'REFUSED', 'NOT PROVIDED', 'NOT PROVIDED BY CONTRIBUTOR',
    'DECLINED TO STATE', 'NONE OF YOUR BUSINESS',
    'DECLINE TO STATE', 'PREFER NOT TO SAY',
    'NOT YOUR BUSINESS', 'NOTYOURBUSINESS',
    # placeholders carrying NO employer info: HOME = works-from-home,
    # TBD = to-be-determined, VOLUNTEER = unpaid
    'NON', 'CHILD', 'UGH', 'SSSZSS', 'HOME', 'TBD', 'VOLUNTEER',
    # misspelled FEC admin note that was surviving into employer locations
    'INFORMATION REQESTED',
    # filer-typed refusals; CTR is one donor's truncated CONTRACTOR, never a company
    'NOT IMPORTANT', 'NOT SURE WHY YOU NEED THIS',
    "EMPLOYER DOESN'T WANT ME TO DISCLOSE", 'NOT SPECIFIED',
    'FAMILY', 'YES', 'CTR', 'COMPANY NAME', 'COMPANY NAME (OPTIONAL)',
    # PERSONAL = the filer answering the question, not the adjective of a real firm
    'PERSONAL', 'DO NOT WISH TO DISCLOSE',
    'RATHER NOT SAY', 'PRIVATE COMPANY', 'COMPANY', 'INDIVIDUAL',
    'RESIDENCE', 'BLANK', 'VARIOUS STUDIOS', 'HOMEOWNER',
    'XYX', 'MALE', 'NO E', 'GROUP', 'UNKEMPT CONCERT DONATION',
})

# Short-name whitelists - THE one home for "short but real" values.
# Confirmed-real entries only: real abbreviations are a finite, slow-growing
# set, so these whitelists converge; a junk denylist never would.
# Each consumer applies its own length gate (deep-clean nulls only <=2-char
# values, quality_scan advises on 2-5-char ones), so a longer entry is simply
# inert where it does not apply - one list serves both.

# employer abbreviations that are real companies
OK_SHORT_EMPLOYERS = frozenset({
    '3M', 'HP', 'BP', 'GE', 'GM', 'LG', 'EY', 'PW',
    'BJ', 'C3', 'AT', 'QC',
    # F5 (the company), UW (University of Washington, filed by a professor),
    # KP (Kaiser Permanente, filed by a physician): kept as filed
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

# Misspellings normalized to SELF-EMPLOYED during cleaning.
SELF_EMPLOYED_TYPOS = frozenset({
    'SLEF', 'SLEF-EMPLOYED', 'SLEF EMPLOYED',
    'SELF-EMPOLYED', 'SELF EMPOLYED', 'SELF-EMPLOYE', 'SELF EMPLOYE',
    'SELF-EMPLYED', 'SELF EMPLYED', 'SELF-EMPLO', 'SELF EMPLO',
    'SELFEMPLOYED', 'SELF-EMPLYOED', 'SELF EMPLYOED',
    'SEL-EMPLOYED', 'SEL EMPLOYED', 'SELD-EMPLOYED', 'SELD EMPLOYED',
    'SELP', 'SELP EMPLOYED', 'SELP-EMPLOYED',
    'SWLF', 'SWLF EMPLOYED', 'SWLF-EMPLOYED',
})

# RETIRED misspellings that break the RETIRE substring. Safety net T fixes
# these AND syncs occupation/category/status; employer_deep_clean keeps its
# own 4-member inline subset that fixes the employer field only - the overlap
# is deliberate, unifying them was measured to lose the occupation sync.
RETIRED_TYPO_EMPLOYERS = frozenset({
    'TETIRED', 'RETURED', 'RETIERD', 'RETIED', 'REITRED', 'RETIREE',
    'RETIREED', 'RERTIRED', 'RETIRD', 'REIRED', 'REITERED', 'RETITED',
    # seen as a previous employer copied from an older FEC filing
    'RETIREF',
})

# Structural junk checked against the whole uppercased employer value.
JUNK_EMPLOYER_RE = (
    r'^(?:'
    r'[\W_]+'                            # pure punctuation / symbols
    r'|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}'  # date-like: 01-29-2022, 1/2/22
    r'|[\d\s\-./]+'                      # all-numeric strings / phone numbers
    r'|X{3,}(?:[-\s]?X+)*'               # masked digits: XXX-XX-XXXX, XXXXXXXXX
    r')$'
)

# Full-value admin notes and refusals; company names containing these words stay.
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
    # Canada-only names in this dataset. Canadian names that also name a US
    # place belong in AMBIGUOUS_CITIES below and are checked against state.
    'MONTREAL', 'OTTAWA', 'CALGARY',
    # no US city of the same name - safe to flag unconditionally.
    # Cities that DO also exist in the US (Paris TX, Berlin CT...) belong in
    # AMBIGUOUS_CITIES below, never here.
    'TOKYO', 'MUMBAI', 'DELHI', 'BANGALORE', 'SAO PAULO', 'MEXICO CITY',
    'KRAGUJEVAC', 'PAPARA',
})

# Cities that exist BOTH abroad AND in the US — flag only when the state
# doesn't match a known US location.
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
