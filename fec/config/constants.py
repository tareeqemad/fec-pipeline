"""Shared status/junk word sets and employer patterns — single source of truth."""

from fec.config.employers import EMPLOYER_NORMALIZE

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
    'DECLINED TO ANSWER', 'REFUSED', 'NOT PROVIDED',
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

# Every observed misspelling of SELF-EMPLOYED. The deep-clean step rewrites
# them during cleaning, and RAW_STATUS_MAP below derives from this set so the
# raw-recovery step re-normalizes them too (the SWLF lesson: a typo cleaning
# already fixed comes back raw unless the map knows it as well).
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

# Raw FEC status words -> normalized. The raw-recovery step (post-merge AL)
# re-reads the ORIGINAL filing, so every self-employed typo above is folded in.
RAW_STATUS_MAP = {
    'RETIRED': 'RETIRED',
    'HOMEMAKER': 'HOMEMAKER', 'HOUSEWIFE': 'HOMEMAKER',
    'STUDENT': 'STUDENT',
    'RETITED': 'RETIRED', 'NAT EMPLOYED': 'NOT EMPLOYED',
    'GARY-SELF': 'SELF-EMPLOYED',  # a donor who typed his name onto SELF
    **{typo: 'SELF-EMPLOYED' for typo in SELF_EMPLOYED_TYPOS},
    # every raw variant the cleaner itself normalizes (MYSELF, RETIREE,
    # SOLE PROPRIETOR...) - so the raw-recovery step can never resurrect a
    # status typo as a "company" the cleaner would have normalized
    **EMPLOYER_NORMALIZE,
}

# Junk employer values from raw FEC data (used in _fill_employer_from_raw).
# 'SE' (could be SELF-EMPLOYED) and 'BD' (could be a real company) deliberately absent.
RAW_JUNK_EMPLOYERS = frozenset({
    '', 'NONE', 'N/A', 'INFORMATION REQUESTED',
    'INFORMATION REQUESTED PER BEST EFFORTS',
    'NOT APPLICABLE', '--NONE--', 'PRIVATE', 'REFUSED',
    'DECLINED TO STATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'NONE OF YOUR BUSINESS', 'AN EMPLOYER', 'EMPLOYED', 'JOB',
    'XX', 'TO', 'RPC', 'DT', 'KP', '.', 'FPC', 'ALPA',
})

# Structural non-company patterns — pattern-based (vs the denylist above) so
# new junk is caught without enumerating it. Anchored full-match; values are
# upper-cased before testing. Used by the cleaning pipeline AND the
# raw-recovery step, so blanked junk never reappears.
JUNK_EMPLOYER_RE = (
    r'^(?:'
    r'[\W_]+'                            # pure punctuation / symbols
    r'|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}'  # date-like: 01-29-2022, 1/2/22
    r'|[\d\s\-./]+'                      # all-numeric strings / phone numbers
    r'|X{3,}(?:[-\s]?X+)*'               # masked digits: XXX-XX-XXXX, XXXXXXXXX
    r')$'
)

# Admin-note / refusal phrasings written INSTEAD of a company. Family-based +
# FULL-anchored so new wording is caught while a real name merely CONTAINING
# such a word (REQUEST FOODS INC, PENDING SYSTEMS LLC) survives. Values are
# upper-cased before testing. Used by the cleaning safety net AND the
# raw-recovery filter, so a blanked admin-note never reappears.
ADMIN_NOTE_EMPLOYER_RE = (
    r'^(?:'
    r'(?:PER\s+)?BEST\s+EFFORTS?'
    r'|(?:INFO(?:RMATION)?\s+)?REQUEST(?:ED)?(?:\s+(?:SENT|MADE|PENDING|PER\s+BEST\s+EFFORTS?))?'
    r'|INFO\s+REQ(?:UEST)?|REQ\s+SENT'
    r'|NO\s+RESPONSE|NO\s+REPLY'
    r'|(?:WILL|TO)\s+(?:BE\s+)?PROVIDED?'
    r'|(?:SAME\s+AS\s+|SEE\s+)ABOVE'
    r'|PENDING'
    r'|DECLINED?(?:\s+TO\s+(?:STATE|ANSWER|DISCLOSE|PROVIDE|RESPOND|COMMENT))?'
    r'|PREFERS?\s+NOT\s+TO\s+(?:SAY|ANSWER|DISCLOSE|STATE|PROVIDE|COMMENT)'
    r'|NOT\s+(?:PROVIDED|GIVEN|DISCLOSED|AVAILABLE|APPLICABLE)'
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
