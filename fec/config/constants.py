"""
config/constants.py — Centralized sets for status words, skip values, and junk patterns.

These sets are used across safety_nets.py, post_merge_fixes.py, and other modules.
Single source of truth — update HERE, not in each function.
"""
import re

# ── Status words: occupations that are really life statuses ──────────
# Used to identify people whose "occupation" is a status, not a job.
STATUS_WORDS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'HOUSEWIFE',
    'SELF-EMPLOYED', 'STUDENT', 'UNEMPLOYED',
})

# ── Skip employers: values that are NOT real employer names ──────────
# Used when filtering for "real" employers (company names).
SKIP_EMPLOYERS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'SELF-EMPLOYED', 'STUDENT',
    'HOMEMAKER', 'UNEMPLOYED', 'CAMPAIGN/COMMITTEE',
    # Placeholder on ORGANIZATION entities (mirror of CAMPAIGN/COMMITTEE) —
    # a status marker, never a company.
    'ORGANIZATION',
    'NOT DISCLOSED', 'NONE', 'N/A', 'NA', '',
    # Common typos of status words — safety net so they never end up as
    # "real" employers if a donor mis-spells them on a filing.
    'NOT EMOLOYED', 'NOT EMPLOYEDD', 'NOT EMPLOYE', 'UNEMPLOYE',
    'RETIRED.', 'RETIRD', 'SELF EMPLOYED',
    # More mis-keyed non-employer values: 'not applicable', a self-employed
    # typo, and a stray occupation a donor entered in the employer field.
    'NOT APPLICABLE', 'NOT APPLICAABLE', 'SELP EMPLOYED', 'PHYSICAN',
})

# ── Skip occupations: values that are NOT real occupations ───────────
SKIP_OCCUPATIONS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'SELF-EMPLOYED', 'HOMEMAKER',
    'STUDENT', 'UNEMPLOYED', '',
})

# ── Wrong employer for committees ────────────────────────────────────
# If a COMMITTEE/PAC entity has one of these as employer, it's wrong.
WRONG_COMMITTEE_EMPLOYERS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'SELF-EMPLOYED', 'STUDENT',
    'HOMEMAKER', 'UNEMPLOYED', 'NOT DISCLOSED',
})

# ── Refusal words: people who refuse to disclose employer ────────────
REFUSAL_EMPLOYERS = frozenset({
    'PRIVATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'DECLINED TO ANSWER', 'REFUSED', 'NOT PROVIDED',
    'DECLINED TO STATE', 'NONE OF YOUR BUSINESS',
    # phrasing variants seen in the data
    'DECLINE TO STATE', 'PREFER NOT TO SAY',
    'NOT YOUR BUSINESS', 'NOTYOURBUSINESS',
    # Placeholders / keyboard junk a filer typed in the employer field. They
    # belong here rather than in SKIP_EMPLOYERS because they carry NO employer
    # information — this set feeds _NULL_EMPLOYER_WORDS (post-merge step AS),
    # which blanks the field, while SKIP_EMPLOYERS values are kept as a status.
    # 'HOME' = works-from-home placeholder, 'TBD' = to-be-determined,
    # 'VOLUNTEER' = unpaid, none of which name an employer.
    'NON', 'CHILD', 'UGH', 'SSSZSS', 'HOME', 'TBD', 'VOLUNTEER',
    # Mis-spelled FEC admin note — the correct "INFORMATION REQUESTED" is
    # already filtered, but this typo was surviving into employers.csv as a
    # company.
    'INFORMATION REQESTED',
    # Filer-typed refusals / non-answers (audit 2026-07-20). 'CTR' is one
    # donor's truncated CONTRACTOR occupation, never a company.
    'NOT IMPORTANT', 'NOT SURE WHY YOU NEED THIS',
    "EMPLOYER DOESN'T WANT ME TO DISCLOSE", 'NOT SPECIFIED',
    'FAMILY', 'YES', 'CTR', 'COMPANY NAME', 'COMPANY NAME (OPTIONAL)',
    # Audit 2026-07-21: last refusals left in contributor_employer.
    # 'PERSONAL' is the filer answering the question rather than naming a
    # company ("personal [business]"), not the adjective of a real firm.
    'PERSONAL', 'DO NOT WISH TO DISCLOSE',
    # Full-column audit 2026-07-21 — non-answers and keyboard junk.
    'RATHER NOT SAY', 'PRIVATE COMPANY', 'COMPANY', 'INDIVIDUAL',
    'RESIDENCE', 'BLANK', 'VARIOUS STUDIOS', 'HOMEOWNER',
    'XYX', 'MALE', 'NO E', 'GROUP', 'UNKEMPT CONCERT DONATION',
})

# ── OK 2-char employer abbreviations (real companies) ────────────────
OK_SHORT_EMPLOYERS = frozenset({
    '3M', 'HP', 'BP', 'GE', 'GM', 'LG', 'EY', 'PW',
    'BJ', 'C3', 'AT', 'QC', 'UBS', 'IBM',
})

# ── OK 2-char occupations (real job codes) ───────────────────────────
# Used to keep short occupations that are legitimate abbreviations
# when nulling truncated garbage values.
OK_SHORT_OCCUPATIONS = frozenset({
    'IT', 'MD', 'RN', 'PA', 'VP', 'DJ', 'GP', 'OT', 'PT',
})

# ── Junk employer values from raw FEC data ───────────────────────────
# Used in _fill_employer_from_raw to filter out garbage.
# NOTE: Removed 'SE' (could be SELF-EMPLOYED) and 'BD' (could be a real company).
RAW_JUNK_EMPLOYERS = frozenset({
    '', 'NONE', 'N/A', 'INFORMATION REQUESTED',
    'INFORMATION REQUESTED PER BEST EFFORTS',
    'NOT APPLICABLE', '--NONE--', 'PRIVATE', 'REFUSED',
    'DECLINED TO STATE', 'CONFIDENTIAL', 'PREFER NOT TO ANSWER',
    'NONE OF YOUR BUSINESS', 'AN EMPLOYER', 'EMPLOYED', 'JOB',
    'XX', 'TO', 'RPC', 'DT', 'KP', '.', 'FPC', 'ALPA',
})

# Structural non-company employer patterns — an employer matching any of
# these is not a company name. Pattern-based (vs the RAW_JUNK_EMPLOYERS
# denylist) so new junk is caught without enumerating it. Used by both the
# cleaning pipeline and the raw-recovery step, so blanked junk never
# reappears. Anchored full-match; values are upper-cased before testing.
JUNK_EMPLOYER_RE = (
    r'^(?:'
    r'[\W_]+'                            # pure punctuation / symbols
    r'|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}'  # date-like: 01-29-2022, 1/2/22
    r'|[\d\s\-./]+'                      # all-numeric strings / phone numbers
    r'|X{3,}(?:[-\s]?X+)*'               # masked digits: XXX-XX-XXXX, XXXXXXXXX
    r')$'
)

# Admin-note / refusal phrasings the FEC system or a filer writes INSTEAD of a
# company ("PER BEST EFFORTS", "REQUEST SENT", "DECLINED", "PREFER NOT TO
# DISCLOSE", "PENDING"…). Family-based + FULL-anchored (like JUNK_EMPLOYER_RE)
# so new wording is caught without enumerating every variant, while a real name
# that merely CONTAINS such a word (REQUEST FOODS INC, PENDING SYSTEMS LLC) is
# left untouched. Values are upper-cased before testing. Used by the cleaning
# safety net AND the raw-recovery filter so a blanked admin-note never reappears.
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

# ── Occupation words that look like employer names ───────────────────
# If these appear in the employer field, the person is probably
# self-employed in that profession.
OCCUPATION_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'DOCTOR', 'PHYSICIAN', 'DENTIST',
    'ACCOUNTANT', 'CONSULTANT', 'ARCHITECT', 'ENGINEER',
    'PROFESSOR', 'TEACHER', 'NURSE', 'PSYCHOLOGIST',
    'THERAPIST', 'SURGEON', 'RADIOLOGIST', 'DERMATOLOGIST',
    'REALTOR', 'PHARMACY OWNER', 'CARDIOLOGIST',
    # Audit 2026-07-21 — professional titles still sitting in the employer field
    'CPA', 'DESIGNER',
})

# ── Role/title words that appear as employer names ───────────────────
ROLE_AS_EMPLOYER = frozenset({
    'OWNER', 'CEO', 'PRESIDENT', 'FOUNDER', 'PRINCIPAL',
    'SENIOR DIRECTOR', 'MANAGING DIRECTOR', 'MANAGING PARTNER',
    'PARTNER',   # bare title — "MANAGING PARTNER" was listed, this wasn't
    # self-employment descriptors a donor wrote instead of a company —
    # these genuinely mean "works for themselves" → SELF-EMPLOYED
    'BUSINESS OWNER', 'BUSINESS OWNER/OPERATOR', 'ENTREPRENEUR',
    'BUSINESSMAN', 'BUSINESSWOMAN', 'INVESTOR', 'PRIVATE INVESTOR',
    # Audit 2026-07-21. CHAIRMAN/COO are titles whose filers put the real
    # company in the OCCUPATION field ('CHAIRMAN' + occ 'KIMCO'), so the
    # swap-back branch below recovers it. FREELANCE(R) is self-employment.
    'CHAIRMAN', 'COO', 'FREELANCE', 'FREELANCER',
    # Full-column audit 2026-07-21 — a role or a self-employment
    # arrangement written where the company belongs.
    'INDEPENDENT', 'INDEPENDENT CONTRACTOR', 'OWN BUSINESS',
    'PRIVATE PRACTICE', 'MONEY MANAGER', 'SHAREHOLDER',
    'MENTAL HEALTH PROFESSIONAL', 'PROJECT MANAGEMENT CONSULTANT',
    'PROFESSIONAL FINANCIAL ANALYST', 'EXEC DIRECTOR', 'EXEC',
    'SCHOOL COUNSELOR', 'DRIVING', 'LANDLORD',
    # truncated at the FEC's 38-char limit: 'independent contractor; affiliated with...'
    'INDEPENDENT CONTRACTOR; AFFILIATED WIT',
    # Compound titles whose filers put the COMPANY in the occupation field.
    # They must be listed here for AD0 (the cross-record swap net) to
    # recognise them as titles at all; AD0 recovers the company, and when
    # no company can be corroborated AD falls back to SELF-EMPLOYED.
    'STUDENT CUSTODIAN', 'FOUNDER AND MANAGING PRINCIPAL', 'DEPUTY CEO',
    'INTERIOR ARCHITECT', 'BUSINESS DEVELOPMENT', 'FOUNDING ATTORNEY',
    'MORTGAGE LOAN ORIGINATOR', 'MANAGING MEMBER & CO-FOUNDER',
    'INSURANCE BROKER', 'FINANCIAL ADVISOR',
})

# ── Industry / sector words a donor wrote instead of a company name ──
# "HEALTHCARE", "REAL ESTATE" name an INDUSTRY, not an employer, and (unlike
# OWNER/INVESTOR) don't imply self-employment. Best UX is an employer field
# that holds a real company or nothing — so these are NULLED while the
# occupation is kept. A filer who swapped the fields (occ is a real company)
# gets a swap-back instead.
SECTOR_AS_EMPLOYER = frozenset({
    'HEALTHCARE', 'HEALTH CARE', 'REAL ESTATE', 'REAL-ESTATE',
    'FINANCE', 'FINANCIAL', 'FINANCIAL SERVICES', 'INSURANCE',
    'TECHNOLOGY', 'EDUCATION', 'MANUFACTURING', 'CONSTRUCTION',
    'HOSPITALITY', 'RETAIL', 'AGRICULTURE', 'BANKING', 'ENERGY',
    'PHARMACEUTICALS', 'PHARMACEUTICAL', 'ENTERTAINMENT', 'MEDIA',
    'TELECOMMUNICATIONS', 'AUTOMOTIVE', 'AEROSPACE', 'LOGISTICS',
    'ENGINEERING', 'BIOTECHNOLOGY', 'BIOTECH', 'ACCOUNTING',
    'ADVERTISING', 'NONPROFIT', 'NON-PROFIT', 'GOVERNMENT',
    'EXECUTIVE', 'MANAGEMENT', 'SALES', 'MARKETING',
    # Bare institution-type words: name a KIND of workplace, not a workplace
    'HOSPITAL', 'LAW FIRM', 'LAW OFFICE', 'UNIVERSITY', 'SCHOOL',
    'BANK', 'CENTER', 'HOSPITALITY/TOURISM',
    # Full-column audit 2026-07-21 (all 8,869 distinct values reviewed).
    # Each names an INDUSTRY or a KIND of workplace, never a specific one,
    # so the employer is nulled and the occupation keeps the information.
    'TRANSPORTATION', 'SOFTWARE', 'HEDGE FUND', 'INVESTMENT BANK',
    'INSURANCE COMPANY', 'CONSTRUCTION CO', 'RE FIRM', 'REALESTATE',
    'MAGAZINE', 'MEDICAL', 'NON PROFIT', 'PUBLIC SECTOR', 'E COMMERCE',
    'COLLEGE', 'A BANK', 'CDN PROVIDER', 'STARTUP', 'STEALTH STARTUP',
    # Government: names the sector, not the agency
    'STATE EMPLOYEE', 'FEDERAL EMPLOYEE', 'LOCAL GOVERNMENT',
})

# ── Job titles that appear as employer (swap candidates) ─────────────
JOB_TITLE_AS_EMPLOYER = frozenset({
    'REAL ESTATE', 'INVESTMENT MANAGEMENT', 'INSURANCE',
    'CONSULTING', 'FINANCE', 'MARKETING', 'MANAGEMENT',
    'CONSTRUCTION', 'EDUCATION',
})

# ── Self-employed occupation words (found in employer field) ─────────
SELF_EMPLOYED_OCC_AS_EMP = frozenset({
    'DESIGNER', 'CATER', 'CATERER', 'WRITER', 'ARTIST', 'SALES',
    'REAL ESTATE', 'PERSONAL', 'BUSINESS', 'FARMING', 'TRADER',
})

# ── Foreign cities that should NOT be treated as US cities ───────────
# Checked against state to avoid false positives (London OH, etc.)
FOREIGN_CITIES_NO_US_STATE = frozenset({
    # Israel
    'REHOVOT', 'NETANYA', 'HERZLIYA', 'HAIFA', 'TEL AVIV',
    'RAANANA', 'MODIIN', 'ASHKELON', 'BEER SHEVA', 'PETAH TIKVA',
    'RISHON LEZION', 'KFAR SABA', 'GIVATAYIM', 'RAMAT GAN', 'SAVYON',
    # Canada
    'TORONTO', 'MONTREAL', 'VANCOUVER', 'OTTAWA', 'CALGARY',
    # Other (no US city of the same name — safe to flag unconditionally)
    'PARIS', 'BERLIN', 'TOKYO', 'SYDNEY', 'MELBOURNE',
    'MUMBAI', 'DELHI', 'BANGALORE', 'SAO PAULO', 'MEXICO CITY',
    'KRAGUJEVAC', 'PAPARA',
})

# Cities that exist BOTH as foreign cities AND as real US cities.
# Only flag these when the state doesn't match a known US location.
AMBIGUOUS_CITIES = {
    'LONDON':    {'OH', 'KY', 'KS', 'AR', 'TX'},
    'PARIS':     {'TX', 'TN', 'KY', 'IL', 'ME', 'AR'},
    'BERLIN':    {'CT', 'MD', 'NH', 'NJ', 'WI', 'PA'},
    'MOSCOW':    {'ID', 'PA', 'TN', 'TX'},
    'SYDNEY':    {'FL', 'MT', 'NE'},
    'MELBOURNE': {'FL'},
}

# ── Status words from raw FEC to normalized ──────────────────────────
RAW_STATUS_MAP = {
    'RETIRED': 'RETIRED', 'NOT EMPLOYED': 'NOT EMPLOYED',
    'SELF': 'SELF-EMPLOYED', 'SELF EMPLOYED': 'SELF-EMPLOYED',
    'SELF-EMPLOYED': 'SELF-EMPLOYED', 'SELFEMPLOYED': 'SELF-EMPLOYED',
    'HOMEMAKER': 'HOMEMAKER', 'HOUSEWIFE': 'HOMEMAKER',
    'STUDENT': 'STUDENT', 'UNEMPLOYED': 'NOT EMPLOYED',
    # Full-column audit 2026-07-21 — status misspellings found in the
    # employer field ('GARY-SELF' = a donor who typed his name onto SELF).
    'RETITED': 'RETIRED', 'NAT EMPLOYED': 'NOT EMPLOYED',
    'GARY-SELF': 'SELF-EMPLOYED',
    # Keyboard-slip spellings of SELF. The raw-recovery step (post-merge AL)
    # re-reads the ORIGINAL filing, so a typo the cleaning phase already fixed
    # comes back raw unless it is mapped here too — that is exactly how 'SWLF'
    # survived on 2 of one donor's 36 filings while the other 34 were correct.
    # Keep in sync with _SE_TYPOS in fec/cleaning/occupations.py.
    'SWLF': 'SELF-EMPLOYED', 'SLEF': 'SELF-EMPLOYED', 'SELP': 'SELF-EMPLOYED',
}

# ── Legal suffixes regex (for employer normalization) ────────────
# Strips LLC, INC, CORP, LTD, etc. from employer names for matching.
LEGAL_SUFFIX_RE = re.compile(
    r',?\s*\b'                                   # word boundary — don't strip inside words
    r'(LLC|LLP|L\.L\.C\.?|L\.L\.P\.?'
    r'|INC\.?|CORP\.?|LTD\.?'
    r'|LP|PLC'
    r'|P\.?C\.?|P\.?A\.?|P\.?L\.?L\.?C\.?)'
    r'\s*\.?\s*$',
    re.IGNORECASE,
)


# ── Values that are NOT a real employer name ─────────────────────────
# The shared answer to "is this string a company?", asked by every stage:
# resolve skips AI lookup for these, the loader keeps them out of the
# employers table, and the previous_employer contract clears them.
# Unions the skip/refusal sets above so there is no second list to keep
# in sync — a typo/status word added there is covered here automatically.
#
# Lives in config/ (data, not logic) because cleaning, resolve AND database
# all need it; before this it sat in fec/resolve/pipeline/constants.py and
# the other two layers had to import upward from resolve to reach it.
NOT_REAL_EMPLOYER = {
    "RETIRED", "SELF-EMPLOYED", "SELF EMPLOYED", "SELF",
    "SELF EMPL.", "SELF EMPL", "SELF-EMP", "SELF EMP",
    "NOT EMPLOYED", "NOT DISCLOSED", "NONE", "N/A", "NA",
    "STUDENT", "HOMEMAKER", "UNEMPLOYED", "UNKNOWN",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "REFUSED", "DECLINED TO STATE", "VOLUNTEER", "DISABLED",
    "CAMPAIGN/COMMITTEE", "",
    # Status / housewife variants the filer wrote in the employer field
    "RETIREE", "RETIEED", "RETIRE", "HOUSEWIFE", "HOUSWIFE", "HOUSE WIFE",
    # Occupations / job titles — NOT a company name (these belong in occupation)
    "PHYSICIAN", "ATTORNEY", "CONSULTANT", "INVESTOR", "PRIVATE INVESTOR",
    "BUSINESSMAN", "BUSINESSWOMAN", "ENTREPRENEUR", "PHILANTHROPIST", "EXECUTIVE",
    # Industries / sectors written instead of an employer
    "REAL ESTATE", "HEALTHCARE", "HEALTH CARE", "FINANCE", "FINANCIAL SERVICES",
} | SKIP_EMPLOYERS | REFUSAL_EMPLOYERS

# Prefix check for variants like "SELF EMPL.", "SELF-EMPL", etc.
NOT_REAL_PREFIXES = ("SELF EMPL", "SELF-EMPL", "NONE/", "N/A/", "NOT EMPLOYED")
